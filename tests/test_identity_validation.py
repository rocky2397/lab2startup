"""Tests for wrong-person attribution guards."""

from __future__ import annotations

from app.identity_validation import (
    extract_leading_subject_name,
    names_plausibly_same,
    profile_identity_matches_researcher,
    researcher_name_tokens,
    signal_description_matches_researcher,
    text_refers_to_different_person,
)
from app.integrations.perplexity import (
    build_founder_search_prompt,
    build_researcher_context,
    parse_perplexity_profile,
    parse_perplexity_signals,
)
from app.models import IdentityConfidence, Paper, Researcher


def test_kuttler_xingang_peng_wrong_person_attribution() -> None:
    """Heinrich Küttler must not inherit Xingang Peng profile text."""
    assert extract_leading_subject_name(
        "Xingang Peng is a prominent researcher in AI-driven drug discovery."
    ) == "Xingang Peng"
    assert text_refers_to_different_person(
        "Heinrich Küttler",
        "Xingang Peng is a prominent researcher in AI-driven drug discovery.",
    )
    assert not names_plausibly_same("Heinrich Küttler", "Xingang Peng")
    assert names_plausibly_same("Heinrich Küttler", "Heinrich Kuttler")
    assert researcher_name_tokens("Heinrich Küttler") == researcher_name_tokens("Heinrich Kuttler")

    profile = {
        "affiliation": "Institute for Artificial Intelligence, Peking University",
        "role": "Researcher",
        "identity_confidence": "high",
        "profile_url": "https://example.com/xingang",
        "identity_explanation": "Xingang Peng is a researcher associated with Peking University.",
    }
    assert not profile_identity_matches_researcher("Heinrich Küttler", profile)
    assert not signal_description_matches_researcher(
        "Heinrich Küttler",
        "Xingang Peng is a prominent researcher in AI-driven drug discovery.",
    )


def test_parse_perplexity_rejects_kuttler_wrong_person_profile() -> None:
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


def test_parse_perplexity_rejects_kuttler_wrong_person_signal() -> None:
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
            "identity_explanation": "No match found.",
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


def test_build_researcher_context_includes_coauthors_in_prompt() -> None:
    paper = Paper(
        id="paper_aci",
        title="Affordance-Compiled Intelligence",
        conference="FSE",
        year=2025,
        topic="AI agents",
        abstract="test",
        authors=[],
    )
    douwe = Researcher(
        id="researcher_douwe_kiela",
        name="Douwe Kiela",
        affiliation="Meta",
        role="Researcher",
    )
    kuttler = Researcher(
        id="researcher_heinrich_kuttler",
        name="Heinrich Küttler",
        affiliation="Unknown",
        role="Researcher",
        papers=[paper.id],
        coauthors=[douwe.id],
    )
    researchers_by_id = {douwe.id: douwe, kuttler.id: kuttler}
    context = build_researcher_context(
        kuttler,
        {paper.id: paper},
        researchers_by_id=researchers_by_id,
    )
    prompt = build_founder_search_prompt(context)
    assert context["coauthor_names"] == ["Douwe Kiela"]
    assert "Douwe Kiela" in prompt
    assert "CRITICAL: Investigate ONLY Heinrich Küttler" in prompt
