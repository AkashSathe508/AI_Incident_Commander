"""
Jira Integration Tool.

Creates Jira issue via Jira Cloud REST API v3 or fallback mock for demo/test mode.
Confirmed API (from Jira official REST docs):
    POST https://{domain}.atlassian.net/rest/api/3/issue
    Headers: {"Authorization": "Basic ...", "Content-Type": "application/json"}
"""

import base64
import logging
import os
import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)


def create_jira_issue(
    summary: str,
    description: str = "",
    project_key: str = "INC",
    issue_type: str = "Task",
) -> dict:
    """
    Creates an issue in Jira or returns mock response if unconfigured.
    """
    domain = os.environ.get("JIRA_DOMAIN", "")
    email = os.environ.get("JIRA_USER_EMAIL", "")
    token = settings.jira_api_token or os.environ.get("JIRA_API_TOKEN", "")

    if not domain or not email or not token:
        logger.info(
            "[JIRA TOOL (MOCK)] Executed Jira issue creation: '%s' (project=%s)",
            summary[:60],
            project_key,
        )
        return {
            "status": "created",
            "issue_key": f"{project_key}-101",
            "url": f"https://{domain or 'demo'}.atlassian.net/browse/{project_key}-101",
            "summary": summary,
            "mock": True,
        }

    url = f"https://{domain}.atlassian.net/rest/api/3/issue"
    auth_str = base64.b64encode(f"{email}:{token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_str}",
        "Content-Type": "application/json",
    }
    payload = {
        "fields": {
            "project": {"key": project_key},
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
        }
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                logger.info("[JIRA TOOL] Created issue %s", data.get("key"))
                return {
                    "status": "created",
                    "issue_key": data.get("key"),
                    "url": f"https://{domain}.atlassian.net/browse/{data.get('key')}",
                    "mock": False,
                }
            else:
                logger.error("Jira API error HTTP %d: %s", resp.status_code, resp.text[:200])
                return {"status": "error", "error": resp.text[:200]}
    except Exception as exc:
        logger.error("Jira tool execution failed: %s", exc)
        return {"status": "error", "error": str(exc)}
