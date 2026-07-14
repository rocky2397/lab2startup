"""Tests for the golden-set eval harness (offline — no API calls)."""

from __future__ import annotations

from datetime import date

from app.agents.report_agent import ReportResult
from app.agents.scoring_agent import ScoringResult
from app.agents.signal_agent import SignalDetectionResult
from app.models import (
    EvidenceStrength,
    PriorityBand,
    Report,
    ScoreBreakdown,
    Signal,
    SignalType,
    VCAction,
)
from evals.harness import (
    GoldenPaper,
    GoldenResearcher,
    GoldenSet,
    build_papers,
    classify_predictions,
    compute_metrics,
    load_golden_set,
    render_markdown_report,
)


def _golden_set() -> GoldenSet:
    paper = GoldenPaper(title="Shared Paper", year=2020)
    return GoldenSet(
        as_of="2026-07",
        researchers=[
            GoldenResearcher(
                name="Founder Found",
                label="founder",
                company="FoundCo",
                affiliation_at_pub="Uni A",
                papers=[paper],
            ),
            GoldenResearcher(
                name="Founder Missed",
                label="founder",
                company="MissCo",
                affiliation_at_pub="Uni B",
                papers=[GoldenPaper(title="Other Paper", year=2021)],
            ),
            GoldenResearcher(
                name="Clean Negative",
                label="non_founder",
                affiliation_at_pub="Uni C",
                papers=[paper],
            ),
            GoldenResearcher(
                name="Noisy Negative",
                label="non_founder",
                affiliation_at_pub="Uni D",
                papers=[GoldenPaper(title="Fourth Paper", year=2022)],
            ),
        ],
    )


def _signal(signal_id: str, researcher_id: str, signal_type: SignalType) -> Signal:
    return Signal(
        id=signal_id,
        signal_type=signal_type,
        description="test",
        source_url="https://example.com/evidence",
        evidence_strength=EvidenceStrength.HIGH,
        date_found=date(2026, 7, 1),
        researcher_id=researcher_id,
    )


def _result(golden: GoldenSet) -> ReportResult:
    papers = build_papers(golden)
    from app.agents.ingestion_agent import extract_researchers

    researchers = extract_researchers(papers)
    signals = [
        _signal("perplexity_1", "researcher_founder_found", SignalType.CONFIRMED_FOUNDER),
        _signal("perplexity_2", "researcher_noisy_negative", SignalType.POSSIBLE_FOUNDER),
    ]
    detection = SignalDetectionResult(
        papers=papers,
        researchers=researchers,
        clusters=[],
        signals=signals,
    )
    breakdown = ScoreBreakdown(
        research_quality=10,
        applied_relevance=10,
        team_continuity=5,
        open_source_or_project_momentum=5,
        commercialization_signal_strength=10,
        recency=5,
    )
    reports = [
        Report(
            id="report_researcher_founder_found",
            researcher_or_cluster="Founder Found",
            summary="s",
            signals=signals[:1],
            score_breakdown=breakdown,
            startup_likelihood_score=45,
            priority_band=PriorityBand.WEAK_SIGNAL,
            recommendation=VCAction.ADD_TO_WATCHLIST,
        )
    ]
    return ReportResult(scoring=ScoringResult(detection=detection), reports=reports)


def test_build_papers_merges_shared_titles() -> None:
    papers = build_papers(_golden_set())
    assert len(papers) == 3
    shared = next(paper for paper in papers if paper.title == "Shared Paper")
    assert {author.name for author in shared.authors} == {"Founder Found", "Clean Negative"}


def test_classification_and_metrics() -> None:
    golden = _golden_set()
    rows = classify_predictions(_result(golden), golden)
    by_name = {row.name: row for row in rows}

    assert by_name["Founder Found"].predicted_strict
    assert not by_name["Founder Missed"].predicted_lenient
    assert not by_name["Clean Negative"].predicted_lenient
    assert by_name["Noisy Negative"].predicted_lenient
    assert not by_name["Noisy Negative"].predicted_strict
    assert by_name["Founder Found"].startup_score == 45

    strict = compute_metrics(rows, lenient=False)
    assert (strict["tp"], strict["fp"], strict["fn"], strict["tn"]) == (1, 0, 1, 2)
    assert strict["precision"] == 1.0
    assert strict["recall"] == 0.5

    lenient = compute_metrics(rows, lenient=True)
    assert (lenient["tp"], lenient["fp"], lenient["fn"], lenient["tn"]) == (1, 1, 1, 1)
    assert lenient["false_positive_rate"] == 0.5


def test_markdown_report_renders() -> None:
    golden = _golden_set()
    rows = classify_predictions(_result(golden), golden)
    report = render_markdown_report(golden, rows, mode="sonar")
    assert "Founder Found" in report
    assert "Missed founders" in report and "Founder Missed" in report
    assert "False positives" in report and "Noisy Negative" in report
    assert "not yet marked `verified`" in report


def test_real_golden_set_is_valid() -> None:
    golden = load_golden_set()
    assert len(golden.researchers) >= 20
    assert len(golden.founders()) >= 10
    assert len(golden.non_founders()) >= 10

    names = [entry.name for entry in golden.researchers]
    assert len(names) == len(set(names)), "duplicate researcher names"
    ids = [entry.researcher_id for entry in golden.researchers]
    assert len(ids) == len(set(ids)), "researcher id collision"

    for entry in golden.researchers:
        assert entry.papers, entry.name
        for paper in entry.papers:
            assert 2015 <= paper.year <= 2025, f"{entry.name}: paper outside 10-year window"
        if entry.label == "founder":
            assert entry.company, f"{entry.name}: founder without company"
