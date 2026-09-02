"""PendingApproval model — tracks human-in-the-loop external action approvals."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow, uuid_pk


class PendingApproval(Base):
    __tablename__ = "pending_approvals"

    id: Mapped[uuid.UUID] = uuid_pk()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # 'jira' | 'slack' | 'pagerduty'
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True)  # 'pending' | 'approved' | 'rejected'
    execution_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=True, default=None)  # Jira issue key, URL, etc.
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(default=None, nullable=True)

    def __repr__(self) -> str:
        return f"<PendingApproval id={self.id} type={self.action_type} status={self.status}>"
