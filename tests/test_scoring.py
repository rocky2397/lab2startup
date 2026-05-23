"""Tests for scoring logic (Step 6)."""

from app.agents.scoring_agent import run_scoring, summarize_scoring
from app.agents.signal_agent import detect_signals
from app.models import PriorityBand, Signal, SignalType, VCAction
from app.scoring import (
    score_commercialization_signal_strength,
    score_researcher,
    score_team_continuity,
)
from app.schemas import load_papers


def test_score_team_continuity_scales_with_coauthors() -> None:
    from app.models import Researcher

    solo = Researcher(id="r1", name="A", affiliation="X", role="Student", coauthors=[])
    team = Researcher(
        id="r2",
        name="B",
        affiliation="X",
        role="Student",
        coauthors=["c1", "c2", "c3", "c4", "c5", "c6"],
    )
    assert score_team_continuity(solo) < score_team_continuity(team)


def test_confirmed_founder_signal_scores_high() -> None:
    from app.models import EvidenceStrength

    signals = [
        Signal(
            id="sig_test",
            researcher_name="Test",
            signal_type=SignalType.CONFIRMED_FOUNDER,
            description="Founder",
            source_url="https://example.com",
            evidence_strength=EvidenceStrength.HIGH,
            date_found="2025-01-01",
        )
    ]
    assert score_commercialization_signal_strength(signals) == 20


def test_marinka_zitnik_ranks_high() -> None:
    result = run_scoring()
    marinka = next(
        score for score in result.researcher_scores if score.entity_name == "Marinka Zitnik"
    )
    assert marinka.startup_likelihood_score >= 70
    assert marinka.priority_band in {PriorityBand.HIGH_PRIORITY, PriorityBand.MONITOR_CLOSELY}
    assert marinka.score_breakdown.commercialization_signal_strength == 20


def test_leslie_kaelbling_no_signal_ranks_low() -> None:
    result = run_scoring()
    leslie = next(
        score for score in result.researcher_scores if score.entity_name == "Leslie Pack Kaelbling"
    )
    assert leslie.startup_likelihood_score <= 60
    assert leslie.recommendation in {VCAction.ADD_TO_WATCHLIST, VCAction.IGNORE_FOR_NOW}


def test_run_scoring_returns_ranked_results() -> None:
    result = run_scoring()
    summary = summarize_scoring(result)

    assert summary["researcher_count"] == 30
    assert summary["cluster_count"] == 7
    assert len(summary["top_researchers"]) == 5
    assert result.ranked_researchers[0].startup_likelihood_score >= result.ranked_researchers[-1].startup_likelihood_score


def test_cluster_scores_populated() -> None:
    result = run_scoring()
    assert all(cluster.score is not None for cluster in result.detection.clusters)
    assert len(result.cluster_scores) == 7


def test_score_researcher_uses_paper_metadata() -> None:
    detection = detect_signals()
    papers_by_id = {paper.id: paper for paper in detection.papers}
    john = next(r for r in detection.researchers if r.name == "John Yang")
    john_signals = detection.signals_for_researcher(john.id)

    score = score_researcher(john, papers_by_id, john_signals)
    assert score.score_breakdown.research_quality >= 16
    assert score.score_breakdown.recency == 10
