"""Start/reset/replay the isolated member 4 telecom demo, or run browser acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check import subprocess_command  # noqa: E402


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def verify_disk(view: dict[str, Any]) -> None:
    root = Path(view["workspace"])
    observed = view["observed"]
    for name, expected in observed["files"].items():
        file = root / name
        actual = (
            hashlib.sha256(file.read_bytes()).hexdigest() if file.exists() else None
        )
        assert actual == expected, name
    assert (
        json.loads((root / "memory.json").read_text(encoding="utf-8"))
        == observed["memory"]
    )
    calls = json.loads((root / "endpoints.json").read_text(encoding="utf-8"))
    assert calls == observed["endpoint_calls"]
    for call in calls:
        action = next(
            a for a in view["actions"] if a["request_id"] == call["request_id"]
        )
        assert call["payload_digest"] == sha(action["envelope"]["canonical_args"])
        assert call["sent"] is False
    checks = view["effect_checks"]
    assert checks[0]["before_digest"] == sha(view["baseline"])
    assert checks[-1]["after_digest"] == sha(observed)
    assert all(
        a["after_digest"] == b["before_digest"]
        for a, b in zip(checks, checks[1:], strict=False)
    )
    assert view["acceptance"]["passed"], view["acceptance"]


def api(
    client: httpx.Client, method: str, route: str, body: dict | None = None
) -> dict:
    response = client.request(method, route, json=body)
    response.raise_for_status()
    return response.json()["data"]


def replay(url: str, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with httpx.Client(base_url=url, timeout=30, trust_env=False) as client:
        api(client, "POST", "/api/intent-demo/reset")
        cases = api(client, "GET", "/api/intent-demo/cases")
        for case in cases:
            view = api(
                client, "POST", "/api/intent-demo/runs", {"case_id": case["case_id"]}
            )
            initial = view
            verify_disk(view)
            if view["status"] in {"BLOCKED", "WAITING_CLARIFICATION", "WAITING_REPLAN"}:
                action = view["actions"][-1]
                assert view["observed"]["config_call_count"] == 0
                assert view["observed"]["send_call_count"] == 0
                assert action["gateway_decision"]["decision"] == "ALLOW"
                assert action["execution_status"] == "NOT_EXECUTED"
                denied = client.post(
                    f"/api/intent-demo/runs/{view['run_id']}/actions/{action['request_id']}/execute"
                )
                assert denied.status_code == 409
                current = api(client, "GET", f"/api/intent-demo/runs/{view['run_id']}")
                assert current["observed"] == view["observed"]
            controls = {
                "BLOCKED": "stop",
                "WAITING_CLARIFICATION": "stop",
                "WAITING_REPLAN": "replan",
                "WAITING_GOAL_CONFIRMATION": "confirm",
            }
            if view["status"] in controls:
                view = api(
                    client,
                    "POST",
                    f"/api/intent-demo/runs/{view['run_id']}/control",
                    {"action": controls[view["status"]]},
                )
            verify_disk(view)
            assert view["status"] == case["expected_state"]["terminal_status"]
            (output / f"{case['case_id']}.json").write_text(
                json.dumps(
                    {"initial": initial, "final": view}, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            result = {
                "case_id": case["case_id"],
                "status": "PASS",
                "initial_status": initial["status"],
                "final_status": view["status"],
                "config_calls": view["observed"]["config_call_count"],
                "send_calls": view["observed"]["send_call_count"],
                "events": len(view["timeline"]),
                "report_sha256": view["observed"]["files"]["reports/risk-report.md"],
            }
            results.append(result)
            print(f"{case['case_id']}: PASS {result['final_status']}", flush=True)
        api(client, "POST", "/api/intent-demo/reset")
        assert all(
            client.get(f"/api/intent-demo/runs/{x['final']['run_id']}").status_code
            == 404
            for x in [
                json.loads(p.read_text(encoding="utf-8")) for p in output.glob("*.json")
            ]
            if "final" in x
        )
    (output / "summary.json").write_text(
        json.dumps(
            {
                "mode": "fixture/mock; real local execution",
                "status": "PASS",
                "cases": results,
                "reset": "PASS",
                "python": sys.version,
                "platform": sys.platform,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def command(arguments: list[str], cwd: Path = ROOT, env: dict | None = None) -> int:
    executable = shutil.which(arguments[0])
    if executable is None:
        raise RuntimeError(f"Missing executable: {arguments[0]}")
    return subprocess.run(
        subprocess_command(executable, arguments[1:]), cwd=cwd, env=env, shell=False
    ).returncode


@contextmanager
def servers(backend_port: int, frontend_port: int):
    for port in (backend_port, frontend_port):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(
                    f"Port {port} is occupied; choose another demo port"
                ) from exc
    runtime = ROOT / ".runtime" / f"member4-service-{backend_port}"
    runtime.mkdir(parents=True, exist_ok=True)
    env = dict(
        os.environ,
        ENABLE_DEMO_FIXTURES="true",
        RUNTIME_MODE="offline",
        CORE_CRYPTO_MODE="fake",
        LLM_API_KEY="",
        PYTHONPATH=str(ROOT / "backend/src") + os.pathsep + str(ROOT),
        WORKSPACE_ROOT=str(runtime / "workspace"),
        CORE_EVENT_LOG_PATH=str(runtime / "core/events.jsonl"),
        CORE_STATE_PATH=str(runtime / "core/state.sqlite3"),
        VITE_API_PROXY_TARGET=f"http://127.0.0.1:{backend_port}",
        INTENT_DEMO_BACKEND_URL=f"http://127.0.0.1:{backend_port}",
        INTENT_DEMO_FRONTEND_URL=f"http://127.0.0.1:{frontend_port}",
    )
    processes = []
    logs = []
    try:
        corepack = shutil.which("corepack")
        if corepack is None:
            raise RuntimeError("corepack is required by the existing frontend")
        for name, executable, args, cwd in [
            (
                "backend",
                sys.executable,
                [
                    "-m",
                    "uvicorn",
                    "ra_agent.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(backend_port),
                    "--workers",
                    "1",
                ],
                ROOT,
            ),
            (
                "frontend",
                corepack,
                [
                    "pnpm",
                    "dev",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(frontend_port),
                    "--strictPort",
                ],
                ROOT / "frontend",
            ),
        ]:
            log = (runtime / f"{name}.log").open("w", encoding="utf-8")
            logs.append(log)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            processes.append(
                subprocess.Popen(
                    subprocess_command(executable, args),
                    cwd=cwd,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    creationflags=flags,
                )
            )
        deadline = time.monotonic() + 30
        for url in (
            env["INTENT_DEMO_BACKEND_URL"] + "/health",
            env["INTENT_DEMO_FRONTEND_URL"],
        ):
            while time.monotonic() < deadline:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError(f"Demo server exited; inspect {runtime}")
                try:
                    if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError(f"Demo readiness timeout; inspect {runtime}")
        print(f"Demo ready: {env['INTENT_DEMO_FRONTEND_URL']}/intent", flush=True)
        yield env
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    process.terminate()
                process.wait(timeout=10)
        for log in logs:
            log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["start", "reset", "replay", "e2e"])
    parser.add_argument("--backend-port", type=int, default=8044)
    parser.add_argument("--frontend-port", type=int, default=5174)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/member4-replay")
    args = parser.parse_args()
    if (
        not 1024 <= args.backend_port <= 65535
        or not 1024 <= args.frontend_port <= 65535
    ):
        parser.error("demo ports must be between 1024 and 65535")
    url = f"http://127.0.0.1:{args.backend_port}"
    if args.mode == "reset":
        with httpx.Client(base_url=url, timeout=30, trust_env=False) as client:
            print(api(client, "POST", "/api/intent-demo/reset"))
    elif args.mode == "replay":
        replay(url, args.output.resolve())
    else:
        with servers(args.backend_port, args.frontend_port) as env:
            if args.mode == "e2e":
                env["INTENT_DEMO_EVIDENCE"] = str(args.output.resolve())
                return command(
                    [
                        "corepack",
                        "pnpm",
                        "exec",
                        "playwright",
                        "test",
                        "--config",
                        "playwright.intent.config.ts",
                    ],
                    ROOT / "frontend",
                    env,
                )
            print(
                "Ctrl+C stops only these demo processes. No LLM key needed.", flush=True
            )
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
