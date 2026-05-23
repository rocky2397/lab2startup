"""Tests for OpenAlex integration (Step 10a)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.integrations.openalex import (
    OpenAlexFetchConfig,
    decode_abstract,
    fetch_papers_from_openalex,
    infer_topic,
    normalize_work,
    write_papers_json,
)
from app.schemas import load_papers

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SWE_AGENT_WORK = FIXTURES_DIR / "openalex_work_swe_agent.json"


@pytest.fixture
def swe_agent_work() -> dict:
    return json.loads(SWE_AGENT_WORK.read_text(encoding="utf-8"))


def test_decode_abstract(swe_agent_work: dict) -> None:
    abstract = decode_abstract(swe_agent_work["abstract_inverted_index"])
    assert abstract.startswith("Language model")
    assert "SWE-agent" in abstract or "software" in abstract


def test_normalize_work(swe_agent_work: dict) -> None:
    paper = normalize_work(
        swe_agent_work,
        conference="NeurIPS",
        topic_keywords=["AI agents"],
    )
    assert paper.openalex_id == "W4399114781"
    assert paper.conference == "NeurIPS"
    assert paper.year == 2024
    assert paper.topic == "AI agents"
    assert paper.title.startswith("SWE-agent")
    assert paper.authors
    assert paper.source_url is not None


def test_infer_topic_prefers_keyword_rules() -> None:
    topic = infer_topic(
        "Efficient LLM inference serving at scale",
        "We optimize training and inference systems for large models.",
    )
    assert topic == "ML systems"


def test_fetch_papers_by_work_ids(monkeypatch: pytest.MonkeyPatch, swe_agent_work: dict) -> None:
    def fake_fetch_works_by_ids(self, work_ids: list[str]) -> list[dict]:
        assert work_ids == ["W4399114781"]
        return [swe_agent_work]

    monkeypatch.setattr(
        "app.integrations.openalex.OpenAlexClient.fetch_works_by_ids",
        fake_fetch_works_by_ids,
    )

    papers = fetch_papers_from_openalex(
        OpenAlexFetchConfig(
            openalex_work_ids=["W4399114781"],
            max_results=5,
            request_delay_seconds=0,
        )
    )

    assert len(papers) == 1
    assert papers[0].openalex_id == "W4399114781"


def test_write_papers_json_roundtrip(tmp_path: Path, swe_agent_work: dict) -> None:
    paper = normalize_work(swe_agent_work, conference="NeurIPS")
    output_path = write_papers_json([paper], tmp_path / "papers.json")

    loaded = load_papers(output_path)
    assert len(loaded) == 1
    assert loaded[0].title == paper.title
    assert loaded[0].openalex_id == "W4399114781"
