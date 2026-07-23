from datetime import UTC, datetime

import pytest

from ra_agent.contracts import SourceType, TaskContract, ToolCallRequest
from ra_agent.security.intent_boundary import RuleBasedIntentBoundaryGuard


def _request(**updates: object) -> ToolCallRequest:
    values: dict[str, object] = {
        "task_id": "task-boundary",
        "step_id": "step-boundary",
        "request_id": "request-boundary",
        "tool_name": "write_file",
        "arguments": {"path": "notes.txt", "content": "safe"},
        "objective": "Write one notes file",
        "context_summary": "boundary test",
        "source_type": SourceType.AGENT,
        "requested_at": datetime.now(UTC),
    }
    values.update(updates)
    return ToolCallRequest.model_validate(values)


@pytest.mark.asyncio
async def test_missing_contract_fails_closed() -> None:
    result = await RuleBasedIntentBoundaryGuard().check(_request())

    assert not result.allowed
    assert result.signals == ["contract_missing"]


@pytest.mark.asyncio
async def test_contract_blocks_out_of_scope_resource_and_bulk_expansion() -> None:
    contract = TaskContract(
        allowed_actions=["write_file"],
        allowed_resources=["notes.txt"],
        max_affected_objects=1,
    )
    result = await RuleBasedIntentBoundaryGuard().check(
        _request(arguments={"paths": ["notes.txt", "other.txt"], "content": "safe"}, task_contract=contract)
    )

    assert not result.allowed
    assert "affected_object_limit_exceeded" in result.signals
    assert "resource_not_allowed" in result.signals


@pytest.mark.asyncio
async def test_contract_allows_one_scoped_action() -> None:
    result = await RuleBasedIntentBoundaryGuard().check(
        _request(
            task_contract=TaskContract(
                allowed_actions=["write_file"],
                allowed_resources=["notes.txt"],
                max_affected_objects=1,
            )
        )
    )

    assert result.allowed
