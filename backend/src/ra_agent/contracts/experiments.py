from pydantic import Field, model_validator

from .common import ContractModel, UTCDateTime
from .enums import ExperimentMode


class ExperimentResult(ContractModel):
    schema_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    repetition: int = Field(ge=0)
    mode: ExperimentMode
    graph_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    started_at: UTCDateTime
    finished_at: UTCDateTime
    git_commit: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    node_version: str = Field(min_length=1)
    os: str = Field(min_length=1)
    environment_fingerprint: str = Field(min_length=1)
    runner_command: str = Field(min_length=1)
    fixture_id: str = Field(min_length=1)
    objective_class: str = Field(min_length=1)
    node_count: int = Field(ge=0)
    dependency_edge_count: int = Field(ge=0)
    max_parallelism: int = Field(gt=0)
    tool_sequence: list[str] = Field(default_factory=list)
    elapsed_ms: int = Field(ge=0)
    graph_elapsed_ms: int = Field(ge=0)
    critical_path_ms: int = Field(ge=0)
    parallel_saved_ms: int = Field(ge=0)
    approval_wait_ms: int = Field(ge=0)
    rollback_elapsed_ms: int = Field(ge=0)
    status: str = Field(min_length=1)
    expected_status: str = Field(min_length=1)
    safety_outcome: str = Field(min_length=1)
    tool_executed_count: int = Field(ge=0)
    unsafe_tool_executed_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    risk_escalation_count: int = Field(ge=0)
    check_count: int = Field(ge=0)
    audit_event_count: int = Field(ge=0)
    approval_requested_count: int = Field(ge=0)
    approval_decision_count: int = Field(ge=0)
    manual_action_count: int = Field(ge=0)
    checkpoint_count: int = Field(ge=0)
    pending_effect_count: int = Field(ge=0)
    commit_count: int = Field(ge=0)
    rollback_count: int = Field(ge=0)
    selective_rollback_count: int = Field(ge=0)
    residual_effect_count: int = Field(ge=0)
    audit_digest: str | None = None
    raw_result_path: str | None = None
    error_code: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> "ExperimentResult":
        if self.finished_at < self.started_at:
            raise ValueError("experiment finished_at must not precede started_at")
        return self
