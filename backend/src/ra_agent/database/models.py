"""SQLAlchemy ORM models for persistent Runtime state."""

from typing import Any

from sqlalchemy import JSON, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

Base = declarative_base()


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    step_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("task_id", "sequence_number", name="uq_audit_events_task_sequence"),
        Index("idx_audit_task_id", "task_id"),
        Index("idx_audit_timestamp", "timestamp"),
    )


class AuditTaskSequenceRow(Base):
    __tablename__ = "audit_task_sequences"

    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    next_sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)


class ApprovalRequestRow(Base):
    __tablename__ = "approval_requests"

    approval_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    step_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")

    __table_args__ = (Index("idx_approval_status", "status"),)


class ApprovalDecisionRow(Base):
    __tablename__ = "approval_decisions"

    approval_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    step_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    consumed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ExecutionClaimRow(Base):
    __tablename__ = "execution_claims"

    request_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    resumable: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="IN_FLIGHT")


class AgentTaskRow(Base):
    """Durable task metadata and the latest resumable Agent state snapshot."""

    __tablename__ = "agent_tasks"

    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    contract: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    agent_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    security_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    security_profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_agent_tasks_created_at", "created_at"),
        Index("idx_agent_tasks_status", "status"),
        Index("idx_agent_tasks_conversation", "conversation_id"),
    )


class SecurityProfileVersionRow(Base):
    __tablename__ = "security_profile_versions"

    profile_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    allowed_actions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    resource_scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    allow_egress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_affected_objects: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_security_profile_latest", "profile_id", "version"),)


class ConversationRow(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    security_profile_id: Mapped[str] = mapped_column(Text, nullable=False)
    context_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_conversations_updated", "updated_at"),)


class ConversationMessageRow(Base):
    __tablename__ = "conversation_messages"

    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence_number", name="uq_conversation_message_sequence"
        ),
        Index("idx_conversation_messages", "conversation_id", "sequence_number"),
    )
