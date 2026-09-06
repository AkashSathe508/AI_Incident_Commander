"""
Final Synthesis Node.

Triggered when a meeting ends.
Pulls the complete meeting context (transcripts, facts, assumptions, decisions, action items, conflicts, risks, timeline)
and invokes Gemini/Groq LLM to synthesize a structured post-incident report including:
- Executive Summary
- Key Timeline Overview
- Root Cause & Impact
- Unresolved Questions & Open Issues
- Previous Incident Context (via context_retriever)

IMPORTANT: Stores the result in meetings.summary (NOT meetings.description).
           meetings.description is preserved for user-set text.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text

from app.config.settings import settings

logger = logging.getLogger(__name__)


def generate_final_synthesis(meeting_id: str) -> dict[str, Any]:
    """
    Synthesizes a final post-incident report for a completed meeting.
    Pulls full meeting state from PostgreSQL and calls Gemini/Groq LLM.
    Stores result in meetings.summary column.
    """
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        logger.warning("DATABASE_URL not set — final synthesis skipped")
        return {}

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    meeting_data: dict[str, Any] = {}
    now_utc = datetime.now(timezone.utc)

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        with engine.connect() as conn:
            # 1. Fetch meeting metadata
            m_sql = text(
                "SELECT id, title, channel_name, created_at, started_at, ended_at "
                "FROM meetings WHERE id = :id"
            )
            m_row = conn.execute(m_sql, {"id": meeting_uuid}).first()
            if not m_row:
                return {}

            meeting_title: str = m_row[1]

            # 2. Fetch transcript segments
            t_sql = text(
                "SELECT text, start_ms FROM transcript_segments "
                "WHERE meeting_id = :id ORDER BY start_ms ASC"
            )
            t_rows = conn.execute(t_sql, {"id": meeting_uuid}).fetchall()
            transcripts = [r[0] for r in t_rows]

            # 3. Fetch participants
            p_sql = text("SELECT name, role FROM participants WHERE meeting_id = :id")
            p_rows = conn.execute(p_sql, {"id": meeting_uuid}).fetchall()
            participants = [f"{r[0]} ({r[1] or 'participant'})" for r in p_rows]

            # 4. Fetch facts
            f_sql = text("SELECT content, confidence FROM facts WHERE meeting_id = :id")
            facts_raw = conn.execute(f_sql, {"id": meeting_uuid}).fetchall()
            facts = [r[0] for r in facts_raw]

            # 5. Fetch hypotheses/assumptions
            a_sql = text("SELECT content, status FROM assumptions WHERE meeting_id = :id")
            assumptions_raw = conn.execute(a_sql, {"id": meeting_uuid}).fetchall()
            assumptions = [f"[{r[1].upper()}] {r[0]}" for r in assumptions_raw]

            # 6. Fetch decisions
            d_sql = text("SELECT content, rationale FROM decisions WHERE meeting_id = :id")
            decisions_raw = conn.execute(d_sql, {"id": meeting_uuid}).fetchall()
            decisions = [r[0] for r in decisions_raw]

            # 7. Fetch action items with assignees
            a_sql = text(
                "SELECT ai.description, ai.status, p.name "
                "FROM action_items ai "
                "LEFT JOIN participants p ON ai.assignee_id = p.id "
                "WHERE ai.meeting_id = :id"
            )
            action_rows = conn.execute(a_sql, {"id": meeting_uuid}).fetchall()
            action_items = [
                f"{r[0]} (Owner: {r[2] or 'Unassigned'}, Status: {r[1]})"
                for r in action_rows
            ]

            # 8. Fetch conflicts
            c_sql = text("SELECT description, status FROM conflicts WHERE meeting_id = :id")
            conflicts_raw = conn.execute(c_sql, {"id": meeting_uuid}).fetchall()
            conflicts = [f"[{r[1].upper()}] {r[0]}" for r in conflicts_raw]

            # 9. Fetch risks
            r_sql = text("SELECT description, severity, status FROM risks WHERE meeting_id = :id")
            risks_raw = conn.execute(r_sql, {"id": meeting_uuid}).fetchall()
            risks = [f"[{r[1].upper()}] {r[0]}" for r in risks_raw]

            # 10. Fetch pending approvals (with execution result)
            pa_sql = text(
                "SELECT title, action_type, status, execution_result "
                "FROM pending_approvals WHERE meeting_id = :id"
            )
            pa_rows = conn.execute(pa_sql, {"id": meeting_uuid}).fetchall()
            approvals = [
                f"{r[0]} ({r[1].upper()}) — {r[2]}"
                + (f" → {json.dumps(r[3])[:80]}" if r[3] else "")
                for r in pa_rows
            ]

            meeting_data = {
                "title": meeting_title,
                "participants": participants,
                "transcript_count": len(transcripts),
                "facts": facts,
                "assumptions": assumptions,
                "decisions": decisions,
                "action_items": action_items,
                "conflicts": conflicts,
                "risks": risks,
                "approvals": approvals,
            }

        # 11. Retrieve cross-meeting context (compact, token-efficient)
        previous_context_text = ""
        try:
            from app.reasoning.context_retriever import retrieve_relevant_context
            ctx = retrieve_relevant_context(meeting_id)
            if ctx.get("has_context"):
                previous_context_text = ctx.get("compact_context_text", "")
        except Exception as ctx_exc:
            logger.warning("[FINAL SYNTHESIS] Context retrieval failed: %s", ctx_exc)

        # 12. Generate synthesis via LLM
        groq_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
        gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")

        # Default summary if LLM unavailable
        executive_summary = (
            f"Incident Response Call for '{meeting_title}'. "
            f"Participants: {len(participants)}. "
            f"Facts: {len(facts)}, Decisions: {len(decisions)}, "
            f"Action Items: {len(action_items)}, Conflicts: {len(conflicts)}."
        )
        unresolved_questions: list[str] = []

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
                        model="gemini-2.0-flash",
                        google_api_key=gemini_key,
                        temperature=0.2,
                        max_retries=0,
                    )

                prompt_sections = [
                    f"Incident Title: {meeting_title}",
                    f"Participants: {', '.join(participants) or 'Not recorded'}",
                ]
                if transcripts:
                    prompt_sections.append(
                        "Transcript Excerpts:\n"
                        + "\n".join([f"- {t}" for t in transcripts[:20]])
                    )
                if facts:
                    prompt_sections.append(
                        "Extracted Facts:\n" + "\n".join([f"- {f}" for f in facts])
                    )
                if assumptions:
                    prompt_sections.append(
                        "Hypotheses:\n" + "\n".join([f"- {a}" for a in assumptions])
                    )
                if decisions:
                    prompt_sections.append(
                        "Decisions Made:\n" + "\n".join([f"- {d}" for d in decisions])
                    )
                if action_items:
                    prompt_sections.append(
                        "Action Items:\n" + "\n".join([f"- {a}" for a in action_items])
                    )
                if conflicts:
                    prompt_sections.append(
                        "Conflicts/Contradictions:\n" + "\n".join([f"- {c}" for c in conflicts])
                    )
                if risks:
                    prompt_sections.append(
                        "Risks:\n" + "\n".join([f"- {r}" for r in risks])
                    )
                if approvals:
                    prompt_sections.append(
                        "Approved Actions:\n" + "\n".join([f"- {a}" for a in approvals])
                    )
                if previous_context_text:
                    prompt_sections.append(previous_context_text)

                prompt_sections.append(
                    "\nGenerate a structured JSON post-incident summary with these EXACT keys:\n"
                    '{"executive_summary": "...", '
                    '"key_findings": ["...", "..."], '
                    '"current_hypothesis": "...", '
                    '"decisions_made": ["..."], '
                    '"actions_taken": ["..."], '
                    '"current_status": "...", '
                    '"unresolved_questions": ["..."], '
                    '"next_steps": ["..."]}'
                )

                prompt = "\n\n".join(prompt_sections)

                res = llm.invoke([
                    SystemMessage(content=(
                        "You are an AI Incident Commander post-incident synthesizer. "
                        "Generate factual, concise summaries ONLY from provided data. "
                        "If information is not available, write 'Not determined during this meeting.' "
                        "Do NOT invent facts."
                    )),
                    HumanMessage(content=prompt),
                ])

                from app.reasoning.parser import parse_llm_json
                parsed = parse_llm_json(res.content)
                executive_summary = parsed.get("executive_summary", executive_summary)
                unresolved_questions = parsed.get("unresolved_questions", [])

                # Build a rich structured summary text
                structured_parts = [executive_summary]
                if parsed.get("key_findings"):
                    structured_parts.append(
                        "Key Findings:\n" + "\n".join([f"• {f}" for f in parsed["key_findings"]])
                    )
                if parsed.get("current_hypothesis"):
                    structured_parts.append(f"Hypothesis: {parsed['current_hypothesis']}")
                if parsed.get("decisions_made"):
                    structured_parts.append(
                        "Decisions:\n" + "\n".join([f"• {d}" for d in parsed["decisions_made"]])
                    )
                if parsed.get("actions_taken"):
                    structured_parts.append(
                        "Actions:\n" + "\n".join([f"• {a}" for a in parsed["actions_taken"]])
                    )
                if parsed.get("current_status"):
                    structured_parts.append(f"Status: {parsed['current_status']}")
                if parsed.get("next_steps"):
                    structured_parts.append(
                        "Next Steps:\n" + "\n".join([f"• {s}" for s in parsed["next_steps"]])
                    )

                executive_summary = "\n\n".join(structured_parts)

            except Exception as exc:
                logger.warning("[FINAL SYNTHESIS] LLM call failed: %s", exc)

        if not unresolved_questions and conflicts:
            unresolved_questions = [
                f"Unresolved conflict requiring escalation: {c}"
                for c in conflicts
            ]

        # 13. Store synthesis in meetings.summary (NOT description)
        with engine.connect() as conn:
            update_sql = text(
                "UPDATE meetings SET summary = :summary, ended_at = :ended_at WHERE id = :id"
            )
            conn.execute(
                update_sql,
                {"summary": executive_summary, "ended_at": now_utc, "id": meeting_uuid},
            )
            conn.commit()

        # 14. Store as an AI Response too
        try:
            from app.reasoning.ai_response_store import store_ai_response
            store_ai_response(
                meeting_id=meeting_id,
                response_text=executive_summary[:2000],
                response_type="summary",
                trigger="meeting_ended",
            )
        except Exception as _exc:
            logger.debug("[FINAL SYNTHESIS] AI response store failed: %s", _exc)

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
        logger.error("[FINAL SYNTHESIS] Failed: %s", exc)
        return {}
    finally:
        engine.dispose()
