"""add_sentinel_enhancements

Revision ID: 0004_sentinel_enhancements
Revises: 8c667a9a131d
Create Date: 2026-09-04

Adds:
  - ai_responses table (Feature 1 — AI Incident Commander Response History)
  - meeting_notes table (Feature 4 — Key Meeting Notes)
  - meetings.summary column (proper summary storage, separate from description)
  - meetings.incident_severity column
  - meetings.root_cause_status column
  - meetings.resolution_status column
  - meetings.integration_config JSONB column (fixes description-overloading bug)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_sentinel_enhancements"
down_revision: Union[str, None] = "8c667a9a131d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. New columns on meetings ────────────────────────────────────────────
    op.add_column("meetings", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("meetings", sa.Column("incident_severity", sa.String(20), nullable=True))
    op.add_column("meetings", sa.Column("root_cause_status", sa.String(50), nullable=True))
    op.add_column("meetings", sa.Column("resolution_status", sa.String(50), nullable=True))
    op.add_column(
        "meetings",
        sa.Column(
            "integration_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column("meetings", sa.Column("default_mode", sa.String(20), nullable=True, server_default="frequent"))
    op.add_column("meetings", sa.Column("is_muted", sa.Boolean(), nullable=False, server_default="false"))

    # ── 2. ai_responses table ─────────────────────────────────────────────────
    op.create_table(
        "ai_responses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            index=True,
        ),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("response_type", sa.String(50), nullable=False, server_default="analysis"),
        sa.Column("trigger", sa.String(255), nullable=True),
        sa.Column(
            "related_fact_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "related_assumption_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "related_action_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("approval_status", sa.String(50), nullable=False, server_default="none"),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("execution_status", sa.String(50), nullable=False, server_default="none"),
        sa.Column("jira_ticket_ref", sa.String(100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_ai_responses_meeting_type", "ai_responses", ["meeting_id", "response_type"])
    op.create_index("ix_ai_responses_meeting_created", "ai_responses", ["meeting_id", "created_at"])
    op.create_index(op.f("ix_ai_responses_approval_status"), "ai_responses", ["approval_status"])

    # ── 3. meeting_notes table ────────────────────────────────────────────────
    op.create_table(
        "meeting_notes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            index=True,
        ),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("author_name", sa.String(255), nullable=False, server_default="Incident Commander"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(50), nullable=False, server_default="observation"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_meeting_notes_meeting_category", "meeting_notes", ["meeting_id", "category"]
    )


def downgrade() -> None:
    # Drop in reverse order
    op.drop_index("ix_meeting_notes_meeting_category", table_name="meeting_notes")
    op.drop_table("meeting_notes")

    op.drop_index(op.f("ix_ai_responses_approval_status"), table_name="ai_responses")
    op.drop_index("ix_ai_responses_meeting_created", table_name="ai_responses")
    op.drop_index("ix_ai_responses_meeting_type", table_name="ai_responses")
    op.drop_table("ai_responses")

    op.drop_column("meetings", "is_muted")
    op.drop_column("meetings", "default_mode")
    op.drop_column("meetings", "integration_config")
    op.drop_column("meetings", "resolution_status")
    op.drop_column("meetings", "root_cause_status")
    op.drop_column("meetings", "incident_severity")
    op.drop_column("meetings", "summary")
