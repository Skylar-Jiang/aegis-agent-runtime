"""Controlled Core mechanism comparisons with real file writes and fresh SM2 keys.

Run from the repository root with the backend environment, for example:
  uv run --project backend --no-editable python scripts/experiment_core_ablations.py

This script changes no service configuration. Baselines are explicit experimental
harnesses, not historical releases or alternate production endpoints. All arms use
the same bounded CoreToolExecutor file adapter to isolate gateway behavior from
Runtime recovery. Temporary private keys and workspace/state files are removed.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import platform
import subprocess
import tempfile
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ra_agent.audit import FileEvidenceRecorder
from ra_agent.contracts import (
    ConfirmationPolicyV1,
    ContractPermissionRule,
    ContractRecord,
    ContractService,
    EffectClass,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractCreateRequest,
    ToolCallEnvelope,
)
from ra_agent.crypto import EnvelopeService, OpenSSLSignatureProvider, generate_sm2_key
from ra_agent.events import SqliteEventStore
from ra_agent.gateway import GatewayError, ToolGateway
from ra_agent.gateway.adapters import CoreToolExecutor
from ra_agent.gateway.state import CoreStateStore
from ra_agent.permissions import PermissionResolver
from ra_agent.tools.path_resolver import SafePathResolver

ROOT = Path(__file__).resolve().parents[1]
MODES = {
    "permission_only": {
        "kind": "baseline",
        "description": "Current permission intersection and bounded real tool execution.",
        "signed_evidence": False,
        "immutable_request_binding": False,
        "execution_result_cache": False,
        "live_permission_source": True,
    },
    "permission_and_sm2": {
        "kind": "baseline",
        "description": "Baseline plus real SM2/SM3 signed request, decision and result evidence.",
        "signed_evidence": True,
        "immutable_request_binding": False,
        "execution_result_cache": False,
        "live_permission_source": True,
    },
    "full_gateway": {
        "kind": "current_gateway",
        "description": "Unmodified ToolGateway, SQLite state/events, current permissions and SM2.",
        "signed_evidence": True,
        "immutable_request_binding": True,
        "execution_result_cache": True,
        "live_permission_source": True,
    },
    "gateway_without_live_authority": {
        "kind": "single_mechanism_ablation",
        "description": "Same gateway without permission_provider; stored initial grants remain.",
        "signed_evidence": True,
        "immutable_request_binding": True,
        "execution_result_cache": True,
        "live_permission_source": False,
    },
}
SCENARIOS = (
    "normal",
    "same_id_retry",
    "evaluated_argument_substitution",
    "permission_tightening",
)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def permission_context() -> PermissionContext:
    def grant(source: str) -> PermissionGrant:
        return PermissionGrant(
            subject="experiment-user" if source == "user" else "*",
            skill="writer" if source == "skill" else "*",
            tool="write_file",
            action="write_file",
            resource="reports/**",
            effect=GrantEffect.ALLOW,
            source=source,
            scope=GrantScope.GLOBAL,
        )

    return PermissionContext(
        user_grants=[grant("user")],
        skill_grants=[grant("skill")],
        system_grants=[grant("system")],
    )


class Authority:
    def __init__(self) -> None:
        self.current = permission_context()

    async def read(self, envelope: ToolCallEnvelope) -> PermissionContext:
        del envelope
        return self.current.model_copy(deep=True)


class ObservedFileExecutor(CoreToolExecutor):
    """Count completed real writes; never substitute or simulate the operation."""

    def __init__(self, workspace: Path, ledger: list[dict[str, Any]]) -> None:
        super().__init__(
            path_resolver=SafePathResolver(
                workspace, max_read_bytes=4096, max_write_bytes=4096, max_path_length=4096
            ),
            memory_path=workspace.parent / "unused-memory.json",
            outbox_path=workspace.parent / "unused-outbox.jsonl",
        )
        self.ledger = ledger

    def _file_operation(self, envelope: ToolCallEnvelope) -> dict[str, Any]:
        if envelope.tool != "write_file":
            raise ValueError("this experiment only executes bounded write_file")
        result = super()._file_operation(envelope)
        actual = self.path_resolver.read_file_bytes(envelope.resource)
        expected = envelope.canonical_args["content"].encode("utf-8")
        if actual != expected:
            raise AssertionError("completed write did not match the actual file bytes")
        self.ledger.append(
            {
                "write_number": len(self.ledger) + 1,
                "request_id": envelope.request_id,
                "resource": envelope.resource,
                "actual_content": actual.decode("utf-8"),
                "actual_bytes": len(actual),
                "actual_sha256": hashlib.sha256(actual).hexdigest(),
            }
        )
        return result


class PermissionBaseline:
    """Intentionally omit request-ID binding and result reuse; retain live checks."""

    def __init__(
        self,
        *,
        store: CoreStateStore,
        contract: ContractRecord,
        authority: Authority,
        executor: ObservedFileExecutor,
        evidence: FileEvidenceRecorder | None,
    ) -> None:
        self.store, self.contract, self.authority = store, contract, authority
        self.executor, self.evidence = executor, evidence
        self.resolver = PermissionResolver()
        self.token = object()
        executor.bind_gateway(self.token)

    async def _record(self, envelope: ToolCallEnvelope, kind: str, payload: dict) -> None:
        if self.evidence is not None:
            await self.evidence.record_object(
                task_id=envelope.task_id, object_type=kind, payload=payload
            )

    async def evaluate(self, envelope: ToolCallEnvelope) -> str:
        effective = self.resolver.resolve_effective_permission(
            envelope, self.contract.contract, await self.authority.read(envelope)
        )
        decision = "DENY" if effective.conflict_code is not None else "ALLOW"
        await self._record(envelope, "ToolCallEnvelope", envelope.model_dump(mode="json"))
        await self._record(
            envelope,
            "ExperimentPermissionDecision",
            {
                "task_id": envelope.task_id,
                "request_id": envelope.request_id,
                "decision": decision,
                "reason": effective.conflict_code.value if effective.conflict_code else "ALLOWED",
            },
        )
        # Deliberately replace the evaluation; no immutable envelope fingerprint.
        with self.store.transaction() as transaction:
            transaction.put("experiment_evaluations", envelope.request_id, {"decision": decision})
        return decision

    async def execute(self, envelope: ToolCallEnvelope) -> dict[str, Any]:
        with self.store.transaction() as transaction:
            evaluated = transaction.get("experiment_evaluations", envelope.request_id)
        if evaluated is None or evaluated["decision"] != "ALLOW":
            raise GatewayError("baseline request must have an allowed evaluation")
        # Live permission rechecking is retained in both honest baselines.
        current = self.resolver.resolve_effective_permission(
            envelope, self.contract.contract, await self.authority.read(envelope)
        )
        if current.conflict_code is not None:
            raise GatewayError(f"Pre-execution recheck failed: {current.conflict_code.value}")
        result = await self.executor.execute(envelope, gateway_token=self.token)
        await self._record(
            envelope,
            "ToolResult",
            {"task_id": envelope.task_id, "request_id": envelope.request_id, "result": result},
        )
        return result


async def confirmed_contract(store: CoreStateStore) -> ContractRecord:
    service = ContractService(store=store)
    draft = await service.create_contract(
        TaskContractCreateRequest(
            session_id="experiment-session",
            task_id="experiment-task",
            user_id="experiment-user",
            goals=["write a bounded report"],
            allowed=[
                ContractPermissionRule(
                    tool="write_file", action="write_file", resource="reports/**", effect="WRITE"
                )
            ],
            confirmation=ConfirmationPolicyV1(required_actions=[]),
            policy_version="experiment-policy:1",
            tool_manifest_digest="experiment-write-file:1",
        )
    )
    return await service.confirm_contract(
        draft.ref.contract_id, version=1, confirmed_by="experiment-user"
    )


async def run_case(
    root: Path,
    mode: str,
    scenario: str,
    signatures: OpenSSLSignatureProvider,
    readonly: EnvelopeService,
    key_id: str,
) -> dict[str, Any]:
    workspace = root / "workspace"
    (workspace / "reports").mkdir(parents=True)
    state_path = root / "state.sqlite3"
    record = await confirmed_contract(CoreStateStore(state_path))
    authority = Authority()
    ledger: list[dict[str, Any]] = []
    evidence = (
        FileEvidenceRecorder(root / "evidence", EnvelopeService(signatures), key_id=key_id)
        if MODES[mode]["signed_evidence"]
        else None
    )
    events = (
        SqliteEventStore(root / "events.sqlite3") if mode.startswith(("full", "gateway")) else None
    )

    def build_harness() -> PermissionBaseline | ToolGateway:
        executor = ObservedFileExecutor(workspace, ledger)
        store = CoreStateStore(state_path)
        if mode.startswith(("full", "gateway")):
            return ToolGateway(
                contracts=ContractService(store=store),
                resolver=PermissionResolver(),
                store=store,
                executor=executor,
                signature_provider=signatures,
                evidence_recorder=evidence,
                event_store=events,
                permission_provider=authority.read
                if MODES[mode]["live_permission_source"]
                else None,
            )
        return PermissionBaseline(
            store=store, contract=record, authority=authority, executor=executor, evidence=evidence
        )

    harness = build_harness()
    envelope = ToolCallEnvelope(
        request_id="same-request-id",
        task_id=record.contract.task_id,
        session_id=record.contract.session_id,
        contract_ref=record.ref,
        skill_ref="writer",
        tool="write_file",
        action="write_file",
        resource="reports/result.txt",
        effect_class=EffectClass.WRITE,
        canonical_args={"path": "reports/result.txt", "content": "approved content"},
    )
    attempts: list[dict[str, Any]] = []

    async def attempt(action: str, candidate: ToolCallEnvelope) -> str:
        before, started = len(ledger), time.perf_counter_ns()
        entry: dict[str, Any] = {"action": action}
        try:
            if action.startswith("evaluate"):
                if isinstance(harness, ToolGateway):
                    result = await harness.evaluate(candidate, authority.current)
                    entry["status"] = result.decision.decision.value
                    entry["reason"] = result.decision.reason_code.value
                else:
                    entry["status"] = await harness.evaluate(candidate)
            else:
                if isinstance(harness, ToolGateway):
                    executed = await harness.execute(candidate.request_id)
                    entry["status"], entry["result"] = executed.status, executed.result
                else:
                    entry["result"] = await harness.execute(candidate)
                    entry["status"] = "EXECUTED"
        except GatewayError as error:
            entry["status"], entry["error"] = "REJECTED", str(error)
        entry["actual_writes"] = len(ledger) - before
        entry["elapsed_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        attempts.append(entry)
        return entry["status"]

    if await attempt("evaluate_original", envelope) != "ALLOW":
        raise AssertionError("normal initial permissions must allow the common workload")
    if scenario == "evaluated_argument_substitution":
        changed = envelope.model_copy(
            update={"canonical_args": {"path": envelope.resource, "content": "substituted content"}}
        )
        if await attempt("evaluate_substitution", changed) == "ALLOW":
            await attempt("execute_substitution", changed)
    elif scenario == "permission_tightening":
        authority.current.system_grants = []
        await attempt("execute_after_permission_tightening", envelope)
    else:
        await attempt("execute_original", envelope)
        if scenario == "same_id_retry":
            await attempt("execute_same_instance_retry", envelope)
            harness = build_harness()
            await attempt("execute_after_gateway_recreation", envelope)

    signed_objects, valid_signatures = [], 0
    for path in sorted((root / "evidence").glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))["object"]
        valid = readonly.verify(
            item["payload"], item["envelope"], object_type=item["envelope"]["object_type"]
        )
        valid_signatures += int(valid)
        signed_objects.append(item)
    if valid_signatures != len(signed_objects):
        raise AssertionError("independent public-key verification rejected recorded evidence")
    tampered_evidence_rejected = None
    if signed_objects:
        item = deepcopy(signed_objects[0])
        item["payload"]["experiment_tampered"] = True
        tampered_evidence_rejected = not readonly.verify(
            item["payload"], item["envelope"], object_type=item["envelope"]["object_type"]
        )
        if not tampered_evidence_rejected:
            raise AssertionError("altered evidence unexpectedly verified")
    target = workspace / envelope.resource
    expected = (
        3
        if scenario == "same_id_retry" and mode.startswith("permission")
        else 0
        if scenario == "evaluated_argument_substitution" and mode == "full_gateway"
        else 0
        if scenario == "permission_tightening" and mode != "gateway_without_live_authority"
        else 1
    )
    if len(ledger) != expected:
        raise AssertionError(f"unexpected {mode}/{scenario} writes: {len(ledger)} != {expected}")
    return {
        "mode": mode,
        "scenario": scenario,
        "final_status": attempts[-1]["status"],
        "actual_writes": len(ledger),
        "expected_writes": expected,
        "actual_file_content": target.read_text(encoding="utf-8") if target.exists() else None,
        "valid_sm2_evidence_objects": valid_signatures,
        "tampered_evidence_rejected": tampered_evidence_rejected,
        "attempts": attempts,
        "write_ledger": ledger,
        "signed_objects": signed_objects,
        "events": await events.list_task_events(envelope.task_id) if events is not None else [],
    }


async def run(output: Path) -> dict[str, Any]:
    cases = []
    key_id = f"ablation-{uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="aegis-core-ablations-") as temporary:
        temporary_root = Path(temporary)
        private_path = temporary_root / "experiment-private.pem"
        public_key = generate_sm2_key(private_path)
        signatures = OpenSSLSignatureProvider(
            {key_id: public_key}, private_keys={key_id: private_path}
        )
        readonly = EnvelopeService(OpenSSLSignatureProvider({key_id: public_key}))
        for mode in MODES:
            scenarios = (
                ("permission_tightening",)
                if mode == "gateway_without_live_authority"
                else SCENARIOS
            )
            for scenario in scenarios:
                case = await run_case(
                    temporary_root / mode / scenario, mode, scenario, signatures, readonly, key_id
                )
                cases.append(case)
                print(
                    json.dumps(
                        {
                            k: case[k]
                            for k in (
                                "mode",
                                "scenario",
                                "final_status",
                                "actual_writes",
                                "valid_sm2_evidence_objects",
                            )
                        }
                    ),
                    flush=True,
                )
    # The private key and every mutable test workspace have been removed here.
    revision_process = await asyncio.to_thread(
        subprocess.run,
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    revision = revision_process.stdout.strip()
    result = {
        "schema": "aegis-core-controlled-ablations-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "revision": revision,
        "working_tree_changes_included": True,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "key_id": key_id,
        "private_key_deleted": True,
        "modes": MODES,
        "scope": {
            "covered": [
                "C2-C4 permission/request/execution controls",
                "C5 full-gateway events",
                "C6 SM2 evidence",
            ],
            "not_covered": [
                "C1 application packaging",
                "C7 browser interaction",
                "Runtime recovery",
                "HTTP integration",
                "statistical performance claims",
            ],
            "shared_tool": "real CoreToolExecutor write_file, SafePathResolver, 4096-byte limit",
            "retry": "original, same-instance retry, then new gateway with the same SQLite state",
            "substitution": "same ID and authorized path; content changes after ALLOW evaluation",
            "permission_tightening": "remove system ALLOW after evaluation, before execution",
        },
        "cases": cases,
    }
    write_json(output / "results.json", result)
    write_json(output / "public-keys.json", {key_id: public_key})
    columns = [
        "mode",
        "scenario",
        "final_status",
        "actual_writes",
        "expected_writes",
        "actual_file_content",
        "valid_sm2_evidence_objects",
        "tampered_evidence_rejected",
    ]
    with (output / "measurements.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: case[key] for key in columns} for case in cases)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root", type=Path, default=ROOT / ".runtime/review-fixes/ablations"
    )
    args = parser.parse_args()
    output = args.output_root.resolve() / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    output.mkdir(parents=True, exist_ok=False)
    asyncio.run(run(output))
    print(f"Results: {output}", flush=True)


if __name__ == "__main__":
    main()
