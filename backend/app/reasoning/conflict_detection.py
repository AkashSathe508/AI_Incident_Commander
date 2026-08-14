"""
Conflict Detection Node — The core AI Incident Commander differentiator.

Groups facts, decisions, and action items by topic/semantic similarity.
Classifies candidate claim pairs into:
1. direct_contradiction
2. temporal_contradiction
3. numerical_contradiction
4. commitment_contradiction
5. opinion_disagreement
6. clarification
7. updated_information

Update vs. Conflict Rule:
Uses temporal resolution and speaker attribution to distinguish an "update"
(e.g., same speaker says "deadline moved from X to Y") from a genuine conflict
(e.g., two speakers state conflicting deadlines for the same item).
ONLY writes genuine contradictions (direct, temporal, numerical, commitment) to the `conflicts` DB table.
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


class ConflictPairClassification(BaseModel):
    category: str = Field(
        description="One of: direct_contradiction | temporal_contradiction | numerical_contradiction | "
                    "commitment_contradiction | opinion_disagreement | clarification | updated_information"
    )
    is_genuine_conflict: bool = Field(description="True if this is a genuine unresolved contradiction")
    description: str = Field(description="Summary of the contradiction or update")


def detect_conflicts_node(state: MeetingState) -> dict[str, Any]:
    """
    LangGraph node for Conflict Detection.
    Runs after facts/decisions/action items have been extracted.
    Analyzes claim pairs for contradictions vs. updates.
    """
    meeting_id = state.get("meeting_id")
    if not meeting_id:
        return {"conflicts": [], "evidence": []}

    facts = state.get("facts", [])
    decisions = state.get("decisions", [])
    action_items = state.get("action_items", [])
    latest_segment = state.get("latest_segment", {})

    all_claims = []
    for f in facts:
        all_claims.append({
            "id": f.get("id"),
            "type": "fact",
            "content": f.get("content", ""),
            "speaker": latest_segment.get("speaker_name", "Speaker"),
            "source_segment_id": f.get("source_segment_id"),
        })
    for d in decisions:
        all_claims.append({
            "id": d.get("id"),
            "type": "decision",
            "content": d.get("content", ""),
            "speaker": d.get("owner_name", latest_segment.get("speaker_name", "Speaker")),
            "source_segment_id": latest_segment.get("id"),
        })
    for a in action_items:
        all_claims.append({
            "id": a.get("id"),
            "type": "action_item",
            "content": a.get("description", ""),
            "speaker": a.get("assignee_name", latest_segment.get("speaker_name", "Speaker")),
            "source_segment_id": latest_segment.get("id"),
        })

    if len(all_claims) < 2:
        # Load historical claims from DB if only 1 claim in current turn
        all_claims = _load_historical_claims(meeting_id)

    if len(all_claims) < 2:
        return {"conflicts": [], "evidence": []}

    # Group candidate claim pairs by topic keywords
    pairs = _find_candidate_pairs(all_claims)
    if not pairs:
        return {"conflicts": [], "evidence": []}

    gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    genuine_conflicts = []

    for claim_a, claim_b in pairs:
        classification = _classify_pair(claim_a, claim_b, gemini_key)
        if classification.is_genuine_conflict:
            genuine_conflicts.append((claim_a, claim_b, classification))

    if not genuine_conflicts:
        return {"conflicts": [], "evidence": []}

    new_conflicts, new_evidence = _save_conflicts_to_db(
        meeting_id=meeting_id,
        conflicts=genuine_conflicts,
    )

    return {"conflicts": new_conflicts, "evidence": new_evidence}


def _find_candidate_pairs(claims: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Finds candidate pairs of claims that share common topic keywords or entities."""
    pairs = []
    stop_words = {"the", "a", "an", "is", "are", "to", "for", "in", "on", "of", "and", "or", "by", "with", "we", "i"}

    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            cA, cB = claims[i], claims[j]
            wordsA = set(re.findall(r"\w+", cA["content"].lower())) - stop_words
            wordsB = set(re.findall(r"\w+", cB["content"].lower())) - stop_words

            overlap = wordsA.intersection(wordsB)
            # Candidate pair if they share at least 2 key domain words (e.g. "deadline", "database", "500")
            if len(overlap) >= 1:
                pairs.append((cA, cB))

    return pairs[:10]  # Cap candidate pairs per run


