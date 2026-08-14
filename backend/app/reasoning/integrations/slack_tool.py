"""
Slack Integration Tool.

Posts messages to Slack via Incoming Webhooks or Slack Web API chat.postMessage.
"""

import logging
import os
import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)


def post_slack_message(
    message: str,
    channel: str = "#incident-room",
) -> dict:
    """
    Posts a message to Slack or returns mock response if unconfigured.
    """
    webhook_url = settings.slack_webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "")
    bot_token = settings.slack_bot_token or os.environ.get("SLACK_BOT_TOKEN", "")

    if not webhook_url and not bot_token:
        logger.info(
            "[SLACK TOOL (MOCK)] Executed Slack message post to %s: '%s'",
            channel,
            message[:60],
        )
        return {
            "status": "posted",
            "channel": channel,
            "message": message,
            "ts": "1700000000.000100",
            "mock": True,
        }

    try:
        with httpx.Client(timeout=8.0) as client:
            if webhook_url:
                resp = client.post(webhook_url, json={"text": message})
                if resp.status_code == 200:
                    logger.info("[SLACK TOOL] Posted message via Webhook")
                    return {"status": "posted", "channel": channel, "mock": False}
            elif bot_token:
                headers = {"Authorization": f"Bearer {bot_token}"}
                payload = {"channel": channel, "text": message}
                resp = client.post("https://slack.com/api/chat.postMessage", headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info("[SLACK TOOL] Posted message via Web API (ts=%s)", data.get("ts"))
                    return {"status": "posted", "ts": data.get("ts"), "mock": False}
    except Exception as exc:
        logger.error("Slack tool execution failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    return {"status": "error", "error": "Failed to send Slack message"}
