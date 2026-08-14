"""Conflict model."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class Conflict(Base):
    __tablename__ = "conflicts"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # status: open | resolved | escalated
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="open", index=True
    )
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # ── Relationships ─────────────────────────────────────────────────────────
    meeting: Mapped["Meeting"] = relationship("Meeting", back_populates="conflicts")

    __table_args__ = (
        Index("ix_conflicts_meeting_status", "meeting_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<Conflict id={self.id} status={self.status}>"


from app.models.meeting import Meeting  # noqa: E402, F401
