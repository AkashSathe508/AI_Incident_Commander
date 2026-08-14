"""
Decision Detection Node.

Detects decisions made, proposed, rejected, or pending during an incident call,
attributing the owner / decision maker where applicable.
Persists decisions to PostgreSQL `decisions` table and links evidence in `evidence`.
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


class ExtractedDecision(BaseModel):
    statement: str = Field(description="Decision statement or agreement made")
    rationale: str | None = Field(default=None, description="Rationale or reason for decision")
    owner_name: str | None = Field(default=None, description="Name or role of person making decision")


def detect_decisions_node(state: MeetingState) -> dict[str, Any]:
    """
    LangGraph node for Decision Detection.
    Takes latest transcript segment, extracts decisions, saves to PostgreSQL `decisions` and `evidence`.
    """
    meeting_id = state.get("meeting_id")
    latest_segment = state.get("latest_segment")

    if not meeting_id or not latest_segment:
        return {"decisions": [], "evidence": []}

    segment_text = latest_segment.get("text", "").strip()
    segment_id_str = latest_segment.get("id")
    speaker_name = latest_segment.get("speaker_name", "Speaker")

    if not segment_text or len(segment_text) < 4:
        return {"decisions": [], "evidence": []}

    gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    extracted: list[ExtractedDecision] = []

    if gemini_key:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import SystemMessage, HumanMessage

            llm = ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                google_api_key=gemini_key,
                temperature=0.0,
            )

            system_prompt = (
                "You are an AI Incident Commander decision detection engine. "
                "Analyze the spoken utterance from an incident call and extract "
                "DECISIONS MADE, PROPOSED, OR AGREED UPON.\n\n"
                "Examples: 'We agreed to rollback to v2.4', 'Let's restart the Redis cluster', "
                "'Decision: scale out the pod replicas to 10'.\n"
                "Return response JSON: {\"decisions\": [{\"statement\": \"...\", \"rationale\": \"...\", \"owner_name\": \"...\"}]}"
            )

            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Speaker ({speaker_name}): \"{segment_text}\"")
            ])

            content = response.content
            if isinstance(content, str):
                clean_json = re.sub(r"```json\s*|\s*```", "", content).strip()
                parsed = json.loads(clean_json)
                for item in parsed.get("decisions", []):
                    if item.get("statement"):
                        extracted.append(
                            ExtractedDecision(
                                statement=item["statement"],
                                rationale=item.get("rationale"),
                                owner_name=item.get("owner_name") or speaker_name,
                            )
                        )
        except Exception as exc:
            logger.warning("Gemini LLM decision detection failed, falling back to rule heuristic: %s", exc)

    # Heuristic fallback
    if not extracted and _is_likely_decision(segment_text):
        extracted.append(
            ExtractedDecision(
                statement=segment_text,
                rationale="Inferred from statement context",
                owner_name=speaker_name,
            )
        )

    if not extracted:
        return {"decisions": [], "evidence": []}

    new_decisions, new_evidence = _save_decisions_to_db(
        meeting_id=meeting_id,
        segment_id_str=segment_id_str,
        segment_text=segment_text,
        items=extracted,
    )

    return {"decisions": new_decisions, "evidence": new_evidence}


def _is_likely_decision(text_content: str) -> bool:
    keywords = ["agreed", "decided", "let's rollback", "let's restart", "we will deploy", "going with option", "decision:"]
    lowered = text_content.lower()
    return any(kw in lowered for kw in keywords)


def _save_decisions_to_db(
    meeting_id: str,
    segment_id_str: str | None,
    segment_text: str,
    items: list[ExtractedDecision],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return [], []

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    created_decisions = []
    created_evidence = []
    now_utc = datetime.now(timezone.utc)

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        segment_uuid = uuid.UUID(segment_id_str) if segment_id_str else None

        with engine.connect() as conn:
            for item in items:
                decision_id = uuid.uuid4()
                evidence_id = uuid.uuid4()

                # Insert into decisions table
                sql_d = text("""
                    INSERT INTO decisions (id, meeting_id, content, rationale, created_at)
                    VALUES (:id, :meeting_id, :content, :rationale, :created_at)
                """)
                conn.execute(
                    sql_d,
                    {
                        "id": decision_id,
                        "meeting_id": meeting_uuid,
                        "content": item.statement,
                        "rationale": item.rationale,
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
                        "content": f"Decision: {item.statement} (From: '{segment_text[:100]}')",
                        "source_type": "transcript",
                        "source_id": str(segment_uuid) if segment_uuid else str(decision_id),
                        "created_at": now_utc,
                    },
                )

                created_decisions.append({
                    "id": str(decision_id),
                    "meeting_id": meeting_id,
                    "content": item.statement,
                    "rationale": item.rationale,
                    "owner_name": item.owner_name,
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
            for cd in created_decisions:
                broadcast_event_sync(meeting_id, "decision_created", cd, created_evidence)

        logger.info("[DECISION DETECTION] Saved %d decisions for meeting %s", len(created_decisions), meeting_id[:8])
    except Exception as exc:
        logger.error("Failed to save decisions to DB: %s", exc)
    finally:
        engine.dispose()

    return created_decisions, created_evidence
