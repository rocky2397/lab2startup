"""Tests for thesis fit rules (Step 17)."""

from __future__ import annotations

from datetime import date

import pytest

from app.fund_profiles import DEFAULT_FUND_ID, load_fund_profile
from app.models import (
    Paper,
    PaperAuthor,
    PriorityBand,
    Report,
    Researcher,
    ScoreBreakdown,
    Signal,
    SignalType,
    VCAction,
)
from app.thesis_fit_models import ThesisFitLevel
from app.thesis_fit_rules import assess_thesis_fit


@pytest.fixture
def backtrace():
    return load_fund_profile(DEFAULT_FUND_ID)


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


def _report(researcher_id: str, name: str, score: int = 50) -> Report:
    return Report(
        id=f"report_{researcher_id}",
        researcher_or_cluster=name,
        summary="summary",
        score_breakdown=_breakdown(applied_relevance=15),
        startup_likelihood_score=score,
        priority_band=PriorityBand.MONITOR_CLOSELY,
        recommendation=VCAction.MONITOR_MONTHLY,
    )


def test_munich_ml_systems_strong_or_moderate(backtrace) -> None:
    researcher = Researcher(
        id="researcher_munich",
        name="Elena Infra",
        affiliation="Technical University of Munich",
        role="PhD",
        papers=["paper_1"],
    )
    paper = Paper(
        id="paper_1",
        title="Distributed ML systems platform",
        conference="NeurIPS",
        year=2024,
        topic="ML systems",
        abstract="MLOps and distributed systems for training",
        authors=[PaperAuthor(name="Elena Infra", affiliation="TUM", role="PhD")],
    )
    assessment = assess_thesis_fit(
        researcher,
        _report("researcher_munich", "Elena Infra"),
        [],
        backtrace,
        papers_by_id={"paper_1": paper},
    )
    assert assessment.europe_nexus == "yes"
    assert assessment.infra_layer in ("infra", "mixed")
    assert assessment.fit_level in (ThesisFitLevel.STRONG, ThesisFitLevel.MODERATE)


def test_stanford_biotech_weak(backtrace) -> None:
    researcher = Researcher(
        id="researcher_bio",
        name="Sam Bio",
        affiliation="Stanford University",
        role="Professor",
        papers=[],
    )
    signals = [
        Signal(
            id="sig_bio",
            signal_type=SignalType.COMMERCIALIZATION,
            description="Drug discovery startup in genomics",
            source_url="https://example.com/bio",
            evidence_strength="high",
            date_found=date.today(),
            researcher_id="researcher_bio",
        )
    ]
    assessment = assess_thesis_fit(
        researcher,
        _report("researcher_bio", "Sam Bio"),
        signals,
        backtrace,
    )
    assert assessment.europe_nexus == "no"
    assert assessment.fit_level == ThesisFitLevel.WEAK


def test_backtrace_yaml_has_thesis_fit(backtrace) -> None:
    assert backtrace.thesis_fit is not None
    assert "Germany" in backtrace.thesis_fit.europe_regions
    assert "MLOps" in "".join(backtrace.thesis_fit.infra_keywords)
