"""
Risk Detection Node.

Derives incident risks primarily from:
1. Unresolved conflicts in meeting state
2. Action items missing assigned owners
3. Conflicting or slipping timeline deadlines

Categorizes risks into: technical | schedule | business | dependency | communication
Assigns severity: low | medium | high | critical
Persists to PostgreSQL `risks` table and links evidence in `evidence`.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text

from app.config.settings import settings
from app.graph.state import MeetingState

logger = logging.getLogger(__name__)


def detect_risks_node(state: MeetingState) -> dict[str, Any]:
    """
    LangGraph node for Risk Detection.
    Runs after Timeline Agent.
    Derives risks from open conflicts, unassigned action items, and conflicting deadlines.
    """
    meeting_id = state.get("meeting_id")
    if not meeting_id:
        return {"risks": [], "evidence": []}

    conflicts = state.get("conflicts", [])
    action_items = state.get("action_items", [])
    timeline_events = state.get("timeline_events", [])

    risk_candidates = []

    # 1. Risks derived from open conflicts
    for c in conflicts:
        if c.get("status") == "open":
            risk_candidates.append({
                "description": f"Unresolved Conflict: {c.get('description', '')}",
                "category": "communication",
                "severity": "high",
                "likelihood": "high",
                "source_id": c.get("id"),
            })

    # 2. Risks derived from unassigned action items
    for a in action_items:
        if not a.get("assignee_name") or a.get("assignee_name") == "Speaker":
            risk_candidates.append({
                "description": f"Unassigned Action Item: Task '{a.get('description', '')}' has no clear owner",
                "category": "dependency",
                "severity": "medium",
                "likelihood": "medium",
                "source_id": a.get("id"),
            })

    # 3. Risks derived from conflicting deadlines
    for te in timeline_events:
        if te.get("event_type") == "conflicting_deadline":
            risk_candidates.append({
                "description": f"Timeline Risk: Conflicting deadlines detected - {te.get('description', '')}",
                "category": "schedule",
                "severity": "critical",
                "likelihood": "high",
                "source_id": te.get("id"),
            })

    if not risk_candidates:
        return {"risks": [], "evidence": []}

    new_risks, new_evidence = _save_risks_to_db(
        meeting_id=meeting_id,
        risks=risk_candidates,
    )

    return {"risks": new_risks, "evidence": new_evidence}


def _save_risks_to_db(
    meeting_id: str,
    risks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return [], []

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    created_risks = []
    created_evidence = []
    now_utc = datetime.now(timezone.utc)

    try:
        meeting_uuid = uuid.UUID(meeting_id)

        with engine.connect() as conn:
            for r in risks:
                risk_id = uuid.uuid4()
                evidence_id = uuid.uuid4()

                # Insert into risks table
                sql_r = text("""
                    INSERT INTO risks (id, meeting_id, description, severity, likelihood, status, created_at)
                    VALUES (:id, :meeting_id, :description, :severity, :likelihood, :status, :created_at)
                """)
                conn.execute(
                    sql_r,
                    {
                        "id": risk_id,
                        "meeting_id": meeting_uuid,
                        "description": r["description"],
                        "severity": r["severity"],
                        "likelihood": r["likelihood"],
                        "status": "open",
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
                        "content": f"Risk [{r['severity'].upper()}]: {r['description']}",
                        "source_type": "transcript",
                        "source_id": str(r.get("source_id")) if r.get("source_id") else str(risk_id),
                        "created_at": now_utc,
                    },
                )

                created_risks.append({
                    "id": str(risk_id),
                    "meeting_id": meeting_id,
                    "description": r["description"],
                    "category": r["category"],
                    "severity": r["severity"],
                    "likelihood": r["likelihood"],
                    "status": "open",
                })

                created_evidence.append({
                    "id": str(evidence_id),
                    "meeting_id": meeting_id,
                    "content": r["description"],
                    "source_type": "transcript",
                    "source_id": r.get("source_id"),
                })

            conn.commit()
        logger.info("[RISK DETECTION] Saved %d risks for meeting %s", len(created_risks), meeting_id[:8])
    except Exception as exc:
        logger.error("Failed to save risks to DB: %s", exc)
    finally:
        engine.dispose()

    return created_risks, created_evidence
