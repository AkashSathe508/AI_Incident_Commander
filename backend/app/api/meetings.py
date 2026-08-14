"""Meetings API — create rooms, generate Agora RTC join tokens, and manage AI agents.

Token generation uses agora-token-builder (AccessToken2 under the hood).
The App Certificate is NEVER sent to the client — only the opaque token string.

Confirmed API (from Agora's live GitHub source RtcTokenBuilder2.py):
    RtcTokenBuilder.build_token_with_uid(
        app_id, app_certificate, channel_name, uid,
        role=1,                   # Role_Publisher
        token_expire=3600,        # seconds from now
        privilege_expire=0,       # 0 = same as token_expire
    )

UID ranges:
    Human participants : 1 – 899_999_999
    AI agent processes : 900_000_001 – 999_999_999  (reserved)
"""

import asyncio
import logging
import random
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.manager import agent_manager
from app.config.settings import settings
from app.db.session import get_db
from app.models.meeting import Meeting
from app.models.participant import Participant

logger = logging.getLogger(__name__)

router = APIRouter()

# Token is valid for 1 hour; privilege (publish/subscribe) inherits same expiry.
_TOKEN_EXPIRY_SECONDS = 3600

# UID ranges — human UIDs must NOT overlap with the AI agent reserved range
_HUMAN_UID_MAX = 899_999_999  # inclusive
_AGENT_UID_MIN = 900_000_001


def _generate_agora_token(channel_name: str, uid: int) -> str | None:
    """
    Generate an Agora AccessToken2 RTC token server-side.

    Returns None when AGORA_APP_CERTIFICATE is not set — Agora allows null
    tokens when the project's "App Certificate" feature is disabled in the
    Agora Console (useful for local development / testing).
    """
    if not settings.agora_app_certificate or not settings.agora_app_id:
        return None  # Test-mode: Agora will accept null token

    try:
        from agora_token_builder import RtcTokenBuilder  # type: ignore[import]

        return RtcTokenBuilder.build_token_with_uid(
            settings.agora_app_id,
            settings.agora_app_certificate,
            channel_name,
            uid,
            role=1,  # Role_Publisher — can publish + subscribe
            token_expire=_TOKEN_EXPIRY_SECONDS,
            privilege_expire=0,  # 0 = same lifetime as token_expire
        )
    except Exception as exc:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agora token generation failed: {exc}",
        ) from exc


# ── Schemas ───────────────────────────────────────────────────────────────────


class CreateMeetingRequest(BaseModel):
    title: str = Field(default="Incident Room", max_length=255)


class CreateMeetingResponse(BaseModel):
    meeting_id: str
    channel_name: str
    title: str


class JoinMeetingRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)


class JoinMeetingResponse(BaseModel):
    token: str | None  # None when Agora auth is disabled
    channel_name: str
    uid: int
    app_id: str
    participant_id: str


class ParticipantInfo(BaseModel):
    id: str
    name: str
    role: str | None


class MeetingInfoResponse(BaseModel):
    meeting_id: str
    title: str
    channel_name: str
    status: str
    participants: list[ParticipantInfo]


class AgentStartResponse(BaseModel):
    meeting_id: str
    started: bool
    pid: int | None = None
    reason: str | None = None


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=CreateMeetingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a meeting room",
)
async def create_meeting(
    body: CreateMeetingRequest,
    db: AsyncSession = Depends(get_db),
) -> CreateMeetingResponse:
    """
    Creates a new meeting and derives a unique Agora channel name from its UUID.
    Returns the meeting ID and a shareable channel identifier.
    """
    # Pre-generate UUID so the channel name can be computed before the INSERT
    meeting_id = uuid.uuid4()
    channel_name = f"room-{meeting_id}"

    meeting = Meeting(
        id=meeting_id,
        title=body.title,
        channel_name=channel_name,
        status="active",
    )
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)

    return CreateMeetingResponse(
        meeting_id=str(meeting.id),
        channel_name=meeting.channel_name,
        title=meeting.title,
    )


@router.post(
    "/{meeting_id}/join",
    response_model=JoinMeetingResponse,
    summary="Join a meeting room — returns a short-lived Agora RTC token",
)
async def join_meeting(
    meeting_id: str,
    body: JoinMeetingRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> JoinMeetingResponse:
    """
    Generates an Agora RTC token for the caller entirely on the server.
    The App Certificate is never exposed to the browser.

    Token lifetime: 1 hour (privilege_expire inherits same value).
    UID: random integer in [1, 899_999_999] — excludes the AI agent reserved range.

    Auto-starts the AI agent when the FIRST human joins the room.
    """
    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="meeting_id must be a valid UUID",
        )

    result = await db.execute(select(Meeting).where(Meeting.id == meeting_uuid))
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found",
        )

    # Human UIDs stay below the AI agent reserved range (900_000_001+)
    uid = random.randint(1, _HUMAN_UID_MAX)

    # Generate the token before persisting the participant so we fail fast
    # if token generation is broken (no partial DB writes)
    token = _generate_agora_token(meeting.channel_name, uid)

    participant = Participant(
        meeting_id=meeting.id,
        name=body.display_name,
        role="participant",
    )
    db.add(participant)
    await db.commit()
    await db.refresh(participant)

    # Count human participants (excluding AI agents) to detect first joiner
    count_result = await db.execute(
        select(func.count()).where(Participant.meeting_id == meeting.id)
    )
    participant_count = count_result.scalar() or 0

    if participant_count == 1:
        # First human joined — auto-start the AI agent in the background
        # BackgroundTasks runs after the response is sent, so it doesn't block
        logger.info(
            "First participant joined meeting %s — scheduling agent start",
            meeting_id[:8],
        )
        background_tasks.add_task(_start_agent_task, meeting_id)

    return JoinMeetingResponse(
        token=token,
        channel_name=meeting.channel_name,
        uid=uid,
        app_id=settings.agora_app_id,
        participant_id=str(participant.id),
    )


