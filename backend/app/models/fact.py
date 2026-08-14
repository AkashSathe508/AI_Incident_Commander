"""Fact model."""

import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_segment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transcript_segments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # ── Relationships ─────────────────────────────────────────────────────────
    meeting: Mapped["Meeting"] = relationship("Meeting", back_populates="facts")
    source_segment: Mapped["TranscriptSegment | None"] = relationship(
        "TranscriptSegment", back_populates="facts"
    )

    __table_args__ = (
        Index("ix_facts_meeting_confidence", "meeting_id", "confidence"),
    )

    def __repr__(self) -> str:
        return f"<Fact id={self.id} confidence={self.confidence}>"


from app.models.meeting import Meeting  # noqa: E402, F401
from app.models.transcript_segment import TranscriptSegment  # noqa: E402, F401
