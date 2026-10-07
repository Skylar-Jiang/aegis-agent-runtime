"""Unified Aegis Core ToolGateway with PR2 confirmation and cross-module wiring."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

from ra_agent.confirmations import ConfirmationConflictError, ConfirmationService
from ra_agent.contracts.core_service import ContractService
from ra_agent.contracts.core_v1 import (
    ConfirmationRecord,
    ConfirmationStatus,
    ContractRecord,
    EffectClass,
    EffectivePermission,
    GatewayDecision,
    GatewayDecisionType,
    GatewayEvaluationResult,
    GatewayExecutionResult,
    GatewayReasonCode,
    PermissionContext,
    TaskContractUpdateRequest,
    ToolCallEnvelope,
)
from ra_agent.core.ids import new_id
from ra_agent.core.offload import offload
from ra_agent.execution._cancellation import complete_before_cancelling
from ra_agent.intent import DecisionResult, IntentDecisionType, IntentEnforcer
from ra_agent.permissions import PermissionResolver

from .authority import PermissionAuthority
from .fakes import DryRunToolExecutor, FakeSignatureProvider, InMemoryEventStore
from .interfaces import EventStore, EvidenceRecorder, SignatureProvider, ToolExecutorAdapter
from .quotas import QuotaExceeded, check_output_limit, reserve_quotas
from .state import CoreStateStore, CoreStateTransaction


class GatewayError(RuntimeError):
    pass


@dataclass(slots=True)
class _EvaluationSnapshot:
    envelope: ToolCallEnvelope
    result: GatewayEvaluationResult
    confirmation_id: str | None = None


class ToolGateway:
    def __init__(
        self,
        *,
        contracts: ContractService,
        resolver: PermissionResolver,
        event_store: EventStore | None = None,
        signature_provider: SignatureProvider | None = None,
        evidence_recorder: EvidenceRecorder | None = None,
        confirmation_service: ConfirmationService | None = None,
        executor: ToolExecutorAdapter | None = None,
        store: CoreStateStore | None = None,
        request_validator: Callable[[ToolCallEnvelope], None] | None = None,
        permission_provider: Callable[
            [ToolCallEnvelope], Awaitable[PermissionContext | PermissionAuthority]
        ]
        | None = None,
        admission_guard: Callable[[], AbstractAsyncContextManager[None]] | None = None,
        task_limit_provider: Callable[[ToolCallEnvelope], Awaitable[dict[str, Any]]] | None = None,
        intent_enforcer: IntentEnforcer | None = None,
        intent_effect_recorder: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._store = store if store is not None else CoreStateStore()
        self.request_validator = request_validator
        self.permission_provider = permission_provider
        self.admission_guard = admission_guard
        self.task_limit_provider = task_limit_provider
        self.intent_enforcer = intent_enforcer
        self.intent_effect_recorder = intent_effect_recorder
        self.contracts = contracts
        self.resolver = resolver
        self.event_store = event_store or InMemoryEventStore()
        self.signature_provider = signature_provider or FakeSignatureProvider()
        self.evidence_recorder = evidence_recorder
        self.confirmations = confirmation_service or ConfirmationService(store=self._store)
        self.executor = executor or DryRunToolExecutor()
        self._adapter_token = object()
        bind = getattr(self.executor, "bind_gateway", None)
        if bind is not None:
            bind(self._adapter_token)

    @staticmethod
    def _fingerprint(envelope: ToolCallEnvelope) -> str:
        payload = json.dumps(
            envelope.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _request_state(self, request_id: str) -> dict[str, Any] | None:
        with self._store.read_transaction() as transaction:
            return transaction.get("requests", request_id)

    def _snapshot(self, request_id: str) -> _EvaluationSnapshot | None:
        state = self._request_state(request_id)
        if state is None or state["evaluation"] is None:
            return None
        return _EvaluationSnapshot(
            envelope=ToolCallEnvelope.model_validate(state["envelope"]),
            result=GatewayEvaluationResult.model_validate(state["evaluation"]),
            confirmation_id=state["confirmation_id"],
        )

    async def evaluate(
        self,
        envelope: ToolCallEnvelope,
        permissions: PermissionContext,
        *,
        confirmation_satisfied: bool = False,
        confirmation_id: str | None = None,
    ) -> GatewayEvaluationResult:
        envelope = envelope.model_copy(deep=True)
        if self.request_validator is not None:
            self.request_validator(envelope)
        fingerprint = self._fingerprint(envelope)

        def register(transaction: CoreStateTransaction) -> GatewayEvaluationResult | None:
            state = transaction.get("requests", envelope.request_id)
            if state is not None:
                if state["fingerprint"] != fingerprint:
                    raise GatewayError("request_id is already bound to a different envelope")
                if (
                    state["evaluation"] is not None
                    and not state["blocked"]
                    and (
                        state["execution_state"] == "EXECUTED"
                        or (
                            state.get("evaluation_revision") == state["revision"]
                            and self.permission_provider is None
                            and self.intent_enforcer is None
                        )
                    )
                ):
                    return GatewayEvaluationResult.model_validate(state["evaluation"])
            else:
                transaction.put(
                    "requests",
                    envelope.request_id,
                    {
                        "fingerprint": fingerprint,
                        "envelope": envelope.model_dump(mode="json"),
                        "permissions": permissions.model_dump(mode="json"),
                        "permissions_explicit": bool(
                            permissions.user_grants
                            or permissions.skill_grants
                            or permissions.system_grants
                        ),
                        "revision": 0,
                        "evaluation": None,
                        "confirmation_id": None,
                        "blocked": False,
                        "execution_state": "READY",
                        "execution_result": None,
                    },
                )
                transaction.put("task_latest_request", envelope.task_id, envelope.request_id)

        cached = await self._store.run(register)
        if cached is not None:
            return cached
        return await self._evaluate_current(
            envelope,
            confirmation_satisfied=confirmation_satisfied,
            confirmation_id=confirmation_id,
        )

    @offload
    def replace_permission_context(
        self,
        request_id: str,
        permissions: PermissionContext,
    ) -> None:
        """Refresh the permission source used by later confirmation/execute rechecks."""

        with self._store.transaction() as transaction:
            state = transaction.get("requests", request_id)
            if state is None:
                raise GatewayError("Unknown request_id")
            if state["execution_state"] == "ADMITTED":
                raise GatewayError("Request has already been admitted for execution")
            state["permissions"] = permissions.model_dump(mode="json")
            state["permissions_explicit"] = True
            state["revision"] += 1
            transaction.put("requests", request_id, state)

    async def _evaluate_current(
        self,
        envelope: ToolCallEnvelope,
        *,
        confirmation_satisfied: bool,
        confirmation_id: str | None,
        record_events: bool = True,
    ) -> GatewayEvaluationResult:
        authority = None
        authority_versions: dict[str, int | str] = {}
        if self.permission_provider is not None:
            provided = await self.permission_provider(envelope.model_copy(deep=True))
            if isinstance(provided, PermissionAuthority):
                authority = provided.permissions.model_copy(deep=True)
                authority_versions = dict(provided.versions)
            else:
                authority = provided.model_copy(deep=True)
        state = await self._store.get("requests", envelope.request_id)
        if state is None:
            raise GatewayError("Permission context is unavailable for request_id")
        permissions = PermissionContext.model_validate(state["permissions"])

        record_result = partial(
            self._record_result,
            permission_revision=state["revision"],
            record_events=record_events,
            authority_versions=authority_versions,
        )

        if state["blocked"]:
            return await record_result(
                envelope,
                EffectivePermission(
                    conflict_code=GatewayReasonCode.VERSION_STALE,
                    conflict_reason="request was superseded by an explicit replan",
                ),
                GatewayDecisionType.REQUIRE_REPLAN,
                GatewayReasonCode.VERSION_STALE,
                confirmation_id=confirmation_id,
                permission_evaluated=False,
            )

        stored = await self.contracts.get_contract_version(
            envelope.contract_ref.contract_id, envelope.contract_ref.version
        )
        if stored is None:
            return await record_result(
                envelope,
                EffectivePermission(
                    conflict_code=GatewayReasonCode.CHECK_UNAVAILABLE,
                    conflict_reason="referenced contract version is unavailable",
                ),
                GatewayDecisionType.DENY,
                GatewayReasonCode.CHECK_UNAVAILABLE,
                confirmation_id=confirmation_id,
                permission_evaluated=False,
            )

        if stored.ref.digest != envelope.contract_ref.digest:
            return await record_result(
                envelope,
                EffectivePermission(
                    conflict_code=GatewayReasonCode.SIGNATURE_INVALID,
                    conflict_reason="contract digest does not match the stored version",
                ),
                GatewayDecisionType.DENY,
                GatewayReasonCode.SIGNATURE_INVALID,
                confirmation_id=confirmation_id,
                permission_evaluated=False,
            )

        active = await self.contracts.get_active_contract(envelope.contract_ref.contract_id)
        if active is None:
            return await record_result(
                envelope,
                EffectivePermission(
                    conflict_code=GatewayReasonCode.CHECK_UNAVAILABLE,
                    conflict_reason="TaskContractV2 has not been confirmed",
                ),
                GatewayDecisionType.DENY,
                GatewayReasonCode.CHECK_UNAVAILABLE,
                confirmation_id=confirmation_id,
                permission_evaluated=False,
            )
        if (
            active.ref.version != envelope.contract_ref.version
            or active.ref.digest != stored.ref.digest
        ):
            return await record_result(
                envelope,
                EffectivePermission(
                    conflict_code=GatewayReasonCode.VERSION_STALE,
                    conflict_reason="a newer confirmed contract version is active",
                ),
                GatewayDecisionType.REQUIRE_REPLAN,
                GatewayReasonCode.VERSION_STALE,
                confirmation_id=confirmation_id,
                permission_evaluated=False,
            )

        effective = self.resolver.resolve_effective_permission(
            envelope,
            active.contract,
            authority if authority is not None else permissions,
        )
        # Caller grants are a ceiling, never a substitute for server authority.
        # An omitted/empty initial context requests the server defaults; an explicit
        # replacement with empty grants is still a revocation.
        if (
            authority is not None
            and effective.conflict_code is None
            and state["permissions_explicit"]
        ):
            requested = self.resolver.resolve_effective_permission(
                envelope,
                active.contract,
                permissions,
            )
            if requested.conflict_code is not None:
                effective = requested
            else:
                constraints = dict(effective.constraints)
                for name, value in requested.constraints.items():
                    previous = constraints.get(name)
                    if (
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and isinstance(previous, (int, float))
                        and not isinstance(previous, bool)
                    ):
                        constraints[name] = min(previous, value)
                    elif name == "earliest_expiry" and previous is not None:
                        constraints[name] = min(previous, value)
                    elif name not in constraints:
                        constraints[name] = value
                effective = EffectivePermission(
                    allowed=[*effective.allowed, *requested.allowed],
                    denied=[*effective.denied, *requested.denied],
                    constraints=constraints,
                    matched_sources=list(
                        dict.fromkeys([*effective.matched_sources, *requested.matched_sources])
                    ),
                    requires_confirmation=(
                        effective.requires_confirmation or requested.requires_confirmation
                    ),
                )
        if effective.conflict_code is not None:
            return await record_result(
                envelope,
                effective,
                GatewayDecisionType.DENY,
                effective.conflict_code,
                confirmation_id=confirmation_id,
            )

        intent_result = await self._evaluate_intent(
            envelope,
            active,
            effective,
            confirmation_satisfied=confirmation_satisfied,
            confirmation_id=confirmation_id,
            record_events=record_events,
            record_result=record_result,
        )
        if intent_result is not None:
            if isinstance(intent_result, GatewayEvaluationResult):
                return intent_result
            confirmation_satisfied, confirmation_id = intent_result

        if effective.requires_confirmation:
            if not confirmation_satisfied:
                existing = await self.confirmations.get_for_request(envelope.request_id)
                if existing is not None and existing.status is ConfirmationStatus.CONFIRMED:
                    confirmation_satisfied = True
                    confirmation_id = existing.confirmation_id
                elif existing is not None and existing.status is ConfirmationStatus.REJECTED:
                    return await record_result(
                        envelope,
                        effective,
                        GatewayDecisionType.DENY,
                        GatewayReasonCode.USER_DENY,
                        confirmation_id=existing.confirmation_id,
                    )
            if confirmation_satisfied:
                record = await self._validated_confirmation(
                    envelope,
                    active,
                    confirmation_id,
                )
                confirmation_id = record.confirmation_id
            else:
                record = await self.confirmations.request_confirmation(
                    request_id=envelope.request_id,
                    task_id=envelope.task_id,
                    contract_ref=active.ref,
                    policy_version=active.contract.policy_version,
                )
                confirmation_id = record.confirmation_id
                return await record_result(
                    envelope,
                    effective,
                    GatewayDecisionType.REQUIRE_CONFIRMATION,
                    GatewayReasonCode.CONFIRMATION_REQUIRED,
                    confirmation_id=confirmation_id,
                )

        return await record_result(
            envelope,
            effective,
            GatewayDecisionType.ALLOW,
            GatewayReasonCode.ALLOWED,
            confirmation_id=confirmation_id,
        )

    async def _evaluate_intent(
        self,
        envelope: ToolCallEnvelope,
        active: ContractRecord,
        effective: EffectivePermission,
        *,
        confirmation_satisfied: bool,
        confirmation_id: str | None,
        record_events: bool,
        record_result: Callable[..., Awaitable[GatewayEvaluationResult]],
    ) -> GatewayEvaluationResult | tuple[bool, str | None] | None:
        if self.intent_enforcer is None:
            return None
        recent_event_records = await self.event_store.list_task_events(envelope.task_id)
        recent_events: list[dict[str, object]] = [
            {
                "type": item.get("type"),
                "state": item.get("state"),
                "actor": item.get("actor"),
                "source_ref": item.get("source_ref"),
                "request_id": item.get("request_id"),
                "occurred_at": item.get("occurred_at"),
                "reason_code": item.get("reason_code"),
                }
                for item in recent_event_records[-16:]
            ]
        decision = await self.intent_enforcer.evaluate(
            envelope,
            trusted_state={
                "contract_id": active.ref.contract_id,
                "contract_version": active.ref.version,
                "contract_digest": active.ref.digest,
                "policy_version": active.contract.policy_version,
            },
            recent_events=recent_events,
        )
        if decision is None:
            return None

        # A detector result is useful only inside its validity window. Expired output
        # is treated as unavailable rather than silently reused.
        if decision.expires_at <= datetime.now(UTC):
            decision = DecisionResult(
                decision_id=decision.decision_id,
                decision=IntentDecisionType.BLOCK,
                risk_score=1.0,
                trigger_dimensions=[*decision.trigger_dimensions, "decision_expired"],
                evidence_refs=decision.evidence_refs,
                reason_code="INTENT_CHECK_UNAVAILABLE",
                policy_version=decision.policy_version,
                detector_version=decision.detector_version,
                expires_at=datetime.now(UTC),
            )

        decision_digest = await self._record_object(
            envelope,
            object_type="DecisionResult",
            payload=decision.model_dump(mode="json"),
        )
        refs = self._dedupe_refs(
            *decision.evidence_refs,
            decision_digest,
        )

        def save_intent_decision(transaction: CoreStateTransaction) -> None:
            state = transaction.get("requests", envelope.request_id)
            if state is None:
                raise GatewayError("Permission context is unavailable for request_id")
            state["intent_decision"] = decision.model_dump(mode="json")
            state["intent_decision_digest"] = decision_digest
            transaction.put("requests", envelope.request_id, state)

        await self._store.run(save_intent_decision)

        if record_events:
            await self._append_event(
                envelope,
                event_type=(
                    "INTENT_CHECK_PASSED"
                    if decision.decision is IntentDecisionType.CONTINUE
                    else (
                        "INTENT_SAFE_TERMINATED"
                        if decision.decision is IntentDecisionType.SAFE_TERMINATE
                        else "INTENT_CHECK_TRIGGERED"
                    )
                ),
                state=decision.decision.value,
                actor="intent-detector",
                source_ref=f"intent-decision:{decision.decision_id}",
                decision=None,
                reason_code=None,
                object_digest=decision_digest,
                result_digest=None,
                evidence_refs=self._event_refs(envelope, decision_digest) + refs,
            )

        version_overrides = {
            "intent_policy": decision.policy_version,
            "intent_detector": decision.detector_version,
        }
        if decision.decision is IntentDecisionType.CONTINUE:
            return None

        if decision.decision is IntentDecisionType.REQUEST_CONFIRMATION:
            existing = await self.confirmations.get_for_request(envelope.request_id)
            if existing is not None and existing.status is ConfirmationStatus.CONFIRMED:
                confirmation_id = existing.confirmation_id
                await self._validated_confirmation(envelope, active, confirmation_id)
                return True, confirmation_id
            if existing is not None and existing.status is ConfirmationStatus.REJECTED:
                return await record_result(
                    envelope,
                    effective,
                    GatewayDecisionType.DENY,
                    GatewayReasonCode.USER_DENY,
                    confirmation_id=existing.confirmation_id,
                    extra_evidence_refs=refs,
                    version_overrides=version_overrides,
                )
            if confirmation_satisfied and confirmation_id is not None:
                await self._validated_confirmation(envelope, active, confirmation_id)
                return True, confirmation_id
            record = await self.confirmations.request_confirmation(
                request_id=envelope.request_id,
                task_id=envelope.task_id,
                contract_ref=active.ref,
                policy_version=active.contract.policy_version,
            )
            return await record_result(
                envelope,
                effective,
                GatewayDecisionType.REQUIRE_CONFIRMATION,
                GatewayReasonCode.INTENT_CONFIRMATION_REQUIRED,
                confirmation_id=record.confirmation_id,
                extra_evidence_refs=refs,
                version_overrides=version_overrides,
            )

        if decision.decision is IntentDecisionType.REPLAN:
            return await record_result(
                envelope,
                effective,
                GatewayDecisionType.REQUIRE_REPLAN,
                GatewayReasonCode.INTENT_REPLAN_REQUIRED,
                confirmation_id=confirmation_id,
                extra_evidence_refs=refs,
                version_overrides=version_overrides,
            )

        reason_code = {
            "INTENT_CHECK_TIMEOUT": GatewayReasonCode.INTENT_CHECK_TIMEOUT,
            "INTENT_CHECK_UNAVAILABLE": GatewayReasonCode.INTENT_CHECK_UNAVAILABLE,
            "INTENT_CONTRACT_VERSION_MISMATCH": (
                GatewayReasonCode.INTENT_CONTRACT_VERSION_MISMATCH
            ),
            "INTENT_SAFE_TERMINATE": GatewayReasonCode.INTENT_SAFE_TERMINATE,
        }.get(decision.reason_code, GatewayReasonCode.INTENT_DRIFT)
        return await record_result(
            envelope,
            effective,
            GatewayDecisionType.DENY,
            reason_code,
            confirmation_id=confirmation_id,
            extra_evidence_refs=refs,
            version_overrides=version_overrides,
        )

    async def _validated_confirmation(
        self,
        envelope: ToolCallEnvelope,
        active: ContractRecord,
        confirmation_id: str | None,
    ) -> ConfirmationRecord:
        if confirmation_id is None:
            raise GatewayError("confirmation_id is required for a confirmed execution path")
        record = await self.confirmations.get_confirmation(confirmation_id)
        if record is None or record.request_id != envelope.request_id:
            raise GatewayError("confirmation does not belong to request_id")
        if record.status is not ConfirmationStatus.CONFIRMED:
            raise GatewayError("confirmation has not been approved")
        if (
            record.task_id != envelope.task_id
            or record.contract_ref.contract_id != active.ref.contract_id
            or record.contract_ref.version != active.ref.version
            or record.contract_ref.digest != active.ref.digest
            or record.policy_version != active.contract.policy_version
        ):
            raise GatewayError("confirmation binding is stale")
        return record

    async def resolve_confirmation(
        self,
        confirmation_id: str,
        *,
        confirmed: bool,
        resolved_by: str,
    ) -> GatewayEvaluationResult:
        previous = await self.confirmations.get_confirmation(confirmation_id)
        try:
            record = await self.confirmations.resolve_confirmation(
                confirmation_id,
                confirmed=confirmed,
                resolved_by=resolved_by,
            )
        except KeyError as exc:
            raise GatewayError("Unknown confirmation_id") from exc
        except ConfirmationConflictError as exc:
            raise GatewayError(str(exc)) from exc

        snapshot = await asyncio.to_thread(self._snapshot, record.request_id)
        if snapshot is None:
            raise GatewayError("Confirmation request no longer has an evaluation snapshot")
        if (
            previous is not None
            and previous.status is record.status
            and snapshot.result.decision.decision is not GatewayDecisionType.REQUIRE_CONFIRMATION
        ):
            state = await self._store.get("requests", record.request_id)
            if not confirmed or (state is not None and state["execution_state"] == "EXECUTED"):
                return snapshot.result
            return await self._evaluate_current(
                snapshot.envelope,
                confirmation_satisfied=True,
                confirmation_id=confirmation_id,
                record_events=False,
            )
        await self._append_event(
            snapshot.envelope,
            event_type=("CONFIRMED" if confirmed else "REJECTED"),
            state=record.status.value,
            actor=resolved_by,
            source_ref=f"confirmation:{confirmation_id}",
            decision=None,
            reason_code=None if confirmed else GatewayReasonCode.USER_DENY,
            object_digest=None,
            result_digest=None,
            evidence_refs=[snapshot.envelope.contract_ref.digest],
        )
        if not confirmed:
            effective = snapshot.result.effective_permission.model_copy(
                update={"requires_confirmation": False}
            )
            return await self._record_result(
                snapshot.envelope,
                effective,
                GatewayDecisionType.DENY,
                GatewayReasonCode.USER_DENY,
                confirmation_id=confirmation_id,
                permission_evaluated=False,
            )
        return await self._evaluate_current(
            snapshot.envelope,
            confirmation_satisfied=True,
            confirmation_id=confirmation_id,
        )

    async def resume_after_confirmation(
        self,
        request_id: str,
        *,
        confirmed: bool,
    ) -> GatewayEvaluationResult:
        """Backward-compatible PR1 method; delegates to ConfirmationService."""

        snapshot = await asyncio.to_thread(self._snapshot, request_id)
        if snapshot is None:
            raise GatewayError("Unknown request_id")
        confirmation_id = snapshot.confirmation_id or snapshot.result.decision.confirmation_id
        if confirmation_id is None:
            raise GatewayError("Request is not waiting for confirmation")
        return await self.resolve_confirmation(
            confirmation_id,
            confirmed=confirmed,
            resolved_by="user",
        )

    async def execute(self, request_id: str) -> GatewayExecutionResult:
        if self.admission_guard is not None:
            async with self.admission_guard():
                return await self._execute_admitted(request_id)
        return await self._execute_admitted(request_id)

    async def _execute_admitted(self, request_id: str) -> GatewayExecutionResult:
        # Claim atomically before evaluation: one persistent row owns all execution attempts.
        claim_id = new_id("claim")

        def claim(
            transaction: CoreStateTransaction,
        ) -> _EvaluationSnapshot | GatewayExecutionResult:
            state = transaction.get("requests", request_id)
            if state is None or state["evaluation"] is None:
                raise GatewayError("Tool call must be evaluated before execute")
            if state["blocked"]:
                raise GatewayError("Tool call was superseded by replan")
            if state["execution_state"] == "EXECUTED":
                return GatewayExecutionResult.model_validate(state["execution_result"])
            if state["execution_state"] != "READY":
                raise GatewayError(
                    "UNKNOWN: execution is in progress or its outcome is unknown; "
                    "automatic retry is disabled"
                )
            snapshot = _EvaluationSnapshot(
                ToolCallEnvelope.model_validate(state["envelope"]),
                GatewayEvaluationResult.model_validate(state["evaluation"]),
                state["confirmation_id"],
            )
            if snapshot.result.decision.decision is not GatewayDecisionType.ALLOW:
                raise GatewayError(
                    f"Tool call is not executable: {snapshot.result.decision.decision.value}"
                )
            state["execution_state"] = "CLAIMED"
            state["claim_id"] = claim_id
            transaction.put("requests", request_id, state)
            return snapshot

        try:
            claimed = await self._store.run(claim)
        except asyncio.CancelledError:
            # The worker is drained before cancellation propagates. Release only
            # our own pre-admission claim; another executor's row is never reset.
            def release_claim(transaction: CoreStateTransaction) -> None:
                current = transaction.get("requests", request_id)
                if (
                    current is not None
                    and current.get("claim_id") == claim_id
                    and current["execution_state"] == "CLAIMED"
                ):
                    current["execution_state"] = "READY"
                    transaction.put("requests", request_id, current)

            await self._store.run(release_claim)
            raise
        if isinstance(claimed, GatewayExecutionResult):
            return claimed
        snapshot = claimed

        started = False
        tool_digest = None
        try:
            if self.request_validator is not None:
                self.request_validator(snapshot.envelope)
            rechecked = await self._evaluate_current(
                snapshot.envelope,
                confirmation_satisfied=snapshot.confirmation_id is not None,
                confirmation_id=snapshot.confirmation_id,
            )
            if rechecked.decision.decision is not GatewayDecisionType.ALLOW:
                raise GatewayError(
                    f"Pre-execution recheck failed: {rechecked.decision.reason_code.value}"
                )
            tool_digest = await self._record_object(
                snapshot.envelope,
                object_type="ToolCallEnvelope",
                payload=snapshot.envelope.model_dump(mode="json"),
            )
            await self._append_event(
                snapshot.envelope,
                event_type="EXECUTION_STARTED",
                state="EXECUTION_STARTED",
                actor="tool-gateway",
                source_ref=f"gateway:{request_id}",
                decision=GatewayDecisionType.ALLOW,
                reason_code=GatewayReasonCode.ALLOWED,
                object_digest=tool_digest,
                result_digest=None,
                evidence_refs=self._event_refs(snapshot.envelope, tool_digest),
            )
            final_check = await self._evaluate_current(
                snapshot.envelope,
                confirmation_satisfied=snapshot.confirmation_id is not None,
                confirmation_id=snapshot.confirmation_id,
                record_events=False,
            )
            if final_check.decision.decision is not GatewayDecisionType.ALLOW:
                raise GatewayError(
                    f"Pre-execution recheck failed: {final_check.decision.reason_code.value}"
                )
            checked_state = await self._store.get("requests", request_id)
            if checked_state is None:
                raise GatewayError("Request state is unavailable")
            revision = checked_state["evaluation_revision"]
            contract_record = await self.contracts.get_contract_version(
                snapshot.envelope.contract_ref.contract_id, snapshot.envelope.contract_ref.version
            )
            if contract_record is None:
                raise GatewayError("Contract is unavailable")
            task_limits = (
                await self.task_limit_provider(snapshot.envelope.model_copy(deep=True))
                if self.task_limit_provider is not None
                else None
            )

            # Permission replacement/replan during evidence I/O invalidates this claim.
            # The admission guard remains held while this worker transaction completes.
            def admit_execution(transaction: CoreStateTransaction) -> None:
                current = transaction.get("requests", request_id)
                if current["revision"] != revision or current["blocked"]:
                    raise GatewayError("Pre-execution authorization changed; evaluate again")
                try:
                    reserve_quotas(
                        transaction,
                        snapshot.envelope,
                        contract_record.contract,
                        final_check.effective_permission,
                        task_limits=task_limits,
                    )
                except QuotaExceeded as exc:
                    raise GatewayError(str(exc)) from exc
                # Admission and quota consumption commit together. Cancellation
                # after this point is conservatively unknown, never auto-replayed.
                current["execution_state"] = "ADMITTED"
                transaction.put("requests", request_id, current)

            await self._store.run(admit_execution)
            started = True
            result = await self.executor.execute(
                snapshot.envelope.model_copy(deep=True),
                gateway_token=self._adapter_token,
            )
            try:
                check_output_limit(result, final_check.effective_permission)
            except QuotaExceeded as exc:
                raise GatewayError(str(exc)) from exc
            result_digest = await self._record_object(
                snapshot.envelope,
                object_type="ToolResult",
                payload={
                    "task_id": snapshot.envelope.task_id,
                    "request_id": request_id,
                    "result": result,
                },
            )
            await self._record_intent_effect(
                snapshot.envelope,
                status="APPLIED",
                after_digest=result_digest,
                side_effect_ref=(
                    result.get("side_effect_ref")
                    if isinstance(result, dict)
                    else None
                ),
            )
            await self._append_event(
                snapshot.envelope,
                event_type="EXECUTION_FINISHED",
                state="EXECUTED",
                actor="tool-gateway",
                source_ref=f"gateway:{request_id}",
                decision=GatewayDecisionType.ALLOW,
                reason_code=GatewayReasonCode.ALLOWED,
                object_digest=tool_digest,
                result_digest=result_digest,
                evidence_refs=self._event_refs(snapshot.envelope, tool_digest, result_digest),
            )
            executed = GatewayExecutionResult(
                request_id=request_id, status="EXECUTED", result=result
            )

            def save_execution(transaction: CoreStateTransaction) -> None:
                current = transaction.get("requests", request_id)
                current["execution_state"] = "EXECUTED"
                current["execution_result"] = executed.model_dump(mode="json")
                transaction.put("requests", request_id, current)

            await self._store.run(save_execution)
            return executed.model_copy(deep=True)
        except BaseException:
            # Cancellation, adapter error, and post-effect audit/storage error can all
            # leave an external effect behind. Never automatically reclaim such work.
            def preserve_outcome(transaction: CoreStateTransaction) -> bool:
                current = transaction.get("requests", request_id)
                if current["execution_state"] == "EXECUTED":
                    return False
                unknown = started or current["execution_state"] == "ADMITTED"
                current["execution_state"] = "UNKNOWN" if unknown else "READY"
                transaction.put("requests", request_id, current)
                return unknown

            unknown = await self._store.run(preserve_outcome)
            if unknown:
                await self._record_intent_effect(
                    snapshot.envelope,
                    status="UNKNOWN",
                    after_digest=None,
                    side_effect_ref=None,
                )
                await self._append_event(
                    snapshot.envelope,
                    event_type="EXECUTION_FAILED",
                    state="UNKNOWN",
                    actor="tool-gateway",
                    source_ref=f"gateway:{request_id}",
                    decision=GatewayDecisionType.DENY,
                    reason_code=GatewayReasonCode.CHECK_UNAVAILABLE,
                    object_digest=tool_digest,
                    result_digest=None,
                    evidence_refs=self._event_refs(snapshot.envelope, tool_digest),
                )
            raise

    async def _record_intent_effect(
        self,
        envelope: ToolCallEnvelope,
        *,
        status: str,
        after_digest: str | None,
        side_effect_ref: str | None,
    ) -> None:
        """Best-effort effect evidence hook for the Intent branch.

        Effect recording must never turn an already completed external action into
        an UNKNOWN gateway result.  The Core event/audit path remains authoritative
        if the optional branch recorder is unavailable.
        """
        if self.intent_effect_recorder is None:
            return
        try:
            state = await self._store.get("requests", envelope.request_id)
            if state is None or state.get("intent_decision") is None:
                return
            await self.intent_effect_recorder(
                request_id=envelope.request_id,
                tool=envelope.tool,
                normalized_target=envelope.resource,
                before_digest=None,
                after_digest=after_digest,
                side_effect_ref=side_effect_ref,
                status=status,
            )
        except Exception:
            return

    async def replan(
        self,
        request_id: str,
        changes: TaskContractUpdateRequest,
    ) -> ContractRecord:
        return await complete_before_cancelling(self._replan(request_id, changes))

    async def _replan(self, request_id: str, changes: TaskContractUpdateRequest) -> ContractRecord:
        snapshot = await asyncio.to_thread(self._snapshot, request_id)
        if snapshot is None:
            raise GatewayError("Unknown request_id")
        record = await self.contracts.update_contract(
            snapshot.envelope.contract_ref.contract_id,
            changes,
        )

        def block_request(transaction: CoreStateTransaction) -> None:
            state = transaction.get("requests", request_id)
            state["blocked"] = True
            state["revision"] += 1
            transaction.put("requests", request_id, state)

        await self._store.run(block_request)
        digest = await self._record_object(
            snapshot.envelope,
            object_type="TaskContractV2",
            payload=record.contract.model_dump(mode="json"),
        )
        await self._append_event(
            snapshot.envelope,
            event_type="REPLAN_CREATED",
            state="DRAFT",
            actor="tool-gateway",
            source_ref=f"contract:{record.ref.contract_id}:v{record.ref.version}",
            decision=GatewayDecisionType.REQUIRE_REPLAN,
            reason_code=GatewayReasonCode.VERSION_STALE,
            object_digest=digest,
            result_digest=None,
            evidence_refs=[record.ref.digest, *([digest] if digest else [])],
        )
        return record

    async def record_contract_event(
        self,
        record: ContractRecord,
        *,
        event_type: str,
        actor: str,
        state: str | None = None,
    ) -> None:
        """Record TaskContractV2 lifecycle without creating a second event model."""

        envelope = ToolCallEnvelope(
            request_id=f"contract-{record.contract.contract_id}-v{record.contract.version}",
            task_id=record.contract.task_id,
            session_id=record.contract.session_id,
            contract_ref=record.ref,
            skill_ref="core-contract-service",
            tool="contract_service",
            action=event_type,
            canonical_args={},
            resource=record.contract.contract_id,
            effect_class=EffectClass.OTHER,
        )
        digest = await self._record_object(
            envelope,
            object_type="TaskContractV2",
            payload=record.contract.model_dump(mode="json"),
        )
        await self._append_event(
            envelope,
            event_type=event_type,
            state=state or record.ref.status.value,
            actor=actor,
            source_ref=f"contract:{record.ref.contract_id}:v{record.ref.version}",
            decision=None,
            reason_code=None,
            object_digest=digest,
            result_digest=None,
            evidence_refs=[record.ref.digest, *([digest] if digest else [])],
        )

    def effective_permission_for(self, request_id: str) -> EffectivePermission | None:
        snapshot = self._snapshot(request_id)
        return snapshot.result.effective_permission if snapshot is not None else None

    async def _record_result(
        self,
        envelope: ToolCallEnvelope,
        effective: EffectivePermission,
        decision_type: GatewayDecisionType,
        reason_code: GatewayReasonCode,
        *,
        confirmation_id: str | None,
        permission_evaluated: bool = True,
        permission_revision: int | None = None,
        record_events: bool = True,
        authority_versions: dict[str, int | str] | None = None,
        extra_evidence_refs: list[str] | None = None,
        version_overrides: dict[str, int | str] | None = None,
    ) -> GatewayEvaluationResult:
        contract = await self.contracts.get_contract_version(
            envelope.contract_ref.contract_id,
            envelope.contract_ref.version,
        )
        versions: dict[str, int | str] = {"contract": envelope.contract_ref.version}
        if contract is not None:
            versions.update(
                {
                    "policy": contract.contract.policy_version,
                    "tool_manifest": contract.contract.tool_manifest_digest,
                }
            )
        if authority_versions:
            versions["contract_policy"] = versions.get("policy", "unavailable")
            versions.update(authority_versions)
        if version_overrides:
            versions.update(version_overrides)
        evidence_refs = self._dedupe_refs(
            envelope.contract_ref.digest,
            *(extra_evidence_refs or []),
        )
        decision = GatewayDecision(
            decision=decision_type,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
            versions=versions,
            confirmation_id=confirmation_id,
        )
        result = GatewayEvaluationResult(
            decision=decision,
            effective_permission=effective,
        )

        if record_events:
            tool_digest = await self._record_object(
                envelope,
                object_type="ToolCallEnvelope",
                payload=envelope.model_dump(mode="json"),
            )
            decision_digest = await self._record_object(
                envelope,
                object_type="GatewayDecision",
                payload={
                    "task_id": envelope.task_id,
                    "request_id": envelope.request_id,
                    **decision.model_dump(mode="json"),
                },
            )
            refs = self._dedupe_refs(
                *self._event_refs(envelope, tool_digest, decision_digest),
                *(extra_evidence_refs or []),
            )
            if permission_evaluated:
                await self._append_event(
                    envelope,
                    event_type="PERMISSION_EVALUATED",
                    state=(
                        "PERMISSION_DENIED" if effective.conflict_code else "PERMISSION_ALLOWED"
                    ),
                    actor="permission-resolver",
                    source_ref=f"gateway:{envelope.request_id}",
                    decision=decision_type,
                    reason_code=reason_code,
                    object_digest=tool_digest,
                    result_digest=decision_digest,
                    evidence_refs=refs,
                )
            event_type = {
                GatewayDecisionType.ALLOW: "GATEWAY_ALLOWED",
                GatewayDecisionType.DENY: "GATEWAY_DENIED",
                GatewayDecisionType.REQUIRE_CONFIRMATION: "WAITING_CONFIRMATION",
                GatewayDecisionType.REQUIRE_REPLAN: "REPLAN_REQUIRED",
            }[decision_type]
            await self._append_event(
                envelope,
                event_type=event_type,
                state=event_type,
                actor="tool-gateway",
                source_ref=f"gateway:{envelope.request_id}",
                decision=decision_type,
                reason_code=reason_code,
                object_digest=tool_digest,
                result_digest=decision_digest,
                evidence_refs=refs,
            )

        def save_evaluation(transaction: CoreStateTransaction) -> None:
            state = transaction.get("requests", envelope.request_id)
            if state["fingerprint"] != self._fingerprint(envelope):
                raise GatewayError("request_id is bound to a different envelope")
            state["evaluation"] = result.model_dump(mode="json")
            state["evaluation_revision"] = permission_revision
            state["confirmation_id"] = confirmation_id
            transaction.put("requests", envelope.request_id, state)

        await self._store.run(save_evaluation)
        return result

    async def _record_object(
        self,
        envelope: ToolCallEnvelope,
        *,
        object_type: str,
        payload: dict[str, Any],
    ) -> str | None:
        if self.evidence_recorder is None:
            # A contract SHA-256 is not a P3 signed-object digest. Fake mode must
            # represent unavailable object binding as null, not mislabel it.
            return None
        return await self.evidence_recorder.record_object(
            task_id=envelope.task_id,
            object_type=object_type,
            payload=payload,
        )

    @staticmethod
    def _dedupe_refs(*refs: str | None) -> list[str]:
        return list(
            dict.fromkeys(
                ref for ref in refs if ref is not None
            )
        )

    @staticmethod
    def _event_refs(envelope: ToolCallEnvelope, *digests: str | None) -> list[str]:
        return ToolGateway._dedupe_refs(
            envelope.contract_ref.digest,
            *digests,
        )

    async def _append_event(
        self,
        envelope: ToolCallEnvelope,
        *,
        event_type: str,
        state: str,
        actor: str,
        source_ref: str,
        decision: GatewayDecisionType | None,
        reason_code: GatewayReasonCode | None,
        object_digest: str | None,
        result_digest: str | None,
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        get_last_event_id = getattr(self.event_store, "get_last_event_id", None)
        if get_last_event_id is not None:
            parent_event_id = await get_last_event_id(envelope.task_id)
        else:
            existing = await self.event_store.list_task_events(envelope.task_id)
            parent_event_id = existing[-1]["event_id"] if existing else None
        return await self.event_store.append_event(
            {
                "event_id": new_id("event"),
                "task_id": envelope.task_id,
                "parent_event_id": parent_event_id,
                "type": event_type,
                "actor": actor,
                "source_ref": source_ref,
                "object_digest": object_digest,
                "state": state,
                "decision": decision.value if decision is not None else None,
                "result_digest": result_digest,
                "occurred_at": datetime.now(UTC).isoformat(),
                "request_id": envelope.request_id,
                "reason_code": reason_code.value if reason_code is not None else None,
                "evidence_refs": evidence_refs,
            }
        )
