"""
WebSocket and Ingestion Broadcast Endpoints.

Provides:
1. WS /meetings/{meeting_id}/live — Real-time stream for transcript & incident intelligence (Facts, Assumptions, Decisions, Actions, Conflicts).
2. POST /api/meetings/{meeting_id}/broadcast — Internal broadcast endpoint for runner/nodes to push live intelligence events.
3. GET /api/meetings/{meeting_id}/intelligence — Fetch historical intelligence items & evidence.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.action_item import ActionItem
from app.models.assumption import Assumption
from app.models.conflict import Conflict
from app.models.decision import Decision
from app.models.evidence import Evidence
from app.models.fact import Fact
from app.models.participant import Participant
from app.models.transcript_segment import TranscriptSegment
from app.ws.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/meetings/{meeting_id}/live")
@router.websocket("/api/meetings/{meeting_id}/live")
@router.websocket("/ws/meetings/{meeting_id}/live")
@router.websocket("/ws/transcripts/{meeting_id}")
async def websocket_live_transcript(websocket: WebSocket, meeting_id: str) -> None:
    """
    WebSocket endpoint for real-time room transcript & live intelligence updates.
    Sends existing full history (segments, facts, assumptions, decisions, actions, conflicts, evidence)
    on connect, then streams live events as they occur.
    """
    await ws_manager.connect(websocket, meeting_id)

    # Load initial full history for this meeting and send to caller
    try:
        async for db in get_db():
            try:
                m_uuid = uuid.UUID(meeting_id)

                # 1. Transcript segments
                seg_res = await db.execute(
                    select(TranscriptSegment)
                    .where(TranscriptSegment.meeting_id == m_uuid)
                    .order_by(TranscriptSegment.start_ms.asc())
                    .limit(200)
                )
                segments = seg_res.scalars().all()

                # 2. Participants map
                p_res = await db.execute(select(Participant).where(Participant.meeting_id == m_uuid))
                participants = {str(p.id): p.name for p in p_res.scalars().all()}

                # 3. Facts
                facts_res = await db.execute(
                    select(Fact).where(Fact.meeting_id == m_uuid).order_by(Fact.created_at.asc())
                )
                facts = facts_res.scalars().all()

                # 4. Assumptions
                assump_res = await db.execute(
                    select(Assumption).where(Assumption.meeting_id == m_uuid).order_by(Assumption.created_at.asc())
                )
                assumptions = assump_res.scalars().all()

                # 5. Decisions
                dec_res = await db.execute(
                    select(Decision).where(Decision.meeting_id == m_uuid).order_by(Decision.created_at.asc())
                )
                decisions = dec_res.scalars().all()

                # 6. Action items
                act_res = await db.execute(
                    select(ActionItem).where(ActionItem.meeting_id == m_uuid).order_by(ActionItem.created_at.asc())
                )
                action_items = act_res.scalars().all()

                # 7. Conflicts
                conf_res = await db.execute(
                    select(Conflict).where(Conflict.meeting_id == m_uuid).order_by(Conflict.created_at.asc())
                )
                conflicts = conf_res.scalars().all()

                # 8. Evidence list
                ev_res = await db.execute(
                    select(Evidence).where(Evidence.meeting_id == m_uuid).order_by(Evidence.created_at.asc())
                )
                evidence_items = ev_res.scalars().all()

                full_history_payload = {
                    "type": "full_history",
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
                    "facts": [
                        {
                            "id": str(f.id),
                            "content": f.content,
                            "confidence": f.confidence,
                            "source_segment_id": str(f.source_segment_id) if f.source_segment_id else None,
                            "created_at": f.created_at.isoformat() if f.created_at else None,
                        }
                        for f in facts
                    ],
                    "assumptions": [
                        {
                            "id": str(a.id),
                            "content": a.content,
                            "status": a.status,
                            "created_at": a.created_at.isoformat() if a.created_at else None,
                        }
                        for a in assumptions
                    ],
                    "decisions": [
                        {
                            "id": str(d.id),
                            "content": d.content,
                            "rationale": d.rationale,
                            "made_by_id": str(d.made_by_id) if d.made_by_id else None,
                            "created_at": d.created_at.isoformat() if d.created_at else None,
                        }
                        for d in decisions
                    ],
                    "action_items": [
                        {
                            "id": str(ai.id),
                            "description": ai.description,
                            "due_date": str(ai.due_date) if ai.due_date else None,
                            "status": ai.status,
                            "assignee_id": str(ai.assignee_id) if ai.assignee_id else None,
                            "created_at": ai.created_at.isoformat() if ai.created_at else None,
                        }
                        for ai in action_items
                    ],
                    "conflicts": [
                        {
                            "id": str(c.id),
                            "description": c.description,
                            "status": c.status,
                            "resolution": c.resolution,
                            "created_at": c.created_at.isoformat() if c.created_at else None,
                        }
                        for c in conflicts
                    ],
                    "evidence": [
                        {
                            "id": str(e.id),
                            "content": e.content,
                            "source_type": e.source_type,
                            "source_id": e.source_id,
                            "created_at": e.created_at.isoformat() if e.created_at else None,
                        }
                        for e in evidence_items
                    ],
                }
                await websocket.send_json(full_history_payload)
            except Exception as exc:
                logger.warning("Error fetching full history for WS client: %s", exc)
            break
    except Exception as exc:
        logger.warning("DB session error in WS handler: %s", exc)

    try:
        while True:
            data = await websocket.receive_text()
            logger.debug("Received WS message: %s", data[:50])
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, meeting_id)
    except Exception as exc:
        logger.warning("WebSocket error for meeting %s: %s", meeting_id[:8], exc)
        ws_manager.disconnect(websocket, meeting_id)


@router.post(
    "/api/meetings/{meeting_id}/broadcast",
    summary="Internal endpoint to broadcast events to WebSocket clients",
)
async def broadcast_event(meeting_id: str, payload: dict[str, Any]) -> dict[str, str]:
    """
    Called by browser/runner/nodes to broadcast transcript segments or live intelligence events.
    If payload is a transcript_segment, persists it to DB and runs the LangGraph reasoning pipeline.
    """
    if payload.get("type") == "transcript_segment" and payload.get("text"):
        from app.ingestion.transcript_ingestor import transcript_ingestor

        # Ingest, persist, create embedding, run reasoning pipeline, and broadcast
        saved_payload = transcript_ingestor.process_and_save(
            meeting_id=meeting_id,
            speaker_id=str(payload.get("speaker_id") or "1"),
            text_content=payload.get("text", ""),
            start_ms=int(payload.get("start_ms", 0)),
            end_ms=int(payload.get("end_ms", 0)),
            confidence=float(payload.get("confidence", 1.0)),
            speaker_name=payload.get("speaker_name"),
            participant_uuid=payload.get("participant_id"),
        )
        if saved_payload:
            await ws_manager.broadcast(meeting_id, saved_payload)
        return {"status": "ok"}

    await ws_manager.broadcast(meeting_id, payload)
    return {"status": "ok"}
