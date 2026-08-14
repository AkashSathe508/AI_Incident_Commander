"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-08-14

Creates all 14 tables for incident-commander, with:
- pgvector extension
- UUID primary keys
- Foreign keys with proper ON DELETE rules
- Indexes on FKs and commonly queried columns
- pgvector IVFFlat index on embeddings.vector
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enable pgvector extension if available ──────────────────────────────────
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
                CREATE EXTENSION IF NOT EXISTS vector;
            END IF;
        END $$;
        """
    )

    # ── meetings ──────────────────────────────────────────────────────────────
    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_meetings_id", "meetings", ["id"])
    op.create_index("ix_meetings_status", "meetings", ["status"])
    op.create_index(
        "ix_meetings_status_created", "meetings", ["status", "created_at"]
    )

    # ── participants ──────────────────────────────────────────────────────────
    op.create_table(
        "participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_participants_id", "participants", ["id"])
    op.create_index("ix_participants_meeting_id", "participants", ["meeting_id"])
    op.create_index(
        "ix_participants_meeting_name", "participants", ["meeting_id", "name"]
    )

    # ── transcript_segments ───────────────────────────────────────────────────
    op.create_table(
        "transcript_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "participant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("start_ms", sa.BigInteger, nullable=False),
        sa.Column("end_ms", sa.BigInteger, nullable=False),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_transcript_segments_id", "transcript_segments", ["id"]
    )
    op.create_index(
        "ix_transcript_segments_meeting_id", "transcript_segments", ["meeting_id"]
    )
    op.create_index(
        "ix_transcript_segments_participant_id",
        "transcript_segments",
        ["participant_id"],
    )
    op.create_index(
        "ix_transcript_segments_meeting_time",
        "transcript_segments",
        ["meeting_id", "start_ms"],
    )

    # ── facts ─────────────────────────────────────────────────────────────────
    op.create_table(
        "facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_segment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transcript_segments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_facts_id", "facts", ["id"])
    op.create_index("ix_facts_meeting_id", "facts", ["meeting_id"])
    op.create_index("ix_facts_source_segment_id", "facts", ["source_segment_id"])
    op.create_index(
        "ix_facts_meeting_confidence", "facts", ["meeting_id", "confidence"]
    )

    # ── assumptions ───────────────────────────────────────────────────────────
    op.create_table(
        "assumptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_assumptions_id", "assumptions", ["id"])
    op.create_index("ix_assumptions_meeting_id", "assumptions", ["meeting_id"])
    op.create_index("ix_assumptions_status", "assumptions", ["status"])
    op.create_index(
        "ix_assumptions_meeting_status", "assumptions", ["meeting_id", "status"]
    )

    # ── decisions ─────────────────────────────────────────────────────────────
    op.create_table(
        "decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "made_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_decisions_id", "decisions", ["id"])
    op.create_index("ix_decisions_meeting_id", "decisions", ["meeting_id"])
    op.create_index("ix_decisions_made_by_id", "decisions", ["made_by_id"])
    op.create_index(
        "ix_decisions_meeting_created", "decisions", ["meeting_id", "created_at"]
    )

    # ── action_items ──────────────────────────────────────────────────────────
    op.create_table(
        "action_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assignee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("participants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="open"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_action_items_id", "action_items", ["id"])
    op.create_index("ix_action_items_meeting_id", "action_items", ["meeting_id"])
    op.create_index("ix_action_items_assignee_id", "action_items", ["assignee_id"])
    op.create_index("ix_action_items_status", "action_items", ["status"])
    op.create_index(
        "ix_action_items_meeting_status", "action_items", ["meeting_id", "status"]
    )

    # ── conflicts ─────────────────────────────────────────────────────────────
    op.create_table(
        "conflicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="open"),
        sa.Column("resolution", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_conflicts_id", "conflicts", ["id"])
    op.create_index("ix_conflicts_meeting_id", "conflicts", ["meeting_id"])
    op.create_index("ix_conflicts_status", "conflicts", ["status"])
    op.create_index(
        "ix_conflicts_meeting_status", "conflicts", ["meeting_id", "status"]
    )

    # ── timeline_events ───────────────────────────────────────────────────────
    op.create_table(
        "timeline_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_timeline_events_id", "timeline_events", ["id"])
    op.create_index("ix_timeline_events_meeting_id", "timeline_events", ["meeting_id"])
    op.create_index("ix_timeline_events_event_type", "timeline_events", ["event_type"])
    op.create_index(
        "ix_timeline_events_meeting_occurred",
        "timeline_events",
        ["meeting_id", "occurred_at"],
    )

    # ── risks ─────────────────────────────────────────────────────────────────
    op.create_table(
        "risks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("severity", sa.String(50), nullable=False),
        sa.Column("likelihood", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="open"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_risks_id", "risks", ["id"])
    op.create_index("ix_risks_meeting_id", "risks", ["meeting_id"])
    op.create_index("ix_risks_severity", "risks", ["severity"])
    op.create_index("ix_risks_status", "risks", ["status"])
    op.create_index(
        "ix_risks_meeting_severity_status",
        "risks",
        ["meeting_id", "severity", "status"],
    )

    # ── evidence ──────────────────────────────────────────────────────────────
    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("source_type", sa.String(100), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_evidence_id", "evidence", ["id"])
    op.create_index("ix_evidence_meeting_id", "evidence", ["meeting_id"])
    op.create_index("ix_evidence_source_type", "evidence", ["source_type"])
    op.create_index(
        "ix_evidence_meeting_source_type", "evidence", ["meeting_id", "source_type"]
    )

    # ── embeddings ────────────────────────────────────────────────────────────
    op.create_table(
        "embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_type", sa.String(100), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "vector",
            sa.Text,  # placeholder; actual type set below via raw SQL
            nullable=False,
        ),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_embeddings_id", "embeddings", ["id"])
    op.create_index("ix_embeddings_source_type", "embeddings", ["source_type"])
    op.create_index("ix_embeddings_source_id", "embeddings", ["source_id"])
    op.create_index(
        "ix_embeddings_source", "embeddings", ["source_type", "source_id"]
    )
    # Replace placeholder Text column with proper vector type if pgvector extension exists
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                ALTER TABLE embeddings ALTER COLUMN vector TYPE vector(1536) USING vector::vector(1536);
                CREATE INDEX IF NOT EXISTS ix_embeddings_vector_ivfflat
                ON embeddings
                USING ivfflat (vector vector_cosine_ops)
                WITH (lists = 100);
            END IF;
        END $$;
        """
    )

    # ── agent_runs ────────────────────────────────────────────────────────────
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("input_data", postgresql.JSONB, nullable=True),
        sa.Column("output_data", postgresql.JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_agent_runs_id", "agent_runs", ["id"])
    op.create_index("ix_agent_runs_meeting_id", "agent_runs", ["meeting_id"])
    op.create_index("ix_agent_runs_agent_name", "agent_runs", ["agent_name"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index(
        "ix_agent_runs_agent_status", "agent_runs", ["agent_name", "status"]
    )
    op.create_index(
        "ix_agent_runs_meeting_agent", "agent_runs", ["meeting_id", "agent_name"]
    )

    # ── graph_checkpoints ─────────────────────────────────────────────────────
    op.create_table(
        "graph_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("checkpoint_data", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_graph_checkpoints_id", "graph_checkpoints", ["id"])
    op.create_index(
        "ix_graph_checkpoints_meeting_id", "graph_checkpoints", ["meeting_id"]
    )
    op.create_index("ix_graph_checkpoints_thread_id", "graph_checkpoints", ["thread_id"])
    op.create_index(
        "ix_graph_checkpoints_thread_created",
        "graph_checkpoints",
        ["thread_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("graph_checkpoints")
    op.drop_table("agent_runs")
    op.drop_table("embeddings")
    op.drop_table("evidence")
    op.drop_table("risks")
    op.drop_table("timeline_events")
    op.drop_table("conflicts")
    op.drop_table("action_items")
    op.drop_table("decisions")
    op.drop_table("assumptions")
    op.drop_table("facts")
    op.drop_table("transcript_segments")
    op.drop_table("participants")
    op.drop_table("meetings")
    op.execute("DROP EXTENSION IF EXISTS vector")
