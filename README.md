# Upgrade Advisor

An **Amazon Bedrock AgentCore** agent that answers a question every team argues about:

> Should we take this dependency upgrade, and what will it actually cost us?

```
$ python run_local.py flask --current 2.0.0

  flask  2.0.0 → 3.1.3
  DEFER   risk 58 (SEVERE)

  Flask 2.0.0 is 1906 days old and 25 releases behind. The path crosses the 3.0
  major boundary, which removed the deprecated `flask.Markup` re-exports and
  dropped Python 3.7. Four security fixes in the path mean staying put is not
  free either — but this needs a scheduled migration, not a dependabot merge.

  Required actions:
    - Replace `flask.Markup`/`flask.escape` imports with `markupsafe` (3.0.0)
    - Drop Python 3.7 from the test matrix (3.0.0)
    - Re-check `send_file` callers: `attachment_filename` → `download_name` (2.2.0)

  Precedent: your team deferred this same upgrade in the Q2 review, citing the
  Python 3.7 runtime. That runtime was retired in August, so the blocker is gone.

  Evidence: notes via agentcore-browser; 25 releases traversed; 1 prior decision recalled
  Score breakdown:
     +30  5x explicit breaking change
     +15  crosses 1 major version boundary/ies
      +8  25 releases behind — large accumulated drift
      +6  current version is 1906 days old
      -8  4x security fix (raises cost of NOT upgrading)
```

## Why this problem

It's a genuinely bad fit for a plain LLM, and a genuinely good fit for an agent:

- **The facts are live.** Which releases exist, which were yanked, what the
  maintainers wrote — none of that is in any model's weights, and all of it
  changes weekly.
- **The answer needs arithmetic, not recall.** A risk score you can't reproduce
  is a vibe. Scoring runs as real code, so the same inputs always give the same
  number and every point is attributed to a signal you can go read.
- **The right answer depends on your history.** "Should we upgrade?" has a
  different answer for a team that already deferred this once and wrote down why.

Each of those maps onto a different AgentCore primitive.

## What each primitive does here

| Primitive | Job in this agent | Code |
|---|---|---|
| **Runtime** | Serverless hosting, session isolation, one `agentcore deploy` | `agent.py` |
| **Code Interpreter** | Registry lookups and risk scoring — every number in the answer | `advisor/registry.py`, `advisor/risk.py` |
| **Browser** | Reads GitHub release pages in a real cloud Chromium | `advisor/changelog.py` |
| **Memory** | The team's prior upgrade decisions, scoped per team | `advisor/memory.py` |

The model orchestrates the four tools and writes the verdict. It is not
permitted to be the source of any fact — the system prompt says so, and the
structured output makes each claim traceable to a tool result.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium      # only needed for the Browser path

cp .env.example .env             # then edit it
```

You need, in your AWS account:

1. **Bedrock model access** for the model in `BEDROCK_MODEL_ID`. If Opus 5 isn't
   enabled, `global.anthropic.claude-sonnet-4-6` works. List what you can call:
   ```bash
   aws bedrock list-inference-profiles --region $AWS_REGION \
     --query 'inferenceProfileSummaries[?contains(inferenceProfileId,`anthropic`)].inferenceProfileId'
   ```
2. **AgentCore permissions** for `bedrock-agentcore` (Code Interpreter, Browser,
   Memory) and `bedrock-agentcore-control` (creating the Memory resource).

Then create the memory resource once:

```bash
python setup_memory.py           # prints AGENTCORE_MEMORY_ID → paste into .env
```

Memory is optional. Without it the agent runs stateless and says so; you just
lose the precedent line in the verdict.

## Run it

```bash
# Locally — real AgentCore services, no Runtime deploy
python run_local.py requests --current 2.28.0
python run_local.py pydantic --current 1.10.13 --target 2.9.2
python run_local.py express  --ecosystem npm --current 4.17.1

python run_local.py flask --current 2.0.0 --json    # machine-readable
```

Run the same package twice to see Memory work: the second run recalls the first
one's decision and addresses it in the `precedent` field.

## Deploy to AgentCore Runtime

```bash
agentcore configure --entrypoint agent.py
agentcore deploy
agentcore invoke '{"package": "flask", "ecosystem": "pypi", "current": "2.0.0"}'
```

`agentcore status` shows the endpoint; `agentcore obs` shows traces for each
tool call.

## Output

`Verdict` (`advisor/verdict.py`) is a Pydantic model, so this drops into a CI
gate as easily as a chat window:

```json
{
  "package": "flask", "current": "2.0.0", "target": "3.1.3",
  "recommendation": "DEFER",
  "risk_score": 58, "risk_band": "SEVERE",
  "rationale": "...",
  "breaking_changes": ["3.0.0 removed the flask.Markup re-export", "..."],
  "required_actions": ["...'],
  "precedent": "...",
  "evidence_quality": "Notes read via browser for 12 of 25 releases; ...",
  "evidence": { "notes_source": "agentcore-browser", "score_breakdown": [...] },
  "chart_png_base64": "iVBOR...",
  "memory_written": true
}
```

`recommendation` is one of `ADOPT`, `ADOPT_WITH_CARE`, `STAGE`, `DEFER` — so
you can fail a build on `DEFER` without parsing prose.

## Design notes

**Nothing degrades silently.** If the Browser session can't be established the
agent falls back to the GitHub REST API, and the verdict reports
`notes_source: github-api-fallback` along with why. Releases whose notes
couldn't be read are counted as risk, not treated as clean.

**Scoring is auditable.** `advisor/risk.py` maps regex signals to points with a
per-signal cap, so one shouty release can't dominate. Security fixes score
*negative* — they raise the cost of not upgrading. The breakdown is returned
with the verdict.

**Memory shifts the recommendation, not the score.** The score is a property of
the upgrade; the recommendation is a property of the upgrade *and* your team.
Keeping them separate is what makes the score comparable across runs.

**Sandboxed code talks back over a JSON envelope** (`advisor/sandbox.py`), so a
stray warning on stderr can't corrupt a result.

## Verification status

Verified locally against the installed SDKs and live registries:

- every generated sandbox script compiles and produces correct output on real
  PyPI/npm data (version ladders, major-bump detection, yanked releases)
- the GitHub release-notes fallback against live GitHub
- risk scoring and chart rendering on fixtures and real packages
- error paths: unknown package, unknown version, unsupported ecosystem, no repo

Not verified here — needs live AWS credentials with Bedrock and AgentCore
access: the Code Interpreter and Browser session handshakes, Memory read/write,
model invocation, and `agentcore deploy`.

## License

MIT — see [LICENSE](LICENSE).
