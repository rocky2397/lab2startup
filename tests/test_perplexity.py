"""Tests for Perplexity integration (Step 10e)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.integrations.perplexity import (
    PerplexityClient,
    PerplexityConfig,
    _target_researchers_for_perplexity,
    build_founder_search_prompt,
    build_researcher_context,
    detect_perplexity_signals,
    enrich_researchers_with_perplexity,
    merge_perplexity_signals,
    parse_perplexity_profile,
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


def test_target_researchers_for_perplexity_respects_cap() -> None:
    researchers = [
        Researcher(id="a", name="Ada", affiliation="MIT", role="Researcher", papers=["p1", "p2"]),
        Researcher(id="b", name="Bob", affiliation="MIT", role="Researcher", papers=["p1"]),
        Researcher(id="c", name="Carol", affiliation="MIT", role="Researcher", papers=[]),
    ]
    capped = _target_researchers_for_perplexity(researchers, PerplexityConfig(max_researchers=2))
    assert [researcher.id for researcher in capped] == ["a", "b"]

    all_researchers = _target_researchers_for_perplexity(researchers, PerplexityConfig(max_researchers=0))
    assert [researcher.id for researcher in all_researchers] == ["a", "b", "c"]


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


def test_parse_perplexity_profile_rejects_wrong_person() -> None:
    researcher = Researcher(
        id="researcher_heinrich_kuttler",
        name="Heinrich Küttler",
        affiliation="Unknown",
        role="Researcher",
        identity_confidence=IdentityConfidence.MEDIUM,
    )
    payload = {
        "profile": {
            "affiliation": "Institute for Artificial Intelligence, Peking University",
            "role": "Researcher",
            "identity_confidence": "high",
            "profile_url": "https://example.com/xingang",
            "identity_explanation": "Xingang Peng is a researcher associated with Peking University.",
        },
        "signals": [],
    }
    updated = parse_perplexity_profile(
        payload,
        researcher=researcher,
        citations=["https://example.com/xingang"],
    )
    assert updated.affiliation == "Unknown"
    assert updated.identity_confidence == IdentityConfidence.LOW
    assert "different person" in updated.identity_confidence_explanation.lower()


def test_parse_perplexity_signals_rejects_wrong_person_description() -> None:
    researcher = Researcher(
        id="researcher_heinrich_kuttler",
        name="Heinrich Küttler",
        affiliation="Unknown",
        role="Researcher",
    )
    payload = {
        "profile": {
            "affiliation": "Unknown",
            "role": "Researcher",
            "identity_confidence": "low",
            "profile_url": "https://example.com/no-signal",
            "identity_explanation": "No public profile found.",
        },
        "signals": [
            {
                "signal_type": "commercialization",
                "description": "Xingang Peng is a prominent researcher in AI-driven drug discovery.",
                "source_url": "https://example.com/article",
                "evidence_strength": "medium",
            }
        ],
    }
    signals = parse_perplexity_signals(
        payload,
        researcher=researcher,
        citations=["https://example.com/article"],
        max_signals=2,
    )
    assert signals == []


def test_parse_perplexity_profile(perplexity_response: dict) -> None:
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Unknown",
        role="Researcher",
        identity_confidence=IdentityConfidence.LOW,
    )
    payload = json.loads(perplexity_response["choices"][0]["message"]["content"])
    updated = parse_perplexity_profile(
        payload,
        researcher=researcher,
        citations=perplexity_response["citations"],
    )
    assert updated.affiliation == "Stanford University"
    assert updated.role == "PhD Student"
    assert updated.identity_confidence == IdentityConfidence.HIGH


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


def test_enrich_researchers_with_perplexity(
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
        affiliation="Unknown",
        role="Researcher",
        papers=[paper.id],
        identity_confidence=IdentityConfidence.LOW,
    )
    low_confidence = Researcher(
        id="researcher_other",
        name="Other Person",
        affiliation="Unknown",
        role="Researcher",
        identity_confidence=IdentityConfidence.LOW,
    )

    def fake_search_researcher_intel(
        self,
        context: dict,
    ) -> tuple[dict, list[str]]:
        assert context["name"] == "John Yang"
        payload = json.loads(perplexity_response["choices"][0]["message"]["content"])
        return payload, perplexity_response["citations"]

    monkeypatch.setattr(
        PerplexityClient,
        "search_researcher_intel",
        fake_search_researcher_intel,
    )

    updated_researchers, signals = enrich_researchers_with_perplexity(
        [paper],
        [researcher, low_confidence],
        PerplexityConfig(
            enabled=True,
            api_key="test-key",
            max_researchers=5,
            request_delay_seconds=0,
        ),
    )
    john = next(item for item in updated_researchers if item.name == "John Yang")
    assert john.affiliation == "Stanford University"
    assert len(signals) == 1
    assert signals[0].researcher_name == "John Yang"


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

    def fake_search_researcher_intel(
        self,
        context: dict,
    ) -> tuple[dict, list[str]]:
        payload = json.loads(perplexity_response["choices"][0]["message"]["content"])
        return payload, perplexity_response["citations"]

    monkeypatch.setattr(
        PerplexityClient,
        "search_researcher_intel",
        fake_search_researcher_intel,
    )

    signals = detect_perplexity_signals(
        [paper],
        [researcher],
        PerplexityConfig(
            enabled=True,
            api_key="test-key",
            max_researchers=5,
            request_delay_seconds=0,
        ),
    )
    assert len(signals) == 1
