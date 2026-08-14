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
    status: str = Field(default="open", description="Status: open | in_progress | completed | cancelled")


def extract_action_items_node(state: MeetingState) -> dict[str, Any]:
    """
    LangGraph node for Action Item Extraction.
    Takes latest transcript segment, extracts tasks & deadlines, normalizes dates via TemporalNormalizer,
    saves to PostgreSQL `action_items` and `evidence`.
    """
    meeting_id = state.get("meeting_id")
    latest_segment = state.get("latest_segment")

    if not meeting_id or not latest_segment:
        return {"action_items": [], "evidence": []}

    segment_text = latest_segment.get("text", "").strip()
    segment_id_str = latest_segment.get("id")
    speaker_name = latest_segment.get("speaker_name", "Speaker")

    if not segment_text or len(segment_text) < 4:
        return {"action_items": [], "evidence": []}

    gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    extracted: list[ExtractedActionItem] = []

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
                "You are an AI Incident Commander action item extraction engine. "
                "Analyze the spoken utterance from an incident call and extract "
                "ACTION ITEMS, TASKS, OR ASSIGNED FOLLOW-UPS.\n\n"
                "Examples: 'Alex will check the database logs by 5 PM', 'I will update the status page in 10 minutes'.\n"
                "Return response JSON: {\"action_items\": [{\"description\": \"...\", \"assignee_name\": \"...\", \"raw_due_date\": \"...\", \"priority\": \"medium\", \"status\": \"open\"}]}"
            )

            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Speaker ({speaker_name}): \"{segment_text}\"")
            ])

            content = response.content
            if isinstance(content, str):
                clean_json = re.sub(r"```json\s*|\s*```", "", content).strip()
                parsed = json.loads(clean_json)
                for item in parsed.get("action_items", []):
                    if item.get("description"):
                        extracted.append(
                            ExtractedActionItem(
                                description=item["description"],
                                assignee_name=item.get("assignee_name") or speaker_name,
                                raw_due_date=item.get("raw_due_date"),
                                priority=item.get("priority", "medium"),
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
    keywords = ["will check", "i'll look into", "action item:", "take a look", "fix this by", "handle this", "assigned to"]
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
        logger.info("[ACTION ITEM EXTRACTION] Saved %d action items for meeting %s", len(created_items), meeting_id[:8])
    except Exception as exc:
        logger.error("Failed to save action items to DB: %s", exc)
    finally:
        engine.dispose()

    return created_items, created_evidence
