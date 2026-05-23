"""Tests for the ingestion agent."""

from app.agents.ingestion_agent import (
    extract_researchers,
    ingest_papers,
    make_researcher_id,
    summarize_ingestion,
)
from app.models import IdentityConfidence
from app.schemas import load_papers


def test_make_researcher_id() -> None:
    assert make_researcher_id("John Yang") == "researcher_john_yang"
    assert make_researcher_id("Carlos E. Jimenez") == "researcher_carlos_e_jimenez"


def test_ingest_papers_loads_real_dataset() -> None:
    result = ingest_papers()
    assert result.paper_count == 7
    assert result.researcher_count == 30


def test_extract_researchers_links_papers_and_coauthors() -> None:
    papers = load_papers()
    researchers = extract_researchers(papers)
    by_id = {researcher.id: researcher for researcher in researchers}

    john = by_id["researcher_john_yang"]
    assert john.name == "John Yang"
    assert john.affiliation == "Stanford University"
    assert john.papers == ["paper_001"]
    assert "researcher_carlos_e_jimenez" in john.coauthors
    assert john.identity_confidence == IdentityConfidence.HIGH

    carlos = by_id["researcher_carlos_e_jimenez"]
    assert "researcher_john_yang" in carlos.coauthors


def test_multi_paper_author_collects_all_papers() -> None:
    researchers = extract_researchers(load_papers())
    marinka = next(r for r in researchers if r.name == "Marinka Zitnik")
    assert marinka.papers == ["paper_006"]
    assert marinka.affiliation == "Harvard University"


def test_summarize_ingestion() -> None:
    summary = summarize_ingestion(ingest_papers())
    assert summary["paper_count"] == 7
    assert summary["researcher_count"] == 30
    assert summary["identity_confidence"]["high"] == 30
