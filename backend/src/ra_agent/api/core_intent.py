"""Operator-confirmed bounded report recovery from completed designated reads.

Local workbench controls have the same single-operator trust boundary as contract
confirmation. An external document/tool cannot approve a recovery or expand scope.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ra_agent.api.core_lifecycle import complete_core_confirmation, set_core_task_status
from ra_agent.contracts import APIResponse
from ra_agent.contracts.core_v1 import (
    EffectClass,
    PermissionContext,
    TaskContractUpdateRequest,
    ToolCallEnvelope,
)
from ra_agent.core.ids import new_id
from ra_agent.execution._cancellation import complete_before_cancelling
from ra_agent.gateway import GatewayError

router = APIRouter(prefix="/api/v1", tags=["intent-recovery"])


class RecoveryApproval(BaseModel):
    confirmed_by: str = Field(min_length=1, max_length=128)


def criteria(record):
    values = record.contract.completion_criteria
    targets = [v.removeprefix("intent:report=") for v in values if v.startswith("intent:report=")]
    required = [
        v.removeprefix("intent:contains=") for v in values if v.startswith("intent:contains=")
    ]
    sources = [
        v.removeprefix("intent:evidence_source=")
        for v in values
        if v.startswith("intent:evidence_source=")
    ]
    if len(targets) != 1 or len(required) != 3 or len(sources) != 1:
        raise HTTPException(
            409,
            "MANUAL_PLAN_REQUIRED: requires designated evidence and three approved report fields",
        )
    return targets[0], required, sources[0]


@router.post("/tasks/{task_id}/intent/recovery")
async def propose_recovery(task_id: str, request: Request) -> APIResponse[dict[str, Any]]:
    store, gateway = request.app.state.core_state_store, request.app.state.core_gateway
    async with request.app.state.core_admission_guard():
        stopped = await store.get("intent_tasks", task_id)
        if not stopped or stopped.get("status") != "STOPPED":
            raise HTTPException(409, "A safely stopped Intent task is required")
        counts = await store.get("intent_recovery_budget", task_id) or {"proposals": 0, "calls": 0}
        if counts["proposals"] >= 2 or counts["calls"] >= 4:
            raise HTTPException(409, "RECOVERY_BUDGET_EXHAUSTED")
        failed = await store.get("requests", stopped["request_id"])
        record = await gateway.contracts.get_active_contract(
            failed["envelope"]["contract_ref"]["contract_id"]
        )
        if record is None:
            raise HTTPException(409, "Confirmed contract unavailable")
        target, required, source = criteria(record)
        history = await store.get("intent_history", f"{task_id}:{record.ref.version}") or []
        origin = None
        for row in history:
            candidate = await store.get("requests", row["request_id"])
            if (
                candidate
                and candidate.get("execution_state") == "EXECUTED"
                and candidate["envelope"]["tool"] == "read_file"
                and candidate["envelope"]["resource"] == source
            ):
                origin = candidate
        if origin is None:
            raise HTTPException(409, "TRUSTED_SNAPSHOT_UNAVAILABLE: designated source was not read")
        content = origin["execution_result"]["result"].get("content")
        if not isinstance(content, str) or not all(token in content for token in required[:2]):
            raise HTTPException(409, "TRUSTED_SNAPSHOT_UNAVAILABLE: evidence fields do not match")
        # Fixed formatting copies only approved fields; untrusted instructions never
        # become a new planning prompt. The evidence values must occur in actual reads.
        report = f"Device: {required[0]}\nEvidence: {required[1]}\nRecommendation: {required[2]}"
        draft = await gateway.contracts.update_contract(
            record.ref.contract_id, TaskContractUpdateRequest()
        )
        plan = {
            "plan_id": new_id("correction"),
            "task_id": task_id,
            "base_contract_version": record.ref.version,
            "base_contract_digest": record.ref.digest,
            "draft_contract_version": draft.ref.version,
            "contract_id": draft.ref.contract_id,
            "source_request_id": origin["envelope"]["request_id"],
            "source_resource": source,
            "source_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "contaminated_refs": [stopped["request_id"]],
            "added_scope": [],
            "confirmation_required": True,
            "retry_budget": 2 - counts["proposals"],
            "proposed_actions": [
                {"tool": "read_file", "resource": source, "args": {"path": source}},
                {
                    "tool": "write_file",
                    "resource": target,
                    "args": {"path": target, "content": report},
                },
            ],
            "status": "PROPOSED",
        }
        counts["proposals"] += 1
        await store.run(
            lambda tx: (
                tx.put("intent_recovery_budget", task_id, counts),
                tx.put("intent_recovery_plans", plan["plan_id"], plan),
            )
        )
        await gateway.record_contract_event(
            draft, event_type="CORRECTION_PROPOSED", actor="bounded-report-corrector"
        )
    return APIResponse(data=plan)


@router.post("/intent/recovery/{plan_id}/approve")
async def approve_recovery(
    plan_id: str, body: RecoveryApproval, request: Request
) -> APIResponse[dict[str, Any]]:
    return await complete_before_cancelling(_approve_recovery(plan_id, body, request))


async def _approve_recovery(plan_id, body, request):
    store, gateway = request.app.state.core_state_store, request.app.state.core_gateway
    async with request.app.state.core_admission_guard():
        plan = await store.get("intent_recovery_plans", plan_id)
        if plan is None or plan["status"] != "PROPOSED":
            raise HTTPException(409, "Recovery plan unavailable or already consumed")
        task_id = plan["task_id"]
        active = await gateway.contracts.get_active_contract(plan["contract_id"])
        if active is None or active.ref.digest != plan["base_contract_digest"]:
            raise HTTPException(409, "RECOVERY_STALE: confirmed contract changed")
        draft = await gateway.contracts.get_contract_version(
            plan["contract_id"], plan["draft_contract_version"]
        )
        if draft is None:
            raise HTTPException(409, "Recovery draft unavailable")
        # Recheck every boundary and semantic field; no caller-supplied plan actions.
        original = active.contract.model_dump(exclude={"version", "parent_digest"})
        proposed = draft.contract.model_dump(exclude={"version", "parent_digest"})
        if original != proposed:
            raise HTTPException(409, "RECOVERY_SCOPE_CHANGED: recovery cannot expand authorization")
        counts = await store.get("intent_recovery_budget", task_id)
        if counts["calls"] + len(plan["proposed_actions"]) > 4:
            raise HTTPException(409, "RECOVERY_BUDGET_EXHAUSTED")
        return await _execute_plan(request, plan, counts, body)


async def _execute_plan(request, plan, counts, body):
    store, gateway = request.app.state.core_state_store, request.app.state.core_gateway
    task_id, plan_id = plan["task_id"], plan["plan_id"]
    record = await gateway.contracts.confirm_contract(
        plan["contract_id"], version=plan["draft_contract_version"], confirmed_by=body.confirmed_by
    )
    await complete_core_confirmation(request, record)
    counts["calls"] += len(plan["proposed_actions"])
    plan.update(status="EXECUTING", approved_by=body.confirmed_by)

    def release(tx):
        state = tx.get("intent_tasks", task_id)
        state["status"] = "RECOVERING"
        tx.put("intent_tasks", task_id, state)
        lifecycle = tx.get("task_lifecycle", task_id)
        lifecycle["status"] = "READY"
        tx.put("task_lifecycle", task_id, lifecycle)
        tx.put("intent_recovery_budget", task_id, counts)
        tx.put("intent_recovery_plans", plan_id, plan)

    await store.run(release)
    executions = []
    try:
        for action in plan["proposed_actions"]:
            envelope = ToolCallEnvelope(
                request_id=new_id("recovery-request"),
                task_id=task_id,
                session_id=record.contract.session_id,
                contract_ref=record.ref,
                skill_ref="core-ui",
                tool=action["tool"],
                action=action["tool"],
                resource=action["resource"],
                canonical_args=action["args"],
                effect_class=EffectClass.READ
                if action["tool"] == "read_file"
                else EffectClass.WRITE,
            )
            evaluation = await gateway.evaluate(envelope, PermissionContext())
            if evaluation.decision.decision.value != "ALLOW":
                raise GatewayError("Recovery action was rejected by current gateway")
            executed = await gateway.execute(envelope.request_id)
            executions.append(executed.model_dump(mode="json"))
            if action["tool"] == "read_file":
                content = executed.result.get("content", "")
                if hashlib.sha256(content.encode()).hexdigest() != plan["source_sha256"]:
                    raise GatewayError("Designated source changed after snapshot; safe stop")
        plan.update(status="COMPLETED", executions=executions)
        await set_core_task_status(request, task_id, "EXECUTED")
        await gateway.record_contract_event(
            record,
            event_type="CORRECTION_COMPLETED",
            actor="bounded-report-corrector",
            state="EXECUTED",
        )
    except (Exception, asyncio.CancelledError) as exc:
        plan.update(status="SAFE_STOPPED", error=str(exc), executions=executions)

        def stop(tx):
            state = tx.get("intent_tasks", task_id)
            state["status"] = "STOPPED"
            tx.put("intent_tasks", task_id, state)

        await store.run(stop)
        await set_core_task_status(request, task_id, "CANCELLED")
        await gateway.record_contract_event(
            record,
            event_type="CORRECTION_SAFE_STOPPED",
            actor="bounded-report-corrector",
            state="CANCELLED",
        )
    await store.run(lambda tx: tx.put("intent_recovery_plans", plan_id, plan))
    return APIResponse(data=plan)