@router.post(
    "/{meeting_id}/agent/start",
    response_model=AgentStartResponse,
    summary="Manually start the AI agent for a meeting",
)
async def start_agent(meeting_id: str, db: AsyncSession = Depends(get_db)) -> AgentStartResponse:
    """
    Spin up the server-side Agora agent subprocess for the given meeting.

    The agent:
    - Joins the Agora channel with a reserved UID (900_000_001+)
    - Subscribes to all remote audio streams
    - Logs per-speaker frame counts every 5 seconds
    - Renews its token at the 55-minute mark

    Note: requires Docker (Linux) — the agora_python_server_sdk is Linux/macOS only.
    The FastAPI process itself does NOT import the SDK.
    """
    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="meeting_id must be a valid UUID",
        )

    result = await db.execute(select(Meeting).where(Meeting.id == meeting_uuid))
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found",
        )

    result = await agent_manager.start_agent(meeting_id)
    return AgentStartResponse(
        meeting_id=meeting_id,
        started=result.get("started", False),
        pid=result.get("pid"),
        reason=result.get("reason"),
    )


@router.post(
    "/{meeting_id}/agent/stop",
    summary="Stop the AI agent for a meeting",
)
async def stop_agent(meeting_id: str) -> dict:
    """Send SIGTERM to the running agent subprocess (graceful shutdown)."""
    stopped = await agent_manager.stop_agent(meeting_id)
    return {"meeting_id": meeting_id, "stopped": stopped}


@router.get(
    "/{meeting_id}/agent/status",
    summary="Get AI agent subprocess status",
)
async def agent_status(meeting_id: str) -> dict:
    return {"meeting_id": meeting_id, **agent_manager.status(meeting_id)}


@router.get(
    "/{meeting_id}",
    response_model=MeetingInfoResponse,
    summary="Get meeting details",
)
async def get_meeting(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
) -> MeetingInfoResponse:
    """Returns meeting metadata and the list of registered participants."""
    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="meeting_id must be a valid UUID",
        )

    result = await db.execute(select(Meeting).where(Meeting.id == meeting_uuid))
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found",
        )

    parts_result = await db.execute(
        select(Participant).where(Participant.meeting_id == meeting.id)
    )
    participants = parts_result.scalars().all()

    return MeetingInfoResponse(
        meeting_id=str(meeting.id),
        title=meeting.title,
        channel_name=meeting.channel_name,
        status=meeting.status,
        participants=[
            ParticipantInfo(id=str(p.id), name=p.name, role=p.role)
            for p in participants
        ],
    )


