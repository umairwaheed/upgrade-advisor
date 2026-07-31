"""Shared configuration, read once from the environment."""

import os

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"

# Strands resolves its own default if this is unset; we pin it so the agent's
# reasoning quality is a deliberate choice rather than a library default.
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-5")

MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")
MEMORY_NAME = os.environ.get("AGENTCORE_MEMORY_NAME", "upgrade_advisor_decisions")

# Memory is partitioned per team, so two teams sharing a deployment never see
# each other's upgrade history.
ACTOR_ID = os.environ.get("UPGRADE_ADVISOR_ACTOR", "platform-team")

# Namespace the semantic strategy writes into. {actorId} is substituted by the
# Memory service at extraction time.
MEMORY_NAMESPACE_TEMPLATE = "/upgrades/{actorId}"


def namespace_for(actor_id: str) -> str:
    return MEMORY_NAMESPACE_TEMPLATE.format(actorId=actor_id)
