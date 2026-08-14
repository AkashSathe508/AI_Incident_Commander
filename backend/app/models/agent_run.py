"""AgentRun model — tracks every LLM agent invocation."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # status: pending | running | completed | failed
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending", index=True
    )
    input_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # ── Relationships ─────────────────────────────────────────────────────────
    meeting: Mapped["Meeting | None"] = relationship(
        "Meeting", back_populates="agent_runs"
    )

    __table_args__ = (
        Index("ix_agent_runs_agent_status", "agent_name", "status"),
        Index("ix_agent_runs_meeting_agent", "meeting_id", "agent_name"),
    )

    def __repr__(self) -> str:
        return f"<AgentRun id={self.id} agent={self.agent_name} status={self.status}>"


from app.models.meeting import Meeting  # noqa: E402, F401
