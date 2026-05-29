"""Tests for tiered profile link discovery."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.integrations.openreview import OpenReviewConfig
from app.models import IdentityConfidence, Researcher
from app.profile_link_discovery import (
    discover_links_from_homepage,
    discover_links_from_openreview_profile,
    discover_profile_links_tier0,
    extract_urls_from_page_content,
    github_user_from_homepage_url,
    researcher_missing_profile_links,
    retry_missing_profile_links,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
OPENREVIEW_PROFILE = FIXTURES_DIR / "openreview_profile_john_yang.json"


@pytest.fixture
def john_yang_profile() -> dict:
    payload = json.loads(OPENREVIEW_PROFILE.read_text(encoding="utf-8"))
    return payload["profiles"][0]


def test_github_user_from_homepage_url() -> None:
    assert github_user_from_homepage_url("https://john-b-yang.github.io/") == "john-b-yang"


def test_extract_urls_from_page_content() -> None:
    html = """
    <a href="https://www.linkedin.com/in/jyang20/">LinkedIn</a>
    <a href='https://github.com/john-yang'>GitHub</a>
    """
    urls = extract_urls_from_page_content(html)
    assert "https://www.linkedin.com/in/jyang20/" in urls
    assert "https://github.com/john-yang" in urls


def test_discover_links_from_openreview_profile(john_yang_profile: dict) -> None:
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Stanford University",
        role="PhD Student",
        openreview_profile_id="~John_Yang3",
        identity_confidence=IdentityConfidence.HIGH,
    )
    updated = discover_links_from_openreview_profile(researcher, john_yang_profile)
    assert updated.linkedin_url == "https://www.linkedin.com/in/jyang20"
    assert updated.github_username == "john-b-yang"
    assert updated.profile_url == "https://john-b-yang.github.io"


def test_discover_links_from_homepage() -> None:
    researcher = Researcher(
        id="researcher_jane_doe",
        name="Jane Doe",
        affiliation="MIT",
        role="Postdoc",
        profile_url="https://janedoe.example.com",
        identity_confidence=IdentityConfidence.HIGH,
    )
    html = '<a href="https://linkedin.com/in/janedoe">LinkedIn</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    updated = discover_links_from_homepage(
        researcher,
        "https://janedoe.example.com",
        client=client,
    )
    assert updated.linkedin_url == "https://www.linkedin.com/in/janedoe"


def test_discover_profile_links_tier0_uses_openreview(monkeypatch: pytest.MonkeyPatch, john_yang_profile: dict) -> None:
    def fake_fetch(researchers, *, config):
        return {"~John_Yang3": john_yang_profile}

    monkeypatch.setattr(
        "app.profile_link_discovery.fetch_openreview_profiles_for_researchers",
        fake_fetch,
    )

    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Stanford University",
        role="PhD Student",
        openreview_profile_id="~John_Yang3",
        identity_confidence=IdentityConfidence.HIGH,
    )
    config = OpenReviewConfig(enabled=True, fetch_profiles=True)
    updated = discover_profile_links_tier0(
        [researcher],
        openreview_config=config,
        fetch_homepages=False,
    )
    assert updated[0].linkedin_url == "https://www.linkedin.com/in/jyang20"
    assert updated[0].github_username == "john-b-yang"


def test_retry_missing_profile_links(monkeypatch: pytest.MonkeyPatch) -> None:
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Stanford University",
        role="PhD Student",
        identity_confidence=IdentityConfidence.HIGH,
    )

    def fake_retry(researcher_arg, context, config):
        return researcher_arg.model_copy(
            update={
                "linkedin_url": "https://www.linkedin.com/in/john-yang",
                "github_username": "john-yang",
            }
        )

    monkeypatch.setattr(
        "app.profile_link_discovery.retry_profile_links_with_perplexity",
        fake_retry,
    )

    from app.integrations.perplexity import PerplexityConfig

    updated = retry_missing_profile_links(
        [researcher],
        papers=[],
        perplexity_config=PerplexityConfig(enabled=True, api_key="test-key"),
        priority_ids={researcher.id},
    )
    assert updated[0].linkedin_url == "https://www.linkedin.com/in/john-yang"
    assert updated[0].github_username == "john-yang"


def test_researcher_missing_profile_links() -> None:
    missing = Researcher(
        id="researcher_x",
        name="X",
        affiliation="Unknown",
        role="Researcher",
    )
    assert researcher_missing_profile_links(missing) is True

    has_github = Researcher(
        id="researcher_y",
        name="Y",
        affiliation="Unknown",
        role="Researcher",
        github_username="abc",
    )
    assert researcher_missing_profile_links(has_github) is False
