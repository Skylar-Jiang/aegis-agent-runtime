from ra_agent.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    CheckpointResult,
    CommitResult,
    DeepCheckResult,
    RiskVerdict,
    RollbackResult,
    ToolCallRequest,
    ToolExecutionResult,
)


class CorrelationError(ValueError):
    error_code = "CORRELATION_MISMATCH"


def _require(label: str, actual: str | None, expected: str | None) -> None:
    if actual != expected:
        raise CorrelationError(f"{label} mismatch: expected {expected!r}, got {actual!r}")


def validate_risk(request: ToolCallRequest, verdict: RiskVerdict) -> None:
    _require("RiskVerdict.request_id", verdict.request_id, request.request_id)


def validate_agent_request(task_id: str, request: ToolCallRequest) -> None:
    _require("ToolCallRequest.task_id", request.task_id, task_id)


def validate_agent_result(request: ToolCallRequest, result: ToolExecutionResult) -> None:
    _require("ToolExecutionResult.task_id", result.task_id, request.task_id)
    _require("ToolExecutionResult.step_id", result.step_id, request.step_id)
    _require("ToolExecutionResult.request_id", result.request_id, request.request_id)


def validate_checkpoint(request: ToolCallRequest, result: CheckpointResult) -> None:
    _require("CheckpointResult.task_id", result.task_id, request.task_id)
    _require("CheckpointResult.step_id", result.step_id, request.step_id)
    _require("CheckpointResult.request_id", result.request_id, request.request_id)


def validate_execution(
    request: ToolCallRequest,
    result: ToolExecutionResult,
    *,
    checkpoint_id: str | None = None,
) -> None:
    _require("ToolExecutionResult.task_id", result.task_id, request.task_id)
    _require("ToolExecutionResult.step_id", result.step_id, request.step_id)
    _require("ToolExecutionResult.request_id", result.request_id, request.request_id)
    _require("ToolExecutionResult.checkpoint_id", result.checkpoint_id, checkpoint_id)


def validate_deep_check(request: ToolCallRequest, result: DeepCheckResult) -> None:
    _require("DeepCheckResult.request_id", result.request_id, request.request_id)


def validate_commit(request: ToolCallRequest, checkpoint_id: str, result: CommitResult) -> None:
    _require("CommitResult.request_id", result.request_id, request.request_id)
    _require("CommitResult.checkpoint_id", result.checkpoint_id, checkpoint_id)


def validate_rollback(request: ToolCallRequest, checkpoint_id: str, result: RollbackResult) -> None:
    _require("RollbackResult.request_id", result.request_id, request.request_id)
    _require("RollbackResult.checkpoint_id", result.checkpoint_id, checkpoint_id)


def validate_approval_request(
    request: ToolCallRequest,
    fingerprint: str,
    approval: ApprovalRequest,
) -> None:
    _require("ApprovalRequest.task_id", approval.task_id, request.task_id)
    _require("ApprovalRequest.step_id", approval.step_id, request.step_id)
    _require("ApprovalRequest.request_id", approval.request_id, request.request_id)
    _require("ApprovalRequest.tool_name", approval.tool_name, request.tool_name)
    _require("ApprovalRequest.request_fingerprint", approval.request_fingerprint, fingerprint)


def validate_approval_decision(approval: ApprovalRequest, decision: ApprovalDecision) -> None:
    _require("ApprovalDecision.approval_id", decision.approval_id, approval.approval_id)
    _require("ApprovalDecision.task_id", decision.task_id, approval.task_id)
    _require("ApprovalDecision.step_id", decision.step_id, approval.step_id)
    _require("ApprovalDecision.request_id", decision.request_id, approval.request_id)
