"""
pgvector Embedding Generator & Database Writer.

Generates 1536-dimensional embeddings (768-dim Gemini text-embedding-004 padded to 1536)
and persists vectors to PostgreSQL `embeddings` table with pgvector extension.

Explicitly embeds:
- Verified Facts
- Verified Decisions
- Transcript Segments
(Guaranteed NO assumptions to keep retrieval clean)
"""

import logging
import os
import uuid
from typing import Any

import httpx
from sqlalchemy import create_engine, text

from app.config.settings import settings

logger = logging.getLogger(__name__)

TARGET_DIM = 1536


def generate_embedding(text_content: str) -> list[float]:
    """
    Generates a 1536-dimensional float vector for input text content using Gemini API.
    """
    if not text_content or not text_content.strip():
        return [0.0] * TARGET_DIM

    gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        logger.debug("GEMINI_API_KEY not set — returning zero-vector placeholder")
        return [0.0] * TARGET_DIM

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={gemini_key}"
    payload = {
        "model": "models/gemini-embedding-001",
        "content": {"parts": [{"text": text_content.strip()}]},
    }

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                embedding_values = data.get("embedding", {}).get("values", [])
                if embedding_values:
                    # Pad 768-dim vector up to 1536 for pgvector schema compatibility
                    if len(embedding_values) < TARGET_DIM:
                        embedding_values.extend([0.0] * (TARGET_DIM - len(embedding_values)))
                    return embedding_values[:TARGET_DIM]
            else:
                logger.warning("Gemini embedding API error HTTP %d: %s", resp.status_code, resp.text[:100])
    except Exception as exc:
        logger.warning("Failed to generate Gemini embedding: %s", exc)

    return [0.0] * TARGET_DIM


def store_entity_embedding(
    meeting_id: str,
    source_type: str,  # 'fact' | 'decision' | 'transcript' (NEVER 'assumption')
    source_id: str,
    text_content: str,
) -> bool:
    """
    Stores vector embedding for a verified fact, decision, or transcript segment.
    """
    if source_type == "assumption":
        logger.debug("Skipping embedding for assumption as per clean retrieval policy")
        return False

    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return False

    vector = generate_embedding(text_content)
    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    try:
        emb_id = uuid.uuid4()
        src_uuid = uuid.UUID(source_id)

        with engine.connect() as conn:
            # Check if embedding already exists
            check_sql = text("SELECT id FROM embeddings WHERE source_type = :st AND source_id = :sid LIMIT 1")
            existing = conn.execute(check_sql, {"st": source_type, "sid": src_uuid}).first()
            if existing:
                engine.dispose()
                return True

            sql = text("""
                INSERT INTO embeddings (id, source_type, source_id, vector, model, created_at)
                VALUES (:id, :source_type, :source_id, :vector, :model, NOW())
            """)
            conn.execute(
                sql,
                {
                    "id": emb_id,
                    "source_type": source_type,
                    "source_id": src_uuid,
                    "vector": str(vector),
                    "model": "text-embedding-004",
                },
            )
            conn.commit()
        logger.info("[EMBEDDING] Persisted pgvector embedding for %s:%s", source_type, source_id[:8])
        return True
    except Exception as exc:
        logger.warning("Failed to store entity embedding: %s", exc)
        return False
    finally:
        engine.dispose()
