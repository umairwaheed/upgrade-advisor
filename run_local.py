#!/usr/bin/env python3
"""Run one advisory from the terminal, without deploying to Runtime.

    python run_local.py requests --current 2.28.0
    python run_local.py pydantic --current 1.10.13 --target 2.9.2
    python run_local.py express --ecosystem npm --current 4.17.1

The AgentCore Browser, Code Interpreter, and Memory calls all go to the real
services — only the Runtime hop is skipped.
"""

import argparse
import base64
import json
import pathlib
import sys

from agent import advise

BANDS = {"LOW": "\033[32m", "MODERATE": "\033[33m", "HIGH": "\033[31m", "SEVERE": "\033[35m"}
RESET = "\033[0m"


def render(result: dict) -> None:
    if "error" in result:
        print(f"\n  error: {result['error']}\n", file=sys.stderr)
        return

    colour = BANDS.get(result.get("risk_band", ""), "")
    print()
    print(f"  {result['package']}  {result['current']} → {result['target']}")
    print(f"  {colour}{result['recommendation']}{RESET}   "
          f"risk {result['risk_score']} ({colour}{result.get('risk_band')}{RESET})")
    print()
    print(f"  {result['rationale']}")

    for title, key in (
        ("Breaking changes", "breaking_changes"),
        ("Required actions", "required_actions"),
    ):
        items = result.get(key) or []
        if items:
            print(f"\n  {title}:")
            for item in items:
                print(f"    - {item}")

    if result.get("precedent"):
        print(f"\n  Precedent: {result['precedent']}")

    evidence = result.get("evidence", {})
    print(f"\n  Evidence: notes via {evidence.get('notes_source')}; "
          f"{evidence.get('releases_skipped')} releases traversed; "
          f"{evidence.get('precedent_used')} prior decision(s) recalled")
    print(f"  {result.get('evidence_quality', '')}")

    breakdown = evidence.get("score_breakdown") or []
    if breakdown:
        print("\n  Score breakdown:")
        for row in breakdown:
            print(f"    {row['points']:+4d}  {row['reason']}")

    if result.get("chart_png_base64"):
        out = pathlib.Path("upgrade_risk.png")
        out.write_bytes(base64.b64decode(result["chart_png_base64"]))
        print(f"\n  Chart: {out.resolve()}")

    print(f"\n  Memory written: {result.get('memory_written')}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the upgrade advisor about a dependency.")
    parser.add_argument("package")
    parser.add_argument("--ecosystem", default="pypi", choices=["pypi", "npm"])
    parser.add_argument("--current", required=True, help="version you are pinned to")
    parser.add_argument("--target", default="latest")
    parser.add_argument("--actor", default=None, help="team id for memory scoping")
    parser.add_argument("--json", action="store_true", help="raw JSON output")
    args = parser.parse_args()

    request = {
        "package": args.package,
        "ecosystem": args.ecosystem,
        "current": args.current,
        "target": args.target,
    }
    if args.actor:
        request["actor_id"] = args.actor

    result = advise(request, session_id=f"cli-{args.package}")

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        render(result)
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
