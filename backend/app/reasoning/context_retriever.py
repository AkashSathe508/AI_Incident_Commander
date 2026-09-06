"""
Cross-Meeting Context Retriever.

Retrieves relevant knowledge from PREVIOUS meetings to provide context
for a NEW meeting about the same incident/project.

IMPORTANT: This does NOT blindly dump the entire DB to the LLM.
Instead it builds a compact, token-efficient context object containing:
  - Summaries from related previous meetings
  - Unresolved conflicts from those meetings
  - Key decisions and their outcomes
  - Action item statuses (done vs pending)

The retrieval strategy uses title-based keyword similarity, which is
sufficient for the current architecture (no auth, no user graph).
"""

import logging
import os
import uuid
from typing import Any

from sqlalchemy import create_engine, text

from app.config.settings import settings

logger = logging.getLogger(__name__)


def retrieve_relevant_context(current_meeting_id: str) -> dict[str, Any]:
    """
    Build a compact cross-meeting context dict for the current meeting.

    Args:
        current_meeting_id: UUID string of the active meeting.

    Returns:
        Dict with previous meeting context, or empty dict if none found.
        Shape:
        {
            "has_context": bool,
            "related_meetings": [
                {
                    "id": str,
                    "title": str,
                    "ended_at": str,
                    "summary": str | None,
                    "key_decisions": [str],
                    "unresolved_conflicts": [str],
                    "completed_actions": [str],
                    "open_actions": [str],
                }
            ],
            "compact_context_text": str  # LLM-ready summary
        }
    """
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return {"has_context": False, "related_meetings": []}

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    try:
        meeting_uuid = uuid.UUID(current_meeting_id)

        with engine.connect() as conn:
            # 1. Get current meeting title to use for keyword matching
            m_sql = text("SELECT id, title FROM meetings WHERE id = :id")
            m_row = conn.execute(m_sql, {"id": meeting_uuid}).first()
            if not m_row:
                return {"has_context": False, "related_meetings": []}

            current_title: str = m_row[1] or ""

            # Build keyword list from title (ignore short/common words)
            stopwords = {"the", "a", "an", "of", "in", "and", "or", "for", "to", "is", "on", "at"}
            keywords = [
                w.lower()
                for w in current_title.split()
                if len(w) > 3 and w.lower() not in stopwords
            ]

            if not keywords:
                return {"has_context": False, "related_meetings": []}

            # 2. Find other meetings with similar title keywords (ended ones only)
            #    Use ILIKE for basic case-insensitive keyword match
            conditions = " OR ".join(
                [f"LOWER(title) LIKE :kw{i}" for i in range(len(keywords))]
            )
            params: dict[str, Any] = {"current_id": meeting_uuid}
            for i, kw in enumerate(keywords[:5]):  # cap at 5 keywords
                params[f"kw{i}"] = f"%{kw}%"

            related_sql = text(f"""
                SELECT id, title, ended_at, summary
                FROM meetings
                WHERE id != :current_id
                  AND status = 'ended'
                  AND ({conditions})
                ORDER BY ended_at DESC
                LIMIT 5
            """)
            related_rows = conn.execute(related_sql, params).fetchall()

            if not related_rows:
                return {"has_context": False, "related_meetings": []}

            related_meetings = []
            for row in related_rows:
                prev_id = row[0]
                prev_title = row[1]
                prev_ended_at = row[2]
                prev_summary = row[3]

                # Fetch decisions
                dec_rows = conn.execute(
                    text("SELECT content FROM decisions WHERE meeting_id = :mid LIMIT 5"),
                    {"mid": prev_id},
                ).fetchall()
                decisions = [r[0] for r in dec_rows]

                # Fetch unresolved conflicts
                conf_rows = conn.execute(
                    text("SELECT description FROM conflicts WHERE meeting_id = :mid AND status = 'open' LIMIT 5"),
                    {"mid": prev_id},
                ).fetchall()
                unresolved_conflicts = [r[0] for r in conf_rows]

                # Fetch action items split by status
                act_rows = conn.execute(
                    text("SELECT description, status FROM action_items WHERE meeting_id = :mid LIMIT 10"),
                    {"mid": prev_id},
                ).fetchall()
                completed_actions = [r[0] for r in act_rows if r[1] == "completed"]
                open_actions = [r[0] for r in act_rows if r[1] not in ("completed", "cancelled")]

                related_meetings.append({
                    "id": str(prev_id),
                    "title": prev_title,
                    "ended_at": prev_ended_at.isoformat() if prev_ended_at else None,
                    "summary": prev_summary,
                    "key_decisions": decisions,
                    "unresolved_conflicts": unresolved_conflicts,
                    "completed_actions": completed_actions,
                    "open_actions": open_actions,
                })

            # Build compact LLM-ready text (token-efficient)
            context_parts = ["=== PREVIOUS INCIDENT CONTEXT ==="]
            for m in related_meetings:
                context_parts.append(f"\nPrevious Meeting: {m['title']} ({m['ended_at'] or 'unknown date'})")
                if m["summary"]:
                    context_parts.append(f"  Summary: {m['summary'][:300]}")
                if m["key_decisions"]:
                    context_parts.append(f"  Decisions: {'; '.join(m['key_decisions'][:3])}")
                if m["unresolved_conflicts"]:
                    context_parts.append(f"  Unresolved: {'; '.join(m['unresolved_conflicts'][:3])}")
                if m["open_actions"]:
                    context_parts.append(f"  Open actions: {'; '.join(m['open_actions'][:3])}")
                if m["completed_actions"]:
                    context_parts.append(f"  Completed: {'; '.join(m['completed_actions'][:3])}")

            compact_text = "\n".join(context_parts)

            logger.info(
                "[CONTEXT RETRIEVER] Found %d related meetings for '%s'",
                len(related_meetings), current_title,
            )

            return {
                "has_context": True,
                "related_meetings": related_meetings,
                "compact_context_text": compact_text,
            }

    except Exception as exc:
        logger.error("[CONTEXT RETRIEVER] Failed: %s", exc)
        return {"has_context": False, "related_meetings": []}
    finally:
        engine.dispose()
