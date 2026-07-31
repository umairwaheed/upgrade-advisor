"""Step 3 — turn release notes into a defensible number.

Scoring runs in the sandbox so the result is reproducible and auditable: the
same inputs always yield the same score, and every point is attributed to a
signal you can go read. The model's job is to *interpret* this, not produce it.
"""

import json
from typing import Any, Dict, Optional, Tuple

from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

from .sandbox import read_file, run_python_json

CHART_PATH = "upgrade_risk.png"

_SCORE_SCRIPT = r'''
import json, re

ladder = json.loads({ladder_json!r})
notes  = json.loads({notes_json!r})
facts  = json.loads({facts_json!r})

# Signal -> (regex, points per matching release, human label)
SIGNALS = {{
    "breaking":    (r"breaking[ \-]?change|backwards[- ]incompatible|no longer supported", 6, "explicit breaking change"),
    "removal":     (r"\b(removed|dropped support|deleted)\b", 4, "removal"),
    "deprecation": (r"\bdeprecat", 1, "deprecation"),
    "migration":   (r"migration guide|upgrade guide|how to migrate", 3, "migration guide published"),
    "security":    (r"\bCVE-\d{{4}}-\d+|security (fix|advisory|release)", -2, "security fix (raises cost of NOT upgrading)"),
}}

per_version, findings = [], []
for entry in ladder:
    v = entry["version"]
    text = (notes.get(v) or notes.get(v.lstrip("vV")) or "")
    row = {{"version": v, "has_notes": bool(text)}}
    for name, (pattern, weight, label) in SIGNALS.items():
        hits = len(re.findall(pattern, text, re.I))
        row[name] = hits
        if hits:
            findings.append({{"version": v, "signal": name, "hits": hits, "label": label}})
    per_version.append(row)

def total(name):
    return sum(r.get(name, 0) for r in per_version)

score, breakdown = 0, []
def add(points, why):
    global score
    if points:
        score += points
        breakdown.append({{"points": points, "reason": why}})

for name, (_, weight, label) in SIGNALS.items():
    n = total(name)
    if n:
        capped = min(n, 5)   # one loud release shouldn't dominate
        add(weight * capped, "{{}}x {{}}".format(n, label))

if facts.get("crosses_major"):
    add(15 * facts.get("major_bumps", 1), "crosses {{}} major version boundary/ies".format(facts.get("major_bumps", 1)))
hops = facts.get("releases_skipped", 0)
if hops > 20:
    add(8, "{{}} releases behind — large accumulated drift".format(hops))
elif hops > 5:
    add(3, "{{}} releases behind".format(hops))
if facts.get("yanked_in_path"):
    add(5, "yanked releases in the path: {{}}".format(", ".join(facts["yanked_in_path"])))
age = facts.get("current_age_days")
if age and age > 730:
    add(6, "current version is {{}} days old".format(age))

missing = [r["version"] for r in per_version if not r["has_notes"]]
if missing:
    add(2, "{{}} releases had no readable notes (unknowns are risk)".format(len(missing)))

score = max(0, score)
band = "LOW" if score < 12 else "MODERATE" if score < 30 else "HIGH" if score < 55 else "SEVERE"

# ---- chart (best effort; never fatal) ----------------------------------
chart = None
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = [r for r in per_version if any(r.get(k) for k in ("breaking", "removal", "deprecation"))]
    rows = rows[-15:] or per_version[-15:]
    if rows:
        labels = [r["version"] for r in rows]
        idx = range(len(rows))
        fig, ax = plt.subplots(figsize=(max(6, len(rows) * 0.7), 3.6))
        bottom = [0] * len(rows)
        for key, colour in (("breaking", "#c0392b"), ("removal", "#e67e22"), ("deprecation", "#f1c40f")):
            vals = [r.get(key, 0) for r in rows]
            ax.bar(list(idx), vals, bottom=bottom, label=key, color=colour)
            bottom = [b + v for b, v in zip(bottom, vals)]
        ax.set_xticks(list(idx)); ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
        ax.set_ylabel("signal hits"); ax.legend(fontsize=8, frameon=False)
        ax.set_title("{{}} {{}} -> {{}}   risk {{}} ({{}})".format(
            facts.get("package"), facts.get("current"), facts.get("target"), score, band), fontsize=10)
        fig.tight_layout(); fig.savefig({chart_path!r}, dpi=140); plt.close(fig)
        chart = {chart_path!r}
except Exception as exc:
    chart = None

emit({{
    "risk_score": score,
    "risk_band": band,
    "breakdown": breakdown,
    "findings": findings[:40],
    "per_version": per_version,
    "releases_without_notes": missing,
    "chart_path": chart,
}})
'''


def score_upgrade(
    client: CodeInterpreter,
    facts: Dict[str, Any],
    notes: Dict[str, str],
) -> Tuple[Dict[str, Any], Optional[bytes]]:
    """Compute a reproducible risk score, plus a chart if matplotlib is present."""
    code = _SCORE_SCRIPT.format(
        ladder_json=json.dumps(facts.get("ladder", [])),
        notes_json=json.dumps(notes),
        facts_json=json.dumps({k: v for k, v in facts.items() if k != "ladder"}),
        chart_path=CHART_PATH,
    )
    result = run_python_json(client, code)

    chart_bytes = read_file(client, result["chart_path"]) if result.get("chart_path") else None
    return result, chart_bytes
