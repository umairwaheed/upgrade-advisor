"""The shape of an answer.

Structured output keeps the verdict machine-readable, so this agent can sit in
a CI gate as easily as in a chat window.
"""

from typing import List, Literal

from pydantic import BaseModel, Field


class Verdict(BaseModel):
    """A reviewable upgrade recommendation."""

    package: str
    current: str
    target: str

    recommendation: Literal["ADOPT", "ADOPT_WITH_CARE", "STAGE", "DEFER"] = Field(
        description=(
            "ADOPT: routine. ADOPT_WITH_CARE: safe but needs a read of the notes. "
            "STAGE: land behind a flag or in a canary first. "
            "DEFER: cost exceeds current benefit."
        )
    )
    risk_score: int = Field(description="The computed score, copied verbatim from the scoring tool.")
    risk_band: str = Field(description="LOW | MODERATE | HIGH | SEVERE, from the scoring tool.")

    rationale: str = Field(description="Two or three sentences. Cite specific versions and signals.")
    breaking_changes: List[str] = Field(
        default_factory=list,
        description="Concrete breaking changes found in the notes, each tagged with its version.",
    )
    required_actions: List[str] = Field(
        default_factory=list,
        description="What an engineer must actually do to land this, in order.",
    )
    precedent: str = Field(
        default="",
        description=(
            "How this squares with the team's past decisions from memory. "
            "Empty string if there is no relevant precedent."
        ),
    )
    evidence_quality: str = Field(
        description="Where the notes came from and what was missing or unreadable."
    )
