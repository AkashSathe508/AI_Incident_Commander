"""
Fact Extraction Node.

Analyzes incoming transcript segments using Google Gemini to extract verifiable,
objective factual claims (explicitly excluding opinions, guesses, predictions).
Persists extracted facts to PostgreSQL `facts` table and links evidence in `evidence` table.
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


class ExtractedFact(BaseModel):
    statement: str = Field(description="Objective factual claim extracted from transcript")
    confidence: float = Field(default=0.9, description="Confidence score between 0.0 and 1.0")


class FactExtractionResponse(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)


def extract_facts_node(state: MeetingState) -> dict[str, Any]:
    """
    LangGraph node for Fact Extraction.
    Takes latest transcript segment from state, calls Gemini (or rule-based fallback),
    saves facts to `facts` DB table, and saves link to `evidence` DB table.
    """
    meeting_id = state.get("meeting_id")
    latest_segment = state.get("latest_segment")

    if not meeting_id or not latest_segment:
        return {"facts": [], "evidence": []}

    segment_text = latest_segment.get("text", "").strip()
    segment_id_str = latest_segment.get("id")
    speaker_name = latest_segment.get("speaker_name", "Speaker")

    if not segment_text or len(segment_text) < 4:
        return {"facts": [], "evidence": []}

    groq_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
    gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    extracted_facts: list[ExtractedFact] = []

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
                    model="gemini-3.5-flash-lite",
                    google_api_key=gemini_key,
                    temperature=0.0,
                    max_retries=0,
                )

            system_prompt = (
                "You are an AI Incident Commander fact extraction engine. "
                "Analyze the spoken utterance from an incident response call and extract "
                "EXPLICIT, OBJECTIVE FACTUAL CLAIMS ONLY.\n\n"
                "RULES:\n"
                "1. ONLY extract clear, objective factual claims (e.g. error codes, system metrics, outage reports, status).\n"
                "2. EXPLICITLY DO NOT include opinions, predictions, guesses, subjective feeling, or speculative comments.\n"
                "3. If no clear factual claim is made, return an empty list.\n"
                "4. Return response in JSON format: {\"facts\": [{\"statement\": \"...\", \"confidence\": 0.95}]}"
            )

            user_prompt = f"Speaker ({speaker_name}): \"{segment_text}\""

            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ])

            from app.reasoning.parser import parse_llm_json
            parsed = parse_llm_json(response.content)
            for item in parsed.get("facts", []):
                if item.get("statement"):
                    extracted_facts.append(
                        ExtractedFact(
                            statement=item["statement"],
                            confidence=float(item.get("confidence", 0.95)),
                        )
                    )
        except Exception as exc:
            logger.warning("Gemini LLM fact extraction failed, falling back to rule heuristic: %s", exc)

    # Heuristic fallback if LLM is unavailable or failed
    if not extracted_facts and _is_likely_fact(segment_text):
        extracted_facts.append(
            ExtractedFact(statement=segment_text, confidence=0.85)
        )

    if not extracted_facts:
        return {"facts": [], "evidence": []}

    # Persist facts and evidence to PostgreSQL
    new_facts_state, new_evidence_state = _save_facts_to_db(
        meeting_id=meeting_id,
        segment_id_str=segment_id_str,
        segment_text=segment_text,
        facts=extracted_facts,
    )

    return {
        "facts": new_facts_state,
        "evidence": new_evidence_state,
    }


def _is_likely_fact(text_content: str) -> bool:
    """Fallback heuristic to identify probable factual claims in incident calls."""
    keywords = [
        "500", "502", "503", "404", "error", "down", "failing", "crash",
        "latency", "timeout", "cpu", "memory", "database", "api", "status",
        "returned", "returning", "deploy", "service", "cluster", "outage"
    ]
    lowered = text_content.lower()
    return any(kw in lowered for kw in keywords)


def _save_facts_to_db(
    meeting_id: str,
    segment_id_str: str | None,
    segment_text: str,
    facts: list[ExtractedFact],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Persists extracted facts to `facts` table and `evidence` join table."""
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        logger.warning("DATABASE_URL not set — facts will not be saved to DB")
        return [], []

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    created_facts = []
    created_evidence = []
    now_utc = datetime.now(timezone.utc)

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        segment_uuid = uuid.UUID(segment_id_str) if segment_id_str else None

        with engine.connect() as conn:
            for fact in facts:
                fact_id = uuid.uuid4()
                evidence_id = uuid.uuid4()

                # 1. Insert into facts table
                fact_sql = text("""
                    INSERT INTO facts (id, meeting_id, source_segment_id, content, confidence, created_at)
                    VALUES (:id, :meeting_id, :source_segment_id, :content, :confidence, :created_at)
                """)
                conn.execute(
                    fact_sql,
                    {
                        "id": fact_id,
                        "meeting_id": meeting_uuid,
                        "source_segment_id": segment_uuid,
                        "content": fact.statement,
                        "confidence": fact.confidence,
                        "created_at": now_utc,
                    },
                )

                # 2. Insert into evidence table
                evidence_sql = text("""
                    INSERT INTO evidence (id, meeting_id, content, source_type, source_id, created_at)
                    VALUES (:id, :meeting_id, :content, :source_type, :source_id, :created_at)
                """)
                conn.execute(
                    evidence_sql,
                    {
                        "id": evidence_id,
                        "meeting_id": meeting_uuid,
                        "content": f"Fact: {fact.statement} (From: '{segment_text[:100]}')",
                        "source_type": "transcript",
                        "source_id": str(segment_uuid) if segment_uuid else str(fact_id),
                        "created_at": now_utc,
                    },
                )

                created_facts.append({
                    "id": str(fact_id),
                    "meeting_id": meeting_id,
                    "source_segment_id": str(segment_uuid) if segment_uuid else None,
                    "content": fact.statement,
                    "confidence": fact.confidence,
                })

                created_evidence.append({
                    "id": str(evidence_id),
                    "meeting_id": meeting_id,
                    "content": fact.statement,
                    "source_type": "transcript",
                    "source_id": str(segment_uuid) if segment_uuid else None,
                })

            conn.commit()

            # Broadcast created facts to WebSocket clients
            from app.ws.manager import broadcast_event_sync
            for cf in created_facts:
                broadcast_event_sync(meeting_id, "fact_created", cf, created_evidence)

        logger.info(
            "[FACT EXTRACTION] Extracted and saved %d facts for meeting %s",
            len(created_facts),
            meeting_id[:8],
        )
    except Exception as exc:
        logger.error("Failed to save extracted facts to DB: %s", exc)
    finally:
        engine.dispose()

    return created_facts, created_evidence
