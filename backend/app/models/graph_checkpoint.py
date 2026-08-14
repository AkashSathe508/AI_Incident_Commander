"""GraphCheckpoint model — persists LangGraph state machine checkpoints."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class GraphCheckpoint(Base):
    __tablename__ = "graph_checkpoints"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # thread_id corresponds to the LangGraph thread identifier
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    checkpoint_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # ── Relationships ─────────────────────────────────────────────────────────
    meeting: Mapped["Meeting | None"] = relationship(
        "Meeting", back_populates="graph_checkpoints"
    )

    __table_args__ = (
        Index(
            "ix_graph_checkpoints_thread_created", "thread_id", "created_at"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GraphCheckpoint id={self.id} thread_id={self.thread_id}>"
        )


from app.models.meeting import Meeting  # noqa: E402, F401
