"""Bounded correction and effect-check state for the Aegis-Intent runtime."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from ra_agent.core.ids import new_id
from ra_agent.core.offload import offload

from .models import CorrectionPlan, CorrectionStatus, EffectCheck, EffectStatus

if TYPE_CHECKING:
    from ra_agent.gateway.state import CoreStateStore


class CorrectionManager:
    def __init__(self, *, store: CoreStateStore | None = None) -> None:
        from ra_agent.gateway.state import CoreStateStore

        self._store = store if store is not None else CoreStateStore()

    @offload
    def create_plan(
        self,
        *,
        task_id: str,
        base_contract_version: int,
        contaminated_refs: list[str],
        proposed_actions: list[str],
        added_scope: list[str],
        retry_budget: int = 1,
    ) -> CorrectionPlan:
        plan = CorrectionPlan(
            plan_id=new_id("correction"),
            task_id=task_id,
            base_contract_version=base_contract_version,
            contaminated_refs=contaminated_refs,
            proposed_actions=proposed_actions,
            added_scope=added_scope,
            confirmation_required=bool(added_scope),
            retry_budget=retry_budget,
            status=(
                CorrectionStatus.WAITING_CONFIRMATION
                if added_scope
                else CorrectionStatus.DRAFT
            ),
        )
        with self._store.transaction() as tx:
            tx.put("correction_plans", plan.plan_id, plan.model_dump(mode="json"))
            tx.put("task_correction_latest", task_id, plan.plan_id)
        return deepcopy(plan)

    @offload
    def get_plan(self, plan_id: str) -> CorrectionPlan | None:
        with self._store.read_transaction() as tx:
            payload = tx.get("correction_plans", plan_id)
            return CorrectionPlan.model_validate(payload) if payload is not None else None

    @offload
    def get_effect(self, request_id: str) -> EffectCheck | None:
        with self._store.read_transaction() as tx:
            effect_id = tx.get("request_effect_latest", request_id)
            if effect_id is None:
                return None
            payload = tx.get("intent_effect_checks", effect_id)
            return EffectCheck.model_validate(payload) if payload is not None else None

    @offload
    def set_status(self, plan_id: str, status: CorrectionStatus) -> CorrectionPlan:
        with self._store.transaction() as tx:
            payload = tx.get("correction_plans", plan_id)
            if payload is None:
                raise KeyError(plan_id)
            current = CorrectionPlan.model_validate(payload)
            allowed: dict[CorrectionStatus, set[CorrectionStatus]] = {
                CorrectionStatus.DRAFT: {
                    CorrectionStatus.WAITING_CONFIRMATION,
                    CorrectionStatus.APPROVED,
                    CorrectionStatus.REJECTED,
                    CorrectionStatus.EXHAUSTED,
                },
                CorrectionStatus.WAITING_CONFIRMATION: {
                    CorrectionStatus.APPROVED,
                    CorrectionStatus.REJECTED,
                    CorrectionStatus.EXHAUSTED,
                },
                CorrectionStatus.APPROVED: {
                    CorrectionStatus.APPLIED,
                    CorrectionStatus.EXHAUSTED,
                },
                CorrectionStatus.APPLIED: set(),
                CorrectionStatus.REJECTED: set(),
                CorrectionStatus.EXHAUSTED: set(),
            }
            if status is not current.status and status not in allowed[current.status]:
                raise ValueError(
                    f"invalid correction transition: {current.status.value}->{status.value}"
                )
            updated = current.model_copy(update={"status": status})
            tx.put("correction_plans", plan_id, updated.model_dump(mode="json"))
            return deepcopy(updated)

    @offload
    def record_effect(
        self,
        *,
        request_id: str,
        tool: str,
        normalized_target: str,
        before_digest: str | None,
        after_digest: str | None,
        side_effect_ref: str | None,
        status: EffectStatus,
    ) -> EffectCheck:
        effect = EffectCheck(
            effect_id=new_id("effect-check"),
            request_id=request_id,
            tool=tool,
            normalized_target=normalized_target,
            before_digest=before_digest,
            after_digest=after_digest,
            side_effect_ref=side_effect_ref,
            status=status,
        )
        with self._store.transaction() as tx:
            tx.put("intent_effect_checks", effect.effect_id, effect.model_dump(mode="json"))
            tx.put("request_effect_latest", request_id, effect.effect_id)
        return deepcopy(effect)
