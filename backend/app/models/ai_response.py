"""AIResponse model — persists actual AI Incident Commander responses."""

import uuid
from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow, uuid_pk


class AIResponse(Base):
    """Stores every AI Incident Commander response generated during a meeting.

    Distinguishes between:
      - analysis      : AI's reading of current state / facts
      - recommendation: AI-proposed action
      - summary       : periodic or final synthesis
      - alert         : urgent AI-generated alert
    """

    __tablename__ = "ai_responses"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The actual text produced by the AI
    response_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Type of response: analysis | recommendation | summary | alert
    response_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="analysis", index=True
    )

    # What triggered this response (e.g. "fact_extracted", "conflict_detected")
    trigger: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # JSON arrays of related entity UUIDs
    related_fact_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    related_assumption_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    related_action_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)

    # Human-in-the-loop approval tracking
    # approval_status: none | pending | approved | rejected
    approval_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="none", index=True
    )
    # FK to pending_approvals if a formal approval was created
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Execution tracking: none | executing | done | failed
    execution_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="none"
    )

    # Optional Jira ticket reference after execution
    jira_ticket_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Confidence score (0.0 - 1.0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    # -- Relationships ---------------------------------------------------------
    meeting: Mapped["Meeting"] = relationship("Meeting", back_populates="ai_responses")

    __table_args__ = (
        Index("ix_ai_responses_meeting_type", "meeting_id", "response_type"),
        Index("ix_ai_responses_meeting_created", "meeting_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AIResponse id={self.id} type={self.response_type}>"


from app.models.meeting import Meeting  # noqa: E402, F401
