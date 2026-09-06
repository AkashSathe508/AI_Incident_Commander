"""Meeting model."""

import uuid
from datetime import datetime

from sqlalchemy import Index, String, Text, Boolean
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Unique Agora channel name — set at creation time as "room-{id}"
    channel_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="active", index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    # -- New columns (additive, all nullable for backward compatibility) --------
    # AI-generated post-meeting summary (separate from description)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Severity: SEV-1 | SEV-2 | SEV-3 | SEV-4
    incident_severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Root cause status: investigating | identified | confirmed | unknown
    root_cause_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Resolution status: open | mitigating | resolved | monitoring
    resolution_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Integration credentials stored as JSONB (replaces the repurposed description field)
    integration_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Mode: "frequent" or "occasional"
    default_mode: Mapped[str | None] = mapped_column(String(20), nullable=True, default="frequent")
    # Muted flag controls audio output
    is_muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Relationships ─────────────────────────────────────────────────────────
    participants: Mapped[list["Participant"]] = relationship(
        "Participant", back_populates="meeting", cascade="all, delete-orphan"
    )
    transcript_segments: Mapped[list["TranscriptSegment"]] = relationship(
        "TranscriptSegment", back_populates="meeting", cascade="all, delete-orphan"
    )
    facts: Mapped[list["Fact"]] = relationship(
        "Fact", back_populates="meeting", cascade="all, delete-orphan"
    )
    assumptions: Mapped[list["Assumption"]] = relationship(
        "Assumption", back_populates="meeting", cascade="all, delete-orphan"
    )
    decisions: Mapped[list["Decision"]] = relationship(
        "Decision", back_populates="meeting", cascade="all, delete-orphan"
    )
    action_items: Mapped[list["ActionItem"]] = relationship(
        "ActionItem", back_populates="meeting", cascade="all, delete-orphan"
    )
    conflicts: Mapped[list["Conflict"]] = relationship(
        "Conflict", back_populates="meeting", cascade="all, delete-orphan"
    )
    timeline_events: Mapped[list["TimelineEvent"]] = relationship(
        "TimelineEvent", back_populates="meeting", cascade="all, delete-orphan"
    )
    risks: Mapped[list["Risk"]] = relationship(
        "Risk", back_populates="meeting", cascade="all, delete-orphan"
    )
    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence", back_populates="meeting", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        "AgentRun", back_populates="meeting", cascade="all, delete-orphan"
    )
    graph_checkpoints: Mapped[list["GraphCheckpoint"]] = relationship(
        "GraphCheckpoint", back_populates="meeting", cascade="all, delete-orphan"
    )
    # New relationships
    ai_responses: Mapped[list["AIResponse"]] = relationship(
        "AIResponse", back_populates="meeting", cascade="all, delete-orphan"
    )
    meeting_notes: Mapped[list["MeetingNote"]] = relationship(
        "MeetingNote", back_populates="meeting", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_meetings_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Meeting id={self.id} title={self.title!r} status={self.status}>"


# Deferred imports for type hints (avoid circular imports)
from app.models.participant import Participant  # noqa: E402, F401
from app.models.transcript_segment import TranscriptSegment  # noqa: E402, F401
from app.models.fact import Fact  # noqa: E402, F401
from app.models.assumption import Assumption  # noqa: E402, F401
from app.models.decision import Decision  # noqa: E402, F401
from app.models.action_item import ActionItem  # noqa: E402, F401
from app.models.conflict import Conflict  # noqa: E402, F401
from app.models.timeline_event import TimelineEvent  # noqa: E402, F401
from app.models.risk import Risk  # noqa: E402, F401
from app.models.evidence import Evidence  # noqa: E402, F401
from app.models.agent_run import AgentRun  # noqa: E402, F401
from app.models.graph_checkpoint import GraphCheckpoint  # noqa: E402, F401
from app.models.ai_response import AIResponse  # noqa: E402, F401
from app.models.meeting_note import MeetingNote  # noqa: E402, F401
