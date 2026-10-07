"""Versioned, bounded task-consistency checks before real Core execution.

Opt-in criteria are approved contract text: intent:report=<path>,
intent:contains=<required literal>. They describe constraints, not gold labels.
This rule version covers report consistency and repeated identical read proposals;
it is not a general semantic classifier or an automatic recovery planner.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ra_agent.contracts.core_v1 import TaskContractV2, ToolCallEnvelope
from ra_agent.contracts.intent import IntentAssessment

if TYPE_CHECKING:
    from ra_agent.gateway.state import CoreStateStore, CoreStateTransaction


@dataclass(frozen=True)
class IntentPolicy:
    version: str = "intent-policy:1"
    repeat_limit: int = 3
    window: int = 8
    check_sequence: bool = True
    check_source: bool = True
    check_report: bool = True

    def __post_init__(self) -> None:
        if not 2 <= self.repeat_limit <= self.window <= 32:
            raise ValueError("intent window must be 2..32 and cover repeat_limit")


class IntentRuleGuard:
    def __init__(
        self,
        store: CoreStateStore,
        policy: IntentPolicy | None = None,
        policy_provider: Callable[[], IntentPolicy] | None = None,
    ) -> None:
        self.store = store
        self.policy = policy or IntentPolicy()
        self.policy_provider = policy_provider

    async def check(self, envelope: ToolCallEnvelope, contract: TaskContractV2) -> IntentAssessment:
        policy = self.policy_provider() if self.policy_provider else self.policy
        return await self.store.run(lambda tx: self._check(tx, envelope, contract, policy))

    def _check(
        self,
        tx: CoreStateTransaction,
        envelope: ToolCallEnvelope,
        contract: TaskContractV2,
        policy: IntentPolicy,
    ) -> IntentAssessment:
        targets = [
            c.removeprefix("intent:report=")
            for c in contract.completion_criteria
            if c.startswith("intent:report=")
        ]
        required = [
            c.removeprefix("intent:contains=")
            for c in contract.completion_criteria
            if c.startswith("intent:contains=")
        ]
        enabled = bool(targets)
        state = tx.get("intent_tasks", envelope.task_id) or {}
        key = f"{envelope.task_id}:{contract.version}"
        history = tx.get("intent_history", key) or []
        index = next(
            (i for i, row in enumerate(history) if row["request_id"] == envelope.request_id), None
        )
        # Rechecking a request does not create another step or read later proposals.
        prefix = history[:index] if index is not None else history
        sequence = history[index]["step_index"] if index is not None else state.get("next_step", 0)
        request_state = tx.get("requests", envelope.request_id)
        context = request_state.get("intent_context") if request_state else None
        if context is not None:
            prefix, sequence = context["prefix"], context["step_index"]
        elif enabled and request_state is not None:
            request_state["intent_context"] = {
                "prefix": prefix[-policy.window :],
                "step_index": sequence,
            }
            tx.put("requests", envelope.request_id, request_state)
        dimensions: list[str] = []
        refs: list[str] = []
        if enabled and state.get("status") == "STOPPED":
            dimensions.append("task_safely_stopped")
        if enabled and (
            not required or any(not t for t in targets) or any(not r for r in required)
        ):
            dimensions.append("intent_criteria_invalid")
        if (
            enabled
            and policy.check_report
            and envelope.tool in {"create_file", "write_file"}
            and envelope.resource in targets
        ):
            content = envelope.canonical_args.get("content")
            if not isinstance(content, str) or any(token not in content for token in required):
                dimensions.append("report_completion_mismatch")
        action_digest = hashlib.sha256(
            json.dumps(
                [envelope.tool, envelope.resource, envelope.canonical_args],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        run = 1
        if enabled and policy.check_sequence and envelope.tool in {"read_file", "memory_read"}:
            for previous in reversed(prefix[-policy.window :]):
                if previous["action_digest"] != action_digest:
                    break
                run += 1
                refs.append(f"request:{previous['request_id']}")
            if run >= policy.repeat_limit:
                dimensions.append("repeated_read_without_new_input")
        if enabled and policy.check_source and dimensions:
            # Origins are completed server-side reads, not caller-provided trust labels.
            for previous in prefix[-policy.window :]:
                request = tx.get("requests", previous["request_id"])
                if request and request.get("execution_state") == "EXECUTED":
                    tool = request["envelope"]["tool"]
                    if tool in {"read_file", "memory_read"}:
                        refs.append(f"request:{previous['request_id']}")
                        refs.append(f"resource:{request['envelope']['resource']}")
            if refs:
                dimensions.append("upstream_read_evidence")
        blocked = bool(dimensions)
        assessment = IntentAssessment(
            risk_score=1.0 if blocked else 0.0,
            trigger_dimensions=dimensions,
            evidence_refs=list(dict.fromkeys(refs))[:64],
            policy_version=policy.version,
            contract_version=contract.version,
            contract_digest=envelope.contract_ref.digest,
            step_index=sequence,
            disposition="SAFE_STOP" if blocked else "CONTINUE",
            enabled=enabled,
            reason="; ".join(dimensions)
            if blocked
            else (
                "within approved report criteria" if enabled else "intent criteria not configured"
            ),
        )
        if enabled:
            row = {
                "request_id": envelope.request_id,
                "step_index": sequence,
                "action_digest": action_digest,
                "assessment": assessment.model_dump(mode="json"),
            }
            if index is None and context is None:
                history.append(row)
                state["next_step"] = sequence + 1
            elif index is not None:
                history[index] = row
            tx.put("intent_history", key, history[-policy.window :])
            if blocked:
                state.update(
                    status="STOPPED",
                    request_id=envelope.request_id,
                    contract_version=contract.version,
                    reason=assessment.reason,
                )
                lifecycle = tx.get("task_lifecycle", envelope.task_id) or {}
                lifecycle.update(status="CANCELLED", intent_reason=assessment.reason)
                tx.put("task_lifecycle", envelope.task_id, lifecycle)
            tx.put("intent_tasks", envelope.task_id, state)
            tx.put("intent_decisions", envelope.request_id, assessment.model_dump(mode="json"))
        return assessment


async def inspect_intent(
    guard: Any, envelope: ToolCallEnvelope, contract: TaskContractV2
) -> IntentAssessment:
    """A failed/slow detector never turns a high-risk call into ALLOW."""
    return await asyncio.wait_for(guard.check(envelope, contract), timeout=2.0)
