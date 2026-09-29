"""Measure the current Core with real SM2/SM3 and isolated SQLite storage.

uv run --project backend --no-editable python scripts/benchmark_core.py
uv run --project backend --no-editable python scripts/benchmark_core.py \
    --history-sizes 100 1000 10000 --samples 25 --objects 16 \
    --object-bytes 65536 --concurrency 4 --verification-jobs 12

Every run creates a new output directory. A newly generated temporary private key
is deleted at exit; deployed keys and .runtime/core-demo are never opened.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import uvicorn

from ra_agent.audit import (
    AuditExportService,
    AuditVerifier,
    FileCheckpointStore,
    FileEvidenceRecorder,
    HashChain,
    create_checkpoint,
)
from ra_agent.audit.integrity import MAX_AUDIT_BUNDLE_BYTES, encode_bundle
from ra_agent.core.config import CoreCryptoMode, RuntimeMode, Settings
from ra_agent.crypto import (
    CryptoError,
    EnvelopeService,
    OpenSSLSignatureProvider,
    find_openssl,
    generate_sm2_key,
)
from ra_agent.events import BehaviorEvent, SqliteEventStore
from ra_agent.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def memory_reader() -> tuple[str, Callable[[], tuple[int, int]]]:
    """Return current RSS and OS lifetime high-water RSS, both in bytes."""
    if os.name == "nt":
        import ctypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("PageFaultCount", ctypes.c_uint32),
                *[
                    (name, ctypes.c_size_t)
                    for name in (
                        "PeakWorkingSetSize",
                        "WorkingSetSize",
                        "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage",
                        "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage",
                        "PagefileUsage",
                        "PeakPagefileUsage",
                    )
                ],
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.argtypes = []
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        read = psapi.GetProcessMemoryInfo
        read.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_uint32,
        ]
        read.restype = ctypes.c_int
        handle = kernel.GetCurrentProcess()

        def windows_read() -> tuple[int, int]:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not read(handle, ctypes.byref(counters), counters.cb):
                raise ctypes.WinError(ctypes.get_last_error())
            return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)

        return (
            "Windows GetProcessMemoryInfo WorkingSetSize/PeakWorkingSetSize",
            windows_read,
        )
    if sys.platform.startswith("linux"):

        def linux_read() -> tuple[int, int]:
            fields = dict(
                line.split(":", 1)
                for line in Path("/proc/self/status").read_text().splitlines()
                if ":" in line
            )
            return (
                int(fields["VmRSS"].split()[0]) * 1024,
                int(fields["VmHWM"].split()[0]) * 1024,
            )

        return "Linux /proc/self/status VmRSS/VmHWM", linux_read
    raise OSError(f"RSS sampling unsupported on {sys.platform}")


class ProcessMemorySampler:
    """Sample in a separate thread; OS peak also catches between-sample peaks."""

    def __init__(self) -> None:
        self.interval = 0.05
        self.started = time.perf_counter_ns()
        self.phase = "initialization"
        self.rows: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None
        self.backend = "unsupported"
        self.error: str | None = None
        try:
            self.backend, self.read = memory_reader()
            self.sample()
        except (OSError, AttributeError, KeyError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def sample(self) -> None:
        rss, peak = self.read()
        with self.lock:
            self.rows.append(
                {
                    "elapsed_ms": (time.perf_counter_ns() - self.started) / 1_000_000,
                    "phase": self.phase,
                    "rss_bytes": rss,
                    "os_lifetime_peak_rss_bytes": peak,
                }
            )

    def start(self) -> None:
        if self.error is not None:
            return

        def worker() -> None:
            while not self.stopped.wait(self.interval):
                try:
                    self.sample()
                except (OSError, AttributeError, KeyError, ValueError) as exc:
                    self.error = f"{type(exc).__name__}: {exc}"
                    return

        self.thread = threading.Thread(target=worker, name="rss-sampler", daemon=True)
        self.thread.start()

    def set_phase(self, phase: str) -> None:
        with self.lock:
            self.phase = phase

    def stop(self) -> None:
        self.stopped.set()
        if self.thread is not None:
            self.thread.join()
        if self.error is None:
            self.sample()

    def report(self) -> dict[str, Any]:
        with self.lock:
            rows = list(self.rows)
        phases: dict[str, dict[str, int]] = {}
        for row in rows:
            phase = phases.setdefault(
                row["phase"], {"samples": 0, "sampled_peak_rss_bytes": 0}
            )
            phase["samples"] += 1
            phase["sampled_peak_rss_bytes"] = max(
                phase["sampled_peak_rss_bytes"], row["rss_bytes"]
            )
        return {
            "status": "supported"
            if self.error is None
            else ("error" if rows else "unsupported"),
            "backend": self.backend,
            "error": self.error,
            "scope": "Current Python process, including benchmark client and Uvicorn server; excludes OpenSSL and offline CLI child processes and all unrelated processes.",
            "peak_semantics": "OS peak is process-lifetime high-water RSS, including startup before sampling; phase peaks are periodic current-RSS samples and may miss brief spikes. Peaks are not reset between phases.",
            "requested_sample_interval_ms": self.interval * 1000,
            "max_sample_gap_ms": max(
                (b["elapsed_ms"] - a["elapsed_ms"] for a, b in zip(rows, rows[1:])),
                default=None,
            ),
            "sampled_peak_rss_bytes": max(
                (row["rss_bytes"] for row in rows), default=None
            ),
            "os_lifetime_peak_rss_bytes": max(
                (row["os_lifetime_peak_rss_bytes"] for row in rows), default=None
            ),
            "phases": phases,
            "raw_samples": rows,
        }


def distribution(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)

    def percentile(fraction: float) -> float | None:
        if not ordered:
            return None
        position = (len(ordered) - 1) * fraction
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        return round(
            ordered[low] + (ordered[high] - ordered[low]) * (position - low), 4
        )

    return {
        "samples": len(values),
        "min_ms": round(min(values), 4) if values else None,
        "p50_ms": percentile(0.5),
        "p95_ms": percentile(0.95),
        "max_ms": round(max(values), 4) if values else None,
        "mean_ms": round(statistics.mean(values), 4) if values else None,
    }


def event(index: int, task_id: str, reference: str | None = None) -> dict[str, Any]:
    return {
        "event_id": f"event-{index}",
        "task_id": task_id,
        "parent_event_id": None,
        "type": "EXECUTION_STARTED",
        "actor": "gateway",
        "source_ref": f"benchmark-request-{index}",
        "object_digest": reference,
        "state": "EXECUTING",
        "decision": "ALLOW",
        "result_digest": None,
        "occurred_at": "2026-09-23T00:00:00Z",
    }


async def elapsed(operation: Awaitable[Any]) -> tuple[Any, float]:
    started = time.perf_counter_ns()
    result = await operation
    return result, (time.perf_counter_ns() - started) / 1_000_000


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def save_results(
    output: Path, result: dict[str, Any], memory: ProcessMemorySampler
) -> None:
    result["process_memory"] = memory.report()
    (output / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "measurements.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        columns = [
            "metric",
            "history_events",
            "samples",
            "min_ms",
            "p50_ms",
            "p95_ms",
            "max_ms",
            "mean_ms",
        ]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for measurement in result["measurements"]:
            writer.writerow({key: measurement.get(key) for key in columns})
    with (output / "memory.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "elapsed_ms",
                "phase",
                "rss_bytes",
                "os_lifetime_peak_rss_bytes",
            ],
        )
        writer.writeheader()
        writer.writerows(result["process_memory"]["raw_samples"])


def record_metric(
    result: dict[str, Any], name: str, values: list[float], **context: Any
) -> None:
    row = {"metric": name, **context, **distribution(values), "raw_ms": values}
    result["measurements"].append(row)
    print(
        json.dumps({key: value for key, value in row.items() if key != "raw_ms"}),
        flush=True,
    )


@asynccontextmanager
async def live_server(settings: Settings):
    app = create_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            access_log=False,
            log_level="error",
            lifespan="on",
        )
    )
    serving = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("isolated HTTP server stopped before startup")
                await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        await serving


async def observed_phase(
    client: httpx.AsyncClient,
    operation: Callable[[], Awaitable[Any]],
    *,
    health_path: str,
) -> tuple[Any, dict[str, Any]]:
    stopped = asyncio.Event()
    lag, health, errors = [], [], []

    async def sample_loop() -> None:
        while not stopped.is_set():
            before = time.perf_counter_ns()
            await asyncio.sleep(0.01)
            lag.append(max(0, (time.perf_counter_ns() - before) / 1_000_000 - 10))

    async def sample_health() -> None:
        while not stopped.is_set():
            started = time.perf_counter_ns()
            try:
                response = await client.get(health_path)
                response.raise_for_status()
                health.append((time.perf_counter_ns() - started) / 1_000_000)
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(type(exc).__name__)
            await asyncio.sleep(0.05)

    probes = [asyncio.create_task(sample_loop()), asyncio.create_task(sample_health())]
    try:
        value = await operation()
    finally:
        stopped.set()
        await asyncio.gather(*probes)
    return value, {"loop_lag_ms": lag, "health_ms": health, "health_errors": errors}


async def boundary_checks(
    client: httpx.AsyncClient,
    *,
    output: Path,
    events: SqliteEventStore,
    evidence: FileEvidenceRecorder,
    checkpoints: FileCheckpointStore,
    envelopes: EnvelopeService,
    verifier: AuditVerifier,
    keys: Path,
    memory: ProcessMemorySampler,
) -> list[dict[str, Any]]:
    """Build real signed UTF-8 bundles at the exact limit and both adjacent bytes.

    All exact-size cases pin an independently saved real signed checkpoint, so
    fresh randomized SM2 signature lengths cannot move the byte boundary. A
    separate larger-ID export checks refusal to publish a new oversize anchor.
    Every CLI is a new process.
    """
    cases = []
    prefix = "中文审计证据："
    for name, difference in (("minus", -1), ("exact", 0), ("plus", 1)):
        memory.set_phase(f"boundary_{name}_build")
        print(
            f"Building signed boundary case: 64 MiB {difference:+d} bytes", flush=True
        )
        target = MAX_AUDIT_BUNDLE_BYTES + difference
        task_id, checkpoint_id = f"boundary-{name}", f"boundary-{name}-checkpoint"
        normalized, references = [], []
        part_bytes = 13 * 1024**2
        for index in range(1, 5):
            reference = await evidence.record_object(
                task_id=task_id,
                object_type="ToolCallEnvelope",
                payload={
                    "task_id": task_id,
                    "part": index,
                    "content": prefix + "x" * part_bytes,
                },
            )
            references.append(reference)
            normalized.append(
                await events.append_event(event(index, task_id, reference))
            )
        fixed_objects = await evidence.list_task_objects(task_id, set(references))
        padding = target - 4 * part_bytes - 16384
        for _ in range(12):
            reference = await evidence.record_object(
                task_id=task_id,
                object_type="ToolCallEnvelope",
                payload={
                    "task_id": task_id,
                    "part": 5,
                    "content": prefix + "x" * padding,
                },
            )
            last_event = BehaviorEvent.model_validate(
                event(5, task_id, reference) | {"sequence": 5}
            ).model_dump(mode="json")
            chain = HashChain(task_id)
            for item in [*normalized, last_event]:
                chain.append_event(item)
            anchor = await asyncio.to_thread(
                create_checkpoint,
                chain,
                envelopes,
                key_id="benchmark-key",
                checkpoint_id=checkpoint_id,
            )
            objects = fixed_objects + await evidence.list_task_objects(
                task_id, {reference}
            )
            candidate = anchor.model_dump() | {
                "schema_version": "1.0",
                "entries": chain.entries,
                "objects": objects,
            }
            raw = await asyncio.to_thread(
                lambda: json.dumps(
                    candidate,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if len(raw) == target:
                break
            padding += target - len(raw)
        else:
            raise RuntimeError("could not stabilize exact signed bundle size")
        assert prefix.encode("utf-8") in raw
        await events.append_event(last_event)
        if difference <= 0:
            await asyncio.to_thread(chain.export_bundle, anchor, objects=objects)
        else:
            try:
                await asyncio.to_thread(chain.export_bundle, anchor, objects=objects)
            except CryptoError as exc:
                assert exc.code == "INPUT_TOO_LARGE"
            else:
                raise AssertionError("64 MiB + 1 was exported by HashChain")
        await asyncio.to_thread(checkpoints.save_checkpoint, anchor)
        memory.set_phase(f"boundary_{name}_http_export")
        started = time.perf_counter_ns()
        response = await client.post(
            "/api/v1/audit/export",
            json={
                "task_id": task_id,
                "checkpoint_id": checkpoint_id,
            },
        )
        export_ms = (time.perf_counter_ns() - started) / 1_000_000
        if difference <= 0:
            response.raise_for_status()
            downloaded = response.json()["data"]
            raw = await asyncio.to_thread(encode_bundle, downloaded)
            assert len(raw) == target
            candidate = downloaded
        else:
            assert response.status_code == 413, response.text[:512]
        fresh_rejection = None
        if difference > 0:
            # Stay oversize even if a fresh signature or timestamp is shorter.
            fresh_id = checkpoint_id + "-fresh-" + "x" * 32
            fresh_response = await client.post(
                "/api/v1/audit/export",
                json={"task_id": task_id, "checkpoint_id": fresh_id},
            )
            assert fresh_response.status_code == 413, fresh_response.text[:512]
            try:
                await asyncio.to_thread(checkpoints.get_trusted_checkpoint, fresh_id)
            except CryptoError as exc:
                assert exc.code == "ANCHOR_NOT_FOUND"
            else:
                raise AssertionError("oversize export published a checkpoint")
            fresh_rejection = {"http_status": 413, "checkpoint_published": False}
        bundle_path = output / f"boundary-{name}.json"
        await asyncio.to_thread(bundle_path.write_bytes, raw)
        memory.set_phase(f"boundary_{name}_http_verify")
        http_verified = await client.post(
            "/api/v1/audit/verify",
            json={
                "bundle": candidate,
                "task_id": task_id,
                "trusted_checkpoint_id": checkpoint_id,
            },
        )
        http_verified.raise_for_status()
        verdict = http_verified.json()["data"]
        assert verdict["valid"] is (difference <= 0), verdict
        if difference > 0:
            assert verdict["errors"][0]["code"] == "INPUT_TOO_LARGE"
        memory.set_phase(f"boundary_{name}_direct_verify")
        direct = await asyncio.to_thread(
            verifier.verify_bundle,
            candidate,
            task_id=task_id,
            trusted_checkpoint_id=checkpoint_id,
        )
        assert direct.valid is (difference <= 0)
        memory.set_phase(f"boundary_{name}_cli_child_excluded")
        cli, cli_ms = await elapsed(
            asyncio.to_thread(
                run_command,
                [
                    sys.executable,
                    "-m",
                    "ra_agent.audit",
                    "--bundle",
                    str(bundle_path),
                    "--keys",
                    str(keys),
                    "--checkpoints",
                    str(checkpoints.directory),
                    "--checkpoint-id",
                    checkpoint_id,
                    "--task-id",
                    task_id,
                ],
            )
        )
        assert cli.returncode == (0 if difference <= 0 else 1), cli.stderr + cli.stdout
        case = {
            "name": name,
            "bytes": len(raw),
            "expected_bytes": target,
            "contains_utf8_chinese": True,
            "http_export_status": response.status_code,
            "http_export_ms": export_ms,
            "http_verification": verdict,
            "direct_verification": direct.model_dump(),
            "cli_exit": cli.returncode,
            "cli_ms": cli_ms,
            "cli_result": json.loads(cli.stdout),
            "bundle_file": bundle_path.name,
            "fresh_oversize_export": fresh_rejection,
        }
        cases.append(case)
        print(
            json.dumps(
                {
                    key: case[key]
                    for key in (
                        "name",
                        "bytes",
                        "http_export_status",
                        "http_export_ms",
                        "cli_exit",
                        "cli_ms",
                    )
                }
            ),
            flush=True,
        )
    return cases


async def benchmark(
    args: argparse.Namespace, output: Path, memory: ProcessMemorySampler
) -> dict[str, Any]:
    commit = run_command(["git", "rev-parse", "HEAD"])
    status = run_command(["git", "status", "--porcelain"])
    openssl = run_command([find_openssl(), "version"])
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "openssl": openssl.stdout.strip(),
            "git_commit": commit.stdout.strip(),
            "working_tree_dirty": bool(status.stdout),
            "workload_note": args.environment_note,
        },
        "parameters": vars(args) | {"output": str(output)},
        "measurement_notes": [
            "Current working-tree implementation; no baseline-versus-patch speedup claim.",
            "Percentiles use linear interpolation; raw monotonic-clock samples are retained.",
            "SQLite append includes thread dispatch, validation, hashing, transaction and commit.",
            "SM2 sign includes the provider's mandatory post-sign verification.",
            "HTTP verification and health use real loopback TCP and the same FastAPI event loop.",
            "Loop lag is excess over a 10 ms sleep; compare the idle baseline on this OS.",
            "Synthetic workload; these results do not establish production capacity or an SLO.",
        ],
        "measurements": [],
    }
    save_results(output, result, memory)
    with tempfile.TemporaryDirectory(prefix="aegis-benchmark-key-") as temporary:
        private = Path(temporary) / "sm2.pem"
        public = await asyncio.to_thread(generate_sm2_key, private)
        keys = output / "public-keys.json"
        keys.write_text(json.dumps({"benchmark-key": public}), encoding="utf-8")
        provider = await asyncio.to_thread(
            OpenSSLSignatureProvider,
            {"benchmark-key": public},
            private_keys={"benchmark-key": private},
        )
        envelopes = EnvelopeService(provider)
        events = SqliteEventStore(output / "events.sqlite3")
        evidence = FileEvidenceRecorder(
            output / "evidence", envelopes, key_id="benchmark-key"
        )
        checkpoints = FileCheckpointStore(output / "checkpoints", envelopes)
        exporter = AuditExportService(
            event_store=events,
            evidence_recorder=evidence,
            envelopes=envelopes,
            checkpoints=checkpoints,
            key_id="benchmark-key",
        )
        readonly = EnvelopeService(
            await asyncio.to_thread(OpenSSLSignatureProvider, {"benchmark-key": public})
        )
        verifier = AuditVerifier(
            readonly, FileCheckpointStore(checkpoints.directory, readonly)
        )
        appended = 0
        for history_size in args.history_sizes:
            memory.set_phase(f"sqlite_history_{history_size}")
            while appended < history_size:
                appended += 1
                await events.append_event(event(appended, "append-benchmark"))
                if appended % 1000 == 0:
                    print(f"Seeded {appended} actual SQLite/SM3 events", flush=True)
            actual_history = appended
            samples = []
            for _ in range(args.samples):
                appended += 1
                _, duration = await elapsed(
                    events.append_event(event(appended, "append-benchmark"))
                )
                samples.append(duration)
            record_metric(
                result, "sqlite_append", samples, history_events=actual_history
            )
            save_results(output, result, memory)
        memory.set_phase("sqlite_complete_replay")
        replayed, duration = await elapsed(events.list_task_events("append-benchmark"))
        assert len(replayed) == appended
        record_metric(
            result, "sqlite_complete_replay", [duration], history_events=appended
        )

        signatures, checks = [], []
        memory.set_phase("sm2_sign_and_verify")
        payload = ("真实 SM2/SM3 性能样本 " + "x" * args.object_bytes).encode()
        for _ in range(args.crypto_samples):
            signed, duration = await elapsed(
                asyncio.to_thread(provider.sign_sm2, payload, key_id="benchmark-key")
            )
            signatures.append(duration)
            valid, duration = await elapsed(
                asyncio.to_thread(
                    readonly.signatures.verify_sm2,
                    payload,
                    signed,
                    key_id="benchmark-key",
                )
            )
            assert valid
            checks.append(duration)
        record_metric(result, "sm2_sign_with_self_verification", signatures)
        record_metric(result, "sm2_verify", checks)

        object_times = []
        memory.set_phase("sign_and_persist_evidence")
        for index in range(1, args.objects + 1):
            reference, duration = await elapsed(
                evidence.record_object(
                    task_id="evidence-benchmark",
                    object_type="ToolCallEnvelope",
                    payload={
                        "task_id": "evidence-benchmark",
                        "request_id": f"request-{index}",
                        "content": "中文审计样本：" + "x" * args.object_bytes,
                    },
                )
            )
            object_times.append(duration)
            await events.append_event(event(index, "evidence-benchmark", reference))
        record_metric(result, "sign_and_persist_evidence", object_times)
        exports, verifies = [], []
        bundle: dict[str, Any] = {}
        checkpoint_id = ""
        for index in range(args.export_samples):
            memory.set_phase("export_with_new_checkpoint")
            checkpoint_id = f"benchmark-checkpoint-{index}"
            bundle, duration = await elapsed(
                exporter.export_task(
                    task_id="evidence-benchmark", checkpoint_id=checkpoint_id
                )
            )
            exports.append(duration)
            memory.set_phase("verify_bundle_service")
            verified, duration = await elapsed(
                asyncio.to_thread(
                    verifier.verify_bundle,
                    bundle,
                    task_id="evidence-benchmark",
                    trusted_checkpoint_id=checkpoint_id,
                )
            )
            assert verified.valid and verified.verified_events == args.objects
            verifies.append(duration)
        record_metric(result, "export_with_new_checkpoint", exports)
        record_metric(result, "verify_bundle_service", verifies)
        bundle_path = output / "bundle.json"
        bundle_path.write_bytes(encode_bundle(bundle))
        result["bundle_bytes"] = bundle_path.stat().st_size
        settings = Settings(
            runtime_mode=RuntimeMode.OFFLINE,
            core_crypto_mode=CoreCryptoMode.SM2,
            core_sm2_key_id="benchmark-key",
            core_sm2_public_keys_path=keys,
            core_sm2_private_key_path=private,
            core_event_log_path=output / "events.jsonl",
            core_state_path=output / "state.sqlite3",
            core_evidence_root=evidence.directory,
            core_audit_checkpoint_root=checkpoints.directory,
            core_memory_path=output / "memory.json",
            core_outbox_path=output / "outbox.jsonl",
            database_url=f"sqlite+aiosqlite:///{(output / 'runtime.db').as_posix()}",
            workspace_root=output / "workspace",
            pending_root=output / "pending",
            checkpoint_root=output / "runtime-checkpoints",
            quarantine_root=output / "quarantine",
            security_config_dir=ROOT / "configs",
            enable_demo_fixtures=False,
        )
        memory.set_phase("http_server_startup")
        async with live_server(settings) as (base_url, app):
            result["http"] = {
                "transport": "loopback TCP",
                "base_url": base_url,
                "audit_concurrency_limit": app.state.core_audit_limiter.total_tokens,
            }
            async with httpx.AsyncClient(
                base_url=base_url, trust_env=False, timeout=120
            ) as client:
                memory.set_phase("http_idle")
                _, baseline = await observed_phase(
                    client, lambda: asyncio.sleep(0.5), health_path="/api/v1/health"
                )
                gate = asyncio.Semaphore(args.concurrency)

                async def verify_request() -> float:
                    async with gate:
                        response, duration = await elapsed(
                            client.post(
                                "/api/v1/audit/verify",
                                json={
                                    "bundle": bundle,
                                    "task_id": "evidence-benchmark",
                                    "trusted_checkpoint_id": checkpoint_id,
                                },
                            )
                        )
                        response.raise_for_status()
                        assert response.json()["data"]["valid"]
                        return duration

                async def load() -> list[float]:
                    return await asyncio.gather(
                        *(verify_request() for _ in range(args.verification_jobs))
                    )

                memory.set_phase("http_concurrent_verification")
                ((durations, probes), total_ms) = await elapsed(
                    observed_phase(client, load, health_path="/api/v1/health")
                )
                record_metric(result, "verify_bundle_http_concurrent", durations)
                for phase, samples in (
                    ("idle", baseline),
                    ("verification_load", probes),
                ):
                    record_metric(result, f"{phase}_loop_lag", samples["loop_lag_ms"])
                    record_metric(result, f"{phase}_health_http", samples["health_ms"])
                    if samples["health_errors"]:
                        raise RuntimeError(
                            f"health failures during {phase}: {samples['health_errors']}"
                        )
                result["concurrent_verification"] = {
                    "jobs": args.verification_jobs,
                    "concurrency": args.concurrency,
                    "wall_ms": total_ms,
                    "completed_per_second": args.verification_jobs * 1000 / total_ms,
                    "health_failures": 0,
                }
                save_results(output, result, memory)
                if args.boundary_checks:
                    result["boundary_checks"] = await boundary_checks(
                        client,
                        output=output,
                        events=events,
                        evidence=evidence,
                        checkpoints=checkpoints,
                        envelopes=envelopes,
                        verifier=verifier,
                        keys=keys,
                        memory=memory,
                    )
                    save_results(output, result, memory)
                memory.set_phase("http_server_shutdown")
        result["storage_bytes"] = {
            "event_database": sum(
                path.stat().st_size for path in output.glob("events.sqlite3*")
            ),
            "signed_evidence_and_index": sum(
                path.stat().st_size
                for path in evidence.directory.rglob("*")
                if path.is_file()
            ),
            "trusted_checkpoints": sum(
                path.stat().st_size for path in checkpoints.directory.glob("*.json")
            ),
            "downloaded_bundle": bundle_path.stat().st_size,
        }
    result["private_key_retained"] = False
    memory.set_phase("finalization")
    result["status"] = "complete"
    result["finished_at"] = datetime.now(UTC).isoformat()
    save_results(output, result, memory)
    return result


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="New output directory; existing paths are refused"
    )
    parser.add_argument(
        "--history-sizes", type=positive_int, nargs="+", default=[100, 1000]
    )
    parser.add_argument("--samples", type=positive_int, default=10)
    parser.add_argument("--crypto-samples", type=positive_int, default=3)
    parser.add_argument("--export-samples", type=positive_int, default=2)
    parser.add_argument("--objects", type=positive_int, default=4)
    parser.add_argument("--object-bytes", type=positive_int, default=4096)
    parser.add_argument("--concurrency", type=positive_int, default=2)
    parser.add_argument("--verification-jobs", type=positive_int, default=4)
    parser.add_argument(
        "--environment-note",
        default="Shared workstation; background workload is not controlled or assumed idle.",
        help="Record concurrent host workload and other measurement limitations",
    )
    parser.add_argument(
        "--boundary-checks",
        action="store_true",
        help="Also run exact 64 MiB - 1 / 64 MiB / 64 MiB + 1 UTF-8 HTTP-to-CLI cases; "
        "writes hundreds of MiB and takes substantially longer",
    )
    args = parser.parse_args()
    args.history_sizes = sorted(set(args.history_sizes))
    output = (
        args.output
        or ROOT
        / ".runtime/review-fixes/benchmarks"
        / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    ).resolve()
    args.output = str(output)
    output.mkdir(parents=True, exist_ok=False)
    print(f"Benchmark output: {output}", flush=True)
    memory = ProcessMemorySampler()
    memory.start()
    try:
        result = asyncio.run(benchmark(args, output, memory))
    finally:
        memory.stop()
    save_results(output, result, memory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
