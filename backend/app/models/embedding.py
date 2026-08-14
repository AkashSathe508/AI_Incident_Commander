"""Embedding model — stores pgvector embeddings for any source entity."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow, uuid_pk

VECTOR_DIM = 1536  # Gemini / OpenAI compatible


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[uuid.UUID] = uuid_pk()
    # Polymorphic reference — can point to any entity type
    source_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    vector: Mapped[list[float]] = mapped_column(Vector(VECTOR_DIM), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    __table_args__ = (
        # Composite index for polymorphic lookups
        Index("ix_embeddings_source", "source_type", "source_id"),
        # IVFFlat ANN index for pgvector similarity search
        Index(
            "ix_embeddings_vector_ivfflat",
            "vector",
            postgresql_using="ivfflat",
            postgresql_ops={"vector": "vector_cosine_ops"},
            postgresql_with={"lists": "100"},
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Embedding id={self.id} "
            f"source={self.source_type}:{self.source_id} model={self.model}>"
        )
