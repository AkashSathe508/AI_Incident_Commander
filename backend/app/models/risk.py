"""Risk model."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class Risk(Base):
    __tablename__ = "risks"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # severity: low | medium | high | critical
    severity: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # likelihood: low | medium | high
    likelihood: Mapped[str] = mapped_column(String(50), nullable=False)
    # status: open | mitigated | accepted | closed
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="open", index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # ── Relationships ─────────────────────────────────────────────────────────
    meeting: Mapped["Meeting"] = relationship("Meeting", back_populates="risks")

    __table_args__ = (
        Index("ix_risks_meeting_severity_status", "meeting_id", "severity", "status"),
    )

    def __repr__(self) -> str:
        return f"<Risk id={self.id} severity={self.severity} status={self.status}>"


from app.models.meeting import Meeting  # noqa: E402, F401
