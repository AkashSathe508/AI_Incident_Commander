"""
WebSocket and Ingestion Broadcast Endpoints.

Provides:
1. WS /meetings/{meeting_id}/live — Live transcript stream for browser clients.
2. POST /api/meetings/{meeting_id}/broadcast — Internal broadcast endpoint used by agent process to send transcript segments to connected WebSockets.
3. GET /api/meetings/{meeting_id}/transcripts — Fetch historical transcript segments for a meeting.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.meeting import Meeting
from app.models.participant import Participant
from app.models.transcript_segment import TranscriptSegment
from app.ws.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/meetings/{meeting_id}/live")
@router.websocket("/api/meetings/{meeting_id}/live")
async def websocket_live_transcript(websocket: WebSocket, meeting_id: str) -> None:
    """
    WebSocket endpoint for real-time room transcript updates.
    Sends existing transcript history on connect, then streams live segments.
    """
    await ws_manager.connect(websocket, meeting_id)

    # Load initial transcript history for this meeting and send to caller
    try:
        async for db in get_db():
            try:
                m_uuid = uuid.UUID(meeting_id)
                stmt = (
                    select(TranscriptSegment)
                    .where(TranscriptSegment.meeting_id == m_uuid)
                    .order_by(TranscriptSegment.start_ms.asc())
                    .limit(100)
                )
                result = await db.execute(stmt)
                segments = result.scalars().all()

                # Fetch participants for name resolution
                p_stmt = select(Participant).where(Participant.meeting_id == m_uuid)
                p_result = await db.execute(p_stmt)
                participants = {str(p.id): p.name for p in p_result.scalars().all()}

                history_payload = {
                    "type": "history",
                    "meeting_id": meeting_id,
                    "segments": [
                        {
                            "type": "transcript_segment",
                            "id": str(s.id),
                            "meeting_id": meeting_id,
                            "participant_id": str(s.participant_id) if s.participant_id else None,
                            "speaker_name": (
                                participants.get(str(s.participant_id), f"Speaker {s.participant_id}")
                                if s.participant_id
                                else "Speaker"
                            ),
                            "text": s.text,
                            "start_ms": s.start_ms,
                            "end_ms": s.end_ms,
                            "confidence": s.confidence,
                            "created_at": s.created_at.isoformat() if s.created_at else None,
                        }
                        for s in segments
                    ],
                }
                await websocket.send_json(history_payload)
            except Exception as exc:
                logger.warning("Error fetching transcript history for WS client: %s", exc)
            break
    except Exception as exc:
        logger.warning("DB session error in WS handler: %s", exc)

    try:
        while True:
            # Keep connection open and accept ping/pong or client messages
            data = await websocket.receive_text()
            logger.debug("Received WS message from client: %s", data[:50])
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, meeting_id)
    except Exception as exc:
        logger.warning("WebSocket error for meeting %s: %s", meeting_id[:8], exc)
        ws_manager.disconnect(websocket, meeting_id)


@router.post(
    "/api/meetings/{meeting_id}/broadcast",
    summary="Internal endpoint to broadcast transcript segment to WebSocket clients",
)
async def broadcast_transcript(meeting_id: str, payload: dict[str, Any]) -> dict[str, str]:
    """
    Called by the agent process to broadcast newly ingested transcript segments
    to all active WebSockets connected to /meetings/{meeting_id}/live.
    """
    await ws_manager.broadcast(meeting_id, payload)
    return {"status": "ok"}


@router.get(
    "/api/meetings/{meeting_id}/transcripts",
    summary="Get all transcript segments for a meeting",
)
async def get_transcripts(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Fetch stored transcript segments for a given meeting ID."""
    try:
        m_uuid = uuid.UUID(meeting_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="meeting_id must be a valid UUID",
        )

    stmt = (
        select(TranscriptSegment)
        .where(TranscriptSegment.meeting_id == m_uuid)
        .order_by(TranscriptSegment.start_ms.asc())
    )
    result = await db.execute(stmt)
    segments = result.scalars().all()

    # Load participants for speaker mapping
    p_stmt = select(Participant).where(Participant.meeting_id == m_uuid)
    p_result = await db.execute(p_stmt)
    participants = {str(p.id): p.name for p in p_result.scalars().all()}

    return {
        "meeting_id": meeting_id,
        "segments": [
            {
                "id": str(s.id),
                "meeting_id": meeting_id,
                "participant_id": str(s.participant_id) if s.participant_id else None,
                "speaker_name": (
                    participants.get(str(s.participant_id), "Speaker")
                    if s.participant_id
                    else "Speaker"
                ),
                "text": s.text,
                "start_ms": s.start_ms,
                "end_ms": s.end_ms,
                "confidence": s.confidence,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in segments
        ],
    }
