"""add security profiles and conversation workbench

Revision ID: 004
Revises: 003
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.add_column(sa.Column("conversation_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("security_profile_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("security_profile_version", sa.Integer(), nullable=True))
        batch.create_index("idx_agent_tasks_conversation", ["conversation_id"])

    op.create_table(
        "security_profile_versions",
        sa.Column("profile_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("allowed_actions", sa.JSON(), nullable=False),
        sa.Column("resource_scopes", sa.JSON(), nullable=False),
        sa.Column("allow_egress", sa.Integer(), nullable=False),
        sa.Column("max_affected_objects", sa.Integer(), nullable=False),
        sa.Column("approval_policy", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("profile_id", "version"),
    )
    op.create_index(
        "idx_security_profile_latest",
        "security_profile_versions",
        ["profile_id", "version"],
    )
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("security_profile_id", sa.Text(), nullable=False),
        sa.Column("context_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index("idx_conversations_updated", "conversations", ["updated_at"])
    op.create_table(
        "conversation_messages",
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("conversation_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("message_id"),
        sa.UniqueConstraint(
            "conversation_id", "sequence_number", name="uq_conversation_message_sequence"
        ),
    )
    op.create_index(
        "idx_conversation_messages",
        "conversation_messages",
        ["conversation_id", "sequence_number"],
    )


def downgrade() -> None:
    op.drop_index("idx_conversation_messages", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("idx_conversations_updated", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("idx_security_profile_latest", table_name="security_profile_versions")
    op.drop_table("security_profile_versions")
    with op.batch_alter_table("agent_tasks") as batch:
        batch.drop_index("idx_agent_tasks_conversation")
        batch.drop_column("security_profile_version")
        batch.drop_column("security_profile_id")
        batch.drop_column("conversation_id")
