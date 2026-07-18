from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ra_agent.contracts import SourceType, ToolCallRequest, ToolSpec
from ra_agent.security.rule_engine import RuleEngine
from ra_agent.tools import DEFAULT_TOOL_SPECS


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def rules() -> RuleEngine:
    engine = RuleEngine.from_directory(ROOT / "configs")
    assert engine.valid, engine.error
    return engine


@pytest.fixture
def tool_specs() -> dict[str, ToolSpec]:
    return {spec.name: spec for spec in DEFAULT_TOOL_SPECS}


@pytest.fixture
def request_factory() -> Callable[..., ToolCallRequest]:
    def make_request(
        tool_name: str = "list_dir",
        *,
        arguments: dict[str, object] | None = None,
        request_id: str = "request-security",
        objective: str = "complete a safe workspace task",
        context_summary: str = "user requested a deterministic tool operation",
        source_type: SourceType = SourceType.USER,
    ) -> ToolCallRequest:
        return ToolCallRequest(
            task_id=f"task-{request_id}",
            step_id=f"step-{request_id}",
            request_id=request_id,
            tool_name=tool_name,
            arguments=arguments or {},
            objective=objective,
            context_summary=context_summary,
            source_type=source_type,
            requested_at=datetime.now(UTC),
        )

    return make_request
