"""Tests for dashboard context UI helpers."""

from __future__ import annotations

from app.models import Paper, PaperAuthor, Researcher
from dashboard.context_ui import (
    format_conference_year_label,
    infer_region_hint,
    researcher_paper_context,
)


def _paper(**overrides) -> Paper:
    base = dict(
        id="paper_1",
        title="Test",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="abstract",
        authors=[PaperAuthor(name="Jane Doe", affiliation="Stanford", role="PhD")],
    )
    base.update(overrides)
    return Paper(**base)


def test_infer_region_hint_from_affiliation() -> None:
    assert infer_region_hint("Stanford University") == "United States"
    assert infer_region_hint("University of Cambridge") == "United Kingdom"
    assert infer_region_hint("ETH Zurich") == "Switzerland"


def test_researcher_paper_context() -> None:
    researcher = Researcher(
        id="researcher_jane",
        name="Jane Doe",
        affiliation="Stanford University",
        role="PhD Student",
        papers=["paper_1", "paper_2"],
    )
    papers_by_id = {
        "paper_1": _paper(id="paper_1", conference="NeurIPS", year=2024),
        "paper_2": _paper(id="paper_2", conference="ICML", year=2023),
    }
    ctx = researcher_paper_context(researcher, papers_by_id)
    assert ctx["paper_count"] == 2
    assert ctx["conferences"] == ["ICML", "NeurIPS"]
    assert ctx["years"] == [2024, 2023]


def test_format_conference_year_label() -> None:
    assert format_conference_year_label(["NeurIPS"], [2024]) == "NeurIPS 2024"
    assert "NeurIPS" in format_conference_year_label(["NeurIPS", "ICML"], [2024, 2023])
