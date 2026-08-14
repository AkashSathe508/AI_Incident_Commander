"""
PagerDuty Integration Tool.

Triggers incidents via PagerDuty Events API v2 (POST https://events.pagerduty.com/v2/enqueue).
"""

import logging
import os
import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)


def trigger_pagerduty_incident(
    summary: str,
    severity: str = "critical",
    source: str = "AI Incident Commander",
) -> dict:
    """
    Triggers a PagerDuty incident or returns mock response if unconfigured.
    """
    routing_key = settings.pagerduty_routing_key or os.environ.get("PAGERDUTY_ROUTING_KEY", "")

    if not routing_key:
        logger.info(
            "[PAGERDUTY TOOL (MOCK)] Executed PagerDuty incident trigger (severity=%s): '%s'",
            severity,
            summary[:60],
        )
        return {
            "status": "triggered",
            "dedup_key": "pd-mock-key-101",
            "summary": summary,
            "severity": severity,
            "mock": True,
        }

    url = "https://events.pagerduty.com/v2/enqueue"
    payload = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "payload": {
            "summary": summary,
            "severity": severity if severity in ("critical", "error", "warning", "info") else "critical",
            "source": source,
        },
    }

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code in (200, 202):
                data = resp.json()
                logger.info("[PAGERDUTY TOOL] Triggered incident dedup_key=%s", data.get("dedup_key"))
                return {"status": "triggered", "dedup_key": data.get("dedup_key"), "mock": False}
            else:
                logger.error("PagerDuty API error HTTP %d: %s", resp.status_code, resp.text[:200])
                return {"status": "error", "error": resp.text[:200]}
    except Exception as exc:
        logger.error("PagerDuty tool execution failed: %s", exc)
        return {"status": "error", "error": str(exc)}
