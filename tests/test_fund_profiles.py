"""Tests for fund profiles (Step 15)."""

from __future__ import annotations

import pytest

from app.fund_profiles import (
    DEFAULT_FUND_ID,
    filter_papers_for_fund,
    load_fund_profile,
    paper_matches_fund,
    resolve_conference_list,
    resolve_paper_source_for_fund,
    validate_conference_for_fund,
)
from app.models import Paper, PaperAuthor


@pytest.fixture
def fund():
    return load_fund_profile(DEFAULT_FUND_ID)


def test_load_default_profile(fund) -> None:
    assert fund.id == "default"
    assert fund.name
    assert "NeurIPS" in fund.conference_names
    assert "ICML" in fund.conference_names
    assert "ICLR" in fund.conference_names
    assert "OSDI" in fund.conference_names
    assert "CCS" in fund.conference_names
    assert len(fund.conference_names) >= 14
    assert fund.topic_scores["biotech AI"] == 4


def test_high_priority_conferences(fund) -> None:
    high = fund.high_priority_conferences
    assert "NeurIPS" in high
    assert "MLSys" in high
    assert "USENIX Security" in high
    assert "ICSE" not in high


def test_resolve_conference_list(fund) -> None:
    selected = resolve_conference_list(
        fund,
        conferences=["NeurIPS", "MLSys"],
    )
    assert selected == ["NeurIPS", "MLSys"]

    by_priority = resolve_conference_list(fund, priority="high")
    assert "NeurIPS" in by_priority
    assert "ICSE" not in by_priority


def test_conference_label_includes_source(fund) -> None:
    label = fund.conference_label("MLSys")
    assert "openalex" in label
    assert "MLSys" in label


def test_validate_conference_for_fund(fund) -> None:
    entry = validate_conference_for_fund("NeurIPS", fund)
    assert entry.name == "NeurIPS"

    with pytest.raises(ValueError, match="not in scope"):
        validate_conference_for_fund("CVPR", fund)


def test_resolve_paper_source_for_fund(fund) -> None:
    assert (
        resolve_paper_source_for_fund(
            conference="NeurIPS",
            fund=fund,
            requested_source=None,
        )
        == "openreview"
    )
    assert (
        resolve_paper_source_for_fund(
            conference="MLSys",
            fund=fund,
            requested_source=None,
        )
        == "openalex"
    )

    with pytest.raises(ValueError, match="not supported"):
        resolve_paper_source_for_fund(
            conference="MLSys",
            fund=fund,
            requested_source="openreview",
        )


def test_paper_matches_fund_excludes_biotech(fund) -> None:
    biotech_paper = Paper(
        id="paper_bio",
        title="Drug discovery with deep learning",
        conference="NeurIPS",
        year=2024,
        topic="biotech AI",
        abstract="clinical trial genomics",
        authors=[PaperAuthor(name="A", affiliation="X", role="Researcher")],
    )
    infra_paper = Paper(
        id="paper_infra",
        title="ML systems for serving LLM agents",
        conference="NeurIPS",
        year=2024,
        topic="ML systems",
        abstract="platform engineering infrastructure",
        authors=[PaperAuthor(name="B", affiliation="Y", role="Researcher")],
    )
    assert paper_matches_fund(biotech_paper, fund) is False
    assert paper_matches_fund(infra_paper, fund) is True
    assert len(filter_papers_for_fund([biotech_paper, infra_paper], fund)) == 1
