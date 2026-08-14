"""ActionItem model."""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class ActionItem(Base):
    __tablename__ = "action_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("participants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # status: open | in_progress | completed | cancelled
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="open", index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # ── Relationships ─────────────────────────────────────────────────────────
    meeting: Mapped["Meeting"] = relationship("Meeting", back_populates="action_items")
    assignee: Mapped["Participant | None"] = relationship(
        "Participant", back_populates="assigned_action_items"
    )

    __table_args__ = (
        Index("ix_action_items_meeting_status", "meeting_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<ActionItem id={self.id} status={self.status}>"


from app.models.meeting import Meeting  # noqa: E402, F401
from app.models.participant import Participant  # noqa: E402, F401
