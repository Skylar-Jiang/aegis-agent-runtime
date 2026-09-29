"""Dependency-aware recovery-plan derivation from durable effect facts."""

from __future__ import annotations

from collections import defaultdict
from typing import Protocol

from ra_agent.contracts import DependencyRecoveryPlan, EffectRecord, EffectStatus
from ra_agent.core.ids import new_id


class _EffectStore(Protocol):
    async def list_by_task_id(self, task_id: str) -> tuple[EffectRecord, ...]: ...


class DependencyRecoveryPlanner:
    """Compute a descendant closure; execution remains selective and explicit."""

    def __init__(self, effect_store: _EffectStore) -> None:
        self._effect_store = effect_store

    async def plan(
        self,
        *,
        task_id: str,
        failed_effect_ids: list[str],
        trigger: str,
    ) -> DependencyRecoveryPlan:
        effects = await self._effect_store.list_by_task_id(task_id)
        by_id = {effect.effect_id: effect for effect in effects}
        if not failed_effect_ids or not set(failed_effect_ids).issubset(by_id):
            raise ValueError("failed effects must belong to recovery task")

        children: dict[str, set[str]] = defaultdict(set)
        for effect in effects:
            for parent_effect_id in effect.parent_effect_ids:
                children[parent_effect_id].add(effect.effect_id)
        affected = self._descendant_closure(children, set(failed_effect_ids))
        rollback = {
            effect_id
            for effect_id in affected
            if by_id[effect_id].status in {EffectStatus.PENDING, EffectStatus.COMMITTED}
        }
        preserved = {
            effect.effect_id
            for effect in effects
            if effect.effect_id not in affected and effect.status is EffectStatus.COMMITTED
        }
        return DependencyRecoveryPlan(
            rollback_plan_id=new_id("rollback-plan"),
            task_id=task_id,
            trigger=trigger,
            failed_effect_ids=sorted(failed_effect_ids),
            affected_effect_ids=sorted(affected),
            rollback_effect_ids=sorted(rollback),
            preserve_effect_ids=sorted(preserved),
            reason="dependency-aware rollback closure derived from persisted effect lineage",
        )

    @staticmethod
    def _descendant_closure(
        children: dict[str, set[str]],
        roots: set[str],
    ) -> set[str]:
        closure = set(roots)
        pending = list(roots)
        while pending:
            parent = pending.pop()
            for child in children[parent]:
                if child not in closure:
                    closure.add(child)
                    pending.append(child)
        return closure
