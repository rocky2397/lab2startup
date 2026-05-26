"""LangGraph state schema for agentic signal detection."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from app.models import Cluster, Paper, Researcher, Signal

InvestigationTier = Literal["skip", "light", "standard", "deep"]
StopReason = Literal[
    "budget_exhausted",
    "queue_empty",
    "early_exit_high_signal",
    "max_researchers_reached",
    "coordinator_stop",
    "error",
]


class AgentTraceRecord(TypedDict):
    """Lightweight trace summary carried in graph state."""

    trace_id: str
    researcher_id: str
    tier: InvestigationTier
    max_steps: int
    status: Literal["completed", "failed", "skipped"]
    tool_calls_count: int
    steps_used: int
    input_tokens: int
    output_tokens: int
    summary: str


class AgenticSignalState(TypedDict, total=False):
    """Mutable state for the agentic signal LangGraph."""

    run_id: str
    conference: str
    year: int
    fund_context: str | None

    papers: list[Paper]
    researchers: list[Researcher]
    clusters: list[Cluster]

    candidate_scores: dict[str, float]
    investigation_queue: list[str]
    tier_by_researcher: dict[str, InvestigationTier]
    current_researcher_id: str | None
    investigated_ids: list[str]

    max_agent_calls: int
    agent_calls_used: int
    max_total_steps: int
    steps_used_total: int

    signals: Annotated[list[Signal], operator.add]
    researcher_updates: dict[str, Researcher]
    traces: Annotated[list[AgentTraceRecord], operator.add]
    errors: Annotated[list[str], operator.add]

    stop_reason: StopReason | None
    should_continue: bool

    topic_scores: dict[str, int]
    prefilter_min_score: float
    early_exit_enabled: bool
    deep_slots: int
    standard_slots: int

    db_path: object
    api_key: str | None
