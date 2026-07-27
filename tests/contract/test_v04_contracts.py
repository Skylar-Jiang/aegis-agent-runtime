from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ra_agent.contracts import (
    CONTRACT_VERSION,
    EffectRecord,
    EffectStatus,
    ExecutionStatus,
    ExperimentMode,
    ExperimentResult,
    RollbackPlan,
    RollbackPlanResult,
    SourceType,
    TaskGraph,
    TaskGraphResult,
    TaskNode,
    ToolCallRequest,
    ToolExecutionResult,
)


def _request(*, task_id: str = "task-1", step_id: str = "step-1") -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=step_id,
        request_id=f"request-{step_id}",
        tool_name="read_file",
        arguments={"path": "README.md"},
        objective="read the project readme",
        context_summary="v0.4 contract test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def _node(node_id: str, *dependencies: str, task_id: str = "task-1") -> TaskNode:
    return TaskNode(
        task_id=task_id,
        graph_id="graph-1",
        node_id=node_id,
        request=_request(task_id=task_id, step_id=node_id),
        dependencies=list(dependencies),
        parallel_safe=True,
        effect_targets=[".runtime/workspace/README.md"],
    )


def test_v04_exports_are_available() -> None:
    assert CONTRACT_VERSION == "0.4"
    assert EffectStatus.COMMITTED.value == "COMMITTED"


def test_task_graph_contract_requires_unique_acyclic_nodes_for_one_task() -> None:
    graph = TaskGraph(
        graph_id="graph-1",
        task_id="task-1",
        nodes=[_node("read"), _node("summarize", "read")],
        max_parallelism=2,
    )

    assert graph.nodes[1].dependencies == ["read"]
    with pytest.raises(ValidationError):
        TaskGraph(
            graph_id="graph-1",
            task_id="task-1",
            nodes=[_node("same"), _node("same")],
            max_parallelism=1,
        )
    with pytest.raises(ValidationError):
        TaskGraph(
            graph_id="graph-1",
            task_id="task-1",
            nodes=[_node("one", "two"), _node("two", "one")],
            max_parallelism=1,
        )
    with pytest.raises(ValidationError):
        TaskGraph(
            graph_id="graph-1",
            task_id="task-1",
            nodes=[_node("foreign", task_id="other-task")],
            max_parallelism=1,
        )


def test_task_graph_contract_rejects_unknown_dependencies_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        TaskGraph(
            graph_id="graph-1",
            task_id="task-1",
            nodes=[_node("read", "unknown")],
            max_parallelism=1,
        )
    with pytest.raises(ValidationError):
        TaskNode(
            **_node("read").model_dump(),
            unsupported=True,
        )


def test_effect_and_graph_results_normalize_utc_and_retain_request_scope() -> None:
    plus_eight = timezone(timedelta(hours=8))
    local_time = datetime(2026, 7, 12, 8, 30, tzinfo=plus_eight)
    execution = ToolExecutionResult(
        task_id="task-1",
        step_id="read",
        request_id="request-read",
        status=ExecutionStatus.COMMITTED,
    )
    effect = EffectRecord(
        effect_id="effect-1",
        task_id="task-1",
        step_id="read",
        request_id="request-read",
        kind="file_read",
        target_ref=".runtime/workspace/README.md",
        status=EffectStatus.COMMITTED,
        checkpoint_id=None,
        artifact_refs=[],
        created_at=local_time,
    )
    result = TaskGraphResult(
        graph_id="graph-1",
        task_id="task-1",
        node_results={"read": execution},
        blocked_nodes={},
        started_at=local_time,
        finished_at=local_time,
    )

    assert effect.created_at == datetime(2026, 7, 12, 0, 30, tzinfo=UTC)
    assert result.started_at == datetime(2026, 7, 12, 0, 30, tzinfo=UTC)
    with pytest.raises(ValidationError):
        EffectRecord(**effect.model_dump(), unexpected="value")


