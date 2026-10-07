"""Runtime bridge between Person 3's detector and Person 1's execution gateway."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ra_agent.contracts.core_v1 import ToolCallEnvelope
from ra_agent.core.ids import new_id

from .models import DecisionResult, IntentCheckContext, IntentDecisionType
from .registry import IntentRegistry


class IntentDecisionProvider(Protocol):
    async def evaluate(
        self,
        context: IntentCheckContext,
        /,
    ) -> DecisionResult | None: ...


class IntentEnforcer:
    """Timeout-bounded detector invocation used directly by ToolGateway.

    ``None`` means the task is not yet bound to an IntentSpec and Core behaviour is
    preserved. A bound task can never silently pass a detector timeout.
    """

    def __init__(
        self,
        provider: IntentDecisionProvider,
        *,
        timeout_seconds: float = 1.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.provider = provider
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def build_context(
        envelope: ToolCallEnvelope,
        *,
        trusted_state: dict[str, object],
        recent_events: list[dict[str, object]] | None = None,
    ) -> IntentCheckContext:
        canonical_args = envelope.canonical_args
        subgoal = canonical_args.get("subgoal")
        if not isinstance(subgoal, str) or not subgoal.strip():
            subgoal = f"{envelope.tool}:{envelope.action}"
        return IntentCheckContext(
            request_id=envelope.request_id,
            task_id=envelope.task_id,
            contract_version=envelope.contract_ref.version,
            subgoal=subgoal.strip(),
            tool=envelope.tool,
            normalized_action=envelope.action.strip().casefold(),
            normalized_target=envelope.resource.strip(),
            effect_class=envelope.effect_class.value,
            canonical_args=dict(canonical_args),
            trusted_state=trusted_state,
            recent_events=list(recent_events or []),
        )

    async def evaluate(
        self,
        envelope: ToolCallEnvelope,
        *,
        trusted_state: dict[str, object],
        recent_events: list[dict[str, object]] | None = None,
    ) -> DecisionResult | None:
        context = self.build_context(
            envelope,
            trusted_state=trusted_state,
            recent_events=recent_events,
        )
        try:
            return await asyncio.wait_for(
                self.provider.evaluate(context),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            return DecisionResult(
                decision_id=new_id("intent-decision"),
                decision=IntentDecisionType.BLOCK,
                risk_score=1.0,
                trigger_dimensions=["detector_timeout"],
                evidence_refs=[],
                reason_code="INTENT_CHECK_TIMEOUT",
                policy_version="intent-runtime-fail-closed-v1",
                detector_version="unavailable",
                expires_at=datetime.now(UTC) + timedelta(seconds=5),
            )
        except Exception:
            # Detector unavailability for a task already bound to Intent is security
            # relevant. The baseline fails closed and leaves diagnosis to evidence/logs.
            return DecisionResult(
                decision_id=new_id("intent-decision"),
                decision=IntentDecisionType.BLOCK,
                risk_score=1.0,
                trigger_dimensions=["detector_unavailable"],
                evidence_refs=[],
                reason_code="INTENT_CHECK_UNAVAILABLE",
                policy_version="intent-runtime-fail-closed-v1",
                detector_version="unavailable",
                expires_at=datetime.now(UTC) + timedelta(seconds=5),
            )


class BaselineIntentDetector:
    """Deterministic integration detector; replaceable by Person 3's detector.

    It intentionally checks only explicit IntentSpec boundaries. It is not presented as
    the final semantic/sequence detector, but makes the execution-before-detection wiring
    testable immediately.
    """

    detector_version = "intent-baseline-boundary-v1"
    policy_version = "intent-policy-v1"

    def __init__(self, registry: IntentRegistry) -> None:
        self.registry = registry

    @staticmethod
    def _digest_context(context: IntentCheckContext) -> str:
        payload = json.dumps(
            context.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _action_allowed(action: str, allowed_actions: list[str]) -> bool:
        if not allowed_actions:
            return True
        aliases = {
            "read": {"read", "read_file", "list_dir"},
            "search": {"search", "retrieve", "query"},
            "generate": {"generate", "render", "compose", "summarize"},
            "write": {"write", "write_file", "create_file", "save"},
            "memory": {"memory", "memory_read", "memory_write", "remember"},
            "send": {"send", "egress", "upload", "post"},
            "delete": {"delete", "delete_file", "remove"},
            "execute": {"execute", "run", "run_shell"},
        }
        normalized = action.casefold()
        for declared in allowed_actions:
            item = declared.casefold()
            if normalized == item or normalized in aliases.get(item, {item}):
                return True
        return False

    @staticmethod
    def _action_forbidden(action: str, forbidden_actions: list[str]) -> bool:
        if not forbidden_actions:
            return False
        return BaselineIntentDetector._action_allowed(action, forbidden_actions)

    @staticmethod
    def _target_in_scope(target: str, scope: list[str]) -> bool:
        if not scope:
            return True
        normalized = target.replace("\\", "/")
        for pattern in scope:
            candidate = pattern.replace("\\", "/")
            if candidate == "*" or fnmatch.fnmatchcase(normalized, candidate):
                return True
            if normalized == candidate or normalized.startswith(candidate.rstrip("/") + "/"):
                return True
        return False

    async def evaluate(self, context: IntentCheckContext) -> DecisionResult | None:
        bound = await self.registry.get_for_task(context.task_id)
        if bound is None:
            return None
        spec, contract = bound
        now = datetime.now(UTC)
        base = {
            "decision_id": new_id("intent-decision"),
            "evidence_refs": [
                f"intent:{spec.intent_id}:v{spec.version}",
                f"intent-contract:{contract.contract_id}:v{contract.contract_version}",
                f"intent-input:{self._digest_context(context)}",
            ],
            "policy_version": self.policy_version,
            "detector_version": self.detector_version,
            "expires_at": now + timedelta(seconds=30),
        }

        # IntentSpec/TaskContract are a projection of the Core contract, but they
        # must still refer to the same active version.  Otherwise a stale Intent
        # record could authorize a decision against a newer or different task
        # contract.  The gateway supplies these values as trusted state.
        core_version = context.trusted_state.get("contract_version")
        # The Intent TaskContract is a branch projection and intentionally has its
        # own contract_id; the shared binding is task_id plus the active version.
        if contract.contract_version != core_version:
            return DecisionResult(
                **base,
                decision=IntentDecisionType.REPLAN,
                risk_score=1.0,
                trigger_dimensions=["contract_version_mismatch"],
                reason_code="INTENT_CONTRACT_VERSION_MISMATCH",
            )

        if contract.status.value != "CONFIRMED" or spec.confirmed_by is None:
            return DecisionResult(
                **base,
                decision=IntentDecisionType.REPLAN,
                risk_score=0.8,
                trigger_dimensions=["intent_unconfirmed"],
                reason_code="INTENT_CONFIRMATION_REQUIRED",
            )

        action = context.normalized_action.casefold()
        if self._action_forbidden(action, spec.forbidden_actions):
            return DecisionResult(
                **base,
                decision=IntentDecisionType.BLOCK,
                risk_score=1.0,
                trigger_dimensions=["forbidden_action"],
                reason_code="INTENT_FORBIDDEN_ACTION",
            )
        if not self._action_allowed(action, spec.allowed_actions):
            return DecisionResult(
                **base,
                decision=IntentDecisionType.REPLAN,
                risk_score=0.9,
                trigger_dimensions=["action_outside_intent"],
                reason_code="INTENT_ACTION_OUTSIDE_SCOPE",
            )
        if not self._target_in_scope(context.normalized_target, spec.scope):
            return DecisionResult(
                **base,
                decision=IntentDecisionType.REPLAN,
                risk_score=0.9,
                trigger_dimensions=["resource_scope"],
                reason_code="INTENT_RESOURCE_OUTSIDE_SCOPE",
            )
        return DecisionResult(
            **base,
            decision=IntentDecisionType.CONTINUE,
            risk_score=0.05,
            trigger_dimensions=[],
            reason_code="INTENT_ALIGNED",
        )
