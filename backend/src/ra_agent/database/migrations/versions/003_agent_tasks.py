"""add durable Agent task snapshots

Revision ID: 003
Revises: 002
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("final_answer", sa.Text(), nullable=True),
        sa.Column("contract", sa.JSON(), nullable=True),
        sa.Column("agent_state", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index("idx_agent_tasks_created_at", "agent_tasks", ["created_at"])
    op.create_index("idx_agent_tasks_status", "agent_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("idx_agent_tasks_status", table_name="agent_tasks")
    op.drop_index("idx_agent_tasks_created_at", table_name="agent_tasks")
    op.drop_table("agent_tasks")
