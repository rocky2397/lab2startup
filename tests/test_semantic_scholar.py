"""Tests for Semantic Scholar integration (Step 10b)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.enrichment_agent import enrich_dataset
from app.agents.ingestion_agent import ingest_papers
from app.integrations.semantic_scholar import (
    SemanticScholarClient,
    SemanticScholarConfig,
    apply_author_metadata,
    apply_paper_metadata,
    enrich_with_semantic_scholar,
    extract_semantic_scholar_paper_id,
    names_match,
)
from app.models import Paper, PaperAuthor, Researcher

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PAPER_BATCH = FIXTURES_DIR / "semantic_scholar_paper_batch.json"
AUTHOR_BATCH = FIXTURES_DIR / "semantic_scholar_author_batch.json"


@pytest.fixture
def paper_batch() -> list[dict]:
    return json.loads(PAPER_BATCH.read_text(encoding="utf-8"))


@pytest.fixture
def author_batch() -> list[dict]:
    return json.loads(AUTHOR_BATCH.read_text(encoding="utf-8"))


def test_extract_semantic_scholar_paper_id_from_arxiv() -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[PaperAuthor(name="John Yang", affiliation="Stanford", role="PhD Student")],
        source_url="https://arxiv.org/abs/2405.15793",
    )
    assert extract_semantic_scholar_paper_id(paper) == "ARXIV:2405.15793"


def test_names_match_handles_middle_names() -> None:
    assert names_match("Carlos E. Jimenez", "Carlos Jimenez")
    assert names_match("Kilian Lieret", "Kilian Adriano Lieret")
    assert not names_match("John Yang", "Shunyu Yao")


def test_apply_paper_metadata(paper_batch: list[dict]) -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[PaperAuthor(name="John Yang", affiliation="Stanford", role="PhD Student")],
        source_url="https://arxiv.org/abs/2405.15793",
    )
    enriched = apply_paper_metadata(paper, paper_batch[0])
    assert enriched.semantic_scholar_id == paper_batch[0]["paperId"]
    assert enriched.citation_count == paper_batch[0]["citationCount"]
    assert enriched.authors[0].semantic_scholar_id == "2109727379"


def test_apply_author_metadata(author_batch: list[dict]) -> None:
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Unknown",
        role="PhD Student",
    )
    enriched = apply_author_metadata(researcher, author_batch[0])
    assert enriched.semantic_scholar_id == "2109727379"
    assert enriched.h_index == 9
    assert enriched.citation_count == 4618


def test_enrich_with_semantic_scholar(
    monkeypatch: pytest.MonkeyPatch,
    paper_batch: list[dict],
    author_batch: list[dict],
) -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[
            PaperAuthor(name="John Yang", affiliation="Stanford University", role="PhD Student"),
            PaperAuthor(name="Ofir Press", affiliation="Princeton University", role="Professor"),
        ],
        source_url="https://arxiv.org/abs/2405.15793",
    )
    researchers = [
        Researcher(
            id="researcher_john_yang",
            name="John Yang",
            affiliation="Stanford University",
            role="PhD Student",
            papers=["paper_001"],
        ),
        Researcher(
            id="researcher_ofir_press",
            name="Ofir Press",
            affiliation="Princeton University",
            role="Professor",
            papers=["paper_001"],
        ),
    ]

    def fake_fetch_papers_batch(self, paper_ids: list[str]) -> list[dict]:
        assert paper_ids == ["ARXIV:2405.15793"]
        return paper_batch

    def fake_fetch_authors_batch(self, author_ids: list[str]) -> list[dict]:
        return author_batch

    monkeypatch.setattr(
        SemanticScholarClient,
        "fetch_papers_batch",
        fake_fetch_papers_batch,
    )
    monkeypatch.setattr(
        SemanticScholarClient,
        "fetch_authors_batch",
        fake_fetch_authors_batch,
    )

    enriched_papers, enriched_researchers = enrich_with_semantic_scholar(
        [paper],
        researchers,
        SemanticScholarConfig(enabled=True, request_delay_seconds=0),
    )

    assert enriched_papers[0].citation_count is not None
    assert enriched_papers[0].citation_count > 0

    john = next(r for r in enriched_researchers if r.name == "John Yang")
    assert john.semantic_scholar_id == "2109727379"
    assert john.h_index == 9


def test_enrich_dataset_disabled_by_default() -> None:
    ingestion = ingest_papers()
    papers, researchers = enrich_dataset(ingestion.papers, ingestion.researchers, None)
    assert papers == ingestion.papers
    assert all(researcher.semantic_scholar_id is None for researcher in researchers)
