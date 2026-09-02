"""
Jira Integration Tool.

Creates Jira issue via Jira Cloud REST API v3 or fallback mock for demo/test mode.
Confirmed API (from Jira official REST docs):
    POST https://{domain}.atlassian.net/rest/api/3/issue
    Headers: {"Authorization": "Basic ...", "Content-Type": "application/json"}

Per-session credentials can be passed as kwargs to override global settings/env vars.
"""

import base64
import logging
import os
import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Jira priority label mapping
PRIORITY_MAP = {
    "urgent": "Highest",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}


def create_jira_issue(
    summary: str,
    description: str = "",
    project_key: str | None = None,
    issue_type: str = "Task",
    priority: str = "medium",
    # Per-session credential overrides (from UI config)
    domain: str | None = None,
    email: str | None = None,
    token: str | None = None,
) -> dict:
    """
    Creates an issue in Jira or returns mock response if unconfigured.

    Credential resolution order:
    1. Explicit kwargs (from per-session UI config)
    2. Global settings / env vars
    3. Mock mode (if any credential is missing)
    """
    resolved_domain = domain or settings.jira_domain or os.environ.get("JIRA_DOMAIN", "")
    resolved_email = email or settings.jira_user_email or os.environ.get("JIRA_USER_EMAIL", "")
    resolved_token = token or settings.jira_api_token or os.environ.get("JIRA_API_TOKEN", "")
    resolved_project = project_key or settings.jira_project_key or os.environ.get("JIRA_PROJECT_KEY", "INC")
    jira_priority = PRIORITY_MAP.get(priority.lower(), "Medium")

    if not resolved_domain or not resolved_email or not resolved_token:
        logger.info(
            "[JIRA TOOL (MOCK)] Executed Jira issue creation: '%s' (project=%s, priority=%s)",
            summary[:60],
            resolved_project,
            priority,
        )
        return {
            "status": "created",
            "issue_key": f"{resolved_project}-MOCK",
            "url": f"https://demo.atlassian.net/browse/{resolved_project}-MOCK",
            "summary": summary,
            "priority": priority,
            "mock": True,
        }

    url = f"https://{resolved_domain}/rest/api/3/issue"
    auth_str = base64.b64encode(f"{resolved_email}:{resolved_token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_str}",
        "Content-Type": "application/json",
    }
    payload = {
        "fields": {
            "project": {"key": resolved_project},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description or summary}],
                    }
                ],
            },
            "issuetype": {"name": issue_type},
            "priority": {"name": jira_priority},
        }
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                issue_key = data.get("key")
                logger.info("[JIRA TOOL] Created issue %s (priority=%s)", issue_key, jira_priority)
                return {
                    "status": "created",
                    "issue_key": issue_key,
                    "url": f"https://{resolved_domain}/browse/{issue_key}",
                    "priority": priority,
                    "mock": False,
                }
            else:
                logger.error("Jira API error HTTP %d: %s", resp.status_code, resp.text[:200])
                return {"status": "error", "error": resp.text[:200], "mock": False}
    except Exception as exc:
        logger.error("Jira tool execution failed: %s", exc)
        return {"status": "error", "error": str(exc), "mock": False}

