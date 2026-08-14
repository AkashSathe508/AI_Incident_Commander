"""add_pending_approvals_table

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-14

Creates the pending_approvals table for Human-in-the-Loop external action authorization.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pending_approvals_meeting_id", "pending_approvals", ["meeting_id"])
    op.create_index("ix_pending_approvals_action_type", "pending_approvals", ["action_type"])
    op.create_index("ix_pending_approvals_status", "pending_approvals", ["status"])


def downgrade() -> None:
    op.drop_index("ix_pending_approvals_status", table_name="pending_approvals")
    op.drop_index("ix_pending_approvals_action_type", table_name="pending_approvals")
    op.drop_index("ix_pending_approvals_meeting_id", table_name="pending_approvals")
    op.drop_table("pending_approvals")
