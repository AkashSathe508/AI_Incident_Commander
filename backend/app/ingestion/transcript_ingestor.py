"""
Transcript Ingestion Service.

Normalizes incoming Deepgram speech-to-text segments, deduplicates on
(meeting_id, speaker_id/uid, start_ms), persists to PostgreSQL, and broadcasts
the segment to all connected WebSocket clients in real-time.
"""

import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)


class TranscriptIngestor:
    """
    Ingests, deduplicates, persists, and broadcasts real-time transcript segments.
    """

    def __init__(self) -> None:
        # In-memory deduplication cache: set of (meeting_id_str, speaker_key, start_ms)
        self._seen_segments: set[tuple[str, str, int]] = set()
        # Cached sync engine — created lazily and reused across calls
        self._engine = None

    def _get_engine(self, sync_url: str):
        """Return a cached SQLAlchemy sync engine, creating it on first use."""
        if self._engine is None:
            from sqlalchemy import create_engine
            self._engine = create_engine(sync_url, pool_pre_ping=True)
        return self._engine

    def process_and_save(
        self,
        meeting_id: str,
        speaker_id: str,  # Agora UID or Participant UUID as string
        text_content: str,
        start_ms: int,
        end_ms: int,
        confidence: float | None = 1.0,
        speaker_name: str | None = None,
        participant_uuid: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Processes a transcript segment:
        1. Clean and validate text
        2. Check deduplication key (meeting_id, speaker_id, start_ms)
        3. Insert into PostgreSQL transcript_segments table
        4. Return dict payload ready for WebSocket broadcast
        """
        clean_text = text_content.strip()
        if not clean_text:
            return None

        meeting_str = str(meeting_id)
        dedup_key = (meeting_str, str(speaker_id), int(start_ms))

        # Check in-memory deduplication cache first
        if dedup_key in self._seen_segments:
            logger.debug("Deduplicated in-memory segment: %s", dedup_key)
            return None

        self._seen_segments.add(dedup_key)

        # Limit cache size to prevent memory bloat (keep last 10,000 segments)
        if len(self._seen_segments) > 10_000:
            # Clear older half arbitrarily or pop
            self._seen_segments = set(list(self._seen_segments)[5_000:])

        segment_id = uuid.uuid4()
        now_utc = datetime.now(timezone.utc)

        # Prepare DB insert
        from app.config.settings import settings
        raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
        if not raw_url:
            logger.warning("DATABASE_URL not set — transcript segment will not be persisted")
            return {
                "type": "transcript_segment",
                "id": str(segment_id),
                "meeting_id": meeting_str,
                "speaker_id": str(speaker_id),
                "speaker_name": speaker_name or f"Speaker {speaker_id}",
                "participant_id": participant_uuid,
                "text": clean_text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "confidence": confidence,
                "created_at": now_utc.isoformat(),
            }

        sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

        try:
            part_id_uuid = uuid.UUID(participant_uuid) if participant_uuid else None
        except ValueError:
            part_id_uuid = None

        try:
            engine = self._get_engine(sync_url)
            with engine.connect() as conn:
                # DB-level deduplication check
                check_sql = text("""
                    SELECT id FROM transcript_segments
                    WHERE meeting_id = :meeting_id AND start_ms = :start_ms AND text = :text
                    LIMIT 1
                """)
                existing = conn.execute(
                    check_sql,
                    {
                        "meeting_id": uuid.UUID(meeting_str),
                        "start_ms": start_ms,
                        "text": clean_text,
                    },
                ).first()

                if existing:
                    logger.info("Deduplicated DB segment for meeting %s at %d ms", meeting_str[:8], start_ms)
                    return None

                insert_sql = text("""
                    INSERT INTO transcript_segments
                    (id, meeting_id, participant_id, text, start_ms, end_ms, confidence, created_at)
                    VALUES
                    (:id, :meeting_id, :participant_id, :text, :start_ms, :end_ms, :confidence, :created_at)
                """)
                conn.execute(
                    insert_sql,
                    {
                        "id": segment_id,
                        "meeting_id": uuid.UUID(meeting_str),
                        "participant_id": part_id_uuid,
                        "text": clean_text,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "confidence": confidence,
                        "created_at": now_utc,
                    },
                )
                conn.commit()
            logger.info(
                "[INGEST] Saved transcript segment meeting=%s speaker=%s text='%s'",
                meeting_str[:8],
                speaker_name or speaker_id,
                clean_text[:40],
            )
        except Exception as exc:
            logger.error("Failed to persist transcript segment to DB: %s", exc)

        # Store pgvector embedding for transcript segment
        try:
            from app.reasoning.embedding import store_entity_embedding
            store_entity_embedding(meeting_str, "transcript", str(segment_id), clean_text)
        except Exception as exc:
            logger.debug("Transcript segment embedding failed: %s", exc)

        payload = {
            "type": "transcript_segment",
            "id": str(segment_id),
            "meeting_id": meeting_str,
            "speaker_id": str(speaker_id),
            "speaker_name": speaker_name or f"Speaker {speaker_id}",
            "participant_id": participant_uuid,
            "text": clean_text,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "confidence": confidence,
            "created_at": now_utc.isoformat(),
        }

        # 5. Trigger LangGraph full reasoning pipeline in a background thread so the
        #    HTTP response is returned immediately — eliminating STT lag caused by
        #    blocking on multiple sequential Groq LLM calls before ack-ing the segment.
        def _run_pipeline() -> None:
            try:
                from app.graph.workflow import run_reasoning_pipeline
                run_reasoning_pipeline(meeting_str, payload)
            except Exception as exc:
                logger.warning("Error running reasoning graph pipeline: %s", exc)

        t = threading.Thread(target=_run_pipeline, daemon=True, name=f"graph-{meeting_str[:8]}")
        t.start()

        return payload


# Ingestor instance
transcript_ingestor = TranscriptIngestor()
