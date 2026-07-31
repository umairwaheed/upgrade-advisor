"""Step 2 — read what the maintainers actually said about each release.

This is the part that genuinely wants a browser. GitHub's releases page is
JS-rendered, paginated, and rate-limits anonymous API traffic; AgentCore
Browser gives us a real Chromium in the cloud with no infrastructure to run.

If Playwright isn't installed or the browser session can't be established, we
fall back to the GitHub REST API from inside the sandbox and say so in the
result — a degraded source is reported, never silently substituted.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from bedrock_agentcore.tools.browser_client import browser_session
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

from .config import REGION
from .sandbox import run_python_json

logger = logging.getLogger(__name__)

_MAX_NOTE_CHARS = 4000


def _wanted_versions(ladder: List[Dict[str, Any]], limit: int) -> List[str]:
    """Newest releases first — those carry the migration-relevant notes."""
    return [r["version"] for r in reversed(ladder)][:limit]


def _normalise(tag: str) -> str:
    return tag.strip().lstrip("vV")


def _read_via_browser(repo_url: str, versions: List[str]) -> Dict[str, str]:
    """Drive AgentCore Browser over CDP to scrape the releases page."""
    from playwright.sync_api import sync_playwright  # imported late: optional dep

    wanted = {_normalise(v) for v in versions}
    notes: Dict[str, str] = {}

    with browser_session(REGION) as browser_client:
        ws_url, headers = browser_client.generate_ws_headers()

        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(ws_url, headers=headers)
            try:
                ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                page = ctx.pages[0] if ctx.pages else ctx.new_page()

                # Two pages of releases covers all but the most prolific projects.
                for page_num in (1, 2):
                    if wanted <= set(notes):
                        break
                    page.goto(
                        f"{repo_url}/releases?page={page_num}",
                        wait_until="domcontentloaded",
                        timeout=60_000,
                    )
                    page.wait_for_timeout(1_500)

                    for section in page.locator("section, .Box--condensed").all():
                        try:
                            text = section.inner_text(timeout=5_000)
                        except Exception:  # noqa: BLE001 - skip detached nodes
                            continue
                        head = text[:200]
                        for version in wanted - set(notes):
                            if re.search(rf"(?<![\w.]){re.escape(version)}(?![\w.])", head):
                                notes[version] = text[:_MAX_NOTE_CHARS]
                                break
            finally:
                browser.close()

    return notes


def _read_via_api(
    client: CodeInterpreter, repo_url: str, versions: List[str]
) -> Dict[str, str]:
    """Fallback: unauthenticated GitHub REST, executed inside the sandbox."""
    code = f"""
        import json, re, urllib.request
        REPO = {repo_url!r}
        WANTED = {{v.lstrip('vV') for v in {versions!r}}}
        m = re.search(r"github\\.com/([\\w.\\-]+)/([\\w.\\-]+)", REPO)
        notes = {{}}
        if m:
            owner, repo = m.group(1), m.group(2)
            url = "https://api.github.com/repos/{{}}/{{}}/releases?per_page=100".format(owner, repo)
            req = urllib.request.Request(url, headers={{
                "User-Agent": "upgrade-advisor/1.0",
                "Accept": "application/vnd.github+json",
            }})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    for rel in json.loads(r.read().decode("utf-8")):
                        tag = (rel.get("tag_name") or "").lstrip("vV")
                        if tag in WANTED:
                            notes[tag] = ((rel.get("name") or "") + "\\n" +
                                          (rel.get("body") or ""))[:{_MAX_NOTE_CHARS}]
            except Exception as exc:
                notes["__error__"] = str(exc)
        emit(notes)
    """
    result = run_python_json(client, code)
    result.pop("__error__", None)
    return result


def read_release_notes(
    client: CodeInterpreter,
    repo_url: Optional[str],
    ladder: List[Dict[str, Any]],
    limit: int = 12,
) -> Dict[str, Any]:
    """Collect maintainer release notes for the versions in the upgrade path."""
    if not repo_url:
        return {"source": "none", "reason": "no source repository found", "notes": {}}

    versions = _wanted_versions(ladder, limit)
    if not versions:
        return {"source": "none", "reason": "no releases in path", "notes": {}}

    try:
        notes = _read_via_browser(repo_url, versions)
        if notes:
            return {"source": "agentcore-browser", "repo_url": repo_url, "notes": notes}
        reason = "browser session returned no matching releases"
    except Exception as exc:  # noqa: BLE001 - fallback is the point
        logger.warning("browser path unavailable (%s); falling back to REST", exc)
        reason = f"browser unavailable: {exc}"

    notes = _read_via_api(client, repo_url, versions)
    return {
        "source": "github-api-fallback",
        "degraded_from_browser": reason,
        "repo_url": repo_url,
        "notes": notes,
    }
