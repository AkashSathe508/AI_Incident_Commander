"""
Robust LLM JSON Parser Utility.

Extracts and parses JSON object or array payloads from arbitrary LLM model responses
(handling markdown backticks, conversational preambles, and raw text).
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def parse_llm_json(content: Any) -> dict[str, Any]:
    """
    Safely parses JSON from an LLM response string or content list.
    """
    if isinstance(content, list):
        content = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in content])

    if not isinstance(content, str) or not content.strip():
        return {}

    text_str = content.strip()

    # 1. Direct JSON parse try
    try:
        data = json.loads(text_str)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # 2. Regex extract JSON object {...} or array [...]
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text_str)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return {"items": data}
        except Exception:
            pass

    # 3. Clean backticks fallback
    clean = re.sub(r"```json\s*|\s*```", "", text_str).strip()
    try:
        data = json.loads(clean)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.debug("Failed to parse LLM JSON response: %s | text='%s'", exc, text_str[:100])

    return {}
