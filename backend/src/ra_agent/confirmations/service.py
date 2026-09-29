"""Atomic confirmation lifecycle with optional durable Core state."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ra_agent.gateway.state import CoreStateStore

from ra_agent.contracts.core_v1 import (
    ConfirmationRecord,
    ConfirmationStatus,
    ContractVersionRef,
)
from ra_agent.core.ids import new_id
from ra_agent.core.offload import offload


class ConfirmationConflictError(RuntimeError):
    """Raised when an already resolved confirmation is resolved inconsistently."""


class ConfirmationService:
    def __init__(self, *, store: CoreStateStore | None = None) -> None:
        from ra_agent.gateway.state import CoreStateStore

        self._store = store if store is not None else CoreStateStore()

    @offload
    def request_confirmation(
        self,
        *,
        request_id: str,
        task_id: str,
        contract_ref: ContractVersionRef,
        policy_version: str,
    ) -> ConfirmationRecord:
        """Create or return the pending confirmation bound to one request.

        Re-evaluating the same request with the same binding does not create duplicate
        prompts. If the binding changed, the old confirmation must not be reused.
        """

        with self._store.transaction() as transaction:
            existing_id = transaction.get("confirmation_requests", request_id)
            if existing_id is not None:
                existing = ConfirmationRecord.model_validate(
                    transaction.get("confirmations", existing_id)
                )
                if (
                    existing.task_id == task_id
                    and existing.contract_ref == contract_ref
                    and existing.policy_version == policy_version
                    and existing.status is ConfirmationStatus.WAITING_CONFIRMATION
                ):
                    return deepcopy(existing)
                if existing.status is ConfirmationStatus.WAITING_CONFIRMATION:
                    raise ConfirmationConflictError(
                        "pending confirmation is bound to a different contract or policy version"
                    )

            record = ConfirmationRecord(
                confirmation_id=new_id("confirmation"),
                request_id=request_id,
                task_id=task_id,
                contract_ref=contract_ref,
                policy_version=policy_version,
                status=ConfirmationStatus.WAITING_CONFIRMATION,
                requested_at=datetime.now(UTC),
            )
            transaction.put("confirmations", record.confirmation_id, record.model_dump(mode="json"))
            transaction.put("confirmation_requests", request_id, record.confirmation_id)
            return deepcopy(record)

    @offload
    def resolve_confirmation(
        self,
        confirmation_id: str,
        *,
        confirmed: bool,
        resolved_by: str,
    ) -> ConfirmationRecord:
        with self._store.transaction() as transaction:
            payload = transaction.get("confirmations", confirmation_id)
            record = ConfirmationRecord.model_validate(payload) if payload is not None else None
            if record is None:
                raise KeyError(confirmation_id)
            target = ConfirmationStatus.CONFIRMED if confirmed else ConfirmationStatus.REJECTED
            if record.status is not ConfirmationStatus.WAITING_CONFIRMATION:
                if record.status is target:
                    return deepcopy(record)
                raise ConfirmationConflictError(
                    f"confirmation already resolved as {record.status.value}"
                )
            updated = record.model_copy(
                update={
                    "status": target,
                    "resolved_at": datetime.now(UTC),
                    "resolved_by": resolved_by,
                }
            )
            transaction.put("confirmations", confirmation_id, updated.model_dump(mode="json"))
            return deepcopy(updated)

    @offload
    def get_confirmation(self, confirmation_id: str) -> ConfirmationRecord | None:
        with self._store.read_transaction() as transaction:
            payload = transaction.get("confirmations", confirmation_id)
            record = ConfirmationRecord.model_validate(payload) if payload is not None else None
            return deepcopy(record) if record is not None else None

    @offload
    def get_for_request(self, request_id: str) -> ConfirmationRecord | None:
        with self._store.read_transaction() as transaction:
            confirmation_id = transaction.get("confirmation_requests", request_id)
            if confirmation_id is None:
                return None
            return ConfirmationRecord.model_validate(
                transaction.get("confirmations", confirmation_id)
            )