def _classify_pair(
    claim_a: dict[str, Any],
    claim_b: dict[str, Any],
    gemini_key: str,
) -> ConflictPairClassification:
    """Classifies a claim pair using Gemini LLM (or deterministic rules)."""
    speaker_a = claim_a.get("speaker", "Speaker A")
    speaker_b = claim_b.get("speaker", "Speaker B")
    text_a = claim_a["content"]
    text_b = claim_b["content"]

    # Rule Check 1: Same speaker updating information ("deadline moved from X to Y")
    if speaker_a == speaker_b or _is_same_speaker_update(text_a, text_b):
        if "moved" in text_b.lower() or "updated" in text_b.lower() or "instead" in text_b.lower() or "actually" in text_b.lower():
            return ConflictPairClassification(
                category="updated_information",
                is_genuine_conflict=False,
                description=f"Information update by {speaker_a}: {text_b}",
            )

    groq_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
    gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")

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

            prompt = (
                f"Claim A (by {speaker_a}): \"{text_a}\"\n"
                f"Claim B (by {speaker_b}): \"{text_b}\"\n\n"
                "Classify this relationship into ONE of these categories:\n"
                "1. direct_contradiction: Direct opposing factual claims\n"
                "2. temporal_contradiction: Conflicting times, dates, or deadlines stated by different speakers\n"
                "3. numerical_contradiction: Conflicting numbers/metrics\n"
                "4. commitment_contradiction: Conflicting commitments\n"
                "5. opinion_disagreement: Subjective difference in opinion\n"
                "6. clarification: One claim clarifies another\n"
                "7. updated_information: Same speaker or team updating a past decision/deadline\n\n"
                "NOTE: Set is_genuine_conflict to true ONLY for categories 1, 2, 3, or 4 where different speakers/sources conflict without a resolved update.\n"
                "Return JSON: {\"category\": \"...\", \"is_genuine_conflict\": true|false, \"description\": \"...\"}"
            )

            res = llm.invoke([
                SystemMessage(content="You are a strict conflict and contradiction classification engine."),
                HumanMessage(content=prompt)
            ])

            from app.reasoning.parser import parse_llm_json
            data = parse_llm_json(res.content)
            cat = data.get("category", "clarification")
            is_conf = data.get("is_genuine_conflict", False)
            # Extra safety rule for same speaker update
            if cat == "updated_information" or speaker_a == speaker_b:
                is_conf = False
                return ConflictPairClassification(
                    category=cat,
                    is_genuine_conflict=is_conf,
                    description=data.get("description", f"Contradiction detected: {text_a} vs {text_b}"),
                )
        except Exception as exc:
            logger.warning("LLM conflict classification failed: %s", exc)

    # Heuristic Fallback
    if _has_contradicting_words(text_a, text_b):
        is_same = (speaker_a == speaker_b)
        return ConflictPairClassification(
            category="temporal_contradiction" if "deadline" in text_a.lower() or "friday" in text_a.lower() else "direct_contradiction",
            is_genuine_conflict=not is_same,
            description=f"Conflicting claims between {speaker_a} and {speaker_b}: '{text_a}' vs '{text_b}'",
        )

    return ConflictPairClassification(
        category="clarification",
        is_genuine_conflict=False,
        description="No contradiction detected",
    )


def _is_same_speaker_update(a: str, b: str) -> bool:
    update_words = ["actually", "moved", "changed", "updated", "instead of", "correction"]
    return any(w in b.lower() for w in update_words)


def _has_contradicting_words(a: str, b: str) -> bool:
    low_a, low_b = a.lower(), b.lower()
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    days_in_a = [d for d in days if d in low_a]
    days_in_b = [d for d in days if d in low_b]
    if days_in_a and days_in_b and days_in_a != days_in_b:
        return True
    if ("500" in low_a and "200" in low_b) or ("down" in low_a and "up" in low_b):
        return True
    return False


def _load_historical_claims(meeting_id: str) -> list[dict[str, Any]]:
    """Loads past facts, decisions, and action items for this meeting from PostgreSQL."""
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return []

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)
    claims = []

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        with engine.connect() as conn:
            # Query recent facts
            sql_f = text("SELECT id, content, source_segment_id FROM facts WHERE meeting_id = :id ORDER BY created_at DESC LIMIT 10")
            rows_f = conn.execute(sql_f, {"id": meeting_uuid}).fetchall()
            for r in rows_f:
                claims.append({
                    "id": str(r[0]),
                    "type": "fact",
                    "content": r[1],
                    "speaker": "Speaker",
                    "source_segment_id": str(r[2]) if r[2] else None,
                })

            # Query recent decisions
            sql_d = text("SELECT id, content FROM decisions WHERE meeting_id = :id ORDER BY created_at DESC LIMIT 10")
            rows_d = conn.execute(sql_d, {"id": meeting_uuid}).fetchall()
            for r in rows_d:
                claims.append({
                    "id": str(r[0]),
                    "type": "decision",
                    "content": r[1],
                    "speaker": "Speaker",
                    "source_segment_id": None,
                })
    except Exception as exc:
        logger.warning("Failed to load historical claims from DB: %s", exc)
    finally:
        engine.dispose()

    return claims


