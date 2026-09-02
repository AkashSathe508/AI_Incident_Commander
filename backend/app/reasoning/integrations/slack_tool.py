"""
Slack Integration Tool.

Posts messages to Slack via Incoming Webhooks or Slack Web API chat.postMessage.

Per-session credentials can be passed as kwargs to override global settings/env vars.
"""

import logging
import os
import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)


def post_slack_message(
    message: str,
    channel: str | None = None,
    # Per-session credential overrides (from UI config)
    webhook_url: str | None = None,
    bot_token: str | None = None,
) -> dict:
    """
    Posts a message to Slack or returns mock response if unconfigured.

    Credential resolution order:
    1. Explicit kwargs (from per-session UI config)
    2. Global settings / env vars
    3. Mock mode (if no credentials found)
    """
    resolved_webhook = webhook_url or settings.slack_webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "")
    resolved_token = bot_token or settings.slack_bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
    resolved_channel = channel or settings.slack_default_channel or os.environ.get("SLACK_DEFAULT_CHANNEL", "#incidents")

    if not resolved_webhook and not resolved_token:
        logger.info(
            "[SLACK TOOL (MOCK)] Executed Slack message post to %s: '%s'",
            resolved_channel,
            message[:60],
        )
        return {
            "status": "posted",
            "channel": resolved_channel,
            "message": message,
            "ts": "1700000000.000100",
            "mock": True,
        }

    try:
        with httpx.Client(timeout=8.0) as client:
            if resolved_webhook:
                payload = {"text": message}
                if resolved_channel:
                    payload["channel"] = resolved_channel
                resp = client.post(resolved_webhook, json=payload)
                if resp.status_code == 200:
                    logger.info("[SLACK TOOL] Posted message via Webhook to %s", resolved_channel)
                    return {"status": "posted", "channel": resolved_channel, "mock": False}
                else:
                    logger.error("Slack Webhook error HTTP %d: %s", resp.status_code, resp.text[:100])
                    return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:100]}", "mock": False}
            elif resolved_token:
                headers = {"Authorization": f"Bearer {resolved_token}"}
                api_payload = {"channel": resolved_channel, "text": message}
                resp = client.post("https://slack.com/api/chat.postMessage", headers=headers, json=api_payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        logger.info("[SLACK TOOL] Posted message via Web API (ts=%s)", data.get("ts"))
                        return {"status": "posted", "ts": data.get("ts"), "channel": resolved_channel, "mock": False}
                    else:
                        logger.error("Slack Web API error: %s", data.get("error"))
                        return {"status": "error", "error": data.get("error", "Unknown Slack API error"), "mock": False}
    except Exception as exc:
        logger.error("Slack tool execution failed: %s", exc)
        return {"status": "error", "error": str(exc), "mock": False}

    return {"status": "error", "error": "Failed to send Slack message", "mock": False}

