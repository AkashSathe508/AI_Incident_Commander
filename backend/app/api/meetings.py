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
import os
import random
import uuid
from datetime import datetime, timezone

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

    agora-token-builder==1.0.0 API:
        RtcTokenBuilder.buildTokenWithUid(
            appId, appCertificate, channelName, uid,
            role,                       # 1 = Role_Publisher
            privilegeExpiredTs,         # absolute Unix timestamp
        )
    """
    if not settings.agora_app_certificate or not settings.agora_app_id:
        return None  # Test-mode: Agora will accept null token

    try:
        import time
        from agora_token_builder import RtcTokenBuilder  # type: ignore[import]

        privilege_expired_ts = int(time.time()) + _TOKEN_EXPIRY_SECONDS
        return RtcTokenBuilder.buildTokenWithUid(
            settings.agora_app_id,
            settings.agora_app_certificate,
            channel_name,
            uid,
            1,                      # role = Role_Publisher
            privilege_expired_ts,   # absolute expiry timestamp
        )
    except Exception as exc:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agora token generation failed: {exc}",
        ) from exc


# ── Schemas ───────────────────────────────────────────────────────────────────


class IntegrationConfig(BaseModel):
    """Per-session integration credentials supplied from the UI."""
    jira_domain: str = ""
    jira_user_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "INC"
    slack_webhook_url: str = ""
    slack_bot_token: str = ""
    slack_channel: str = "#incidents"


class CreateMeetingRequest(BaseModel):
    title: str = Field(default="Incident Room", max_length=255)
    integration_config: IntegrationConfig = Field(default_factory=IntegrationConfig)


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
    Integration config (Jira/Slack credentials) is stored in the dedicated JSONB column.
    """
    # Pre-generate UUID so the channel name can be computed before the INSERT
    meeting_id = uuid.uuid4()
    channel_name = f"room-{meeting_id}"
    integration_meta = body.integration_config.model_dump()

    meeting = Meeting(
        id=meeting_id,
        title=body.title,
        channel_name=channel_name,
        status="active",
        integration_config=integration_meta,  # dedicated JSONB column
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
    meeting.ended_at = datetime.now(timezone.utc)
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

    # Read from the dedicated summary column (not description)
    executive_summary = meeting.summary or f"Incident response report for {meeting.title}"

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
    time_res = await db.execute(
        select(TimelineEvent)
        .where(TimelineEvent.meeting_id == meeting_uuid)
        .order_by(TimelineEvent.occurred_at.asc())
    )

    return {
        "meeting_id": meeting_id,
        "title": meeting.title,
        "status": meeting.status,
        "incident_severity": meeting.incident_severity,
        "root_cause_status": meeting.root_cause_status,
        "resolution_status": meeting.resolution_status,
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
        "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
        "executive_summary": executive_summary,
        "unresolved_questions": [],
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


class AskQuestionRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=500)


@router.post(
    "/{meeting_id}/ask",
    summary="Ask a natural-language question about the meeting",
)
async def ask_meeting(
    meeting_id: str,
    body: AskQuestionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Performs hybrid pgvector similarity + keyword search across verified facts, decisions,
    and transcript segments, then invokes Gemini LLM for a strictly grounded answer with citations.
    """
    import json
    import re
    from app.reasoning.embedding import generate_embedding
    from app.models.fact import Fact
    from app.models.decision import Decision
    from app.models.transcript_segment import TranscriptSegment

    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid meeting_id")

    question = body.question.strip()
    query_vec = generate_embedding(question)

    candidates = []
    seen_texts = set()

    # 1. Full-text / Keyword search candidates
    kw_words = [w.lower() for w in re.findall(r"\w+", question) if len(w) > 3]

    # Search facts
    f_stmt = select(Fact).where(Fact.meeting_id == meeting_uuid)
    f_res = await db.execute(f_stmt)
    for f in f_res.scalars().all():
        if f.content not in seen_texts:
            score = sum(1 for w in kw_words if w in f.content.lower())
            candidates.append({
                "source_type": "fact",
                "source_id": str(f.id),
                "text": f.content,
                "score": score + 0.5,
            })
            seen_texts.add(f.content)

    # Search decisions
    d_stmt = select(Decision).where(Decision.meeting_id == meeting_uuid)
    d_res = await db.execute(d_stmt)
    for d in d_res.scalars().all():
        if d.content not in seen_texts:
            score = sum(1 for w in kw_words if w in d.content.lower())
            candidates.append({
                "source_type": "decision",
                "source_id": str(d.id),
                "text": d.content,
                "score": score + 0.8,
            })
            seen_texts.add(d.content)

    # Search transcript segments
    t_stmt = select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_uuid)
    t_res = await db.execute(t_stmt)
    for t in t_res.scalars().all():
        if t.text not in seen_texts:
            score = sum(1 for w in kw_words if w in t.text.lower())
            if score > 0:
                candidates.append({
                    "source_type": "transcript",
                    "source_id": str(t.id),
                    "text": t.text,
                    "score": score,
                })
                seen_texts.add(t.text)

    # Sort candidates by match score
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top_candidates = candidates[:8]

    if not top_candidates:
        return {
            "answer": "This topic is not covered in this meeting.",
            "citations": [],
        }

    groq_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
    gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
    answer_text = "This topic is not covered in this meeting."
    cited_items = []

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

            context_str = "\n".join([
                f"[{c['source_type'].upper()} ID {c['source_id']}]: {c['text']}"
                for c in top_candidates
            ])

            system_prompt = (
                "You are an AI Incident Commander Q&A engine.\n"
                "STRICT MANDATE:\n"
                "1. Answer the user's question ONLY using the provided meeting context below.\n"
                "2. If the user asks an unrelated question (e.g. weather, general trivia) or something NOT discussed in the context, "
                "you MUST respond EXACTLY with: 'This topic is not covered in this meeting.'\n"
                "3. If answered, provide clear citations referencing the source IDs.\n"
                "Return response JSON: {\"answer\": \"...\", \"cited_source_ids\": [\"...\"]}"
            )

            user_prompt = f"Context:\n{context_str}\n\nUser Question: \"{question}\""

            res = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ])

            from app.reasoning.parser import parse_llm_json
            parsed = parse_llm_json(res.content)
            answer_text = parsed.get("answer", answer_text)
            cited_ids = [str(cid) for cid in parsed.get("cited_source_ids", [])]
            for c in top_candidates:
                if c["source_id"] in cited_ids or any(cid in str(c["source_id"]) for cid in cited_ids):
                    cited_items.append(c)
        except Exception as exc:
            logger.warning("Gemini Q&A call failed: %s", exc)

    if not cited_items and "not covered" not in answer_text.lower():
        cited_items = top_candidates[:2]

    return {
        "answer": answer_text,
        "citations": cited_items,
    }


@router.get(
    "/{meeting_id}/approvals",
    summary="Get all pending and resolved external action approvals",
)
async def get_pending_approvals(meeting_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Returns pending and resolved action authorization requests for human review.
    """
    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid meeting_id")

    from app.models.pending_approval import PendingApproval
    stmt = select(PendingApproval).where(PendingApproval.meeting_id == meeting_uuid).order_by(PendingApproval.created_at.desc())
    res = await db.execute(stmt)
    approvals = res.scalars().all()

    return {
        "meeting_id": meeting_id,
        "approvals": [
            {
                "id": str(a.id),
                "action_type": a.action_type,
                "title": a.title,
                "description": a.description,
                "payload": a.payload,
                "status": a.status,
                "execution_result": a.execution_result,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
            }
            for a in approvals
        ],
    }


@router.post(
    "/{meeting_id}/approvals/{approval_id}/approve",
    summary="Approve a pending action — resumes graph and executes integration call",
)
async def approve_action(meeting_id: str, approval_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Approves a proposed action, resuming execution and calling the external API (Jira/Slack/PagerDuty).
    """
    try:
        meeting_uuid = uuid.UUID(meeting_id)
        appr_uuid = uuid.UUID(approval_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format")

    from app.models.pending_approval import PendingApproval
    stmt = select(PendingApproval).where(
        PendingApproval.id == appr_uuid,
        PendingApproval.meeting_id == meeting_uuid,
    )
    res = await db.execute(stmt)
    approval = res.scalar_one_or_none()

    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending approval not found")

    approval.status = "approved"
    approval.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    # Execute the requested external tool action
    execution_result = {}
    action_type = approval.action_type
    payload = approval.payload or {}

    # Load per-session integration config from dedicated JSONB column
    meeting_result = await db.execute(select(Meeting).where(Meeting.id == meeting_uuid))
    the_meeting = meeting_result.scalar_one_or_none()
    integration_cfg: dict = {}
    if the_meeting and the_meeting.integration_config:
        integration_cfg = the_meeting.integration_config
    elif the_meeting and the_meeting.description:
        # Fallback: legacy meetings stored config in description
        import json as _json
        try:
            meta = _json.loads(the_meeting.description)
            integration_cfg = meta.get("integration_config", {})
        except Exception:
            pass

    try:
        if action_type == "jira":
            from app.reasoning.integrations.jira_tool import create_jira_issue
            execution_result = create_jira_issue(
                summary=payload.get("summary", approval.title),
                description=payload.get("description", ""),
                project_key=payload.get("project_key") or integration_cfg.get("jira_project_key") or "INC",
                issue_type=payload.get("issue_type", "Task"),
                priority=payload.get("priority", "medium"),
                domain=integration_cfg.get("jira_domain") or None,
                email=integration_cfg.get("jira_user_email") or None,
                token=integration_cfg.get("jira_api_token") or None,
            )
        elif action_type == "slack":
            from app.reasoning.integrations.slack_tool import post_slack_message
            execution_result = post_slack_message(
                message=payload.get("message", approval.title),
                channel=payload.get("channel") or integration_cfg.get("slack_channel") or "#incidents",
                webhook_url=integration_cfg.get("slack_webhook_url") or None,
                bot_token=integration_cfg.get("slack_bot_token") or None,
            )
        elif action_type == "pagerduty":
            from app.reasoning.integrations.pagerduty_tool import trigger_pagerduty_incident
            execution_result = trigger_pagerduty_incident(
                summary=payload.get("summary", approval.title),
                severity=payload.get("severity", "critical"),
                source=payload.get("source", "AI Incident Commander"),
            )
    except Exception as exc:
        logger.error("Error executing approved integration %s: %s", action_type, exc)
        execution_result = {"status": "error", "error": str(exc)}

    # Persist the execution result so it can be shown in Jira backlog / report
    approval.execution_result = execution_result
    await db.commit()

    # Broadcast updated approval event via WebSocket
    approval_dict = {
        "id": str(approval.id),
        "meeting_id": meeting_id,
        "action_type": approval.action_type,
        "title": approval.title,
        "status": "approved",
        "result": execution_result,
    }
    from app.reasoning.approval_engine import _broadcast_approval_event
    _broadcast_approval_event(meeting_id, approval_dict)

    return {
        "approval_id": approval_id,
        "status": "approved",
        "execution_result": execution_result,
    }


@router.post(
    "/{meeting_id}/approvals/{approval_id}/reject",
    summary="Reject a pending action — cancels execution",
)
async def reject_action(meeting_id: str, approval_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Rejects a proposed action. The external integration call will NEVER be executed.
    """
    try:
        meeting_uuid = uuid.UUID(meeting_id)
        appr_uuid = uuid.UUID(approval_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format")

    from app.models.pending_approval import PendingApproval
    stmt = select(PendingApproval).where(
        PendingApproval.id == appr_uuid,
        PendingApproval.meeting_id == meeting_uuid,
    )
    res = await db.execute(stmt)
    approval = res.scalar_one_or_none()

    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending approval not found")

    approval.status = "rejected"
    approval.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    # Broadcast rejected event via WebSocket
    approval_dict = {
        "id": str(approval.id),
        "meeting_id": meeting_id,
        "action_type": approval.action_type,
        "title": approval.title,
        "status": "rejected",
    }
    from app.reasoning.approval_engine import _broadcast_approval_event
    _broadcast_approval_event(meeting_id, approval_dict)

    return {
        "approval_id": approval_id,
        "status": "rejected",
    }


# ── New: List Meetings endpoint ──────────────────────────────────────────────


@router.get(
    "",
    summary="List all meetings",
)
async def list_meetings(
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Returns all meetings ordered by creation date descending."""
    from sqlalchemy import desc
    from app.models.fact import Fact
    from app.models.action_item import ActionItem
    from app.models.participant import Participant as Part

    result = await db.execute(
        select(Meeting).order_by(desc(Meeting.created_at)).limit(limit).offset(offset)
    )
    meetings = result.scalars().all()

    items = []
    for m in meetings:
        # Count participants
        p_count = await db.execute(
            select(func.count()).where(Part.meeting_id == m.id)
        )
        pc = p_count.scalar() or 0

        # Count open actions
        a_count = await db.execute(
            select(func.count()).where(
                ActionItem.meeting_id == m.id,
                ActionItem.status == "open",
            )
        )
        ac = a_count.scalar() or 0

        items.append({
            "id": str(m.id),
            "title": m.title,
            "status": m.status,
            "incident_severity": m.incident_severity,
            "root_cause_status": m.root_cause_status,
            "resolution_status": m.resolution_status,
            "channel_name": m.channel_name,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "started_at": m.started_at.isoformat() if m.started_at else None,
            "ended_at": m.ended_at.isoformat() if m.ended_at else None,
            "summary": m.summary,
            "participant_count": pc,
            "open_action_count": ac,
        })

    return {"meetings": items, "total": len(items)}


# ── New: AI Responses endpoint ────────────────────────────────────────────────


@router.get(
    "/{meeting_id}/ai-responses",
    summary="Get AI Incident Commander response history for a meeting",
)
async def get_ai_responses(meeting_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Returns all AI-generated responses for a meeting, ordered chronologically."""
    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid meeting_id")

    from app.models.ai_response import AIResponse
    from sqlalchemy import asc

    stmt = (
        select(AIResponse)
        .where(AIResponse.meeting_id == meeting_uuid)
        .order_by(asc(AIResponse.created_at))
    )
    res = await db.execute(stmt)
    responses = res.scalars().all()

    return {
        "meeting_id": meeting_id,
        "ai_responses": [
            {
                "id": str(r.id),
                "response_text": r.response_text,
                "response_type": r.response_type,
                "trigger": r.trigger,
                "related_fact_ids": r.related_fact_ids or [],
                "related_assumption_ids": r.related_assumption_ids or [],
                "related_action_ids": r.related_action_ids or [],
                "approval_status": r.approval_status,
                "approval_id": str(r.approval_id) if r.approval_id else None,
                "execution_status": r.execution_status,
                "jira_ticket_ref": r.jira_ticket_ref,
                "confidence": r.confidence,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in responses
        ],
    }


# ── New: Meeting Notes endpoints ──────────────────────────────────────────────


class CreateNoteRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    author_name: str = Field(default="Incident Commander", max_length=100)
    category: str = Field(default="observation", max_length=50)


class UpdateNoteRequest(BaseModel):
    content: str | None = Field(default=None, max_length=5000)
    category: str | None = Field(default=None, max_length=50)


@router.post(
    "/{meeting_id}/notes",
    status_code=status.HTTP_201_CREATED,
    summary="Add a note to a meeting",
)
async def create_note(meeting_id: str, body: CreateNoteRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """Creates a new note (observation, decision, follow-up, etc.) for a meeting."""
    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid meeting_id")

    from app.models.meeting_note import MeetingNote
    note = MeetingNote(
        meeting_id=meeting_uuid,
        author_name=body.author_name,
        content=body.content,
        category=body.category,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)

    note_dict = {
        "id": str(note.id),
        "meeting_id": meeting_id,
        "author_name": note.author_name,
        "content": note.content,
        "category": note.category,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }

    # Broadcast via WebSocket
    from app.ws.manager import ws_manager
    await ws_manager.broadcast(meeting_id, {"type": "note_created", "note": note_dict})

    return note_dict


@router.get(
    "/{meeting_id}/notes",
    summary="List all notes for a meeting",
)
async def list_notes(meeting_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Returns all notes for a meeting ordered by creation time."""
    try:
        meeting_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid meeting_id")

    from app.models.meeting_note import MeetingNote
    from sqlalchemy import asc

    stmt = select(MeetingNote).where(MeetingNote.meeting_id == meeting_uuid).order_by(asc(MeetingNote.created_at))
    res = await db.execute(stmt)
    notes = res.scalars().all()

    return {
        "meeting_id": meeting_id,
        "notes": [
            {
                "id": str(n.id),
                "author_name": n.author_name,
                "content": n.content,
                "category": n.category,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "updated_at": n.updated_at.isoformat() if n.updated_at else None,
            }
            for n in notes
        ],
    }


@router.put(
    "/{meeting_id}/notes/{note_id}",
    summary="Edit a meeting note",
)
async def update_note(
    meeting_id: str,
    note_id: str,
    body: UpdateNoteRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Updates the content or category of an existing note."""
    try:
        meeting_uuid = uuid.UUID(meeting_id)
        note_uuid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format")

    from app.models.meeting_note import MeetingNote
    stmt = select(MeetingNote).where(
        MeetingNote.id == note_uuid,
        MeetingNote.meeting_id == meeting_uuid,
    )
    res = await db.execute(stmt)
    note = res.scalar_one_or_none()

    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    if body.content is not None:
        note.content = body.content
    if body.category is not None:
        note.category = body.category

    await db.commit()
    await db.refresh(note)

    return {
        "id": str(note.id),
        "author_name": note.author_name,
        "content": note.content,
        "category": note.category,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }


@router.delete(
    "/{meeting_id}/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a meeting note",
)
async def delete_note(meeting_id: str, note_id: str, db: AsyncSession = Depends(get_db)) -> None:
    """Deletes a note from a meeting."""
    try:
        meeting_uuid = uuid.UUID(meeting_id)
        note_uuid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format")

    from app.models.meeting_note import MeetingNote
    stmt = select(MeetingNote).where(
        MeetingNote.id == note_uuid,
        MeetingNote.meeting_id == meeting_uuid,
    )
    res = await db.execute(stmt)
    note = res.scalar_one_or_none()

    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    await db.delete(note)
    await db.commit()


# ── New: Previous Context endpoint ────────────────────────────────────────────


@router.get(
    "/{meeting_id}/context",
    summary="Get relevant context from previous meetings about the same incident",
)
async def get_meeting_context(meeting_id: str) -> dict:
    """
    Returns a compact cross-meeting context object built from previously ended meetings
    with a similar title. This enables continuity across related incident meetings.
    """
    try:
        uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid meeting_id")

    try:
        from app.reasoning.context_retriever import retrieve_relevant_context
        return retrieve_relevant_context(meeting_id)
    except Exception as exc:
        logger.warning("Context retrieval failed: %s", exc)
        return {"has_context": False, "related_meetings": []}


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

