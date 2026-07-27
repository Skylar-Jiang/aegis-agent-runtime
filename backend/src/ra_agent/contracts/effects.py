from pydantic import Field, model_validator

from .common import ContractModel, UTCDateTime
from .enums import EffectStatus, ExecutionStatus


class EffectRecord(ContractModel):
    effect_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    status: EffectStatus
    checkpoint_id: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: UTCDateTime


class RollbackPlan(ContractModel):
    plan_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    request_ids: list[str] = Field(default_factory=list)
    checkpoint_ids: list[str] = Field(default_factory=list)
    effect_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope(self) -> "RollbackPlan":
        scopes = (self.request_ids, self.checkpoint_ids, self.effect_ids)
        if not any(scopes):
            raise ValueError("rollback plan must declare an explicit related scope")
        if any(len(scope) != len(set(scope)) for scope in scopes):
            raise ValueError("rollback plan scope identifiers must be unique")
        return self

    def validate_effect_scope(self, effects: list[EffectRecord]) -> None:
        """Verify the explicit plan scope against effect facts from one task."""

        task_effects = [effect for effect in effects if effect.task_id == self.task_id]
        known_effect_ids = {effect.effect_id for effect in task_effects}
        known_request_ids = {effect.request_id for effect in task_effects}
        known_checkpoint_ids = {
            effect.checkpoint_id for effect in task_effects if effect.checkpoint_id is not None
        }
        if not set(self.effect_ids).issubset(known_effect_ids):
            raise ValueError("rollback plan effect_ids must belong to its task")
        if not set(self.request_ids).issubset(known_request_ids):
            raise ValueError("rollback plan request_ids must belong to its task")
        if not set(self.checkpoint_ids).issubset(known_checkpoint_ids):
            raise ValueError("rollback plan checkpoint_ids must belong to its task")


class RollbackPlanResult(ContractModel):
    plan_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    rolled_back_request_ids: list[str] = Field(default_factory=list)
    failed_request_ids: list[str] = Field(default_factory=list)
    status: ExecutionStatus
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_request_results(self) -> "RollbackPlanResult":
        if set(self.rolled_back_request_ids) & set(self.failed_request_ids):
            raise ValueError("rollback result request ids must not overlap")
        return self
