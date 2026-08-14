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
