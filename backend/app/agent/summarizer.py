"""
Spoken Summary Generator for AI Agent.

Queries meeting context from PostgreSQL and uses Gemini LLM to synthesize a concise,
conversational 2-3 sentence spoken status update suitable for out-loud room playback.
"""

import json
import logging
import os
import re
import uuid

from sqlalchemy import create_engine, text

from app.config.settings import settings

logger = logging.getLogger(__name__)


def generate_spoken_summary(meeting_id: str) -> str:
    """
    Generates a 2-3 sentence spoken summary of the current meeting state for room playback.
    """
    raw_url = settings.database_url or os.environ.get("DATABASE_URL", "")
    if not raw_url:
        return ""

    sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url, pool_pre_ping=True)

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        with engine.connect() as conn:
            # Fetch recent facts
            sql_f = text("SELECT content FROM facts WHERE meeting_id = :id ORDER BY created_at DESC LIMIT 5")
            facts = [r[0] for r in conn.execute(sql_f, {"id": meeting_uuid}).fetchall()]

            # Fetch recent decisions
            sql_d = text("SELECT content FROM decisions WHERE meeting_id = :id ORDER BY created_at DESC LIMIT 5")
            decisions = [r[0] for r in conn.execute(sql_d, {"id": meeting_uuid}).fetchall()]

            # Fetch open action items
            sql_a = text("SELECT description FROM action_items WHERE meeting_id = :id AND status = 'open' LIMIT 5")
            action_items = [r[0] for r in conn.execute(sql_a, {"id": meeting_uuid}).fetchall()]

            # Fetch open conflicts
            sql_c = text("SELECT description FROM conflicts WHERE meeting_id = :id AND status = 'open' LIMIT 5")
            conflicts = [r[0] for r in conn.execute(sql_c, {"id": meeting_uuid}).fetchall()]

        if not facts and not decisions and not action_items and not conflicts:
            return ""

        groq_key = settings.groq_api_key or os.environ.get("GROQ_API_KEY", "")
        gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")

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
                    f"Current Incident State:\n"
                    f"Facts: {'; '.join(facts)}\n"
                    f"Decisions: {'; '.join(decisions)}\n"
                    f"Open Action Items: {'; '.join(action_items)}\n"
                    f"Open Conflicts: {'; '.join(conflicts)}\n\n"
                    "Task: Write a concise 2 to 3 sentence spoken summary suitable to read out loud into the room.\n"
                    "Example: 'So far: payment API returning 500s. Deadline conflict between Friday and Wednesday is unresolved. Two action items open.'\n"
                    "DO NOT include markdown formatting or bullet points."
                )

                res = llm.invoke([
                    SystemMessage(content="You are a clear spoken AI assistant giving a 2-sentence voice summary."),
                    HumanMessage(content=prompt)
                ])

                content = res.content
                if isinstance(content, list):
                    content = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in content])
                if isinstance(content, str):
                    summary_text = content.strip()
                    logger.info("[SUMMARIZER] Generated spoken summary: '%s'", summary_text)
                    return summary_text
            except Exception as exc:
                logger.warning("LLM spoken summary generation failed: %s", exc)

        # Fallback summary heuristic if LLM is unavailable
        parts = []
        if facts:
            parts.append(f"So far: {facts[0]}.")
        if conflicts:
            parts.append(f"Unresolved conflict: {conflicts[0]}.")
        if action_items:
            parts.append(f"{len(action_items)} action items open.")

        fallback = " ".join(parts)
        logger.info("[SUMMARIZER] Generated fallback spoken summary: '%s'", fallback)
        return fallback
    except Exception as exc:
        logger.error("Failed to generate spoken summary: %s", exc)
        return ""
    finally:
        engine.dispose()