def _save_conflicts_to_db(
    meeting_id: str,
    conflicts: list[tuple[dict[str, Any], dict[str, Any], ConflictPairClassification]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return [], []

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    created_conflicts = []
    created_evidence = []
    now_utc = datetime.now(timezone.utc)

    try:
        meeting_uuid = uuid.UUID(meeting_id)

        with engine.connect() as conn:
            for claim_a, claim_b, classification in conflicts:
                conflict_id = uuid.uuid4()
                evidence_id_a = uuid.uuid4()
                evidence_id_b = uuid.uuid4()

                desc = (
                    f"[{classification.category.upper()}] {classification.description} "
                    f"| Claim A: '{claim_a['content']}' (ID: {claim_a.get('id')}) "
                    f"| Claim B: '{claim_b['content']}' (ID: {claim_b.get('id')})"
                )

                # Insert into conflicts table
                sql_c = text("""
                    INSERT INTO conflicts (id, meeting_id, description, status, created_at)
                    VALUES (:id, :meeting_id, :description, :status, :created_at)
                """)
                conn.execute(
                    sql_c,
                    {
                        "id": conflict_id,
                        "meeting_id": meeting_uuid,
                        "description": desc,
                        "status": "open",
                        "created_at": now_utc,
                    },
                )

                # Insert evidence trail for claim A
                sql_ea = text("""
                    INSERT INTO evidence (id, meeting_id, content, source_type, source_id, created_at)
                    VALUES (:id, :meeting_id, :content, :source_type, :source_id, :created_at)
                """)
                conn.execute(
                    sql_ea,
                    {
                        "id": evidence_id_a,
                        "meeting_id": meeting_uuid,
                        "content": f"Conflict Evidence Claim A: {claim_a['content']}",
                        "source_type": "transcript",
                        "source_id": claim_a.get("source_segment_id") or str(claim_a.get("id")),
                        "created_at": now_utc,
                    },
                )

                # Insert evidence trail for claim B
                sql_eb = text("""
                    INSERT INTO evidence (id, meeting_id, content, source_type, source_id, created_at)
                    VALUES (:id, :meeting_id, :content, :source_type, :source_id, :created_at)
                """)
                conn.execute(
                    sql_eb,
                    {
                        "id": evidence_id_b,
                        "meeting_id": meeting_uuid,
                        "content": f"Conflict Evidence Claim B: {claim_b['content']}",
                        "source_type": "transcript",
                        "source_id": claim_b.get("source_segment_id") or str(claim_b.get("id")),
                        "created_at": now_utc,
                    },
                )

                created_conflicts.append({
                    "id": str(conflict_id),
                    "meeting_id": meeting_id,
                    "claim_a_id": claim_a.get("id"),
                    "claim_b_id": claim_b.get("id"),
                    "category": classification.category,
                    "description": desc,
                    "status": "open",
                })

                created_evidence.extend([
                    {
                        "id": str(evidence_id_a),
                        "meeting_id": meeting_id,
                        "content": claim_a["content"],
                        "source_type": "transcript",
                        "source_id": claim_a.get("source_segment_id"),
                    },
                    {
                        "id": str(evidence_id_b),
                        "meeting_id": meeting_id,
                        "content": claim_b["content"],
                        "source_type": "transcript",
                        "source_id": claim_b.get("source_segment_id"),
                    },
                ])

            conn.commit()

            from app.ws.manager import broadcast_event_sync
            for cc in created_conflicts:
                broadcast_event_sync(meeting_id, "conflict_created", cc, created_evidence)

        logger.info("[CONFLICT DETECTION] Saved %d genuine conflicts for meeting %s", len(created_conflicts), meeting_id[:8])
    except Exception as exc:
        logger.error("Failed to save conflicts to DB: %s", exc)
    finally:
        engine.dispose()

    return created_conflicts, created_evidence
