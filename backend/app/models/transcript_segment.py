"""TranscriptSegment model."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Float, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    participant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("participants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # ── Relationships ─────────────────────────────────────────────────────────
    meeting: Mapped["Meeting"] = relationship(
        "Meeting", back_populates="transcript_segments"
    )
    participant: Mapped["Participant | None"] = relationship(
        "Participant", back_populates="transcript_segments"
    )
    facts: Mapped[list["Fact"]] = relationship(
        "Fact", back_populates="source_segment"
    )

    __table_args__ = (
        Index("ix_transcript_segments_meeting_time", "meeting_id", "start_ms"),
    )

    def __repr__(self) -> str:
        return (
            f"<TranscriptSegment id={self.id} "
            f"start_ms={self.start_ms} end_ms={self.end_ms}>"
        )


from app.models.meeting import Meeting  # noqa: E402, F401
from app.models.participant import Participant  # noqa: E402, F401
from app.models.fact import Fact  # noqa: E402, F401
