"""Standalone Aegis Core PR2 verification.

Run from repository root after installing backend dependencies:
    python scripts/verify_core_pr2.py

The script uses temporary files only.  It exercises the merged P2 EventStore, P3
SM2/SM3 evidence + AuditVerifier, P1 confirmation/replan/TOCTOU logic and real
filesystem/Memory/simulated-egress adapters.
"""

from __future__ import annotations

import asyncio
import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend" / "src"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ra_agent.audit import (  # noqa: E402
    AuditExportService,
    AuditVerifier,
    FileCheckpointStore,
    FileEvidenceRecorder,
    HashChain,
)
from ra_agent.confirmations import ConfirmationService  # noqa: E402
from ra_agent.contracts import (  # noqa: E402
    ConfirmationPolicyV1,
    ContractPermissionRule,
    ContractService,
    EffectClass,
    GatewayDecisionType,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractCreateRequest,
    TaskContractUpdateRequest,
    ToolCallEnvelope,
)
from ra_agent.crypto import EnvelopeService, OpenSSLSignatureProvider, generate_sm2_key  # noqa: E402
from ra_agent.events import JsonlEventStore  # noqa: E402
from ra_agent.gateway import AdapterBypassError, CoreToolExecutor, GatewayError, ToolGateway  # noqa: E402
from ra_agent.permissions import PermissionResolver  # noqa: E402
from ra_agent.tools.path_resolver import SafePathResolver  # noqa: E402


def allow_context() -> PermissionContext:
    def grant(source: str, *, subject: str = "*", skill: str = "*") -> PermissionGrant:
        return PermissionGrant(
            subject=subject,
            skill=skill,
            tool="*",
            action="*",
            resource="*",
            effect=GrantEffect.ALLOW,
            scope=GrantScope.GLOBAL,
            source=source,
        )

    return PermissionContext(
        user_grants=[grant("user", subject="user-pr2")],
        skill_grants=[grant("skill", skill="core-pr2")],
        system_grants=[grant("system")],
    )


def system_deny_context() -> PermissionContext:
    context = allow_context()
    context.system_grants = [
        PermissionGrant(
            subject="*",
            skill="*",
            tool="*",
            action="*",
            resource="*",
            effect=GrantEffect.DENY,
            scope=GrantScope.GLOBAL,
            source="system-deny",
        )
    ]
    return context


