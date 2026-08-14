"""
Timeline Agent Node.

Constructs an ordered sequence of events, deadlines, and dependencies from
normalized dates, facts, decisions, and action items.
Persists timeline events to PostgreSQL `timeline_events` table and links evidence.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text

from app.config.settings import settings
from app.graph.state import MeetingState
from app.temporal.normalizer import temporal_normalizer

logger = logging.getLogger(__name__)


def timeline_agent_node(state: MeetingState) -> dict[str, Any]:
    """
    LangGraph node for Timeline Agent.
    Runs after conflict detection.
    Pulls normalized dates from state facts/decisions/actions and inserts ordered `timeline_events`.
    """
    meeting_id = state.get("meeting_id")
    if not meeting_id:
        return {"timeline_events": [], "evidence": []}

    facts = state.get("facts", [])
    decisions = state.get("decisions", [])
    action_items = state.get("action_items", [])
    conflicts = state.get("conflicts", [])

    candidates = []
    now_utc = datetime.now(timezone.utc)

    # 1. Fact timeline events
    for f in facts:
        norm = temporal_normalizer.normalize(f.get("content", ""), ref_time=now_utc)
        candidates.append({
            "event_type": "fact_recorded",
            "description": f.get("content", ""),
            "occurred_at": norm["timestamp"] if norm["resolved"] else now_utc,
            "source_id": f.get("id"),
        })

    # 2. Decision timeline events
    for d in decisions:
        norm = temporal_normalizer.normalize(d.get("content", ""), ref_time=now_utc)
        candidates.append({
            "event_type": "decision_made",
            "description": d.get("content", ""),
            "occurred_at": norm["timestamp"] if norm["resolved"] else now_utc,
            "source_id": d.get("id"),
        })

    # 3. Action item deadline events
    for a in action_items:
        due_str = a.get("due_date") or a.get("description", "")
        norm = temporal_normalizer.normalize(str(due_str), ref_time=now_utc)
        candidates.append({
            "event_type": "action_item_deadline",
            "description": f"Deadline: {a.get('description', '')}",
            "occurred_at": norm["timestamp"] if norm["resolved"] else now_utc,
            "source_id": a.get("id"),
        })

    # 4. Conflict timeline events (conflicting dates)
    for c in conflicts:
        candidates.append({
            "event_type": "conflicting_deadline",
            "description": f"Conflicting Deadline Flagged: {c.get('description', '')}",
            "occurred_at": now_utc,
            "source_id": c.get("id"),
        })

    if not candidates:
        return {"timeline_events": [], "evidence": []}

    # Order candidates chronologically
    candidates.sort(key=lambda x: x["occurred_at"])

    new_timeline_events, new_evidence = _save_timeline_events_to_db(
        meeting_id=meeting_id,
        events=candidates,
    )

    return {"timeline_events": new_timeline_events, "evidence": new_evidence}


def _save_timeline_events_to_db(
    meeting_id: str,
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return [], []

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    created_events = []
    created_evidence = []
    now_utc = datetime.now(timezone.utc)

    try:
        meeting_uuid = uuid.UUID(meeting_id)

        with engine.connect() as conn:
            for ev in events:
                event_id = uuid.uuid4()
                evidence_id = uuid.uuid4()

                # Insert into timeline_events table
                sql_t = text("""
                    INSERT INTO timeline_events (id, meeting_id, event_type, description, occurred_at, created_at)
                    VALUES (:id, :meeting_id, :event_type, :description, :occurred_at, :created_at)
                """)
                conn.execute(
                    sql_t,
                    {
                        "id": event_id,
                        "meeting_id": meeting_uuid,
                        "event_type": ev["event_type"],
                        "description": ev["description"],
                        "occurred_at": ev["occurred_at"],
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
                        "content": f"Timeline Event [{ev['event_type']}]: {ev['description']}",
                        "source_type": "transcript",
                        "source_id": str(ev.get("source_id")) if ev.get("source_id") else str(event_id),
                        "created_at": now_utc,
                    },
                )

                created_events.append({
                    "id": str(event_id),
                    "meeting_id": meeting_id,
                    "event_type": ev["event_type"],
                    "description": ev["description"],
                    "occurred_at": ev["occurred_at"].isoformat(),
                })

                created_evidence.append({
                    "id": str(evidence_id),
                    "meeting_id": meeting_id,
                    "content": ev["description"],
                    "source_type": "transcript",
                    "source_id": ev.get("source_id"),
                })

            conn.commit()
        logger.info("[TIMELINE AGENT] Saved %d timeline events for meeting %s", len(created_events), meeting_id[:8])
    except Exception as exc:
        logger.error("Failed to save timeline events to DB: %s", exc)
    finally:
        engine.dispose()

    return created_events, created_evidence
