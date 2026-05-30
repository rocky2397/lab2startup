"""Tests for run diff agent (Step 16)."""

from __future__ import annotations

from datetime import date

from app.agents.diff_agent import compute_run_diff
from app.agents.report_agent import ReportResult, run_reports
from app.models import (
    PriorityBand,
    Report,
    Researcher,
    ScoreBreakdown,
    Signal,
    SignalType,
    VCAction,
)
from app.run_diff_store import deserialize_run_diff, serialize_run_diff
from app.scoring import EntityScore


def _breakdown(**overrides: int) -> ScoreBreakdown:
    base = dict(
        research_quality=10,
        applied_relevance=10,
        team_continuity=5,
        open_source_or_project_momentum=5,
        commercialization_signal_strength=5,
        recency=5,
    )
    base.update(overrides)
    return ScoreBreakdown(**base)


def _minimal_result(
    *,
    researchers: list[Researcher],
    reports: list[Report],
    signals: list[Signal] | None = None,
) -> ReportResult:
    base = run_reports(include_clusters=False)
    detection = base.scoring.detection
    detection.researchers = researchers
    detection.signals = signals or []
    base.scoring.researcher_scores = [
        EntityScore(
            entity_id=researcher.id,
            entity_type="researcher",
            entity_name=researcher.name,
            score_breakdown=_breakdown(),
            startup_likelihood_score=50,
            priority_band=PriorityBand.MONITOR_CLOSELY,
            recommendation=VCAction.MONITOR_MONTHLY,
        )
        for researcher in researchers
    ]
    base.reports = reports
    return base


def test_first_run_empty_diff() -> None:
    result = run_reports(include_clusters=False)
    diff = compute_run_diff(
        result,
        None,
        run_id="run_a",
        prior_run_id=None,
        conference="NeurIPS",
        year=2024,
    )
    assert diff.prior_run_id is None
    assert diff.deltas == []
    assert diff.summary.total_deltas == 0


def test_new_researcher_score_and_signal() -> None:
    prior_researcher = Researcher(
        id="researcher_alice",
        name="Alice Prior",
        affiliation="TU Munich",
        role="PhD",
        papers=[],
    )
    prior_report = Report(
        id="report_researcher_alice",
        researcher_or_cluster="Alice Prior",
        summary="prior",
        score_breakdown=_breakdown(),
        startup_likelihood_score=40,
        priority_band=PriorityBand.WEAK_SIGNAL,
        recommendation=VCAction.ADD_TO_WATCHLIST,
    )
    prior_signal = Signal(
        id="sig_1",
        signal_type=SignalType.COMMERCIALIZATION,
        description="Prior signal",
        source_url="https://example.com/prior",
        evidence_strength="medium",
        date_found=date.today(),
        researcher_id="researcher_alice",
    )
    prior = _minimal_result(
        researchers=[prior_researcher],
        reports=[prior_report],
        signals=[prior_signal],
    )

    current_researcher = Researcher(
        id="researcher_bob",
        name="Bob New",
        affiliation="ETH Zurich",
        role="Postdoc",
        papers=[],
    )
    current_report = Report(
        id="report_researcher_bob",
        researcher_or_cluster="Bob New",
        summary="current",
        score_breakdown=_breakdown(),
        startup_likelihood_score=85,
        priority_band=PriorityBand.HIGH_PRIORITY,
        recommendation=VCAction.TAKE_MEETING,
    )
    alice_report = Report(
        id="report_researcher_alice",
        researcher_or_cluster="Alice Prior",
        summary="current alice",
        score_breakdown=_breakdown(),
        startup_likelihood_score=55,
        priority_band=PriorityBand.MONITOR_CLOSELY,
        recommendation=VCAction.MONITOR_MONTHLY,
    )
    new_signal = Signal(
        id="sig_2",
        signal_type=SignalType.POSSIBLE_FOUNDER,
        description="New signal",
        source_url="https://example.com/new",
        evidence_strength="high",
        date_found=date.today(),
        researcher_id="researcher_alice",
    )
    current = _minimal_result(
        researchers=[prior_researcher, current_researcher],
        reports=[alice_report, current_report],
        signals=[prior_signal, new_signal],
    )

    diff = compute_run_diff(
        current,
        prior,
        run_id="run_b",
        prior_run_id="run_a",
        conference="NeurIPS",
        year=2024,
        score_threshold=5,
    )

    change_types = {delta.change_type for delta in diff.deltas}
    assert "new_researcher" in change_types
    assert "score_increased" in change_types
    assert "new_signal" in change_types
    assert "new_take_meeting" in change_types
    assert diff.summary.new_researchers >= 1
    assert diff.summary.new_take_meeting >= 1


def test_run_diff_serialization_roundtrip() -> None:
    result = run_reports(include_clusters=False)
    diff = compute_run_diff(
        result,
        None,
        run_id="run_x",
        conference="NeurIPS",
        year=2024,
    )
    restored = deserialize_run_diff(serialize_run_diff(diff))
    assert restored.run_id == diff.run_id
    assert restored.summary.total_deltas == diff.summary.total_deltas
