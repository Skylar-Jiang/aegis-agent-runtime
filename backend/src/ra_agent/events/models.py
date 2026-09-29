"""Core behavior facts. This does not replace Runtime Base's AuditEvent."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from ra_agent.contracts.common import ContractModel, UTCDateTime
from ra_agent.contracts.core_v1 import GatewayDecisionType, GatewayReasonCode

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]
Reference = Annotated[str, Field(min_length=1, max_length=512, pattern=r"\S")]
HexDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Sequence = Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]


class BehaviorEvent(ContractModel):
    """A stored event with all twelve plan fields explicitly present.

    Nullable facts are explicit; consumers must not invent missing provenance.
    The store allocates sequence before validating this persisted representation.
    """

    schema_version: Literal["1.0"] = "1.0"
    event_id: Identifier
    sequence: Sequence
    task_id: Identifier
    parent_event_id: Identifier | None
    type: Identifier
    actor: Identifier
    source_ref: Reference
    object_digest: HexDigest | None
    state: Identifier
    decision: GatewayDecisionType | None
    result_digest: HexDigest | None
    occurred_at: UTCDateTime

    # Keep P1's request correlation and machine-readable explanation.
    request_id: Identifier | None = None
    reason_code: GatewayReasonCode | None = None
    evidence_refs: list[Reference] = Field(default_factory=list, max_length=100)

    # Reserved Cap extension points; Core stores these without interpreting them.
    prepared_effect_id: Identifier | None = None
    effect_descriptor_digest: HexDigest | None = None
    result_commitment: Reference | None = None
    commit_epoch: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)] | None = None
    execution_receipt_id: Identifier | None = None
    compliance_proof_ref: Reference | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def no_numeric_timestamp(cls, value: object) -> object:
        if isinstance(value, (int, float, bool)):
            raise ValueError("occurred_at must be an aware datetime or ISO 8601 string")
        return value
