"""MeetingNote model — per-meeting notes added by Incident Commander or team."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class MeetingNote(Base):
    """A structured note attached to a meeting.

    Categories:
      - observation    : Important observation about the incident
      - decision       : A decision that was made
      - follow_up      : Something to follow up after the meeting
      - unresolved     : An open question that remains unresolved
      - technical      : Technical detail worth preserving
      - lesson_learned : Lessons learned for post-mortem
    """

    __tablename__ = "meeting_notes"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_name: Mapped[str] = mapped_column(String(255), nullable=False, default="Incident Commander")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # category: observation | decision | follow_up | unresolved | technical | lesson_learned
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="observation", index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    # -- Relationships ---------------------------------------------------------
    meeting: Mapped["Meeting"] = relationship("Meeting", back_populates="meeting_notes")

    __table_args__ = (
        Index("ix_meeting_notes_meeting_category", "meeting_id", "category"),
    )

    def __repr__(self) -> str:
        return f"<MeetingNote id={self.id} category={self.category}>"


from app.models.meeting import Meeting  # noqa: E402, F401
