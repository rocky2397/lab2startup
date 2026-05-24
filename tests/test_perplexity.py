"""Tests for Perplexity integration (Step 10e)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.integrations.perplexity import (
    PerplexityClient,
    PerplexityConfig,
    build_founder_search_prompt,
    build_researcher_context,
    detect_perplexity_signals,
    merge_perplexity_signals,
    parse_perplexity_signals,
)
from app.models import (
    EvidenceStrength,
    IdentityConfidence,
    Paper,
    PaperAuthor,
    Researcher,
    Signal,
    SignalType,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
RESPONSE_FIXTURE = FIXTURES_DIR / "perplexity_founder_response.json"


@pytest.fixture
def perplexity_response() -> dict:
    return json.loads(RESPONSE_FIXTURE.read_text(encoding="utf-8"))


def test_build_researcher_context() -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[PaperAuthor(name="John Yang", affiliation="Stanford", role="PhD Student")],
    )
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Stanford University",
        role="PhD Student",
        papers=[paper.id],
        openreview_url="https://openreview.net/profile?id=~John_Yang3",
    )
    context = build_researcher_context(researcher, {paper.id: paper})
    assert context["name"] == "John Yang"
    assert context["papers"][0]["title"] == "SWE-agent"
    assert "OpenReview" in build_founder_search_prompt(context)


def test_parse_perplexity_signals(perplexity_response: dict) -> None:
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Stanford University",
        role="PhD Student",
    )
    payload = json.loads(perplexity_response["choices"][0]["message"]["content"])
    signals = parse_perplexity_signals(
        payload,
        researcher=researcher,
        citations=perplexity_response["citations"],
        max_signals=2,
    )
    assert len(signals) == 1
    assert signals[0].signal_type == SignalType.COMMERCIALIZATION
    assert signals[0].source_url == "https://john-b-yang.github.io/"
    assert signals[0].evidence_strength == EvidenceStrength.MEDIUM


def test_merge_perplexity_signals_deduplicates_urls() -> None:
    existing = [
        Signal(
            id="sig_001",
            signal_type=SignalType.COMMERCIALIZATION,
            description="existing",
            source_url="https://john-b-yang.github.io/",
            evidence_strength=EvidenceStrength.MEDIUM,
            date_found="2025-05-22",
            researcher_name="John Yang",
        )
    ]
    new = [
        Signal(
            id="perplexity_john_yang_1",
            signal_type=SignalType.POSSIBLE_FOUNDER,
            description="duplicate",
            source_url="https://john-b-yang.github.io/",
            evidence_strength=EvidenceStrength.HIGH,
            date_found="2025-05-22",
            researcher_name="John Yang",
        ),
        Signal(
            id="perplexity_john_yang_2",
            signal_type=SignalType.COMMERCIALIZATION,
            description="new",
            source_url="https://programbench.com/",
            evidence_strength=EvidenceStrength.MEDIUM,
            date_found="2025-05-22",
            researcher_name="John Yang",
        ),
    ]
    merged = merge_perplexity_signals(existing, new)
    assert len(merged) == 2
    assert merged[1].source_url == "https://programbench.com/"


def test_detect_perplexity_signals(
    monkeypatch: pytest.MonkeyPatch,
    perplexity_response: dict,
) -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[PaperAuthor(name="John Yang", affiliation="Stanford", role="PhD Student")],
    )
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Stanford University",
        role="PhD Student",
        papers=[paper.id],
        identity_confidence=IdentityConfidence.HIGH,
    )
    low_confidence = Researcher(
        id="researcher_other",
        name="Other Person",
        affiliation="Unknown",
        role="Researcher",
        identity_confidence=IdentityConfidence.LOW,
    )

    def fake_search_founder_signals(
        self,
        context: dict,
    ) -> tuple[dict, list[str]]:
        assert context["name"] == "John Yang"
        payload = json.loads(perplexity_response["choices"][0]["message"]["content"])
        return payload, perplexity_response["citations"]

    monkeypatch.setattr(
        PerplexityClient,
        "search_founder_signals",
        fake_search_founder_signals,
    )

    signals = detect_perplexity_signals(
        [paper],
        [researcher, low_confidence],
        PerplexityConfig(
            enabled=True,
            api_key="test-key",
            max_researchers=5,
            request_delay_seconds=0,
        ),
    )
    assert len(signals) == 1
    assert signals[0].researcher_name == "John Yang"
