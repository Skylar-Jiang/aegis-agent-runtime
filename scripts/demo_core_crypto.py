"""Run the real Core Gateway -> event chain -> signed checkpoint -> verifier flow.

Usage:
    uv run --project backend --no-editable python scripts/demo_core_crypto.py \
        --output .runtime/p3-demo
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import tempfile
from pathlib import Path

from ra_agent.audit import (
    AuditExportService,
    AuditVerifier,
    FileCheckpointStore,
    FileEvidenceRecorder,
    HashChain,
)
from ra_agent.contracts import (
    ContractPermissionRule,
    ContractService,
    EffectClass,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractCreateRequest,
    ToolCallEnvelope,
)
from ra_agent.crypto import (
    Canonicalizer,
    EnvelopeService,
    OpenSSLSignatureProvider,
    generate_sm2_key,
)
from ra_agent.events import JsonlEventStore
from ra_agent.gateway import ToolGateway
from ra_agent.permissions import PermissionResolver


def _permissions() -> PermissionContext:
    def grant(source: str, *, subject: str = "*", skill: str = "*") -> PermissionGrant:
        return PermissionGrant(
            subject=subject,
            skill=skill,
            tool="create_file",
            action="create_file",
            resource="reports/**",
            effect=GrantEffect.ALLOW,
            scope=GrantScope.GLOBAL,
            source=source,
        )

    return PermissionContext(
        user_grants=[grant("user", subject="fixture-user-1")],
        skill_grants=[grant("skill", skill="fixture-writer")],
        system_grants=[grant("system")],
    )


async def _run(output: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="aegis-demo-key-") as directory:
        private_key = Path(directory) / "private.pem"
        public = generate_sm2_key(private_key)
        keys = {"fixture-audit-key": public}
        provider = OpenSSLSignatureProvider(
            keys,
            private_keys={"fixture-audit-key": private_key},
        )
        signer = EnvelopeService(provider)
        evidence = FileEvidenceRecorder(
            output / "signed_objects",
            signer,
            key_id="fixture-audit-key",
        )
        event_store = JsonlEventStore(
            output / "events-store.jsonl",
            chain_factory=HashChain,
        )
        contracts = ContractService()
        created = await contracts.create_contract(
            TaskContractCreateRequest(
                session_id="fixture-session-1",
                task_id="fixture-task-1",
                user_id="fixture-user-1",
                goals=["Create a verified demo report"],
                completion_criteria=["Gateway records a signed dry-run result"],
                allowed=[
                    ContractPermissionRule(
                        tool="create_file",
                        action="create_file",
                        resource="reports/**",
                        effect="WRITE",
                    )
                ],
                policy_version="fixture-policy:1",
                tool_manifest_digest="a" * 64,
            )
        )
        confirmed = await contracts.confirm_contract(
            created.contract.contract_id,
            version=created.contract.version,
            confirmed_by="fixture-user-1",
        )
        call = ToolCallEnvelope(
            request_id="fixture-request-1",
            task_id=confirmed.contract.task_id,
            session_id=confirmed.contract.session_id,
            contract_ref=confirmed.ref,
            skill_ref="fixture-writer",
            tool="create_file",
            action="create_file",
            canonical_args={
                "path": "reports/demo.md",
                "content": "verified",
            },
            resource="reports/demo.md",
            effect_class=EffectClass.WRITE,
        )
        gateway = ToolGateway(
            contracts=contracts,
            resolver=PermissionResolver(),
            event_store=event_store,
            signature_provider=provider,
            evidence_recorder=evidence,
        )
        await gateway.evaluate(call, _permissions())
        await gateway.execute(call.request_id)

        checkpoint_writer = FileCheckpointStore(output / "trusted_checkpoints", signer)
        exporter = AuditExportService(
            event_store=event_store,
            evidence_recorder=evidence,
            envelopes=signer,
            checkpoints=checkpoint_writer,
            key_id="fixture-audit-key",
        )
        bundle = await exporter.export_task(
            task_id=call.task_id,
            checkpoint_id="fixture-cp-1",
        )
        events = await event_store.list_task_events(call.task_id)

        canonical = Canonicalizer()
        (output / "bundle.json").write_bytes(canonical.canonicalize(bundle) + b"\n")
        (output / "public_keys.json").write_bytes(canonical.canonicalize(keys) + b"\n")
        (output / "events.jsonl").write_bytes(
            b"\n".join(canonical.canonicalize(event) for event in events) + b"\n"
        )

        readonly = EnvelopeService(OpenSSLSignatureProvider(keys))
        verifier = AuditVerifier(
            readonly,
            FileCheckpointStore(output / "trusted_checkpoints", readonly),
        )
        normal = verifier.verify_bundle(
            bundle,
            trusted_checkpoint_id="fixture-cp-1",
            task_id=call.task_id,
        )
        tampered_bundle = copy.deepcopy(bundle)
        tampered_bundle["entries"][0]["event"]["decision"] = "DENY"
        tampered = verifier.verify_bundle(
            tampered_bundle,
            trusted_checkpoint_id="fixture-cp-1",
            task_id=call.task_id,
        )
        return {
            "pipeline": "Gateway->JsonlEventStore->AuditExportService->AuditVerifier",
            "normal": normal.model_dump(),
            "tampered": tampered.model_dump(),
            "events": len(events),
            "signed_objects": len(bundle["objects"]),
            "private_key_saved": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New directory, never overwritten",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = asyncio.run(_run(args.output))
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["normal"]["valid"] and not result["tampered"]["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
