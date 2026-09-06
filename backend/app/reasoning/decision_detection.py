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
    decision_type: str = Field(default="technical", description="One of: rollback | escalation | delegation | technical | process | other")


def detect_decisions_node(state: MeetingState) -> dict[str, Any]:
    """
    LangGraph node for Decision Detection.
    Takes latest transcript segment, extracts decisions, saves to PostgreSQL `decisions` and `evidence`.
    """
    meeting_id = state.get("meeting_id")
    latest_segment = state.get("latest_segment")

    logger.info(
        "[DECISION DETECTION] Node entered — meeting=%s segment_text='%s'",
        (meeting_id or "")[:8],
        (latest_segment or {}).get("text", "")[:60],
    )

    if not meeting_id or not latest_segment:
        return {"decisions": [], "evidence": []}

    segment_text = latest_segment.get("text", "").strip()
    segment_id_str = latest_segment.get("id")
    speaker_name = latest_segment.get("speaker_name", "Speaker")

    if not segment_text or len(segment_text) < 4:
        return {"decisions": [], "evidence": []}

    groq_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
    gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    extracted: list[ExtractedDecision] = []

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
                "You are an AI Incident Commander decision detection engine. "
                "Analyze the spoken utterance from an incident call and extract "
                "DECISIONS MADE, PROPOSED, AGREED UPON, OR COMMITTED TO.\n\n"
                "Be INCLUSIVE — extract both formal decisions and implied commitments.\n"
                "EXAMPLES:\n"
                "- 'We agreed to rollback to v2.4' → rollback decision\n"
                "- 'Let\'s restart the Redis cluster' → technical decision\n"
                "- 'I\'m escalating this to the VP of Engineering' → escalation decision\n"
                "- 'John will handle the database fix' → delegation decision\n"
                "- 'Going with option A' → technical decision\n"
                "- 'We\'ll switch to the backup service' → process decision\n\n"
                "DECISION TYPES:\n"
                "- rollback: revert a deployment or change\n"
                "- escalation: bring in higher authority or on-call\n"
                "- delegation: assign work to a specific person\n"
                "- technical: technical action or configuration change\n"
                "- process: workflow or communication change\n"
                "- other: any other clear decision\n\n"
                "Return JSON: {\"decisions\": [{\"statement\": \"...\", \"rationale\": \"...\", \"owner_name\": \"...\", \"decision_type\": \"technical\"}]}\n"
                "If NO decisions found, return: {\"decisions\": []}"
            )

            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Speaker ({speaker_name}): \"{segment_text}\"")
            ])

            logger.info("[DECISION DETECTION] Raw LLM response: %s", str(response.content)[:300])

            from app.reasoning.parser import parse_llm_json
            parsed = parse_llm_json(response.content)
            for item in parsed.get("decisions", []):
                if item.get("statement"):
                    extracted.append(
                        ExtractedDecision(
                            statement=item["statement"],
                            rationale=item.get("rationale"),
                            owner_name=item.get("owner_name") or speaker_name,
                            decision_type=item.get("decision_type", "technical"),
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

    # Store AI response for history
    if new_decisions:
        try:
            from app.reasoning.ai_response_store import store_ai_response
            dec_summary = "; ".join(d.get("content", "") for d in new_decisions[:2])
            ai_text = f"Decision detected: {dec_summary}"
            store_ai_response(
                meeting_id=meeting_id,
                response_text=ai_text,
                response_type="recommendation",
                trigger="decision_detected",
                related_action_ids=[d.get("id") for d in new_decisions],
            )
        except Exception as _exc:
            logger.debug("[DECISION DETECTION] AI response store failed: %s", _exc)

    return {"decisions": new_decisions, "evidence": new_evidence}


def _is_likely_decision(text_content: str) -> bool:
    keywords = [
        "agreed", "decided", "let's rollback", "let's restart", "we will deploy",
        "going with option", "decision:", "i'm going to", "we should", "going to",
        "let's go with", "we'll", "switching to", "rolling back", "restarting",
        "approve", "reject", "confirmed", "finalized",
    ]
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
