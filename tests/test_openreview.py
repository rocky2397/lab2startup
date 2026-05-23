"""Tests for OpenReview integration (Step 10c)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.integrations.openreview import (
    OpenReviewClient,
    OpenReviewConfig,
    enrich_papers_with_openreview,
    normalize_note,
    normalize_title,
    sync_researchers_with_openreview,
    venue_id_for_conference,
)
from app.models import Paper, PaperAuthor, Researcher
from app.agents.ingestion_agent import extract_researchers

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
NOTE_FIXTURE = FIXTURES_DIR / "openreview_note_swe_agent.json"
PROFILE_FIXTURE = FIXTURES_DIR / "openreview_profile_john_yang.json"


@pytest.fixture
def swe_note() -> dict:
    payload = json.loads(NOTE_FIXTURE.read_text(encoding="utf-8"))
    return payload["notes"][0]


@pytest.fixture
def john_profile() -> dict:
    payload = json.loads(PROFILE_FIXTURE.read_text(encoding="utf-8"))
    return payload["profiles"][0]


def test_venue_id_for_conference() -> None:
    assert venue_id_for_conference("NeurIPS", 2024) == "NeurIPS.cc/2024/Conference"


def test_normalize_note(swe_note: dict, john_profile: dict) -> None:
    paper = normalize_note(
        swe_note,
        conference="NeurIPS",
        profiles_by_id={john_profile["id"]: john_profile},
    )
    assert paper.openreview_id == "mXpq6ut8J3"
    assert paper.title.startswith("SWE-agent")
    assert paper.year == 2024
    assert paper.openreview_url == "https://openreview.net/forum?id=mXpq6ut8J3"
    assert paper.authors[0].name == "John Yang"
    assert paper.authors[0].openreview_profile_id == "~John_Yang3"


def test_enrich_papers_with_openreview(
    monkeypatch: pytest.MonkeyPatch,
    swe_note: dict,
    john_profile: dict,
) -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[
            PaperAuthor(name="John Yang", affiliation="Unknown", role="Researcher"),
        ],
    )

    def fake_iter_submission_notes(self, **kwargs: object) -> list[dict]:
        return [swe_note]

    def fake_get_profiles(self, profile_ids: list[str]) -> dict[str, dict]:
        return {john_profile["id"]: john_profile}

    monkeypatch.setattr(OpenReviewClient, "iter_submission_notes", fake_iter_submission_notes)
    monkeypatch.setattr(OpenReviewClient, "get_profiles", fake_get_profiles)

    enriched = enrich_papers_with_openreview(
        [paper],
        OpenReviewConfig(
            enabled=True,
            conference="NeurIPS",
            year=2024,
            request_delay_seconds=0,
        ),
    )

    assert enriched[0].openreview_id == "mXpq6ut8J3"
    assert enriched[0].authors[0].openreview_profile_id == "~John_Yang3"
    assert enriched[0].authors[0].affiliation != "Unknown"


def test_sync_researchers_with_openreview() -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[
            PaperAuthor(
                name="John Yang",
                affiliation="Stanford University",
                role="PhD student",
                openreview_profile_id="~John_Yang3",
            )
        ],
        openreview_id="mXpq6ut8J3",
        openreview_url="https://openreview.net/forum?id=mXpq6ut8J3",
    )
    researchers = [
        Researcher(
            id="researcher_john_yang",
            name="John Yang",
            affiliation="Unknown",
            role="Researcher",
            papers=["paper_001"],
        )
    ]

    synced = sync_researchers_with_openreview([paper], researchers)
    assert synced[0].openreview_profile_id == "~John_Yang3"
    assert synced[0].openreview_url == "https://openreview.net/profile?id=~John_Yang3"
    assert synced[0].identity_confidence.value == "high"


def test_title_normalization_matches_fixture(swe_note: dict) -> None:
    title = swe_note["content"]["title"]["value"]
    assert normalize_title(title) == normalize_title(
        "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering"
    )


def test_extract_researchers_after_openreview_sync() -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[
            PaperAuthor(
                name="John Yang",
                affiliation="Stanford University",
                role="PhD student",
                openreview_profile_id="~John_Yang3",
            )
        ],
        openreview_id="mXpq6ut8J3",
    )
    researchers = sync_researchers_with_openreview(
        [paper],
        extract_researchers([paper]),
    )
    assert researchers[0].affiliation == "Stanford University"
