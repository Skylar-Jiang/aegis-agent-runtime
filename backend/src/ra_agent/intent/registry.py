"""Versioned storage for Aegis-Intent contracts on the existing CoreStateStore."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ra_agent.core.ids import new_id
from ra_agent.core.offload import offload

if TYPE_CHECKING:
    from ra_agent.gateway.state import CoreStateStore

from .models import IntentContractStatus, IntentSpec, TaskContract


class IntentRegistry:
    def __init__(self, *, store: CoreStateStore | None = None) -> None:
        from ra_agent.gateway.state import CoreStateStore

        self._store = store if store is not None else CoreStateStore()

    @staticmethod
    def _contract_digest_payload(
        *,
        contract_id: str,
        contract_version: int,
        intent_ref: str,
        resources: list[str],
        tools: list[str],
        permissions: dict[str, Any],
        completion_conditions: list[str],
        parent_version: int | None,
        status: IntentContractStatus,
    ) -> dict[str, Any]:
        return {
            "contract_id": contract_id,
            "contract_version": contract_version,
            "intent_ref": intent_ref,
            "resources": resources,
            "tools": tools,
            "permissions": permissions,
            "completion_conditions": completion_conditions,
            "parent_version": parent_version,
            "status": status.value,
        }

    @classmethod
    def _make_contract(
        cls,
        *,
        spec: IntentSpec,
        contract_id: str,
        contract_version: int,
        resources: list[str],
        tools: list[str],
        permissions: dict[str, Any],
        parent_version: int | None,
        status: IntentContractStatus,
    ) -> TaskContract:
        payload = cls._contract_digest_payload(
            contract_id=contract_id,
            contract_version=contract_version,
            intent_ref=f"{spec.intent_id}:v{spec.version}",
            resources=resources,
            tools=tools,
            permissions=permissions,
            completion_conditions=spec.success_criteria,
            parent_version=parent_version,
            status=status,
        )
        return TaskContract(**payload, digest=TaskContract.compute_digest(payload))

    @offload
    def create(
        self,
        spec: IntentSpec,
        *,
        resources: list[str] | None = None,
        tools: list[str] | None = None,
        permissions: dict[str, Any] | None = None,
    ) -> tuple[IntentSpec, TaskContract]:
        with self._store.transaction() as tx:
            if tx.get("intent_specs", spec.intent_id) is not None:
                raise ValueError(f"intent_id already exists: {spec.intent_id}")
            current_id = tx.get("task_intent_latest", spec.task_id)
            if current_id is not None:
                raise ValueError(f"task already has an active intent: {spec.task_id}")
            contract_id = new_id("intent-contract")
            contract = self._make_contract(
                spec=spec,
                contract_id=contract_id,
                contract_version=1,
                resources=list(resources if resources is not None else spec.scope),
                tools=list(tools or []),
                permissions=deepcopy(
                    permissions
                    or {
                        "allowed_actions": spec.allowed_actions,
                        "forbidden_actions": spec.forbidden_actions,
                    }
                ),
                parent_version=None,
                status=IntentContractStatus.DRAFT,
            )
            tx.put("intent_specs", spec.intent_id, [spec.model_dump(mode="json")])
            tx.put("intent_contracts", contract_id, [contract.model_dump(mode="json")])
            tx.put("task_intent_latest", spec.task_id, spec.intent_id)
            tx.put("task_intent_contract", spec.task_id, contract_id)
            return deepcopy(spec), deepcopy(contract)

    @offload
    def get_for_task(self, task_id: str) -> tuple[IntentSpec, TaskContract] | None:
        with self._store.read_transaction() as tx:
            intent_id = tx.get("task_intent_latest", task_id)
            contract_id = tx.get("task_intent_contract", task_id)
            if intent_id is None or contract_id is None:
                return None
            specs = tx.get("intent_specs", intent_id) or []
            contracts = tx.get("intent_contracts", contract_id) or []
            if not specs or not contracts:
                return None
            return (
                IntentSpec.model_validate(specs[-1]),
                TaskContract.model_validate(contracts[-1]),
            )

    @offload
    def get_intent(self, intent_id: str) -> IntentSpec | None:
        with self._store.read_transaction() as tx:
            specs = tx.get("intent_specs", intent_id) or []
            return IntentSpec.model_validate(specs[-1]) if specs else None

    @offload
    def confirm(self, intent_id: str, *, confirmed_by: str) -> tuple[IntentSpec, TaskContract]:
        with self._store.transaction() as tx:
            specs_payload = tx.get("intent_specs", intent_id) or []
            if not specs_payload:
                raise KeyError(intent_id)
            current = IntentSpec.model_validate(specs_payload[-1])
            now = datetime.now(UTC)
            confirmed = current.model_copy(
                update={
                    "confirmed_by": confirmed_by,
                    "confirmed_at": now,
                }
            )
            specs_payload[-1] = confirmed.model_dump(mode="json")
            tx.put("intent_specs", intent_id, specs_payload)

            contract_id = tx.get("task_intent_contract", current.task_id)
            contracts_payload = tx.get("intent_contracts", contract_id) or []
            if not contracts_payload:
                raise KeyError(f"contract for {intent_id}")
            latest = TaskContract.model_validate(contracts_payload[-1])
            payload = self._contract_digest_payload(
                contract_id=latest.contract_id,
                contract_version=latest.contract_version,
                intent_ref=latest.intent_ref,
                resources=latest.resources,
                tools=latest.tools,
                permissions=latest.permissions,
                completion_conditions=latest.completion_conditions,
                parent_version=latest.parent_version,
                status=IntentContractStatus.CONFIRMED,
            )
            confirmed_contract = TaskContract(
                **payload,
                digest=TaskContract.compute_digest(payload),
            )
            contracts_payload[-1] = confirmed_contract.model_dump(mode="json")
            tx.put("intent_contracts", latest.contract_id, contracts_payload)
            return deepcopy(confirmed), deepcopy(confirmed_contract)

    @staticmethod
    def _set_expands(old: list[str], new: list[str]) -> bool:
        old_set = {item.casefold() for item in old}
        return not {item.casefold() for item in new}.issubset(old_set)

    @offload
    def update(
        self,
        intent_id: str,
        *,
        goal: str | None = None,
        scope: list[str] | None = None,
        allowed_actions: list[str] | None = None,
        forbidden_actions: list[str] | None = None,
        success_criteria: list[str] | None = None,
        source_refs: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> tuple[IntentSpec, TaskContract, bool]:
        """Create a new DRAFT version and report whether trusted confirmation is required."""

        with self._store.transaction() as tx:
            specs_payload = tx.get("intent_specs", intent_id) or []
            if not specs_payload:
                raise KeyError(intent_id)
            previous = IntentSpec.model_validate(specs_payload[-1])
            new_scope = list(scope if scope is not None else previous.scope)
            new_allowed = list(
                allowed_actions if allowed_actions is not None else previous.allowed_actions
            )
            new_forbidden = list(
                forbidden_actions if forbidden_actions is not None else previous.forbidden_actions
            )
            expansion = self._set_expands(previous.scope, new_scope) or self._set_expands(
                previous.allowed_actions, new_allowed
            )
            updated = IntentSpec(
                intent_id=previous.intent_id,
                task_id=previous.task_id,
                goal=(goal.strip() if goal is not None else previous.goal),
                scope=new_scope,
                allowed_actions=new_allowed,
                forbidden_actions=new_forbidden,
                success_criteria=(
                    list(success_criteria)
                    if success_criteria is not None
                    else previous.success_criteria
                ),
                source_refs=list(source_refs if source_refs is not None else previous.source_refs),
                version=previous.version + 1,
                confirmed_by=None,
                confirmed_at=None,
            )
            specs_payload.append(updated.model_dump(mode="json"))
            tx.put("intent_specs", intent_id, specs_payload)

            contract_id = tx.get("task_intent_contract", previous.task_id)
            contracts_payload = tx.get("intent_contracts", contract_id) or []
            previous_contract = TaskContract.model_validate(contracts_payload[-1])
            if previous_contract.status is IntentContractStatus.CONFIRMED:
                superseded_payload = self._contract_digest_payload(
                    contract_id=previous_contract.contract_id,
                    contract_version=previous_contract.contract_version,
                    intent_ref=previous_contract.intent_ref,
                    resources=previous_contract.resources,
                    tools=previous_contract.tools,
                    permissions=previous_contract.permissions,
                    completion_conditions=previous_contract.completion_conditions,
                    parent_version=previous_contract.parent_version,
                    status=IntentContractStatus.SUPERSEDED,
                )
                contracts_payload[-1] = TaskContract(
                    **superseded_payload,
                    digest=TaskContract.compute_digest(superseded_payload),
                ).model_dump(mode="json")
            new_contract = self._make_contract(
                spec=updated,
                contract_id=previous_contract.contract_id,
                contract_version=previous_contract.contract_version + 1,
                resources=new_scope,
                tools=list(tools if tools is not None else previous_contract.tools),
                permissions={
                    "allowed_actions": new_allowed,
                    "forbidden_actions": new_forbidden,
                },
                parent_version=previous_contract.contract_version,
                status=IntentContractStatus.DRAFT,
            )
            contracts_payload.append(new_contract.model_dump(mode="json"))
            tx.put("intent_contracts", previous_contract.contract_id, contracts_payload)
            return deepcopy(updated), deepcopy(new_contract), expansion
