import pytest

from ra_agent.contracts import (
    ContractPermissionRule,
    ContractService,
    ContractVersionStatus,
    TaskContractCreateRequest,
    TaskContractUpdateRequest,
)


@pytest.mark.asyncio
async def test_contract_service_versions_confirm_and_supersede() -> None:
    service = ContractService()
    first = await service.create_contract(
        TaskContractCreateRequest(
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
            goals=["write report"],
            allowed=[ContractPermissionRule(action="create_file", resource="reports/**")],
            policy_version="policy:1",
            tool_manifest_digest="tools-v1",
        )
    )
    assert first.contract.version == 1
    assert first.ref.status is ContractVersionStatus.DRAFT

    confirmed = await service.confirm_contract(
        first.contract.contract_id, version=1, confirmed_by="user-1"
    )
    assert confirmed.ref.status is ContractVersionStatus.CONFIRMED
    assert (await service.get_active_contract(first.contract.contract_id)) == confirmed

    second = await service.update_contract(
        first.contract.contract_id,
        TaskContractUpdateRequest(goals=["write and summarize report"]),
    )
    assert second.contract.version == 2
    assert second.contract.parent_digest == first.ref.digest
    assert second.ref.status is ContractVersionStatus.DRAFT

    confirmed_second = await service.confirm_contract(
        first.contract.contract_id, version=2, confirmed_by="user-1"
    )
    versions = await service.list_versions(first.contract.contract_id)
    assert versions[0].ref.status is ContractVersionStatus.SUPERSEDED
    assert confirmed_second.ref.status is ContractVersionStatus.CONFIRMED
    active_contract = await service.get_active_contract(first.contract.contract_id)
    assert active_contract is not None
    assert active_contract.contract.version == 2
