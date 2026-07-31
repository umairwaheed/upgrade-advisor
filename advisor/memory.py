"""Step 4 — institutional memory.

Without this the agent gives the same generic answer forever. With it, run N+1
knows that your team deferred this exact upgrade last quarter and why, and that
you consistently accept HIGH-risk upgrades when they carry a security fix.

AgentCore Memory does the extraction and consolidation; we only write raw
conversation turns and read back the distilled facts.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from bedrock_agentcore.memory import MemoryClient

from .config import MEMORY_ID, MEMORY_NAME, REGION, namespace_for

logger = logging.getLogger(__name__)


class DecisionLog:
    """Read/write wrapper around one AgentCore Memory resource.

    Degrades to a no-op if no memory id is configured, so the rest of the
    pipeline stays runnable before setup.
    """

    def __init__(self, memory_id: Optional[str] = None, region: str = REGION):
        self.memory_id = memory_id or MEMORY_ID
        self.enabled = bool(self.memory_id)
        self._client = MemoryClient(region_name=region) if self.enabled else None
        if not self.enabled:
            logger.warning(
                "AGENTCORE_MEMORY_ID is unset — running stateless. "
                "Run `python setup_memory.py` to enable cross-run memory."
            )

    # -- read ------------------------------------------------------------
    def recall(self, actor_id: str, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Retrieve prior decisions relevant to this upgrade."""
        if not self.enabled:
            return []
        try:
            hits = self._client.retrieve_memories(
                memory_id=self.memory_id,
                namespace=namespace_for(actor_id),
                query=query,
                top_k=top_k,
            )
        except Exception as exc:  # noqa: BLE001 - memory must never break a run
            logger.warning("memory recall failed: %s", exc)
            return []

        out = []
        for hit in hits:
            content = hit.get("content")
            text = content.get("text") if isinstance(content, dict) else content
            if text:
                out.append({"memory": text, "score": hit.get("score")})
        return out

    # -- write -----------------------------------------------------------
    def record(
        self,
        actor_id: str,
        session_id: str,
        request: Dict[str, Any],
        verdict: Dict[str, Any],
    ) -> bool:
        """Persist this advisory so the next run can learn from it."""
        if not self.enabled:
            return False

        summary = (
            f"Upgrade review: {request.get('package')} "
            f"{request.get('current')} -> {verdict.get('target', request.get('target'))} "
            f"({request.get('ecosystem')}). "
            f"Risk {verdict.get('risk_score')} ({verdict.get('risk_band')}). "
            f"Recommendation: {verdict.get('recommendation')}. "
            f"Rationale: {verdict.get('rationale')}"
        )
        try:
            self._client.create_event(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                messages=[
                    (json.dumps(request), "USER"),
                    (summary, "ASSISTANT"),
                ],
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory write failed: %s", exc)
            return False


def ensure_memory(region: str = REGION, name: str = MEMORY_NAME) -> str:
    """Create (or fetch) the memory resource. Used by setup_memory.py."""
    client = MemoryClient(region_name=region)
    result = client.create_or_get_memory(
        name=name,
        description="Dependency-upgrade decisions, scoped per team.",
        strategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "upgrade_decisions",
                    "description": (
                        "Facts about which dependency upgrades this team accepted, "
                        "deferred, or rejected, and the reasoning behind each."
                    ),
                    "namespaceTemplates": ["/upgrades/{actorId}"],
                }
            }
        ],
    )
    return result["id"] if "id" in result else result["memoryId"]
