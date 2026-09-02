"""
Action Item Extraction Node.

Extracts tasks, assigned owners, due dates (normalized via temporal_normalizer),
priority, and status ('open') from transcript text.
Persists action items to PostgreSQL `action_items` table and links evidence in `evidence`.
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
from app.temporal.normalizer import temporal_normalizer

logger = logging.getLogger(__name__)


class ExtractedActionItem(BaseModel):
    description: str = Field(description="Action item description/task")
    assignee_name: str | None = Field(default=None, description="Name of assigned person")
    raw_due_date: str | None = Field(default=None, description="Raw due date or deadline expression")
    priority: str = Field(default="medium", description="Priority: low | medium | high | urgent")
    issue_type: str = Field(default="Task", description="Issue type: Bug | Task | Story")
    status: str = Field(default="open", description="Status: open | in_progress | completed | cancelled")


def extract_action_items_node(state: MeetingState) -> dict[str, Any]:
    """
    LangGraph node for Action Item Extraction.
    Takes latest transcript segment, extracts tasks & deadlines, normalizes dates via TemporalNormalizer,
    saves to PostgreSQL `action_items` and `evidence`.
    """
    meeting_id = state.get("meeting_id")
    latest_segment = state.get("latest_segment")

    logger.info(
        "[ACTION ITEM EXTRACTION] Node entered — meeting=%s segment_text='%s'",
        (meeting_id or "")[:8],
        (latest_segment or {}).get("text", "")[:60],
    )

    if not meeting_id or not latest_segment:
        return {"action_items": [], "evidence": []}

    segment_text = latest_segment.get("text", "").strip()
    segment_id_str = latest_segment.get("id")
    speaker_name = latest_segment.get("speaker_name", "Speaker")

    if not segment_text or len(segment_text) < 4:
        return {"action_items": [], "evidence": []}

    groq_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
    gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    extracted: list[ExtractedActionItem] = []

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
                "You are an AI Incident Commander action item extraction engine. "
                "Analyze the spoken utterance from an incident call and extract "
                "CONCRETE ACTION ITEMS, TASKS, ASSIGNED FOLLOW-UPS, OR WORK ITEMS.\n\n"
                "CLASSIFICATION RULES:\n"
                "- DO extract: tasks assigned to someone, steps someone will take, work that needs doing\n"
                "- DO NOT extract: pure facts/observations, status updates with no action, completed work\n\n"
                "EXAMPLES:\n"
                "- 'Alex will check the database logs by 5 PM' → action item (assignee=Alex, priority=high)\n"
                "- 'I\'ll update the status page in 10 minutes' → action item (priority=medium)\n"
                "- 'Someone needs to restart the Redis cluster NOW' → action item (priority=urgent)\n"
                "- 'We need to rollback the deploy immediately' → action item (priority=urgent)\n"
                "- 'Let\'s set up monitoring alerts for this' → action item (priority=low)\n"
                "- 'The database is down' → NOT an action item (this is a fact)\n\n"
                "PRIORITY CALIBRATION (be precise):\n"
                "- urgent: system down NOW, data loss risk, customer impact, words like 'immediately', 'ASAP', 'emergency', 'now', 'right away'\n"
                "- high: major impact, must be done within the hour, words like 'quickly', 'soon', 'before EOD'\n"
                "- medium: moderate impact, should be done today\n"
                "- low: nice to have, minor impact, no deadline pressure\n\n"
                "ISSUE TYPE CLASSIFICATION:\n"
                "- Bug: fix errors, crashes, outages, failures\n"
                "- Task: investigate, analyze, review, monitor, check, restart, rollback\n"
                "- Story: implement, build, add features, improve\n\n"
                "Return JSON: {\"action_items\": [{\"description\": \"...\", \"assignee_name\": \"...\", \"raw_due_date\": \"...\", \"priority\": \"urgent|high|medium|low\", \"issue_type\": \"Bug|Task|Story\", \"status\": \"open\"}]}\n"
                "If NO action items found, return: {\"action_items\": []}"
            )

            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Speaker ({speaker_name}): \"{segment_text}\"")
            ])

            logger.info("[ACTION ITEM EXTRACTION] Raw LLM response: %s", str(response.content)[:300])

            from app.reasoning.parser import parse_llm_json
            parsed = parse_llm_json(response.content)
            for item in parsed.get("action_items", []):
                if item.get("description"):
                    extracted.append(
                        ExtractedActionItem(
                            description=item["description"],
                            assignee_name=item.get("assignee_name") or speaker_name,
                            raw_due_date=item.get("raw_due_date"),
                            priority=item.get("priority", "medium"),
                            issue_type=item.get("issue_type", "Task"),
                            status=item.get("status", "open"),
                        )
                    )
        except Exception as exc:
            logger.warning("Gemini LLM action item extraction failed, falling back to rule heuristic: %s", exc)

    # Rule heuristic fallback
    if not extracted and _is_likely_action_item(segment_text):
        extracted.append(
            ExtractedActionItem(
                description=segment_text,
                assignee_name=speaker_name,
                raw_due_date=_extract_deadline_phrase(segment_text),
                status="open",
            )
        )

    if not extracted:
        return {"action_items": [], "evidence": []}

    # Normalize due dates using TemporalNormalizer
    normalized_items = []
    for item in extracted:
        norm_res = temporal_normalizer.normalize(item.raw_due_date or segment_text)
        due_date_obj = norm_res["timestamp"].date() if (norm_res["resolved"] and norm_res["timestamp"]) else None
        normalized_items.append((item, due_date_obj, norm_res["resolved"]))

    new_action_items, new_evidence = _save_action_items_to_db(
        meeting_id=meeting_id,
        segment_id_str=segment_id_str,
        segment_text=segment_text,
        items_with_dates=normalized_items,
    )

    return {"action_items": new_action_items, "evidence": new_evidence}


def _is_likely_action_item(text_content: str) -> bool:
    keywords = [
        "will check", "i'll look into", "action item:", "take a look", "fix this by",
        "handle this", "assigned to", "i found", "we need to", "someone needs",
        "needs to be", "can you", "please", "let me", "going to", "should",
        "follow up", "investigate", "looking into", "work on",
    ]
    lowered = text_content.lower()
    return any(kw in lowered for kw in keywords)


def _extract_deadline_phrase(text_content: str) -> str | None:
    match = re.search(r"(by\s+\w+|in\s+\d+\s*\w+|next\s+\w+|tomorrow|today)", text_content, re.IGNORECASE)
    return match.group(1) if match else None


def _save_action_items_to_db(
    meeting_id: str,
    segment_id_str: str | None,
    segment_text: str,
    items_with_dates: list[tuple[ExtractedActionItem, Any, bool]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return [], []

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    created_items = []
    created_evidence = []
    now_utc = datetime.now(timezone.utc)

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        segment_uuid = uuid.UUID(segment_id_str) if segment_id_str else None

        with engine.connect() as conn:
            for item, due_date, resolved in items_with_dates:
                action_id = uuid.uuid4()
                evidence_id = uuid.uuid4()

                # Insert into action_items table
                sql_a = text("""
                    INSERT INTO action_items (id, meeting_id, description, due_date, status, created_at)
                    VALUES (:id, :meeting_id, :description, :due_date, :status, :created_at)
                """)
                conn.execute(
                    sql_a,
                    {
                        "id": action_id,
                        "meeting_id": meeting_uuid,
                        "description": item.description,
                        "due_date": due_date,
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
                        "content": f"Action Item: {item.description} (Assignee: {item.assignee_name}, Resolved Deadline: {resolved})",
                        "source_type": "transcript",
                        "source_id": str(segment_uuid) if segment_uuid else str(action_id),
                        "created_at": now_utc,
                    },
                )

                created_items.append({
                    "id": str(action_id),
                    "meeting_id": meeting_id,
                    "description": item.description,
                    "assignee_name": item.assignee_name,
                    "due_date": str(due_date) if due_date else None,
                    "date_resolved": resolved,
                    "status": item.status,
                })
                created_evidence.append({
                    "id": str(evidence_id),
                    "meeting_id": meeting_id,
                    "content": item.description,
                    "source_type": "transcript",
                    "source_id": str(segment_uuid) if segment_uuid else None,
                })

            conn.commit()

            from app.ws.manager import broadcast_event_sync
            for ca in created_items:
                broadcast_event_sync(meeting_id, "action_item_created", ca, created_evidence)

        logger.info("[ACTION ITEM EXTRACTION] Saved %d action items for meeting %s", len(created_items), meeting_id[:8])
    except Exception as exc:
        logger.error("Failed to save action items to DB: %s", exc)
    finally:
        engine.dispose()

    return created_items, created_evidence
