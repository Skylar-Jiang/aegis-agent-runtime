"""TaskContractV2 version service with optional durable Core state."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ra_agent.gateway.state import CoreStateStore, CoreStateTransaction

from ra_agent.core.ids import new_id
from ra_agent.core.offload import offload

from .core_v1 import (
    ContractRecord,
    ContractVersionRef,
    ContractVersionStatus,
    TaskContractCreateRequest,
    TaskContractUpdateRequest,
    TaskContractV2,
)


class ContractService:
    def __init__(self, *, store: CoreStateStore | None = None) -> None:
        from ra_agent.gateway.state import CoreStateStore

        self._store = store if store is not None else CoreStateStore()

    @staticmethod
    def _versions(transaction: CoreStateTransaction, contract_id: str) -> list[ContractRecord]:
        return [
            ContractRecord.model_validate(item)
            for item in (transaction.get("contracts", contract_id) or [])
        ]

    @staticmethod
    def _save_versions(
        transaction: CoreStateTransaction, contract_id: str, versions: list[ContractRecord]
    ) -> None:
        transaction.put(
            "contracts", contract_id, [record.model_dump(mode="json") for record in versions]
        )

    @staticmethod
    def digest_contract(contract: TaskContractV2) -> str:
        payload = contract.model_dump(mode="json", exclude_none=False)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @offload
    def create_contract(self, request: TaskContractCreateRequest) -> ContractRecord:
        with self._store.transaction() as transaction:
            contract = TaskContractV2(
                contract_id=new_id("contract"),
                version=1,
                parent_digest=None,
                **request.model_dump(),
            )
            record = self._draft_record(contract)
            self._save_versions(transaction, contract.contract_id, [record])
            transaction.put("task_contracts", contract.task_id, contract.contract_id)
            return deepcopy(record)

    @offload
    def update_contract(
        self, contract_id: str, request: TaskContractUpdateRequest
    ) -> ContractRecord:
        with self._store.transaction() as transaction:
            versions = self._versions(transaction, contract_id)
            if not versions:
                raise KeyError(contract_id)
            previous = versions[-1]
            base = previous.contract.model_dump()
            changes = request.model_dump(exclude_none=True)
            base.update(changes)
            base["version"] = previous.contract.version + 1
            base["parent_digest"] = previous.ref.digest
            contract = TaskContractV2.model_validate(base)
            record = self._draft_record(contract)
            versions.append(record)
            self._save_versions(transaction, contract_id, versions)
            return deepcopy(record)

    @offload
    def confirm_contract(
        self, contract_id: str, *, version: int, confirmed_by: str
    ) -> ContractRecord:
        with self._store.transaction() as transaction:
            versions = self._versions(transaction, contract_id)
            if not versions:
                raise KeyError(contract_id)
            target_index = next(
                (index for index, item in enumerate(versions) if item.contract.version == version),
                None,
            )
            if target_index is None:
                raise KeyError(f"{contract_id}:v{version}")
            if target_index != len(versions) - 1:
                raise ValueError("Only the latest contract version may be confirmed")
            existing = versions[target_index]
            if existing.ref.status is ContractVersionStatus.CONFIRMED:
                # A retry must never rewrite the identity/time bound to approvals.
                if existing.ref.confirmed_by != confirmed_by:
                    raise ValueError("Contract version was already confirmed by another user")
                return deepcopy(existing)
            now = datetime.now(UTC)
            refreshed: list[ContractRecord] = []
            for index, item in enumerate(versions):
                if item.ref.status is ContractVersionStatus.CONFIRMED and index != target_index:
                    item = item.model_copy(
                        update={
                            "ref": item.ref.model_copy(
                                update={"status": ContractVersionStatus.SUPERSEDED}
                            )
                        }
                    )
                if index == target_index:
                    item = item.model_copy(
                        update={
                            "ref": item.ref.model_copy(
                                update={
                                    "status": ContractVersionStatus.CONFIRMED,
                                    "confirmed_by": confirmed_by,
                                    "confirmed_at": now,
                                }
                            )
                        }
                    )
                refreshed.append(item)
            self._save_versions(transaction, contract_id, refreshed)
            return deepcopy(refreshed[target_index])

    @offload
    def get_active_contract(self, contract_id: str) -> ContractRecord | None:
        with self._store.read_transaction() as transaction:
            versions = self._versions(transaction, contract_id)
            for item in reversed(versions):
                if item.ref.status is ContractVersionStatus.CONFIRMED:
                    return deepcopy(item)
            return None

    @offload
    def get_contract_version(self, contract_id: str, version: int) -> ContractRecord | None:
        with self._store.read_transaction() as transaction:
            for item in self._versions(transaction, contract_id):
                if item.contract.version == version:
                    return deepcopy(item)
            return None

    @offload
    def get_latest_contract(self, contract_id: str) -> ContractRecord | None:
        with self._store.read_transaction() as transaction:
            versions = self._versions(transaction, contract_id)
            return deepcopy(versions[-1]) if versions else None

    @offload
    def list_versions(self, contract_id: str) -> list[ContractRecord]:
        with self._store.read_transaction() as transaction:
            return deepcopy(self._versions(transaction, contract_id))

    def _draft_record(self, contract: TaskContractV2) -> ContractRecord:
        return ContractRecord(
            contract=contract,
            ref=ContractVersionRef(
                contract_id=contract.contract_id,
                version=contract.version,
                digest=self.digest_contract(contract),
                status=ContractVersionStatus.DRAFT,
            ),
        )
