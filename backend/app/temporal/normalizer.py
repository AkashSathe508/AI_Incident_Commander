"""
Temporal Normalization Module.

Normalizes temporal expressions in transcript text (absolute & relative dates/times).
- Absolute dates parsed via python-dateutil parser
- Relative dates ("by Friday", "next week", "in 2 hours", "tomorrow") parsed via deterministic rules
- LLM fallback for ambiguous phrasing ("end of Q3")
- Never silently guesses — sets resolved=False for unresolved dates
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from dateutil import parser as dateutil_parser

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Days of week mapping (0 = Monday, ..., 6 = Sunday)
WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


class TemporalNormalizer:
    """
    Normalizes text time references to UTC timestamps.
    """

    def normalize(
        self,
        expression: str,
        ref_time: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Normalizes a temporal expression string.

        Returns:
            {
                "resolved": bool,
                "timestamp": datetime | None,
                "raw_expression": str,
                "resolution_method": "absolute" | "relative_rule" | "llm" | "unresolved"
            }
        """
        if not expression or not expression.strip():
            return {
                "resolved": False,
                "timestamp": None,
                "raw_expression": "",
                "resolution_method": "unresolved",
            }

        raw = expression.strip()
        ref = ref_time or datetime.now(timezone.utc)

        # 1. Try absolute parsing via dateutil
        abs_res = self._try_absolute_parse(raw, ref)
        if abs_res["resolved"]:
            return abs_res

        # 2. Try deterministic relative rule-based parsing
        rel_res = self._try_relative_rules(raw, ref)
        if rel_res["resolved"]:
            return rel_res

        # 3. LLM fallback for ambiguous phrasing if Gemini API key is present
        gemini_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            llm_res = self._try_llm_parse(raw, ref, gemini_key)
            if llm_res["resolved"]:
                return llm_res

        # 4. Unresolved fallback — never silently guess
        return {
            "resolved": False,
            "timestamp": None,
            "raw_expression": raw,
            "resolution_method": "unresolved",
        }

    def _try_absolute_parse(self, text_str: str, ref: datetime) -> dict[str, Any]:
        """Attempts to parse absolute date formats (e.g., '2026-08-15', 'August 15th, 2026')."""
        try:
            # Avoid parsing isolated numbers or small words as dates
            if text_str.isdigit() and len(text_str) < 4:
                return {"resolved": False, "timestamp": None, "raw_expression": text_str, "resolution_method": "unresolved"}

            dt = dateutil_parser.parse(text_str, default=ref, fuzzy=True)
            # Ensure timezone awareness
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            return {
                "resolved": True,
                "timestamp": dt,
                "raw_expression": text_str,
                "resolution_method": "absolute",
            }
        except (ValueError, OverflowError):
            return {
                "resolved": False,
                "timestamp": None,
                "raw_expression": text_str,
                "resolution_method": "unresolved",
            }

    def _try_relative_rules(self, text_str: str, ref: datetime) -> dict[str, Any]:
        """Parses common relative date rules ('today', 'tomorrow', 'next week', 'by Friday', 'in N hours')."""
        low = text_str.lower().strip()

        if low in ("today", "now"):
            return {"resolved": True, "timestamp": ref, "raw_expression": text_str, "resolution_method": "relative_rule"}

        if low == "tomorrow":
            dt = ref + timedelta(days=1)
            return {"resolved": True, "timestamp": dt, "raw_expression": text_str, "resolution_method": "relative_rule"}

        if low in ("yesterday",):
            dt = ref - timedelta(days=1)
            return {"resolved": True, "timestamp": dt, "raw_expression": text_str, "resolution_method": "relative_rule"}

        if "next week" in low:
            dt = ref + timedelta(days=7)
            return {"resolved": True, "timestamp": dt, "raw_expression": text_str, "resolution_method": "relative_rule"}

        # Relative hours/days/minutes (e.g. "in 2 hours", "in 3 days")
        m_in = re.search(r"in\s+(\d+)\s*(hour|hr|minute|min|day|week)s?", low)
        if m_in:
            val = int(m_in.group(1))
            unit = m_in.group(2)
            if "hour" in unit or "hr" in unit:
                dt = ref + timedelta(hours=val)
            elif "min" in unit:
                dt = ref + timedelta(minutes=val)
            elif "day" in unit:
                dt = ref + timedelta(days=val)
            elif "week" in unit:
                dt = ref + timedelta(weeks=val)
            else:
                dt = ref
            return {"resolved": True, "timestamp": dt, "raw_expression": text_str, "resolution_method": "relative_rule"}

        # Weekday match (e.g., "by Friday", "next Tuesday", "on Wednesday")
        for day_name, day_idx in WEEKDAYS.items():
            if re.search(rf"\b{day_name}\b", low):
                current_day = ref.weekday()
                days_ahead = day_idx - current_day
                if days_ahead <= 0:  # Target day already happened this week, move to next week
                    days_ahead += 7
                if "next" in low and days_ahead < 7:
                    days_ahead += 7
                dt = ref + timedelta(days=days_ahead)
                return {"resolved": True, "timestamp": dt, "raw_expression": text_str, "resolution_method": "relative_rule"}

        return {"resolved": False, "timestamp": None, "raw_expression": text_str, "resolution_method": "unresolved"}

    def _try_llm_parse(self, text_str: str, ref: datetime, api_key: str) -> dict[str, Any]:
        """LLM fallback for genuinely ambiguous temporal phrases ('end of Q3')."""
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import SystemMessage, HumanMessage

            llm = ChatGoogleGenerativeAI(
                model="gemini-3.5-flash-lite",
                google_api_key=api_key,
                temperature=0.0,
                max_retries=0,
            )

            prompt = (
                f"Reference datetime: {ref.isoformat()}\n"
                f"Temporal phrase: \"{text_str}\"\n\n"
                "If the phrase refers to a clear time or date, return ISO 8601 string: {\"resolved\": true, \"iso_datetime\": \"YYYY-MM-DDTHH:MM:SSZ\"}.\n"
                "If the phrase is vague/ambiguous (e.g. 'sometime later'), return {\"resolved\": false}."
            )

            res = llm.invoke([
                SystemMessage(content="You are a precise temporal resolution parser."),
                HumanMessage(content=prompt)
            ])

            content = res.content
            if isinstance(content, list):
                content = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in content])
            if isinstance(content, str):
                import json
                clean_json = re.sub(r"```json\s*|\s*```", "", content).strip()
                data = json.loads(clean_json)
                if data.get("resolved") and data.get("iso_datetime"):
                    dt = datetime.fromisoformat(data["iso_datetime"].replace("Z", "+00:00"))
                    return {
                        "resolved": True,
                        "timestamp": dt,
                        "raw_expression": text_str,
                        "resolution_method": "llm",
                    }
        except Exception as exc:
            logger.warning("LLM temporal resolution failed: %s", exc)

        return {"resolved": False, "timestamp": None, "raw_expression": text_str, "resolution_method": "unresolved"}


# Singleton normalizer
temporal_normalizer = TemporalNormalizer()
