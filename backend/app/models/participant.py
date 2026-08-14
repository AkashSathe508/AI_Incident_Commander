"""Participant model."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(nullable=True)
    left_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # ── Relationships ─────────────────────────────────────────────────────────
    meeting: Mapped["Meeting"] = relationship("Meeting", back_populates="participants")
    transcript_segments: Mapped[list["TranscriptSegment"]] = relationship(
        "TranscriptSegment", back_populates="participant"
    )
    decisions_made: Mapped[list["Decision"]] = relationship(
        "Decision", back_populates="made_by_participant"
    )
    assigned_action_items: Mapped[list["ActionItem"]] = relationship(
        "ActionItem", back_populates="assignee"
    )

    __table_args__ = (
        Index("ix_participants_meeting_name", "meeting_id", "name"),
    )

    def __repr__(self) -> str:
        return f"<Participant id={self.id} name={self.name!r}>"


from app.models.meeting import Meeting  # noqa: E402, F401
from app.models.transcript_segment import TranscriptSegment  # noqa: E402, F401
from app.models.decision import Decision  # noqa: E402, F401
from app.models.action_item import ActionItem  # noqa: E402, F401
