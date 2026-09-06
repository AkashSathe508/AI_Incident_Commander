from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID
import uuid

from .base import Base

class UserPreferences(Base):
    __tablename__ = "user_preferences"

    id: UUID = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: str = Column(String, nullable=False, unique=True, index=True)
    mode: str = Column(String, nullable=False, default="occasional")  # "frequent" or "occasional"
    is_muted: bool = Column(Boolean, nullable=False, default=False)
