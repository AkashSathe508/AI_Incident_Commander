"""
Human-in-the-Loop Action Approval Engine & Triggers.

Evaluates meeting state against trigger conditions:
1. Jira Trigger: Unassigned action item -> Proposes Jira ticket.
2. Slack Trigger: Unresolved high-severity conflict -> Proposes Slack post.
3. PagerDuty Trigger: Critical risk -> Proposes PagerDuty incident page.

Proposes action by creating a `pending_approvals` row with status='pending'
and broadcasting a live WebSocket notification. Action is NEVER executed until human approval.
"""

import asyncio
import logging
import os
import uuid
from typing import Any

import httpx
from sqlalchemy import create_engine, text

from app.config.settings import settings
from app.graph.state import MeetingState

logger = logging.getLogger(__name__)


def evaluate_action_triggers(state: MeetingState) -> list[dict[str, Any]]:
    """
    LangGraph node / helper for Human-in-the-Loop action triggers.
    Evaluates state and creates pending_approvals records for human authorization.
    """
    meeting_id = state.get("meeting_id")
    if not meeting_id:
        return []

    created_approvals = []

    # 1. Jira Trigger: Unassigned Action Items
    action_items = state.get("action_items", [])
    for ai in action_items:
        assignee = ai.get("assignee_name") or ai.get("assignee")
        if not assignee or str(assignee).lower() in ("unassigned", "none", "null", ""):
            desc = ai.get("description", "Action item requires assignment")
            title = f"Create Jira Ticket: '{desc[:60]}'"
            payload = {
                "summary": desc,
                "description": f"Incident Action Item with no current owner.\nDue: {ai.get('due_date', 'ASAP')}",
                "project_key": "INC",
                "issue_type": "Task",
            }
            appr = _create_pending_approval_if_new(meeting_id, "jira", title, desc, payload)
            if appr:
                created_approvals.append(appr)

    # 2. Slack Trigger: High-severity unresolved conflicts
    conflicts = state.get("conflicts", [])
    for conf in conflicts:
        status = str(conf.get("status", "open")).lower()
        if status in ("open", "unresolved"):
            desc = conf.get("description", "Unresolved incident conflict")
            title = f"Post Slack Incident Alert: '{desc[:60]}'"
            payload = {
                "message": f"⚠️ INCIDENT CONFLICT DETECTED:\n{desc}\nPlease resolve immediately.",
                "channel": "#incident-room",
            }
            appr = _create_pending_approval_if_new(meeting_id, "slack", title, desc, payload)
            if appr:
                created_approvals.append(appr)

    # 3. PagerDuty Trigger: Critical Risks
    risks = state.get("risks", [])
    for r in risks:
        sev = str(r.get("severity", "medium")).lower()
        if sev in ("critical", "high"):
            desc = r.get("description", "Critical operational risk detected")
            title = f"Page PagerDuty On-Call: '{desc[:60]}'"
            payload = {
                "summary": f"CRITICAL INCIDENT RISK: {desc}",
                "severity": "critical",
                "source": "AI Incident Commander",
            }
            appr = _create_pending_approval_if_new(meeting_id, "pagerduty", title, desc, payload)
            if appr:
                created_approvals.append(appr)

    return created_approvals


def _create_pending_approval_if_new(
    meeting_id: str,
    action_type: str,
    title: str,
    description: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Inserts a pending_approval record into PostgreSQL if it does not already exist.
    """
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return None

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        appr_id = uuid.uuid4()

        with engine.connect() as conn:
            # Check if pending or approved approval already exists for this title
            check_sql = text("""
                SELECT id FROM pending_approvals
                WHERE meeting_id = :mid AND action_type = :atype AND title = :title
                LIMIT 1
            """)
            existing = conn.execute(check_sql, {"mid": meeting_uuid, "atype": action_type, "title": title}).first()
            if existing:
                return None

            import json
            insert_sql = text("""
                INSERT INTO pending_approvals (id, meeting_id, action_type, title, description, payload, status, created_at)
                VALUES (:id, :meeting_id, :action_type, :title, :description, :payload, 'pending', NOW())
            """)
            conn.execute(
                insert_sql,
                {
                    "id": appr_id,
                    "meeting_id": meeting_uuid,
                    "action_type": action_type,
                    "title": title,
                    "description": description,
                    "payload": json.dumps(payload),
                },
            )
            conn.commit()

        approval_dict = {
            "id": str(appr_id),
            "meeting_id": meeting_id,
            "action_type": action_type,
            "title": title,
            "description": description,
            "payload": payload,
            "status": "pending",
        }
        logger.info("[APPROVAL ENGINE] Created pending %s approval: '%s'", action_type.upper(), title)

        # Broadcast event to WebSocket subscribers
        _broadcast_approval_event(meeting_id, approval_dict)
        return approval_dict
    except Exception as exc:
        logger.error("Failed to create pending approval: %s", exc)
        return None
    finally:
        engine.dispose()


def _broadcast_approval_event(meeting_id: str, approval_data: dict[str, Any]) -> None:
    """Helper to post approval event to backend internal broadcast endpoint."""
    backend_url = os.environ.get("BACKEND_INTERNAL_URL", "http://127.0.0.1:8000")
    url = f"{backend_url}/api/meetings/{meeting_id}/broadcast"
    msg_payload = {
        "type": "pending_approval_created",
        "approval": approval_data,
    }
    try:
        with httpx.Client(timeout=3.0) as client:
            client.post(url, json=msg_payload)
    except Exception as exc:
        logger.debug("Approval WS broadcast notification failed: %s", exc)
