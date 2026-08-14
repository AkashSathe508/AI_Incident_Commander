"""
Final Synthesis Node.

Triggered when a meeting ends.
Pulls the complete meeting context (transcripts, facts, assumptions, decisions, action items, conflicts, risks, timeline)
and invokes Gemini LLM to synthesize an executive report including:
- Executive Summary
- Key Timeline Overview
- Root Cause & Impact
- Unresolved Questions & Open Issues
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text

from app.config.settings import settings

logger = logging.getLogger(__name__)


def generate_final_synthesis(meeting_id: str) -> dict[str, Any]:
    """
    Synthesizes a final post-incident report for a completed meeting.
    Pulls full meeting state from PostgreSQL and calls Gemini LLM.
    """
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        logger.warning("DATABASE_URL not set — final synthesis skipped")
        return {}

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    meeting_data = {}
    now_utc = datetime.now(timezone.utc)

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        with engine.connect() as conn:
            # 1. Fetch meeting title & channel
            m_sql = text("SELECT id, title, channel_name, created_at FROM meetings WHERE id = :id")
            m_row = conn.execute(m_sql, {"id": meeting_uuid}).first()
            if not m_row:
                return {}

            # 2. Fetch transcript segments
            t_sql = text("SELECT text, start_ms FROM transcript_segments WHERE meeting_id = :id ORDER BY start_ms ASC")
            t_rows = conn.execute(t_sql, {"id": meeting_uuid}).fetchall()
            transcripts = [r[0] for r in t_rows]

            # 3. Fetch facts
            f_sql = text("SELECT content, confidence FROM facts WHERE meeting_id = :id")
            facts = [r[0] for r in conn.execute(f_sql, {"id": meeting_uuid}).fetchall()]

            # 4. Fetch decisions
            d_sql = text("SELECT content, rationale FROM decisions WHERE meeting_id = :id")
            decisions = [r[0] for r in conn.execute(d_sql, {"id": meeting_uuid}).fetchall()]

            # 5. Fetch action items
            a_sql = text("SELECT description, due_date, status FROM action_items WHERE meeting_id = :id")
            action_items = [f"{r[0]} (Due: {r[1]}, Status: {r[2]})" for r in conn.execute(a_sql, {"id": meeting_uuid}).fetchall()]

            # 6. Fetch conflicts
            c_sql = text("SELECT description, status FROM conflicts WHERE meeting_id = :id")
            conflicts = [r[0] for r in conn.execute(c_sql, {"id": meeting_uuid}).fetchall()]

            # 7. Fetch risks
            r_sql = text("SELECT description, severity, status FROM risks WHERE meeting_id = :id")
            risks = [f"[{r[1].upper()}] {r[0]}" for r in conn.execute(r_sql, {"id": meeting_uuid}).fetchall()]

            meeting_data = {
                "title": m_row[1],
                "transcript_count": len(transcripts),
                "facts": facts,
                "decisions": decisions,
                "action_items": action_items,
                "conflicts": conflicts,
                "risks": risks,
            }

            # Synthesize via Gemini LLM
            groq_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
            gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
            executive_summary = f"Incident Response Call for '{m_row[1]}'. Total transcripts processed: {len(transcripts)}. Facts: {len(facts)}, Decisions: {len(decisions)}, Action Items: {len(action_items)}, Conflicts: {len(conflicts)}."
            unresolved_questions = []

            if groq_key or gemini_key:
                try:
                    from langchain_core.messages import SystemMessage, HumanMessage
                    if groq_key:
                        from langchain_groq import ChatGroq
                        llm = ChatGroq(
                            model_name="llama-3.3-70b-versatile",
                            groq_api_key=groq_key,
                            temperature=0.2,
                        )
                    else:
                        from langchain_google_genai import ChatGoogleGenerativeAI
                        llm = ChatGoogleGenerativeAI(
                            model="gemini-3.5-flash-lite",
                            google_api_key=gemini_key,
                            temperature=0.2,
                            max_retries=0,
                        )

                    prompt = (
                        f"Incident Title: {m_row[1]}\n"
                        f"Transcripts:\n" + "\n".join([f"- {t}" for t in transcripts[:30]]) + "\n\n"
                        f"Extracted Facts:\n" + "\n".join([f"- {f}" for f in facts]) + "\n\n"
                        f"Decisions Made:\n" + "\n".join([f"- {d}" for d in decisions]) + "\n\n"
                        f"Open Conflicts:\n" + "\n".join([f"- {c}" for c in conflicts]) + "\n\n"
                        "Generate a JSON report: {\"executive_summary\": \"...\", \"unresolved_questions\": [\"...\"]}"
                    )

                    res = llm.invoke([
                        SystemMessage(content="You are an AI Incident Commander Executive Synthesizer."),
                        HumanMessage(content=prompt)
                    ])

                    from app.reasoning.parser import parse_llm_json
                    parsed = parse_llm_json(res.content)
                    executive_summary = parsed.get("executive_summary", executive_summary)
                    unresolved_questions = parsed.get("unresolved_questions", [])
                        unresolved_questions = parsed.get("unresolved_questions", [])
                except Exception as exc:
                    logger.warning("Gemini final synthesis LLM call failed: %s", exc)

            if not unresolved_questions and conflicts:
                unresolved_questions = [f"Unresolved conflict requiring escalation: {c}" for c in conflicts]

            report_json = json.dumps({
                "executive_summary": executive_summary,
                "unresolved_questions": unresolved_questions,
                "generated_at": now_utc.isoformat(),
            })

            # Save report JSON into meetings.description
            update_sql = text("UPDATE meetings SET description = :report, ended_at = :ended_at WHERE id = :id")
            conn.execute(update_sql, {"report": report_json, "ended_at": now_utc, "id": meeting_uuid})
            conn.commit()

            logger.info("[FINAL SYNTHESIS] Saved final report for meeting %s", meeting_id[:8])

            return {
                "meeting_id": meeting_id,
                "executive_summary": executive_summary,
                "unresolved_questions": unresolved_questions,
                "facts_count": len(facts),
                "decisions_count": len(decisions),
                "conflicts_count": len(conflicts),
            }
    except Exception as exc:
        logger.error("Failed to generate final synthesis: %s", exc)
        return {}
    finally:
        engine.dispose()
