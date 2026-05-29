"""Tests for reusing researcher enrichment across runs."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.integrations.openreview import OpenReviewConfig
from app.models import IdentityConfidence, Researcher
from app.profile_link_discovery import (
    fetch_openreview_profiles_for_researchers,
    researcher_needs_openreview_profile_fetch,
)
from app.researcher_enrichment import merge_researcher_enrichment


def test_merge_researcher_enrichment_applies_cached_affiliation() -> None:
    fresh = Researcher(
        id="researcher_a",
        name="Alice Example",
        affiliation="Unknown",
        role="Coauthor",
        openreview_profile_id="~Alice_Example1",
    )
    cached = Researcher(
        id="researcher_a",
        name="Alice Example",
        affiliation="Stanford University",
        role="PhD Student",
        github_username="alice",
        openreview_profile_id="~Alice_Example1",
    )

    merged = merge_researcher_enrichment([fresh], [cached])
    assert merged[0].affiliation == "Stanford University"
    assert merged[0].role == "PhD Student"
    assert merged[0].github_username == "alice"


def test_researcher_needs_openreview_profile_fetch() -> None:
    enriched = Researcher(
        id="researcher_a",
        name="Alice Example",
        affiliation="Stanford University",
        role="PhD Student",
        github_username="alice",
        openreview_profile_id="~Alice_Example1",
    )
    assert researcher_needs_openreview_profile_fetch(enriched) is False

    unknown = Researcher(
        id="researcher_b",
        name="Bob Example",
        affiliation="Unknown",
        role="Coauthor",
        openreview_profile_id="~Bob_Example1",
    )
    assert researcher_needs_openreview_profile_fetch(unknown) is True


def test_fetch_openreview_profiles_skips_already_enriched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enriched = Researcher(
        id="researcher_a",
        name="Alice Example",
        affiliation="Stanford University",
        role="PhD Student",
        github_username="alice",
        openreview_profile_id="~Alice_Example1",
    )
    unknown = Researcher(
        id="researcher_b",
        name="Bob Example",
        affiliation="Unknown",
        role="Coauthor",
        openreview_profile_id="~Bob_Example1",
    )

    get_profiles = MagicMock(return_value={})
    monkeypatch.setattr(
        "app.profile_link_discovery.OpenReviewClient",
        lambda **kwargs: MagicMock(
            __enter__=lambda self: self,
            __exit__=lambda *args: None,
            get_profiles=get_profiles,
        ),
    )

    config = OpenReviewConfig(enabled=True, fetch_profiles=True)
    fetch_openreview_profiles_for_researchers([enriched, unknown], config=config)

    get_profiles.assert_called_once_with(["~Bob_Example1"])
