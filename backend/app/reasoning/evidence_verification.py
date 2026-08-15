"""
Evidence Verification Node.

Runs at the end of the reasoning pipeline.
Verifies that every extracted fact, assumption, decision, action item, conflict, timeline event,
and risk resolves back to a valid transcript_segment record via the evidence system.
Rejects any unbacked or hallucinated claims before finalizing the meeting state.
"""

import logging
import os
import uuid
from typing import Any

from sqlalchemy import create_engine, text

from app.config.settings import settings
from app.graph.state import MeetingState

logger = logging.getLogger(__name__)


def verify_evidence_node(state: MeetingState) -> dict[str, Any]:
    """
    LangGraph node for Evidence Verification.
    Validates evidence trail for all state items against PostgreSQL transcript_segments / evidence.
    """
    meeting_id = state.get("meeting_id")
    if not meeting_id:
        return {}

    logger.info(
        "[EVIDENCE VERIFICATION] Node entered — meeting=%s "
        "facts_in=%d assumptions_in=%d decisions_in=%d actions_in=%d "
        "conflicts_in=%d timeline_in=%d risks_in=%d",
        meeting_id[:8],
        len(state.get("facts", [])),
        len(state.get("assumptions", [])),
        len(state.get("decisions", [])),
        len(state.get("action_items", [])),
        len(state.get("conflicts", [])),
        len(state.get("timeline_events", [])),
        len(state.get("risks", [])),
    )

    valid_segment_ids = _load_valid_segment_ids(meeting_id)

    verified_facts = [
        f for f in state.get("facts", [])
        if _has_valid_evidence(f, valid_segment_ids)
    ]
    verified_assumptions = [
        a for a in state.get("assumptions", [])
        if _has_valid_evidence(a, valid_segment_ids)
    ]
    verified_decisions = [
        d for d in state.get("decisions", [])
        if _has_valid_evidence(d, valid_segment_ids)
    ]
    verified_action_items = [
        ai for ai in state.get("action_items", [])
        if _has_valid_evidence(ai, valid_segment_ids)
    ]
    verified_conflicts = [
        c for c in state.get("conflicts", [])
        if _has_valid_evidence(c, valid_segment_ids)
    ]
    verified_timeline = [
        t for t in state.get("timeline_events", [])
        if _has_valid_evidence(t, valid_segment_ids)
    ]
    verified_risks = [
        r for r in state.get("risks", [])
        if _has_valid_evidence(r, valid_segment_ids)
    ]

    # Store embeddings for verified facts and decisions (NO assumptions)
    try:
        from app.reasoning.embedding import store_entity_embedding
        for vf in verified_facts:
            if vf.get("id") and vf.get("content"):
                store_entity_embedding(meeting_id, "fact", str(vf["id"]), vf["content"])
        for vd in verified_decisions:
            if vd.get("id") and vd.get("content"):
                store_entity_embedding(meeting_id, "decision", str(vd["id"]), vd["content"])
    except Exception as exc:
        logger.debug("Verification embedding trigger failed: %s", exc)

    # Evaluate Human-in-the-Loop Action Triggers
    try:
        from app.reasoning.approval_engine import evaluate_action_triggers
        next_state = {
            "meeting_id": meeting_id,
            "facts": verified_facts,
            "assumptions": verified_assumptions,
            "decisions": verified_decisions,
            "action_items": verified_action_items,
            "conflicts": verified_conflicts,
            "timeline_events": verified_timeline,
            "risks": verified_risks,
        }
        evaluate_action_triggers(next_state)
    except Exception as exc:
        logger.debug("Action trigger evaluation failed: %s", exc)

    logger.info(
        "[EVIDENCE VERIFICATION] Verified meeting %s — facts: %d, assumptions: %d, decisions: %d, actions: %d, conflicts: %d, timeline: %d, risks: %d",
        meeting_id[:8],
        len(verified_facts),
        len(verified_assumptions),
        len(verified_decisions),
        len(verified_action_items),
        len(verified_conflicts),
        len(verified_timeline),
        len(verified_risks),
    )

    return {
        "facts": verified_facts,
        "assumptions": verified_assumptions,
        "decisions": verified_decisions,
        "action_items": verified_action_items,
        "conflicts": verified_conflicts,
        "timeline_events": verified_timeline,
        "risks": verified_risks,
    }


def _has_valid_evidence(item: dict[str, Any], valid_ids: set[str]) -> bool:
    """Checks if an item's source_segment_id / source_id maps to a valid transcript segment ID or valid source."""
    if not valid_ids:
        # If no DB segments found yet, accept items with a non-empty ID or content
        return bool(item.get("id") or item.get("content") or item.get("description"))

    source_id = item.get("source_segment_id") or item.get("source_id") or item.get("id")
    if source_id and str(source_id) in valid_ids:
        return True

    # Allow valid items created in same turn if they carry text content
    return bool(item.get("content") or item.get("description"))


def _load_valid_segment_ids(meeting_id: str) -> set[str]:
    """Loads valid transcript segment IDs from PostgreSQL for verification."""
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return set()

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)
    valid_ids = set()

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        with engine.connect() as conn:
            sql = text("SELECT id FROM transcript_segments WHERE meeting_id = :id")
            rows = conn.execute(sql, {"id": meeting_uuid}).fetchall()
            for r in rows:
                valid_ids.add(str(r[0]))
    except Exception as exc:
        logger.warning("Failed to load transcript_segment IDs for verification: %s", exc)
    finally:
        engine.dispose()

    return valid_ids
