"""Rule-based startup likelihood scoring (Step 6)."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import (
    Cluster,
    EvidenceStrength,
    IdentityConfidence,
    Paper,
    PriorityBand,
    Researcher,
    ScoreBreakdown,
    Signal,
    SignalType,
    VCAction,
    classify_priority_band,
    recommend_vc_action,
)

APPLIED_TOPIC_SCORES = {
    "AI agents": 18,
    "robotics": 17,
    "biotech AI": 19,
}

SIGNAL_TYPE_SCORES: dict[SignalType, dict[EvidenceStrength, int]] = {
    SignalType.CONFIRMED_FOUNDER: {
        EvidenceStrength.HIGH: 20,
        EvidenceStrength.MEDIUM: 16,
        EvidenceStrength.LOW: 12,
    },
    SignalType.POSSIBLE_FOUNDER: {
        EvidenceStrength.HIGH: 14,
        EvidenceStrength.MEDIUM: 11,
        EvidenceStrength.LOW: 8,
    },
    SignalType.COMMERCIALIZATION: {
        EvidenceStrength.HIGH: 12,
        EvidenceStrength.MEDIUM: 9,
        EvidenceStrength.LOW: 5,
    },
    SignalType.NO_SIGNAL: {
        EvidenceStrength.HIGH: 2,
        EvidenceStrength.MEDIUM: 1,
        EvidenceStrength.LOW: 0,
    },
}


@dataclass
class EntityScore:
    """Score result for a researcher or cluster."""

    entity_id: str
    entity_type: str
    entity_name: str
    score_breakdown: ScoreBreakdown
    startup_likelihood_score: int
    priority_band: PriorityBand
    recommendation: VCAction


def _researcher_papers(researcher: Researcher, papers_by_id: dict[str, Paper]) -> list[Paper]:
    return [papers_by_id[paper_id] for paper_id in researcher.papers]


def score_research_quality(researcher: Researcher, papers_by_id: dict[str, Paper]) -> int:
    """Score paper quality from conference tier and publication recency."""
    papers = _researcher_papers(researcher, papers_by_id)
    if not papers:
        return 0

    paper_scores: list[int] = []
    for paper in papers:
        base = 12 if paper.conference == "NeurIPS" else 8
        year_bonus = 6 if paper.year >= 2024 else 4 if paper.year >= 2023 else 2
        paper_scores.append(min(20, base + year_bonus))

    return min(20, max(paper_scores))


def score_applied_relevance(researcher: Researcher, papers_by_id: dict[str, Paper]) -> int:
    """Score how applied the research topics are for startup/commercial use."""
    papers = _researcher_papers(researcher, papers_by_id)
    if not papers:
        return 0

    topic_scores = [APPLIED_TOPIC_SCORES.get(paper.topic, 10) for paper in papers]
    return min(20, max(topic_scores))


def score_team_continuity(researcher: Researcher) -> int:
    """Score coauthor network strength as a proxy for team formation potential."""
    coauthor_count = len(researcher.coauthors)
    if coauthor_count >= 6:
        return 15
    if coauthor_count >= 4:
        return 12
    if coauthor_count >= 2:
        return 9
    if coauthor_count == 1:
        return 6
    return 3


def score_open_source_or_project_momentum(signals: list[Signal]) -> int:
    """Score public project activity from commercialization-style signals."""
    score = 0
    for signal in signals:
        if signal.signal_type != SignalType.COMMERCIALIZATION:
            continue

        url = signal.source_url.lower()
        if "github.com" in url:
            score += 8
        elif "openreview.net" in url or "arxiv.org" in url:
            score += 5
        elif signal.evidence_strength == EvidenceStrength.HIGH:
            score += 7
        elif signal.evidence_strength == EvidenceStrength.MEDIUM:
            score += 5
        else:
            score += 3

    return min(15, score)


def score_commercialization_signal_strength(signals: list[Signal]) -> int:
    """Score founder/commercialization evidence from attached signals."""
    if not signals:
        return 0

    best = 0
    total = 0
    for signal in signals:
        points = SIGNAL_TYPE_SCORES[signal.signal_type][signal.evidence_strength]
        best = max(best, points)
        total += points // 2

    return min(20, max(best, total))


def score_recency(researcher: Researcher, papers_by_id: dict[str, Paper]) -> int:
    """Score how recent the research activity is."""
    papers = _researcher_papers(researcher, papers_by_id)
    if not papers:
        return 0

    latest_year = max(paper.year for paper in papers)
    if latest_year >= 2024:
        return 10
    if latest_year >= 2023:
        return 7
    return 4


def _identity_penalty(researcher: Researcher) -> int:
    """Reduce score slightly when identity resolution is uncertain."""
    if researcher.identity_confidence == IdentityConfidence.HIGH:
        return 0
    if researcher.identity_confidence == IdentityConfidence.MEDIUM:
        return 3
    return 6


def score_researcher(
    researcher: Researcher,
    papers_by_id: dict[str, Paper],
    signals: list[Signal],
) -> EntityScore:
    """Compute a full score breakdown for one researcher."""
    breakdown = ScoreBreakdown(
        research_quality=score_research_quality(researcher, papers_by_id),
        applied_relevance=score_applied_relevance(researcher, papers_by_id),
        team_continuity=score_team_continuity(researcher),
        open_source_or_project_momentum=score_open_source_or_project_momentum(signals),
        commercialization_signal_strength=score_commercialization_signal_strength(signals),
        recency=score_recency(researcher, papers_by_id),
    )
    total = max(0, breakdown.startup_likelihood_score - _identity_penalty(researcher))
    priority_band = classify_priority_band(total)
    return EntityScore(
        entity_id=researcher.id,
        entity_type="researcher",
        entity_name=researcher.name,
        score_breakdown=breakdown,
        startup_likelihood_score=total,
        priority_band=priority_band,
        recommendation=recommend_vc_action(priority_band),
    )


def score_cluster(
    cluster: Cluster,
    researcher_scores: dict[str, EntityScore],
) -> EntityScore:
    """Compute a cluster score from member averages plus team continuity boost."""
    member_scores = [researcher_scores[member_id] for member_id in cluster.researchers]
    if not member_scores:
        breakdown = ScoreBreakdown(
            research_quality=0,
            applied_relevance=0,
            team_continuity=0,
            open_source_or_project_momentum=0,
            commercialization_signal_strength=0,
            recency=0,
        )
        total = 0
    else:
        count = len(member_scores)
        breakdown = ScoreBreakdown(
            research_quality=round(sum(s.score_breakdown.research_quality for s in member_scores) / count),
            applied_relevance=round(sum(s.score_breakdown.applied_relevance for s in member_scores) / count),
            team_continuity=min(
                15,
                round(sum(s.score_breakdown.team_continuity for s in member_scores) / count) + 2,
            ),
            open_source_or_project_momentum=round(
                sum(s.score_breakdown.open_source_or_project_momentum for s in member_scores) / count
            ),
            commercialization_signal_strength=min(
                20,
                round(sum(s.score_breakdown.commercialization_signal_strength for s in member_scores) / count) + 2,
            ),
            recency=round(sum(s.score_breakdown.recency for s in member_scores) / count),
        )
        total = breakdown.startup_likelihood_score

    priority_band = classify_priority_band(total)
    return EntityScore(
        entity_id=cluster.id,
        entity_type="cluster",
        entity_name=cluster.name,
        score_breakdown=breakdown,
        startup_likelihood_score=total,
        priority_band=priority_band,
        recommendation=recommend_vc_action(priority_band),
    )


def rank_entity_scores(scores: list[EntityScore]) -> list[EntityScore]:
    """Sort scores from highest to lowest startup likelihood."""
    return sorted(
        scores,
        key=lambda score: (-score.startup_likelihood_score, score.entity_name),
    )
