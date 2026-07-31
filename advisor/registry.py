"""Step 1 — establish the ground truth of what an upgrade actually contains.

Runs entirely inside the Code Interpreter sandbox: hits the package registry,
sorts the versions, and returns the exact ladder of releases between where you
are and where you'd land. The model is never asked to recall release history.
"""

from typing import Any, Dict

from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

from .sandbox import run_python_json

_LADDER_SCRIPT = r'''
import json, re, urllib.request, urllib.error
from datetime import datetime, timezone

PACKAGE   = {package!r}
ECOSYSTEM = {ecosystem!r}
CURRENT   = {current!r}
TARGET    = {target!r}

def get_json(url):
    req = urllib.request.Request(url, headers={{"User-Agent": "upgrade-advisor/1.0"}})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            emit({{"error": "package '{{}}' not found in {{}}".format(PACKAGE, ECOSYSTEM)}})
        else:
            emit({{"error": "registry returned HTTP {{}} for {{}}".format(e.code, PACKAGE)}})
        raise SystemExit
    except Exception as e:
        emit({{"error": "could not reach the {{}} registry: {{}}".format(ECOSYSTEM, e)}})
        raise SystemExit

def parse(v):
    """Sortable key. Release segment first; any pre-release sorts below final."""
    m = re.match(r"^v?(\d+(?:\.\d+)*)(.*)$", str(v).strip())
    if not m:
        return ((0,), 1, str(v))
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums = nums + (0,) * (4 - len(nums)) if len(nums) < 4 else nums
    rest = m.group(2)
    is_pre = 1 if re.search(r"(a|b|rc|alpha|beta|dev|pre|-)", rest) else 2
    return (nums, is_pre, rest)

def is_prerelease(v):
    return parse(v)[1] == 1

# ---- registry adapters -------------------------------------------------
if ECOSYSTEM == "pypi":
    data = get_json("https://pypi.org/pypi/{{}}/json".format(PACKAGE))
    releases = {{}}
    for ver, files in (data.get("releases") or {{}}).items():
        if not files:
            continue
        releases[ver] = {{
            "released_at": min(f.get("upload_time_iso_8601") or "" for f in files) or None,
            "yanked": any(f.get("yanked") for f in files),
        }}
    latest = data["info"]["version"]
    urls = data["info"].get("project_urls") or {{}}
    homepage = data["info"].get("home_page") or ""
    candidates = list(urls.items()) + [("home_page", homepage)]
elif ECOSYSTEM == "npm":
    data = get_json("https://registry.npmjs.org/{{}}".format(PACKAGE))
    times = data.get("time") or {{}}
    releases = {{
        ver: {{"released_at": times.get(ver), "yanked": False}}
        for ver in (data.get("versions") or {{}})
    }}
    latest = (data.get("dist-tags") or {{}}).get("latest")
    repo = (data.get("repository") or {{}})
    candidates = [("repository", repo.get("url") or ""), ("homepage", data.get("homepage") or "")]
else:
    emit({{"error": "unsupported ecosystem: " + ECOSYSTEM}})
    raise SystemExit

# ---- locate the source repository (needed for changelog reading) -------
# Registries list funding, docs and issue-tracker URLs alongside the source
# repo, and several of those also live on github.com. Rank by how likely the
# label is to mean "source", and reject GitHub paths that are never repos.
NOT_A_REPO_OWNER = {{"sponsors", "orgs", "users", "apps", "features", "about"}}

def rank(label):
    l = (label or "").lower()
    if any(k in l for k in ("source", "repository", "repo", "code")):
        return 0
    if any(k in l for k in ("home", "documentation", "docs")):
        return 1
    if any(k in l for k in ("funding", "sponsor", "donate", "chat", "twitter")):
        return 9
    return 5

repo_url = None
for label, value in sorted(candidates, key=lambda kv: rank(kv[0])):
    m = re.search(r"github\.com[/:]([\w.\-]+)/([\w.\-]+?)(?:\.git|/|#|\?|$)", str(value or ""))
    if not m:
        continue
    owner, name = m.group(1), m.group(2)
    if owner.lower() in NOT_A_REPO_OWNER or not name:
        continue
    repo_url = "https://github.com/{{}}/{{}}".format(owner, name)
    break

# ---- build the ladder --------------------------------------------------
resolved_target = latest if TARGET in ("latest", "", None) else TARGET
if CURRENT not in releases:
    emit({{"error": "current version {{}} not found for {{}}".format(CURRENT, PACKAGE),
          "known_versions_sample": sorted(releases, key=parse)[-10:]}})
    raise SystemExit
if resolved_target not in releases:
    emit({{"error": "target version {{}} not found for {{}}".format(resolved_target, PACKAGE)}})
    raise SystemExit

lo, hi = parse(CURRENT), parse(resolved_target)
ladder = [
    {{"version": v, **meta}}
    for v, meta in releases.items()
    if lo < parse(v) <= hi and not is_prerelease(v)
]
ladder.sort(key=lambda r: parse(r["version"]))

def major(v):
    return parse(v)[0][0]

now = datetime.now(timezone.utc)
def age_days(ts):
    if not ts:
        return None
    try:
        return (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).days
    except Exception:
        return None

emit({{
    "package": PACKAGE,
    "ecosystem": ECOSYSTEM,
    "current": CURRENT,
    "target": resolved_target,
    "latest": latest,
    "repo_url": repo_url,
    "releases_skipped": len(ladder),
    "major_bumps": max(0, major(resolved_target) - major(CURRENT)),
    "crosses_major": major(resolved_target) > major(CURRENT),
    "yanked_in_path": [r["version"] for r in ladder if r["yanked"]],
    "current_age_days": age_days(releases[CURRENT].get("released_at")),
    "target_age_days": age_days(releases[resolved_target].get("released_at")),
    "ladder": ladder[-40:],
}})
'''


def fetch_release_ladder(
    client: CodeInterpreter,
    package: str,
    ecosystem: str,
    current: str,
    target: str = "latest",
) -> Dict[str, Any]:
    """Return the exact set of releases an upgrade would traverse."""
    code = _LADDER_SCRIPT.format(
        package=package, ecosystem=ecosystem, current=current, target=target
    )
    return run_python_json(client, code)
