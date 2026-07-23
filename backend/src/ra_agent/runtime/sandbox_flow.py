import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from ra_agent.audit import AuditRecorder
from ra_agent.contracts import (
    ApprovalDecision,
    AuditEventType,
    ExecutionStatus,
    PolicyDecision,
    PostCheckResult,
    PreCheckResult,
    RiskVerdict,
    StepStatus,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution import CheckpointManager, CommitGate, RollbackManager, ToolExecutor
from ra_agent.execution.cleanup import CleanupContext, RequestCleanupCoordinator
from ra_agent.security import DeepSafetyChecker, PostExecutionChecker, PreExecutionChecker

from .correlation import (
    CorrelationError,
    validate_checkpoint,
    validate_commit,
    validate_deep_check,
    validate_execution,
    validate_post_check,
    validate_pre_check,
    validate_rollback,
)
from .state_machine import transition


@dataclass(slots=True)
class _SandboxContext:
    state: StepStatus
    checkpoint_id: str | None
    approval_decision: ApprovalDecision | None = None
    rollback_attempted: bool = False
    post_check: PostCheckResult | None = None


class SandboxFlow:
    """Coordinates Mock pending execution; it performs no sandbox work itself."""

    _FILESYSTEM_TOOLS = frozenset({"write_file", "delete_file"})
    _MANAGED_PENDING_TOOLS = frozenset({"download_url", "memory_write"})

    def __init__(
        self,
        *,
        checkpoint_manager: CheckpointManager,
        executor: ToolExecutor,
        deep_checker: DeepSafetyChecker,
        pre_checker: PreExecutionChecker,
        post_checker: PostExecutionChecker,
        commit_gate: CommitGate,
        rollback_manager: RollbackManager,
        audit_recorder: AuditRecorder,
        cleanup_coordinator: RequestCleanupCoordinator | None = None,
    ) -> None:
        self.checkpoint_manager = checkpoint_manager
        self.executor = executor
        self.deep_checker = deep_checker
        self.pre_checker = pre_checker
        self.post_checker = post_checker
        self.commit_gate = commit_gate
        self.rollback_manager = rollback_manager
        self.audit_recorder = audit_recorder
        self.cleanup_coordinator = cleanup_coordinator

    async def run(
        self,
        request: ToolCallRequest,
        verdict: RiskVerdict,
        *,
        start_state: StepStatus = StepStatus.RISK_CLASSIFYING,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
        state = (
            start_state
            if start_state is StepStatus.READY
            else transition(start_state, StepStatus.READY)
        )
        pre_check = await self._pre_check(request, verdict, state)
        if isinstance(pre_check, ToolExecutionResult):
            return pre_check
        if request.tool_name in self._MANAGED_PENDING_TOOLS:
            context = _SandboxContext(
                state=state,
                checkpoint_id=None,
                approval_decision=approval_decision,
            )
            return await self._run_after_checkpoint(request, verdict, context)
        state = transition(state, StepStatus.CHECKPOINT_CREATING)
        try:
            checkpoint = await self.checkpoint_manager.create(request)
            validate_checkpoint(request, checkpoint)
        except CorrelationError as error:
            return await self._fail(request, state, str(error), error.error_code)
        except Exception as error:
            return await self._fail(request, state, self._reason(error), "CHECKPOINT_FAILED")

        context = _SandboxContext(
            state=state,
            checkpoint_id=checkpoint.checkpoint_id,
            approval_decision=approval_decision,
        )
        try:
            await self._record(
                request,
                AuditEventType.CHECKPOINT_CREATED,
                state,
                "checkpoint created",
                verdict,
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "status": checkpoint.status.value,
                },
            )
            if checkpoint.status is not ExecutionStatus.SUCCESS:
                return await self._fail(
                    request, state, "Checkpoint creation failed", "CHECKPOINT_FAILED"
                )
            return await self._run_after_checkpoint(request, verdict, context)
        except asyncio.CancelledError:
            if not context.rollback_attempted:
                await asyncio.shield(
                    self._rollback(
                        request,
                        verdict,
                        context,
                        "sandbox flow cancelled",
                        "CANCELLED",
                    )
                )
            raise

    async def _pre_check(
        self, request: ToolCallRequest, verdict: RiskVerdict, state: StepStatus
    ) -> PreCheckResult | ToolExecutionResult:
        await self._record(
            request, AuditEventType.PRE_CHECK_STARTED, state, "pre check started", verdict
        )
        try:
            checked = await self.pre_checker.check(request, verdict)
            validate_pre_check(request, checked)
        except CorrelationError as error:
            return await self._pre_block(request, verdict, state, str(error), error.error_code)
        except Exception as error:
            return await self._pre_block(
                request,
                verdict,
                state,
                self._reason(error),
                "PRE_CHECK_FAILED",
            )
        await self._record(
            request,
            AuditEventType.PRE_CHECK_FINISHED,
            state,
            "pre check finished",
            verdict,
            {"passed": checked.passed, "reason": checked.reason},
        )
        if not checked.passed:
            return await self._pre_block(
                request, verdict, state, checked.reason, "PRE_CHECK_REJECTED"
            )
        return checked

    async def _pre_block(
        self,
        request: ToolCallRequest,
        verdict: RiskVerdict,
        state: StepStatus,
        reason: str,
        error_code: str,
    ) -> ToolExecutionResult:
        state = transition(state, StepStatus.BLOCKED)
        await self._record(
            request,
            AuditEventType.TOOL_BLOCKED,
            state,
            "sandbox execution blocked by pre check",
            verdict,
            {"reason": reason, "error_code": error_code},
        )
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.BLOCKED,
            error=reason,
            error_code=error_code,
        )

    async def _run_after_checkpoint(
        self,
        request: ToolCallRequest,
        verdict: RiskVerdict,
        context: _SandboxContext,
    ) -> ToolExecutionResult:
        context.state = transition(context.state, StepStatus.EXECUTING_SANDBOX)
        started_at = datetime.now(UTC)
        await self._record(
            request,
            AuditEventType.EXECUTION_STARTED,
            context.state,
            "sandbox execution started",
            verdict,
            {"checkpoint_id": context.checkpoint_id},
        )
        try:
            execution = await self.executor.execute(
                request,
                checkpoint_id=context.checkpoint_id,
                approval_decision=context.approval_decision,
            )
        except Exception as error:
            reason = self._reason(error)
            await self._record(
                request,
                AuditEventType.EXECUTION_FINISHED,
                context.state,
                "sandbox execution failed",
                verdict,
                {"reason": reason},
            )
            return await self._rollback(request, verdict, context, reason, "EXECUTION_FAILED")

        await self._record(
            request,
            AuditEventType.EXECUTION_FINISHED,
            context.state,
            "sandbox execution finished",
            verdict,
            {"status": execution.status.value},
        )
        try:
            validate_execution(request, execution, checkpoint_id=context.checkpoint_id)
        except CorrelationError as error:
            return await self._rollback(request, verdict, context, str(error), error.error_code)
        if execution.status is not ExecutionStatus.PENDING_COMMIT:
            return await self._rollback(
                request,
                verdict,
                context,
                "Sandbox execution did not return PENDING_COMMIT",
                "INVALID_PENDING_RESULT",
            )

        context.state = transition(context.state, StepStatus.SAFETY_CHECKING)
        post_check, reason, error_code = await self._post_check(
            request, verdict, context, execution
        )
        if post_check is None:
            return await self._rollback(
                request,
                verdict,
                context,
                reason,
                error_code,
            )
        context.post_check = post_check
        if request.tool_name in self._MANAGED_PENDING_TOOLS:
            return await self._commit_managed_resource(
                request, verdict, context, execution, started_at
            )
        await self._record(
            request,
            AuditEventType.DEEP_CHECK_STARTED,
            context.state,
            "deep check started",
            verdict,
        )
        try:
            deep_check = await self.deep_checker.check(request, execution)
            validate_deep_check(request, deep_check)
        except CorrelationError as error:
            await self._record_stage_failure(
                request,
                AuditEventType.DEEP_CHECK_FINISHED,
                context.state,
                "deep check failed",
                verdict,
                str(error),
            )
            return await self._rollback(request, verdict, context, str(error), error.error_code)
        except Exception as error:
            reason = self._reason(error)
            await self._record_stage_failure(
                request,
                AuditEventType.DEEP_CHECK_FINISHED,
                context.state,
                "deep check failed",
                verdict,
                reason,
            )
            return await self._rollback(request, verdict, context, reason, "DEEP_CHECK_FAILED")
        await self._record(
            request,
            AuditEventType.DEEP_CHECK_FINISHED,
            context.state,
            "deep check finished",
            verdict,
            {"passed": deep_check.passed, "reason": deep_check.reason},
        )
        if not deep_check.passed:
            return await self._rollback(
                request,
                verdict,
                context,
                deep_check.reason,
                "DEEP_CHECK_REJECTED",
            )

        context.state = transition(context.state, StepStatus.COMMITTING)
        await self._record(
            request,
            AuditEventType.COMMIT_STARTED,
            context.state,
            "commit started",
            verdict,
            {"checkpoint_id": context.checkpoint_id},
        )
        try:
            if context.checkpoint_id is None:
                return await self._rollback(
                    request,
                    verdict,
                    context,
                    "filesystem commit requires a checkpoint",
                    "COMMIT_FAILED",
                )
            commit = await self.commit_gate.commit(execution, deep_check)
            validate_commit(request, context.checkpoint_id, commit)
        except CorrelationError as error:
            await self._record_stage_failure(
                request,
                AuditEventType.COMMIT_FINISHED,
                context.state,
                "commit failed",
                verdict,
                str(error),
            )
            return await self._rollback(request, verdict, context, str(error), error.error_code)
        except Exception as error:
            reason = self._reason(error)
            await self._record_stage_failure(
                request,
                AuditEventType.COMMIT_FINISHED,
                context.state,
                "commit failed",
                verdict,
                reason,
            )
            return await self._rollback(request, verdict, context, reason, "COMMIT_FAILED")
        await self._record(
            request,
            AuditEventType.COMMIT_FINISHED,
            context.state,
            "commit finished",
            verdict,
            {"status": commit.status.value},
        )
        if commit.status is not ExecutionStatus.COMMITTED:
            return await self._rollback(
                request, verdict, context, "Commit did not succeed", "COMMIT_FAILED"
            )

        context.state = transition(context.state, StepStatus.COMMITTED)
        return execution.model_copy(
            update={
                "status": ExecutionStatus.COMMITTED,
                "started_at": execution.started_at or started_at,
                "finished_at": execution.finished_at or datetime.now(UTC),
            }
        )

    async def _post_check(
        self,
        request: ToolCallRequest,
        verdict: RiskVerdict,
        context: _SandboxContext,
        execution: ToolExecutionResult,
    ) -> tuple[PostCheckResult | None, str, str]:
        await self._record(
            request,
            AuditEventType.POST_CHECK_STARTED,
            context.state,
            "post check started",
            verdict,
        )
        try:
            checked = await self.post_checker.check(request, execution)
            validate_post_check(request, checked)
        except CorrelationError as error:
            await self._record_stage_failure(
                request,
                AuditEventType.POST_CHECK_FINISHED,
                context.state,
                "post check failed",
                verdict,
                str(error),
            )
            return None, str(error), error.error_code
        except Exception as error:
            reason = self._reason(error)
            await self._record_stage_failure(
                request,
                AuditEventType.POST_CHECK_FINISHED,
                context.state,
                "post check failed",
                verdict,
                reason,
            )
            return None, reason, "POST_CHECK_FAILED"
        await self._record(
            request,
            AuditEventType.POST_CHECK_FINISHED,
            context.state,
            "post check finished",
            verdict,
            {"passed": checked.passed, "reason": checked.reason},
        )
        if not checked.passed:
            return None, checked.reason, "POST_CHECK_REJECTED"
        return checked, "", ""

    async def _commit_managed_resource(
        self,
        request: ToolCallRequest,
        verdict: RiskVerdict,
        context: _SandboxContext,
        execution: ToolExecutionResult,
        started_at: datetime,
    ) -> ToolExecutionResult:
        post_check = context.post_check
        if self.cleanup_coordinator is None or post_check is None:
            return await self._rollback(
                request,
                verdict,
                context,
                "managed pending resource dependencies are not configured",
                "MANAGED_COMMIT_UNAVAILABLE",
            )
        context.state = transition(context.state, StepStatus.COMMITTING)
        await self._record(
            request,
            AuditEventType.COMMIT_STARTED,
            context.state,
            "managed resource commit started",
            verdict,
        )
        try:
            committed = await self.cleanup_coordinator.commit(request, execution, post_check)
        except Exception as error:
            reason = self._reason(error)
            await self._record_stage_failure(
                request,
                AuditEventType.COMMIT_FINISHED,
                context.state,
                "managed resource commit failed",
                verdict,
                reason,
            )
            return await self._rollback(request, verdict, context, reason, "COMMIT_FAILED")
        await self._record(
            request,
            AuditEventType.COMMIT_FINISHED,
            context.state,
            "managed resource commit finished",
            verdict,
            {"resource": committed},
        )
        context.state = transition(context.state, StepStatus.COMMITTED)
        return execution.model_copy(
            update={
                "status": ExecutionStatus.COMMITTED,
                "started_at": execution.started_at or started_at,
                "finished_at": execution.finished_at or datetime.now(UTC),
            }
        )

    async def _rollback(
        self,
        request: ToolCallRequest,
        verdict: RiskVerdict,
        context: _SandboxContext,
        reason: str,
        error_code: str,
    ) -> ToolExecutionResult:
        context.rollback_attempted = True
        cleanup = asyncio.create_task(
            self._rollback_once(request, verdict, context, reason, error_code)
        )
        try:
            return await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def _rollback_once(
        self,
        request: ToolCallRequest,
        verdict: RiskVerdict,
        context: _SandboxContext,
        reason: str,
        error_code: str,
    ) -> ToolExecutionResult:
        context.state = transition(context.state, StepStatus.ROLLING_BACK)
        await self._record(
            request,
            AuditEventType.ROLLBACK_STARTED,
            context.state,
            "rollback started",
            verdict,
            {"checkpoint_id": context.checkpoint_id, "reason": reason},
        )
        try:
            if request.tool_name in self._MANAGED_PENDING_TOOLS:
                if self.cleanup_coordinator is None:
                    raise RuntimeError("managed pending resource cleanup is not configured")
                await self.cleanup_coordinator.abort(
                    CleanupContext(
                        task_id=request.task_id,
                        step_id=request.step_id,
                        request_id=request.request_id,
                        tool_name=request.tool_name,
                    ),
                    reason=reason,
                )
                rollback_status = ExecutionStatus.ROLLED_BACK
            else:
                if context.checkpoint_id is None:
                    raise RuntimeError("filesystem rollback requires a checkpoint")
                rollback = await self.rollback_manager.rollback(
                    context.checkpoint_id, request.request_id
                )
                validate_rollback(request, context.checkpoint_id, rollback)
                rollback_status = rollback.status
        except CorrelationError as error:
            await self._record_stage_failure(
                request,
                AuditEventType.ROLLBACK_FINISHED,
                context.state,
                "rollback failed",
                verdict,
                str(error),
            )
            context.state = transition(context.state, StepStatus.FAILED)
            return await self._fail(request, context.state, str(error), error.error_code)
        except Exception as error:
            failure = self._reason(error)
            await self._record_stage_failure(
                request,
                AuditEventType.ROLLBACK_FINISHED,
                context.state,
                "rollback failed",
                verdict,
                failure,
            )
            context.state = transition(context.state, StepStatus.FAILED)
            return await self._fail(request, context.state, failure, "ROLLBACK_FAILED")
        await self._record(
            request,
            AuditEventType.ROLLBACK_FINISHED,
            context.state,
            "rollback finished",
            verdict,
            {"status": rollback_status.value},
        )
        if rollback_status is not ExecutionStatus.ROLLED_BACK:
            context.state = transition(context.state, StepStatus.FAILED)
            return await self._fail(
                request, context.state, "Rollback did not succeed", "ROLLBACK_FAILED"
            )
        context.state = transition(context.state, StepStatus.ROLLED_BACK)
        await self._record_failure(request, context.state, reason, error_code)
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            checkpoint_id=context.checkpoint_id,
            status=ExecutionStatus.ROLLED_BACK,
            error=reason,
            error_code=error_code,
        )

    async def _fail(
        self, request: ToolCallRequest, state: StepStatus, reason: str, error_code: str
    ) -> ToolExecutionResult:
        if state is not StepStatus.FAILED:
            state = transition(state, StepStatus.FAILED)
        await self._record_failure(request, state, reason, error_code)
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.FAILED,
            error=reason,
            error_code=error_code,
        )

    async def _record_stage_failure(
        self,
        request: ToolCallRequest,
        event_type: AuditEventType,
        state: StepStatus,
        summary: str,
        verdict: RiskVerdict,
        reason: str,
    ) -> None:
        await self._record(
            request,
            event_type,
            state,
            summary,
            verdict,
            {"status": ExecutionStatus.FAILED.value, "reason": reason},
        )

    async def _record_failure(
        self, request: ToolCallRequest, state: StepStatus, reason: str, error_code: str
    ) -> None:
        await self.audit_recorder.record(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            event_type=AuditEventType.STEP_FAILED,
            actor="runtime-sandbox-flow",
            status=state.value,
            summary="sandbox flow failed",
            details={"reason": reason, "error_code": error_code},
        )

    async def _record(
        self,
        request: ToolCallRequest,
        event_type: AuditEventType,
        state: StepStatus,
        summary: str,
        verdict: RiskVerdict,
        details: dict[str, object] | None = None,
    ) -> None:
        await self.audit_recorder.record(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            event_type=event_type,
            actor="runtime-sandbox-flow",
            status=state.value,
            risk_level=verdict.risk_level,
            decision=PolicyDecision.SANDBOX_CHECK,
            summary=summary,
            details=details,
        )

    @staticmethod
    def _reason(error: Exception) -> str:
        return str(error) or type(error).__name__
