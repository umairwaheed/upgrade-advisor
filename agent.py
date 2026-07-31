"""Upgrade Advisor — AgentCore Runtime entrypoint.

    POST /invocations
    {"package": "requests", "ecosystem": "pypi", "current": "2.28.0", "target": "latest"}

The model orchestrates four tools; every fact in the answer comes from one of
them. Four AgentCore primitives are doing the work:

  Runtime          this file — serverless, session-isolated, `agentcore deploy`
  Code Interpreter registry lookups and risk scoring (advisor/registry, /risk)
  Browser          reading maintainer release notes (advisor/changelog)
  Memory           the team's prior upgrade decisions (advisor/memory)
"""

import base64
import contextvars
import json
import logging
from typing import Any, Dict, Optional

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

from advisor import changelog, registry, risk
from advisor.config import ACTOR_ID, MODEL_ID, REGION
from advisor.memory import DecisionLog
from advisor.sandbox import sandbox
from advisor.verdict import Verdict

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("upgrade-advisor")

app = BedrockAgentCoreApp()

# Per-invocation state. A contextvar (not a global) so concurrent sessions in
# one Runtime container never share a sandbox or a chart.
_ctx: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar("run_ctx")

SYSTEM_PROMPT = """\
You are a dependency upgrade advisor for a software team. You answer one
question: should this team take this upgrade now, and what will it cost them?

Work in this order:
1. get_release_ladder  — establish exactly which releases the upgrade traverses.
2. recall_prior_decisions — check what this team decided about similar upgrades.
3. read_release_notes  — read what the maintainers said about those releases.
4. score_upgrade_risk  — get the computed risk score.

Rules:
- Never invent a version number, a release date, or a breaking change. If a
  tool did not report it, it is not evidence.
- Copy risk_score and risk_band verbatim from score_upgrade_risk. Do not
  re-derive or adjust them; if you disagree, argue in the rationale.
- Missing release notes are a finding, not an absence of risk. Say so.
- Precedent from memory should shift your recommendation, not your score. If
  the team has deferred this package before, address why that reasoning does
  or does not still hold.
- Be specific and short. "Pydantic 2.0 removed `parse_obj`" beats "there are
  breaking changes in the v2 line".
"""


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------
@tool
def get_release_ladder(package: str, ecosystem: str, current: str, target: str = "latest") -> str:
    """Look up every release between the current and target version.

    Runs against the live package registry inside the Code Interpreter sandbox.

    Args:
        package: Package name, e.g. "requests" or "express".
        ecosystem: Either "pypi" or "npm".
        current: The version currently pinned, e.g. "2.28.0".
        target: Version to upgrade to, or "latest".

    Returns:
        JSON with the ordered release ladder, major-version bumps, yanked
        releases in the path, version ages, and the source repository URL.
    """
    ctx = _ctx.get()
    facts = registry.fetch_release_ladder(ctx["sandbox"], package, ecosystem, current, target)
    ctx["facts"] = facts
    return json.dumps(facts)


@tool
def read_release_notes(limit: int = 12) -> str:
    """Read the maintainers' release notes for the versions in the upgrade path.

    Uses the AgentCore Browser to read the project's GitHub releases page.
    Falls back to the GitHub REST API if the browser is unavailable, and
    reports which source was used.

    Args:
        limit: How many of the most recent releases in the path to read.

    Returns:
        JSON mapping version -> release note text, plus the source used.
    """
    ctx = _ctx.get()
    facts = ctx.get("facts")
    if not facts:
        return json.dumps({"error": "call get_release_ladder first"})

    result = changelog.read_release_notes(
        ctx["sandbox"], facts.get("repo_url"), facts.get("ladder", []), limit=limit
    )
    ctx["notes"] = result.get("notes", {})
    ctx["notes_source"] = result.get("source")
    return json.dumps(result)


@tool
def score_upgrade_risk() -> str:
    """Compute a reproducible risk score from the ladder and the release notes.

    Scoring runs in the sandbox, so the same inputs always produce the same
    number and every point is attributed to a named signal.

    Returns:
        JSON with risk_score, risk_band, a point-by-point breakdown, and the
        per-version signal counts.
    """
    ctx = _ctx.get()
    facts = ctx.get("facts")
    if not facts:
        return json.dumps({"error": "call get_release_ladder first"})

    result, chart = risk.score_upgrade(ctx["sandbox"], facts, ctx.get("notes", {}))
    ctx["risk"] = result
    ctx["chart"] = chart
    return json.dumps({k: v for k, v in result.items() if k != "per_version"})


@tool
def recall_prior_decisions(query: str) -> str:
    """Retrieve this team's past upgrade decisions from AgentCore Memory.

    Args:
        query: What to look for, e.g. "pydantic major version upgrade".

    Returns:
        JSON list of remembered decisions, or an empty list on a first run.
    """
    ctx = _ctx.get()
    memories = ctx["memory"].recall(ctx["actor_id"], query)
    ctx["precedent"] = memories
    return json.dumps({"count": len(memories), "memories": memories})


TOOLS = [get_release_ladder, read_release_notes, score_upgrade_risk, recall_prior_decisions]


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------
def advise(request: Dict[str, Any], session_id: str = "local") -> Dict[str, Any]:
    """Run one advisory end to end and return a structured verdict."""
    package = request.get("package")
    if not package:
        return {"error": "'package' is required"}

    ecosystem = request.get("ecosystem", "pypi")
    current = request.get("current")
    if not current:
        return {"error": "'current' (the version you are pinned to) is required"}
    target = request.get("target", "latest")
    actor_id = request.get("actor_id") or ACTOR_ID

    memory = DecisionLog()

    with sandbox(REGION) as sbx:
        token = _ctx.set(
            {"sandbox": sbx, "memory": memory, "actor_id": actor_id, "chart": None}
        )
        try:
            agent = Agent(
                model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
                tools=TOOLS,
                system_prompt=SYSTEM_PROMPT,
            )
            agent(
                f"Should we upgrade {package} ({ecosystem}) from {current} to {target}? "
                f"Work through your tools, then give me the verdict."
            )
            verdict: Verdict = agent.structured_output(Verdict)

            ctx = _ctx.get()
            payload = verdict.model_dump()
            payload["evidence"] = {
                "notes_source": ctx.get("notes_source"),
                "releases_skipped": (ctx.get("facts") or {}).get("releases_skipped"),
                "repo_url": (ctx.get("facts") or {}).get("repo_url"),
                "score_breakdown": (ctx.get("risk") or {}).get("breakdown", []),
                "precedent_used": len(ctx.get("precedent") or []),
            }
            if ctx.get("chart"):
                payload["chart_png_base64"] = base64.b64encode(ctx["chart"]).decode("ascii")

            payload["memory_written"] = memory.record(
                actor_id=actor_id,
                session_id=session_id,
                request={
                    "package": package,
                    "ecosystem": ecosystem,
                    "current": current,
                    "target": target,
                },
                verdict=payload,
            )
            return payload
        finally:
            _ctx.reset(token)


# --------------------------------------------------------------------------
# AgentCore Runtime entrypoint
# --------------------------------------------------------------------------
@app.entrypoint
def invoke(payload: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
    """Handle one Runtime invocation."""
    session_id = getattr(context, "session_id", None) or "runtime"
    logger.info("advising on %s (session %s)", payload.get("package"), session_id)
    try:
        return advise(payload, session_id=session_id)
    except Exception as exc:  # noqa: BLE001 - surface failures as data, not 500s
        logger.exception("advisory failed")
        return {"error": str(exc), "type": type(exc).__name__}


if __name__ == "__main__":
    app.run()
