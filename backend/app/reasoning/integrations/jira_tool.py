"""
Jira Integration Tool.

Creates Jira issue via Jira Cloud REST API v3 or fallback mock for demo/test mode.
Confirmed API (from Jira official REST docs):
    POST https://{domain}/rest/api/3/issue
    Headers: {"Authorization": "Basic ...", "Content-Type": "application/json"}

Per-session credentials can be passed as kwargs to override global settings/env vars.
"""

import base64
import logging
import os
import re
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

# Valid Atlassian domain pattern — must end in .atlassian.net or be a custom domain
_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]+)\.[a-zA-Z]{2,}$")


def _is_valid_domain(domain: str) -> bool:
    """Returns True only if domain looks like a real resolvable hostname."""
    d = domain.strip()
    if not d:
        return False
    # Reject obviously bad values (spaces, placeholders, comments)
    if " " in d or d.startswith("#") or d.startswith("e.g"):
        return False
    return bool(_DOMAIN_RE.match(d))


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
    3. Mock mode (if any credential is missing or domain is invalid)
    """
    resolved_domain = (domain or settings.jira_domain or os.environ.get("JIRA_DOMAIN", "")).strip()
    resolved_email = (email or settings.jira_user_email or os.environ.get("JIRA_USER_EMAIL", "")).strip()
    resolved_token = (token or settings.jira_api_token or os.environ.get("JIRA_API_TOKEN", "")).strip()
    resolved_project = (project_key or settings.jira_project_key or os.environ.get("JIRA_PROJECT_KEY", "INC")).strip()
    jira_priority = PRIORITY_MAP.get(priority.lower(), "Medium")

    # Guard: if credentials are incomplete or domain is invalid, return mock
    domain_valid = _is_valid_domain(resolved_domain)
    credentials_complete = bool(resolved_domain and resolved_email and resolved_token and domain_valid)

    if not credentials_complete:
        missing = []
        if not resolved_domain or not domain_valid:
            missing.append("JIRA_DOMAIN (e.g. yourcompany.atlassian.net)")
        if not resolved_email:
            missing.append("JIRA_USER_EMAIL")
        if not resolved_token:
            missing.append("JIRA_API_TOKEN")

        logger.info(
            "[JIRA TOOL (MOCK)] Missing credentials [%s] — returning mock issue for '%s' (project=%s, priority=%s)",
            ", ".join(missing),
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
            "mock_reason": f"Missing: {', '.join(missing)}",
        }

    url = f"https://{resolved_domain}/rest/api/3/issue"
    auth_str = base64.b64encode(f"{resolved_email}:{resolved_token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_str}",
        "Content-Type": "application/json",
        "Accept": "application/json",
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
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                issue_key = data.get("key", "UNKNOWN")
                issue_url = f"https://{resolved_domain}/browse/{issue_key}"
                logger.info("[JIRA TOOL] Created issue %s at %s (priority=%s)", issue_key, issue_url, jira_priority)
                return {
                    "status": "created",
                    "issue_key": issue_key,
                    "url": issue_url,
                    "summary": summary,
                    "priority": priority,
                    "mock": False,
                }
            elif resp.status_code == 401:
                logger.error("Jira authentication failed (401) — email/token mismatch for domain %s", resolved_domain)
                return {
                    "status": "error",
                    "error": (
                        f"Jira authentication failed (401). "
                        f"The email '{resolved_email}' doesn't match the Atlassian account that owns the API token. "
                        f"Check your correct email at https://id.atlassian.com/manage-profile/ and update JIRA_USER_EMAIL in .env."
                    ),
                    "mock": False,
                }
            elif resp.status_code == 403:
                logger.error("Jira permission denied (403) for domain %s", resolved_domain)
                return {
                    "status": "error",
                    "error": f"Jira permission denied (403). Ensure the account '{resolved_email}' has project creation rights on {resolved_domain}.",
                    "mock": False,
                }
            else:
                error_body = resp.text[:300]
                logger.error("Jira API error HTTP %d: %s", resp.status_code, error_body)
                return {
                    "status": "error",
                    "error": f"Jira API returned HTTP {resp.status_code}: {error_body}",
                    "mock": False,
                }
    except httpx.ConnectError as exc:
        logger.error("Jira connection failed — check JIRA_DOMAIN '%s': %s", resolved_domain, exc)
        return {
            "status": "error",
            "error": f"Could not connect to Jira at '{resolved_domain}'. Check that JIRA_DOMAIN is correct (e.g. yourcompany.atlassian.net).",
            "mock": False,
        }
    except Exception as exc:
        logger.error("Jira tool execution failed: %s", exc)
        return {"status": "error", "error": str(exc), "mock": False}
