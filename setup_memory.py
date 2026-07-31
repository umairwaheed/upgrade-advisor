#!/usr/bin/env python3
"""One-time setup: create the AgentCore Memory resource this agent writes to.

    python setup_memory.py

Prints the memory id. Put it in .env as AGENTCORE_MEMORY_ID.
Safe to re-run — it returns the existing resource if one already exists.
"""

import sys

from advisor.config import MEMORY_NAME, REGION
from advisor.memory import ensure_memory


def main() -> int:
    print(f"Creating/fetching memory '{MEMORY_NAME}' in {REGION} …")
    print("(first creation takes a couple of minutes while the strategy provisions)")
    try:
        memory_id = ensure_memory(region=REGION, name=MEMORY_NAME)
    except Exception as exc:  # noqa: BLE001
        print(f"\nFailed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "\nCheck that your AWS credentials are valid and that the caller has "
            "bedrock-agentcore-control permissions for Memory.",
            file=sys.stderr,
        )
        return 1

    print(f"\n  AGENTCORE_MEMORY_ID={memory_id}\n")
    print("Add that line to your .env and re-run the agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
