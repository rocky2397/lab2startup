"""Deterministic coordinator: prefilter scores, tiers, and stop rules."""

from __future__ import annotations

from app.agent_trace_store import lookup_researcher_history
from app.agents.signal_graph_state import AgenticSignalState, InvestigationTier, StopReason
from app.config import AgenticSignalConfig
from app.integrations.perplexity_agent import TIER_MAX_STEPS
from app.models import EvidenceStrength, IdentityConfidence, Researcher, SignalType


def compute_prefilter_score(
    researcher: Researcher,
    *,
    papers_by_id: dict[str, object],
    topic_scores: dict[str, int] | None = None,
    db_path=None,
) -> float:
    """Deterministic ranking score before any Agent API call."""
    paper_count = len(researcher.papers)
    score = min(paper_count * 5, 25.0)

    topic_scores = topic_scores or {}
    applied_topic = 0.0
    for paper_id in researcher.papers:
        paper = papers_by_id.get(paper_id)
        if paper is None:
            continue
        topic = getattr(paper, "topic", None) or ""
        applied_topic = max(applied_topic, float(topic_scores.get(topic, 0)))
    score += min(applied_topic, 25.0)

    years = [
        getattr(papers_by_id[paper_id], "year", 0)
        for paper_id in researcher.papers
        if paper_id in papers_by_id
    ]
    if years:
        latest = max(years)
        recency = max(0, 15 - (2026 - latest) * 3)
        score += float(recency)

    score += min(len(researcher.coauthors) * 2, 10.0)

    history = lookup_researcher_history(
        researcher_id=researcher.id,
        researcher_name=researcher.name,
        db_path=db_path,
    )
    if history:
        score += min(float(history.get("last_signal_count") or 0) * 3, 15.0)

    if researcher.identity_confidence == IdentityConfidence.LOW:
        score -= 10.0
    elif researcher.identity_confidence == IdentityConfidence.MEDIUM:
        score -= 5.0

    return round(score, 2)


def assign_tier(
    rank: int,
    researcher: Researcher,
    *,
    deep_slots: int,
    standard_slots: int,
    prefilter_score: float,
    prefilter_min_score: float,
) -> InvestigationTier:
    """Map queue rank and identity to investigation tier."""
    if prefilter_score < prefilter_min_score:
        return "skip"
    if researcher.identity_confidence == IdentityConfidence.LOW:
        return "skip"
    if rank <= deep_slots:
        return "deep"
    if rank <= deep_slots + standard_slots:
        return "standard"
    if researcher.identity_confidence == IdentityConfidence.MEDIUM:
        return "light"
    return "light"


def build_investigation_plan(
    researchers: list[Researcher],
    *,
    papers_by_id: dict[str, object],
    config: AgenticSignalConfig,
    topic_scores: dict[str, int] | None = None,
    db_path=None,
) -> tuple[dict[str, float], list[str], dict[str, InvestigationTier]]:
    """Compute scores, ordered queue, and per-researcher tiers."""
    scores: dict[str, float] = {}
    for researcher in researchers:
        scores[researcher.id] = compute_prefilter_score(
            researcher,
            papers_by_id=papers_by_id,
            topic_scores=topic_scores,
            db_path=db_path,
        )

    ranked_ids = sorted(
        scores.keys(),
        key=lambda researcher_id: (-scores[researcher_id], researcher_id),
    )
    researchers_by_id = {researcher.id: researcher for researcher in researchers}

    queue: list[str] = []
    tiers: dict[str, InvestigationTier] = {}
    for rank, researcher_id in enumerate(ranked_ids, start=1):
        researcher = researchers_by_id[researcher_id]
        tier = assign_tier(
            rank,
            researcher,
            deep_slots=config.deep_slots,
            standard_slots=config.standard_slots,
            prefilter_score=scores[researcher_id],
            prefilter_min_score=config.prefilter_min_score,
        )
        tiers[researcher_id] = tier
        if tier != "skip" and len(queue) < config.max_agent_calls + config.queue_reserve:
            queue.append(researcher_id)

    queue = queue[: config.max_agent_calls + config.queue_reserve]
    return scores, queue, tiers


def should_early_exit(signals: list, *, enabled: bool) -> bool:
    """Stop remaining queue when high-confidence founder evidence appears."""
    if not enabled:
        return False

    high_confirmed = any(
        signal.signal_type == SignalType.CONFIRMED_FOUNDER
        and signal.evidence_strength == EvidenceStrength.HIGH
        for signal in signals
    )
    if high_confirmed:
        return True

    strong_count = sum(
        1
        for signal in signals
        if signal.signal_type in {SignalType.CONFIRMED_FOUNDER, SignalType.POSSIBLE_FOUNDER}
        and signal.evidence_strength in {EvidenceStrength.HIGH, EvidenceStrength.MEDIUM}
    )
    return strong_count >= 3


def evaluate_continue(state: AgenticSignalState) -> AgenticSignalState:
    """Decide whether to investigate more researchers."""
    queue = list(state.get("investigation_queue") or [])
    agent_calls_used = int(state.get("agent_calls_used") or 0)
    max_agent_calls = int(state.get("max_agent_calls") or 0)
    steps_used_total = int(state.get("steps_used_total") or 0)
    max_total_steps = int(state.get("max_total_steps") or 0)
    signals = list(state.get("signals") or [])

    stop_reason: StopReason | None = None
    should_continue = True

    if agent_calls_used >= max_agent_calls:
        should_continue = False
        stop_reason = "budget_exhausted"
    elif not queue:
        should_continue = False
        stop_reason = "queue_empty"
    elif max_total_steps and steps_used_total >= max_total_steps:
        should_continue = False
        stop_reason = "budget_exhausted"
    elif should_early_exit(
        signals,
        enabled=bool(state.get("early_exit_enabled", True)),
    ):
        should_continue = False
        stop_reason = "early_exit_high_signal"

    return {
        "should_continue": should_continue,
        "stop_reason": stop_reason,
    }


def tier_max_steps(tier: InvestigationTier) -> int:
    return TIER_MAX_STEPS.get(tier, 0)
