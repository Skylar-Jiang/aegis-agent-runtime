from __future__ import annotations

from datetime import UTC, datetime

from ra_agent.contracts import SourceType, ToolCallRequest
from ra_agent.runtime.effect_targets import (
    EffectOperation,
    EffectScope,
    conflict_reason,
    infer_effect_targets,
)


def _request(tool_name: str, arguments: dict[str, object]) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="target-test",
        step_id="step",
        request_id=f"request-{tool_name}",
        tool_name=tool_name,
        arguments=arguments,
        objective="test effect target inference",
        context_summary="runtime effect target unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def test_infers_file_read_write_and_normalizes_aliases() -> None:
    read = infer_effect_targets(
        _request("read_file", {"path": "project/./config.yaml"})
    )
    write = infer_effect_targets(
        _request("write_file", {"path": "project/config.yaml", "content": "x"})
    )

    assert read[0].canonical_target == "project/config.yaml"
    assert read[0].operation is EffectOperation.READ
    assert write[0].operation is EffectOperation.WRITE
    assert conflict_reason(read[0], write[0]) == "read_write_exact"


def test_infers_directory_subtree_containment_and_keeps_reads_parallel() -> None:
    directory_read = infer_effect_targets(_request("list_dir", {"path": "project"}))
    nested_write = infer_effect_targets(
        _request("write_file", {"path": "project/subdir/file.txt", "content": "x"})
    )
    another_read = infer_effect_targets(
        _request("read_file", {"path": "project/file.txt"})
    )

    assert directory_read[0].scope is EffectScope.SUBTREE
    assert conflict_reason(directory_read[0], nested_write[0]) == "subtree_read_write"
    assert conflict_reason(directory_read[0], another_read[0]) is None


def test_unknown_or_unsafe_target_is_conservative() -> None:
    targets = infer_effect_targets(
        _request("write_file", {"path": "../unsafe", "content": "x"})
    )

    assert len(targets) == 1
    assert targets[0].resource_type == "untrusted"
