"""
Minimal Jira Cloud REST client — raise a ticket directly from Review &
Approve when a mapping/rule is rejected or otherwise needs follow-up.
Optional: if JIRA_* env vars aren't set, callers get a clear
JiraNotConfiguredError instead of a confusing network failure.
"""
import os

import requests

JIRA_URL = os.getenv("JIRA_URL", "").rstrip("/")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "")
JIRA_ISSUE_TYPE = os.getenv("JIRA_ISSUE_TYPE", "Task")


class JiraNotConfiguredError(Exception):
    pass


class JiraError(Exception):
    pass


def is_configured() -> bool:
    return bool(JIRA_URL and JIRA_EMAIL and JIRA_API_TOKEN and JIRA_PROJECT_KEY)


def test_connection() -> dict:
    """Verify credentials by calling /rest/api/3/myself. Returns {"ok": True, "display_name": "..."}.
    Raises JiraNotConfiguredError or JiraError on failure."""
    if not is_configured():
        raise JiraNotConfiguredError(
            "Jira isn't configured — set JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY in .env."
        )
    try:
        resp = requests.get(
            f"{JIRA_URL}/rest/api/3/myself",
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise JiraError(f"Could not reach Jira at {JIRA_URL}: {exc}") from exc
    if resp.status_code != 200:
        raise JiraError(f"Auth failed ({resp.status_code}): {resp.text[:200]}")
    return {"ok": True, "display_name": resp.json().get("displayName", JIRA_EMAIL)}


def create_ticket(summary: str, description: str, labels: list = None) -> dict:
    """Creates a Jira issue via the Cloud REST API v3. Returns {"key": "...", "url": "..."}.

    Raises JiraNotConfiguredError if JIRA_* env vars are missing, or
    JiraError on any API failure (bad auth, invalid project key, etc.).
    """
    if not is_configured():
        raise JiraNotConfiguredError(
            "Jira isn't configured — set JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN, "
            "JIRA_PROJECT_KEY in .env to enable ticket creation."
        )

    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": summary,
            "description": {
                "type": "doc", "version": 1,
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }],
            },
            "issuetype": {"name": JIRA_ISSUE_TYPE},
            **({"labels": labels} if labels else {}),
        }
    }

    try:
        resp = requests.post(
            f"{JIRA_URL}/rest/api/3/issue",
            json=payload,
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise JiraError(f"Could not reach Jira at {JIRA_URL}: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise JiraError(f"Jira returned {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    key = data.get("key", "")
    return {"key": key, "url": f"{JIRA_URL}/browse/{key}" if key else ""}
