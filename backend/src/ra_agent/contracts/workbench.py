from typing import Literal

from pydantic import Field

from .common import ContractModel, UTCDateTime


class ApprovalPolicy(ContractModel):
    required_actions: list[str] = Field(
        default_factory=lambda: ["delete_file", "run_shell", "send_email_dry_run"]
    )
    bulk_action_threshold: int = Field(default=20, gt=0)


class SecurityProfileUpdate(ContractModel):
    allowed_actions: list[str] = Field(min_length=1)
    resource_scopes: list[str] = Field(min_length=1)
    allow_egress: bool = False
    max_affected_objects: int = Field(default=100, gt=0)
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)


class SecurityProfile(SecurityProfileUpdate):
    profile_id: str
    version: int = Field(gt=0)
    created_at: UTCDateTime
    denied_actions: list[str] = Field(default_factory=list)


class ConversationCreateRequest(ContractModel):
    title: str | None = Field(default=None, max_length=200)
    security_profile_id: str = "default"


class ConversationMessageCreateRequest(ContractModel):
    content: str = Field(min_length=1, max_length=50_000)


class ConversationMessage(ContractModel):
    message_id: str
    conversation_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    task_id: str | None = None
    created_at: UTCDateTime
    sequence_number: int = Field(gt=0)


class Conversation(ContractModel):
    conversation_id: str
    title: str
    security_profile_id: str
    context_summary: str = ""
    created_at: UTCDateTime
    updated_at: UTCDateTime
    messages: list[ConversationMessage] = Field(default_factory=list)


class ConversationTurnResponse(ContractModel):
    conversation_id: str
    message: ConversationMessage
    task_id: str
    status: str