def test_rollback_plans_require_an_explicit_unique_related_scope() -> None:
    plan = RollbackPlan(
        plan_id="rollback-1",
        task_id="task-1",
        trigger="post_check_failed",
        request_ids=["request-1"],
        checkpoint_ids=["checkpoint-1"],
        effect_ids=["effect-1"],
        reason="restore only the failed request effects",
    )
    result = RollbackPlanResult(
        plan_id=plan.plan_id,
        task_id=plan.task_id,
        rolled_back_request_ids=["request-1"],
        failed_request_ids=[],
        status=ExecutionStatus.ROLLED_BACK,
        reason="restored",
    )

    assert result.status is ExecutionStatus.ROLLED_BACK
    effect = EffectRecord(
        effect_id="effect-1",
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        kind="file_write",
        target_ref=".runtime/workspace/demo.txt",
        status=EffectStatus.PENDING,
        checkpoint_id="checkpoint-1",
        created_at=datetime.now(UTC),
    )
    plan.validate_effect_scope([effect])
    with pytest.raises(ValueError, match="must belong to its task"):
        plan.validate_effect_scope([effect.model_copy(update={"task_id": "other-task"})])
    with pytest.raises(ValidationError):
        RollbackPlan(
            plan_id="rollback-1",
            task_id="task-1",
            trigger="post_check_failed",
            request_ids=[],
            checkpoint_ids=[],
            effect_ids=[],
            reason="missing scope",
        )
    with pytest.raises(ValidationError):
        RollbackPlan(
            plan_id="rollback-1",
            task_id="task-1",
            trigger="post_check_failed",
            request_ids=["request-1", "request-1"],
            checkpoint_ids=[],
            effect_ids=[],
            reason="ambiguous scope",
        )


def test_experiment_result_schema_is_strict_and_normalizes_utc() -> None:
    local_time = datetime(2026, 7, 12, 8, 30, tzinfo=timezone(timedelta(hours=8)))
    experiment = ExperimentResult(
        schema_version="v0.4",
        run_id="run-1",
        case_id="case-1",
        repetition=1,
        mode=ExperimentMode.ADAPTIVE_RUNTIME,
        graph_id="graph-1",
        task_id="task-1",
        started_at=local_time,
        finished_at=local_time,
        git_commit="deadbeef",
        python_version="3.11",
        node_version="20",
        os="Windows",
        environment_fingerprint="test-environment",
        runner_command="pytest tests/e2e",
        fixture_id="fixture-1",
        objective_class="read_only",
        node_count=1,
        dependency_edge_count=0,
        max_parallelism=1,
        tool_sequence=["read_file"],
        elapsed_ms=10,
        graph_elapsed_ms=10,
        critical_path_ms=10,
        parallel_saved_ms=0,
        approval_wait_ms=0,
        rollback_elapsed_ms=0,
        status="COMPLETED",
        expected_status="COMPLETED",
        safety_outcome="PASS",
        tool_executed_count=1,
        unsafe_tool_executed_count=0,
        blocked_count=0,
        risk_escalation_count=0,
        check_count=2,
        audit_event_count=5,
        approval_requested_count=0,
        approval_decision_count=0,
        manual_action_count=0,
        checkpoint_count=0,
        pending_effect_count=0,
        commit_count=1,
        rollback_count=0,
        selective_rollback_count=0,
        residual_effect_count=0,
        audit_digest="digest",
        raw_result_path="artifacts/run-1.json",
        error_code=None,
        notes=None,
    )

    assert experiment.started_at == datetime(2026, 7, 12, 0, 30, tzinfo=UTC)
    with pytest.raises(ValidationError):
        ExperimentResult(**(experiment.model_dump() | {"elapsed_ms": -1}))
    with pytest.raises(ValidationError):
        ExperimentResult(**(experiment.model_dump() | {"extra_field": "not allowed"}))