def call(record, request_id: str, *, tool: str, resource: str, effect: EffectClass, args: dict):
    return ToolCallEnvelope(
        request_id=request_id,
        task_id=record.contract.task_id,
        session_id=record.contract.session_id,
        contract_ref=record.ref,
        skill_ref="core-pr2",
        tool=tool,
        action=tool,
        canonical_args=args,
        resource=resource,
        effect_class=effect,
    )


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="aegis-core-pr2-") as temp_text:
        temp = Path(temp_text)
        workspace = temp / "workspace"
        workspace.mkdir()

        private_key = temp / "sm2-private.pem"
        public_pem = generate_sm2_key(private_key)
        signatures = OpenSSLSignatureProvider(
            {"verify-key": public_pem},
            private_keys={"verify-key": private_key},
        )
        envelopes = EnvelopeService(signatures)
        event_store = JsonlEventStore(temp / "events.jsonl", chain_factory=HashChain)
        evidence = FileEvidenceRecorder(temp / "evidence", envelopes, key_id="verify-key")
        checkpoints = FileCheckpointStore(temp / "checkpoints", envelopes)
        exporter = AuditExportService(
            event_store=event_store,
            evidence_recorder=evidence,
            envelopes=envelopes,
            checkpoints=checkpoints,
            key_id="verify-key",
        )
        verifier = AuditVerifier(envelopes, checkpoints)
        executor = CoreToolExecutor(
            path_resolver=SafePathResolver(
                workspace,
                max_path_length=4096,
                max_read_bytes=1024 * 1024,
                max_write_bytes=1024 * 1024,
            ),
            memory_path=temp / "memory.json",
            outbox_path=temp / "outbox.jsonl",
        )

        contracts = ContractService()
        created = await contracts.create_contract(
            TaskContractCreateRequest(
                session_id="session-pr2",
                task_id="task-pr2",
                user_id="user-pr2",
                goals=["exercise PR2 integration"],
                allowed=[ContractPermissionRule(tool="*", action="*", resource="*", effect="*")],
                confirmation=ConfirmationPolicyV1(required_actions=["create_file"]),
                policy_version="policy:pr2",
                tool_manifest_digest="tools:pr2",
            )
        )
        record = await contracts.confirm_contract(
            created.contract.contract_id, version=1, confirmed_by="user-pr2"
        )
        gateway = ToolGateway(
            contracts=contracts,
            resolver=PermissionResolver(),
            event_store=event_store,
            signature_provider=signatures,
            evidence_recorder=evidence,
            confirmation_service=ConfirmationService(),
            executor=executor,
        )
        await gateway.record_contract_event(record, event_type="CONTRACT_CONFIRMED", actor="user-pr2")

        file_call = call(
            record,
            "req-file",
            tool="create_file",
            resource="pr2-demo.txt",
            effect=EffectClass.WRITE,
            args={"path": "pr2-demo.txt", "content": "Aegis Core PR2"},
        )
        first = await gateway.evaluate(file_call, allow_context())
        assert first.decision.decision is GatewayDecisionType.REQUIRE_CONFIRMATION
        print("[PASS] Gateway -> WAITING_CONFIRMATION")
        approved = await gateway.resolve_confirmation(
            first.decision.confirmation_id or "", confirmed=True, resolved_by="user-pr2"
        )
        assert approved.decision.decision is GatewayDecisionType.ALLOW
        print("[PASS] CONFIRMED -> contract/policy/permission re-check -> ALLOW")
        executed = await gateway.execute("req-file")
        assert executed.status == "EXECUTED"
        assert (workspace / "pr2-demo.txt").read_text(encoding="utf-8") == "Aegis Core PR2"
        print("[PASS] real file adapter executed only through Gateway")

        memory_call = call(
            record,
            "req-memory",
            tool="memory_write",
            resource="demo-key",
            effect=EffectClass.MEMORY,
            args={"key": "demo-key", "value": "demo-value"},
        )
        assert (await gateway.evaluate(memory_call, allow_context())).decision.decision is GatewayDecisionType.ALLOW
        assert (await gateway.execute("req-memory")).result["stored"] is True
        print("[PASS] real Memory adapter")

        egress_call = call(
            record,
            "req-egress",
            tool="send_email_dry_run",
            resource="review@example.invalid",
            effect=EffectClass.NETWORK,
            args={"to": "review@example.invalid", "subject": "PR2", "body": "dry run"},
        )
        assert (await gateway.evaluate(egress_call, allow_context())).decision.decision is GatewayDecisionType.ALLOW
        assert (await gateway.execute("req-egress")).result["sent"] is False
        assert (temp / "outbox.jsonl").exists()
        print("[PASS] simulated outbound adapter records auditable outbox without network send")

        try:
            await executor.execute(memory_call)
        except AdapterBypassError:
            print("[PASS] direct adapter bypass rejected")
        else:
            raise AssertionError("direct adapter bypass was not rejected")

        # TOCTOU: a request evaluated as ALLOW must fail if the current system layer tightens.
        deny_call = call(
            record,
            "req-tighten",
            tool="memory_write",
            resource="blocked",
            effect=EffectClass.MEMORY,
            args={"key": "blocked", "value": "must-not-write"},
        )
        assert (await gateway.evaluate(deny_call, allow_context())).decision.decision is GatewayDecisionType.ALLOW
        await gateway.replace_permission_context("req-tighten", system_deny_context())
        try:
            await gateway.execute("req-tighten")
        except GatewayError as exc:
            assert "SYSTEM_DENY" in str(exc)
            print("[PASS] execute-time permission recheck blocks newly denied side effect")
        else:
            raise AssertionError("TOCTOU permission tightening did not block execute")

        # Replan creates DRAFT v2 and permanently blocks the original request.
        replan_call = call(
            record,
            "req-replan",
            tool="memory_write",
            resource="replan",
            effect=EffectClass.MEMORY,
            args={"key": "replan", "value": "old"},
        )
        await gateway.evaluate(replan_call, allow_context())
        draft_v2 = await gateway.replan(
            "req-replan", TaskContractUpdateRequest(goals=["replanned goal"])
        )
        assert draft_v2.ref.status.value == "DRAFT" and draft_v2.contract.version == 2
        try:
            await gateway.execute("req-replan")
        except GatewayError:
            print("[PASS] REQUIRE_REPLAN creates DRAFT v2 and old request cannot execute")
        else:
            raise AssertionError("old request executed after replan")

        events = await event_store.list_task_events("task-pr2")
        event_types = {event["type"] for event in events}
        expected = {
            "CONTRACT_CONFIRMED",
            "PERMISSION_EVALUATED",
            "GATEWAY_ALLOWED",
            "WAITING_CONFIRMATION",
            "CONFIRMED",
            "EXECUTION_STARTED",
            "EXECUTION_FINISHED",
            "GATEWAY_DENIED",
            "REPLAN_CREATED",
        }
        assert expected <= event_types
        assert all(event["parent_event_id"] == (events[index - 1]["event_id"] if index else None) for index, event in enumerate(events))
        print(f"[PASS] real P2 JsonlEventStore captured {len(events)} ordered/linked BehaviorEvents")

        bundle = await exporter.export_task(task_id="task-pr2", checkpoint_id="pr2-checkpoint")
        result = verifier.verify_bundle(
            bundle, trusted_checkpoint_id="pr2-checkpoint", task_id="task-pr2"
        )
        assert result.valid and result.verified_events == len(events)
        print("[PASS] EventStore -> SM2/SM3 evidence -> AuditVerifier")

        tampered = copy.deepcopy(bundle)
        tampered["entries"][0]["event"]["state"] = "TAMPERED"
        tampered_result = verifier.verify_bundle(
            tampered, trusted_checkpoint_id="pr2-checkpoint", task_id="task-pr2"
        )
        assert not tampered_result.valid
        print("[PASS] tampered audit bundle rejected")

        assert signatures.verify_sm2(
            b"probe",
            signatures.sign_sm2(b"probe", key_id="verify-key"),
            key_id="verify-key",
        )
        print("[PASS] real SignatureProvider sign_sm2/verify_sm2")

    print("\nAegis Core P1 PR2 verification: ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
