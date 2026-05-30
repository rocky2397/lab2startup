"""Models for run-to-run diff comparisons (Step 16)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

ChangeType = Literal[
    "new_researcher",
    "removed_researcher",
    "score_increased",
    "score_decreased",
    "recommendation_changed",
    "new_signal",
    "signal_removed",
    "affiliation_changed",
    "role_changed",
    "new_take_meeting",
]


class ResearcherDelta(BaseModel):
    """A single researcher-level change between two runs."""

    researcher_id: str
    name: str
    change_type: ChangeType
    before: str | int | None = None
    after: str | int | None = None
    detail: str = ""


class RunDiffSummary(BaseModel):
    """Aggregate counts for a run diff."""

    total_deltas: int = 0
    new_researchers: int = 0
    score_increases: int = 0
    score_decreases: int = 0
    recommendation_changes: int = 0
    new_signals: int = 0
    affiliation_changes: int = 0
    role_changes: int = 0
    new_take_meeting: int = 0


class RunDiff(BaseModel):
    """Comparison of a pipeline run against a prior complete run."""

    run_id: str
    prior_run_id: str | None = None
    conference: str
    year: int
    fund_profile: str | None = None
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deltas: list[ResearcherDelta] = Field(default_factory=list)
    summary: RunDiffSummary = Field(default_factory=RunDiffSummary)


def summarize_deltas(deltas: list[ResearcherDelta]) -> RunDiffSummary:
    counts: dict[str, int] = {}
    for delta in deltas:
        counts[delta.change_type] = counts.get(delta.change_type, 0) + 1
    return RunDiffSummary(
        total_deltas=len(deltas),
        new_researchers=counts.get("new_researcher", 0),
        score_increases=counts.get("score_increased", 0),
        score_decreases=counts.get("score_decreased", 0),
        recommendation_changes=counts.get("recommendation_changed", 0),
        new_signals=counts.get("new_signal", 0),
        affiliation_changes=counts.get("affiliation_changed", 0),
        role_changes=counts.get("role_changed", 0),
        new_take_meeting=counts.get("new_take_meeting", 0),
    )