@router.post(
    "/{meeting_id}/end",
    summary="End a meeting — triggers Final Synthesis report generation",
)
async def end_meeting(
    meeting_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Ends the meeting session, stops the AI agent subprocess, and triggers
    the Final Synthesis LangGraph node to produce an executive post-incident report.
    """
    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid meeting_id")

    result = await db.execute(select(Meeting).where(Meeting.id == meeting_uuid))
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    meeting.status = "ended"
    meeting.ended_at = func.now()
    await db.commit()

    # Stop AI agent subprocess if running
    await agent_manager.stop_agent(meeting_id)

    # Schedule Final Synthesis in background task
    from app.reasoning.final_synthesis import generate_final_synthesis
    background_tasks.add_task(generate_final_synthesis, meeting_id)

    return {"meeting_id": meeting_id, "status": "ended", "synthesis_triggered": True}


@router.get(
    "/{meeting_id}/report",
    summary="Get full synthesized post-incident report",
)
async def get_report(meeting_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Returns full synthesized post-incident report containing executive summary,
    timeline, facts, decisions, action items, conflicts, risks, and unresolved questions.
    """
    import json
    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid meeting_id")

    result = await db.execute(select(Meeting).where(Meeting.id == meeting_uuid))
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    # If description is JSON string, parse it
    report_meta = {}
    if meeting.description:
        try:
            report_meta = json.loads(meeting.description)
        except Exception:
            report_meta = {"executive_summary": meeting.description, "unresolved_questions": []}

    # Fetch facts, decisions, action items, conflicts, risks, timeline
    from app.models.fact import Fact
    from app.models.decision import Decision
    from app.models.action_item import ActionItem
    from app.models.conflict import Conflict
    from app.models.risk import Risk
    from app.models.timeline_event import TimelineEvent

    facts_res = await db.execute(select(Fact).where(Fact.meeting_id == meeting_uuid))
    dec_res = await db.execute(select(Decision).where(Decision.meeting_id == meeting_uuid))
    act_res = await db.execute(select(ActionItem).where(ActionItem.meeting_id == meeting_uuid))
    conf_res = await db.execute(select(Conflict).where(Conflict.meeting_id == meeting_uuid))
    risk_res = await db.execute(select(Risk).where(Risk.meeting_id == meeting_uuid))
    time_res = await db.execute(select(TimelineEvent).where(TimelineEvent.meeting_id == meeting_uuid).order_by(TimelineEvent.occurred_at.asc()))

    return {
        "meeting_id": meeting_id,
        "title": meeting.title,
        "status": meeting.status,
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
        "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
        "executive_summary": report_meta.get("executive_summary", f"Incident response report for {meeting.title}"),
        "unresolved_questions": report_meta.get("unresolved_questions", []),
        "facts": [{"id": str(f.id), "content": f.content, "confidence": f.confidence} for f in facts_res.scalars().all()],
        "decisions": [{"id": str(d.id), "content": d.content, "rationale": d.rationale} for d in dec_res.scalars().all()],
        "action_items": [{"id": str(a.id), "description": a.description, "due_date": str(a.due_date) if a.due_date else None, "status": a.status} for a in act_res.scalars().all()],
        "conflicts": [{"id": str(c.id), "description": c.description, "status": c.status} for c in conf_res.scalars().all()],
        "risks": [{"id": str(r.id), "description": r.description, "severity": r.severity, "status": r.status} for r in risk_res.scalars().all()],
        "timeline_events": [{"id": str(t.id), "event_type": t.event_type, "description": t.description, "occurred_at": t.occurred_at.isoformat()} for t in time_res.scalars().all()],
    }


@router.get(
    "/{meeting_id}/evidence/{conclusion_id}",
    summary="Recursive evidence lookup back to transcript segments",
)
async def get_evidence_chain(meeting_id: str, conclusion_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Performs a recursive evidence lookup tracing a claim/conclusion ID through the evidence table
    back to the exact target transcript segments, speaker names, and timestamps.
    """
    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid meeting_id")

    from app.models.evidence import Evidence
    from app.models.transcript_segment import TranscriptSegment

    # Query evidence rows matching conclusion_id
    ev_stmt = select(Evidence).where(
        Evidence.meeting_id == meeting_uuid,
        (Evidence.source_id == conclusion_id) | (Evidence.id == uuid.UUID(conclusion_id) if _is_valid_uuid(conclusion_id) else False)
    )
    ev_result = await db.execute(ev_stmt)
    evidence_rows = ev_result.scalars().all()

    # Query matching transcript segments
    segment_ids = []
    for e in evidence_rows:
        if e.source_id and _is_valid_uuid(e.source_id):
            segment_ids.append(uuid.UUID(e.source_id))

    transcript_items = []
    if segment_ids:
        t_stmt = select(TranscriptSegment).where(TranscriptSegment.id.in_(segment_ids))
        t_res = await db.execute(t_stmt)
        for t in t_res.scalars().all():
            transcript_items.append({
                "segment_id": str(t.id),
                "text": t.text,
                "start_ms": t.start_ms,
                "end_ms": t.end_ms,
                "speaker": f"Speaker {str(t.participant_id)[:8]}" if t.participant_id else "Speaker",
            })

    if not transcript_items:
        # Fallback query transcript segments directly matching conclusion_id
        if _is_valid_uuid(conclusion_id):
            t_stmt = select(TranscriptSegment).where(TranscriptSegment.id == uuid.UUID(conclusion_id))
            t_res = await db.execute(t_stmt)
            for t in t_res.scalars().all():
                transcript_items.append({
                    "segment_id": str(t.id),
                    "text": t.text,
                    "start_ms": t.start_ms,
                    "end_ms": t.end_ms,
                    "speaker": f"Speaker {str(t.participant_id)[:8]}" if t.participant_id else "Speaker",
                })

    return {
        "meeting_id": meeting_id,
        "conclusion_id": conclusion_id,
        "evidence_entries": [
            {
                "id": str(e.id),
                "content": e.content,
                "source_type": e.source_type,
                "source_id": e.source_id,
            }
            for e in evidence_rows
        ],
        "transcript_chain": transcript_items,
    }


def _is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(str(val))
        return True
    except (ValueError, TypeError):
        return False


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _start_agent_task(meeting_id: str) -> None:
    """
    Background task: start the agent subprocess.
    Exceptions are caught and logged — never propagate to the client.
    """
    try:
        result = await agent_manager.start_agent(meeting_id)
        if result.get("started"):
            logger.info(
                "Auto-started agent for meeting %s (pid=%s)",
                meeting_id[:8], result.get("pid"),
            )
        else:
            logger.info(
                "Agent not started for meeting %s: %s",
                meeting_id[:8], result.get("reason"),
            )
    except Exception as exc:
        logger.error(
            "Auto-agent start failed for meeting %s: %s",
            meeting_id[:8], exc,
        )

