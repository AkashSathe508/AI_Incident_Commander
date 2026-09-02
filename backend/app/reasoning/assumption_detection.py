"""
Assumption Detection Node.

Extracts unverified assumptions, predictions, guesses, hypotheses, and opinions from transcript text.
Explicitly configured NEVER to overlap with Fact Extraction outputs.
Persists assumptions to PostgreSQL `assumptions` table (status='pending') and links evidence in `evidence`.
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from app.config.settings import settings
from app.graph.state import MeetingState

logger = logging.getLogger(__name__)


class ExtractedAssumption(BaseModel):
    statement: str = Field(description="Speculative assumption, prediction, or opinion claim")
    status: str = Field(default="pending", description="Status: pending | confirmed | rejected")
    basis: str | None = Field(default=None, description="Stated basis or reasoning behind the assumption")
    owner_name: str | None = Field(default=None, description="Name of speaker making the assumption")


def detect_assumptions_node(state: MeetingState) -> dict[str, Any]:
    """
    LangGraph node for Assumption Detection.
    Takes latest transcript segment, extracts assumptions/predictions (no facts),
    saves to PostgreSQL `assumptions` and `evidence` tables.
    """
    meeting_id = state.get("meeting_id")
    latest_segment = state.get("latest_segment")

    if not meeting_id or not latest_segment:
        return {"assumptions": [], "evidence": []}

    segment_text = latest_segment.get("text", "").strip()
    segment_id_str = latest_segment.get("id")
    speaker_name = latest_segment.get("speaker_name", "Speaker")

    if not segment_text or len(segment_text) < 4:
        return {"assumptions": [], "evidence": []}

    groq_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
    gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    extracted: list[ExtractedAssumption] = []

    if groq_key or gemini_key:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            if groq_key:
                from langchain_groq import ChatGroq
                llm = ChatGroq(
                    model_name="llama-3.3-70b-versatile",
                    groq_api_key=groq_key,
                    temperature=0.0,
                )
            else:
                from langchain_google_genai import ChatGoogleGenerativeAI
                llm = ChatGoogleGenerativeAI(
                    model="gemini-2.0-flash",
                    google_api_key=gemini_key,
                    temperature=0.0,
                    max_retries=0,
                )

            system_prompt = (
                "You are an AI Incident Commander hypothesis detection engine. "
                "Analyze the spoken utterance from an incident call and extract "
                "UNVERIFIED ASSUMPTIONS, HYPOTHESES, PREDICTIONS, GUESSES, OR OPINIONS.\n\n"
                "MANDATE: Be INCLUSIVE. Extract even tentative hypotheses.\n"
                "EXAMPLES of what to extract:\n"
                "- 'I think it might be the database' → hypothesis\n"
                "- 'Probably caused by the latest release' → hypothesis\n"
                "- 'Maybe memory ran out' → hypothesis\n"
                "- 'It could be a network issue' → hypothesis\n"
                "- 'I suspect the config change caused this' → hypothesis\n"
                "- 'This looks like a deadlock to me' → hypothesis\n"
                "EXAMPLES of what NOT to extract:\n"
                "- 'API returned 500' → fact (not a hypothesis)\n"
                "- 'CPU is at 98%' → fact (not a hypothesis)\n"
                "Return JSON: {\"assumptions\": [{\"statement\": \"...\", \"status\": \"pending\", \"basis\": \"...\", \"owner_name\": \"...\"}]}\n"
                "If NO hypotheses found, return: {\"assumptions\": []}"
            )

            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Speaker ({speaker_name}): \"{segment_text}\"")
            ])

            from app.reasoning.parser import parse_llm_json
            parsed = parse_llm_json(response.content)
            for item in parsed.get("assumptions", []):
                if item.get("statement"):
                    extracted.append(
                        ExtractedAssumption(
                            statement=item["statement"],
                            status=item.get("status", "pending"),
                            basis=item.get("basis"),
                            owner_name=item.get("owner_name") or speaker_name,
                        )
                    )
        except Exception as exc:
            logger.warning("Gemini LLM assumption detection failed, falling back to rule heuristic: %s", exc)

    # Heuristic fallback if LLM is unavailable or failed
    if not extracted and _is_likely_assumption(segment_text):
        extracted.append(ExtractedAssumption(statement=segment_text, status="pending", owner_name=speaker_name, basis=None))

    if not extracted:
        return {"assumptions": [], "evidence": []}

    new_assumptions, new_evidence = _save_assumptions_to_db(
        meeting_id=meeting_id,
        segment_id_str=segment_id_str,
        segment_text=segment_text,
        items=extracted,
    )

    return {"assumptions": new_assumptions, "evidence": new_evidence}


def _is_likely_assumption(text_content: str) -> bool:
    """Fallback rule to catch speculative statements."""
    keywords = ["i think", "maybe", "probably", "guess", "might be", "could be", "assuming", "suppose", "suspect"]
    lowered = text_content.lower()
    return any(kw in lowered for kw in keywords)


def _save_assumptions_to_db(
    meeting_id: str,
    segment_id_str: str | None,
    segment_text: str,
    items: list[ExtractedAssumption],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return [], []

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    created_assumptions = []
    created_evidence = []
    now_utc = datetime.now(timezone.utc)

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        segment_uuid = uuid.UUID(segment_id_str) if segment_id_str else None

        with engine.connect() as conn:
            for item in items:
                assumption_id = uuid.uuid4()
                evidence_id = uuid.uuid4()

                # Insert into assumptions table
                sql_a = text("""
                    INSERT INTO assumptions (id, meeting_id, content, status, created_at)
                    VALUES (:id, :meeting_id, :content, :status, :created_at)
                """)
                conn.execute(
                    sql_a,
                    {
                        "id": assumption_id,
                        "meeting_id": meeting_uuid,
                        "content": item.statement,
                        "status": item.status,
                        "created_at": now_utc,
                    },
                )

                # Insert into evidence table
                sql_e = text("""
                    INSERT INTO evidence (id, meeting_id, content, source_type, source_id, created_at)
                    VALUES (:id, :meeting_id, :content, :source_type, :source_id, :created_at)
                """)
                conn.execute(
                    sql_e,
                    {
                        "id": evidence_id,
                        "meeting_id": meeting_uuid,
                        "content": f"Assumption: {item.statement} (From: '{segment_text[:100]}')",
                        "source_type": "transcript",
                        "source_id": str(segment_uuid) if segment_uuid else str(assumption_id),
                        "created_at": now_utc,
                    },
                )

                created_assumptions.append({
                    "id": str(assumption_id),
                    "meeting_id": meeting_id,
                    "content": item.statement,
                    "status": item.status,
                })
                created_evidence.append({
                    "id": str(evidence_id),
                    "meeting_id": meeting_id,
                    "content": item.statement,
                    "source_type": "transcript",
                    "source_id": str(segment_uuid) if segment_uuid else None,
                })

            conn.commit()

            from app.ws.manager import broadcast_event_sync
            for ca in created_assumptions:
                broadcast_event_sync(meeting_id, "assumption_created", ca, created_evidence)

        logger.info("[ASSUMPTION DETECTION] Saved %d assumptions for meeting %s", len(created_assumptions), meeting_id[:8])
    except Exception as exc:
        logger.error("Failed to save assumptions to DB: %s", exc)
    finally:
        engine.dispose()

    return created_assumptions, created_evidence
