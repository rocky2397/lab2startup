"""Tests for Pydantic models and JSON loading."""

import pytest
from pydantic import ValidationError

from app.models import (
    Paper,
    PriorityBand,
    ScoreBreakdown,
    Signal,
    SignalType,
    classify_priority_band,
    recommend_vc_action,
)
from app.schemas import (
    DEFAULT_PAPERS_PATH,
    DEFAULT_SIGNALS_PATH,
    load_papers,
    load_sample_data,
    load_signals,
    summarize_dataset,
)


def test_load_sample_papers() -> None:
    papers = load_papers()
    assert len(papers) == 7
    assert all(paper.conference == "NeurIPS" for paper in papers)
    assert papers[0].title.startswith("SWE-agent")
    assert papers[0].openalex_id == "W4399114781"
    assert papers[0].source_url is not None


def test_load_sample_signals() -> None:
    signals = load_signals()
    assert len(signals) == 9
    signal_types = {signal.signal_type for signal in signals}
    assert SignalType.CONFIRMED_FOUNDER in signal_types
    assert SignalType.POSSIBLE_FOUNDER in signal_types
    assert SignalType.NO_SIGNAL in signal_types


def test_load_sample_data_together() -> None:
    papers, signals = load_sample_data()
    assert len(papers) == 7
    assert len(signals) == 9


def test_summarize_dataset() -> None:
    papers, signals = load_sample_data()
    summary = summarize_dataset(papers, signals)

    assert summary.paper_count == 7
    assert summary.signal_count == 9
    assert summary.unique_researcher_names >= 20
    assert summary.topics == ["AI agents", "biotech AI", "robotics"]
    assert "possible_founder" in summary.signal_types


def test_default_data_paths_exist() -> None:
    assert DEFAULT_PAPERS_PATH.exists()
    assert DEFAULT_SIGNALS_PATH.exists()


def test_paper_validation_rejects_missing_fields() -> None:
    with pytest.raises(ValidationError):
        Paper.model_validate({"id": "paper_bad"})


def test_score_breakdown_total() -> None:
    breakdown = ScoreBreakdown(
        research_quality=15,
        applied_relevance=12,
        team_continuity=10,
        open_source_or_project_momentum=8,
        commercialization_signal_strength=14,
        recency=7,
    )
    assert breakdown.startup_likelihood_score == 66


def test_priority_and_recommendation_mapping() -> None:
    assert classify_priority_band(85) == PriorityBand.HIGH_PRIORITY
    assert classify_priority_band(65) == PriorityBand.MONITOR_CLOSELY
    assert classify_priority_band(45) == PriorityBand.WEAK_SIGNAL
    assert classify_priority_band(20) == PriorityBand.LOW_PRIORITY

    assert recommend_vc_action(PriorityBand.HIGH_PRIORITY).value == "take_meeting"


def test_signal_accepts_researcher_name_from_json() -> None:
    signal = Signal.model_validate(
        {
            "id": "sig_test",
            "researcher_name": "John Yang",
            "signal_type": "confirmed_founder",
            "description": "Test signal",
            "source_url": "https://example.com",
            "evidence_strength": "high",
            "date_found": "2024-01-01",
        }
    )
    assert signal.researcher_name == "John Yang"
    assert signal.researcher_id is None
