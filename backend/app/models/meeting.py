"""Meeting model."""

import uuid
from datetime import datetime

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="active", index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

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
