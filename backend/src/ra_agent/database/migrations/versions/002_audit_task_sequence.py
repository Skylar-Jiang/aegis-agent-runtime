"""add durable audit task sequence allocation

Revision ID: 002
Revises: 001
Create Date: 2026-07-20
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_task_sequences",
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("next_sequence_number", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
    )
    # Earlier versions allocated sequences with a process-local lock.  Repair any
    # existing duplicate values before applying the database-level invariant.
    op.execute(
        """
        WITH ranked AS (
            SELECT event_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY task_id
                       ORDER BY sequence_number, timestamp, event_id
                   ) AS sequence_number
            FROM audit_events
        )
        UPDATE audit_events
        SET sequence_number = (
            SELECT ranked.sequence_number
            FROM ranked
            WHERE ranked.event_id = audit_events.event_id
        )
        """
    )
    op.execute(
        """
        INSERT INTO audit_task_sequences (task_id, next_sequence_number)
        SELECT task_id, MAX(sequence_number) + 1
        FROM audit_events
        GROUP BY task_id
        """
    )
    op.create_index(
        "uq_audit_events_task_sequence",
        "audit_events",
        ["task_id", "sequence_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_audit_events_task_sequence", table_name="audit_events")
    op.drop_table("audit_task_sequences")
