"""Tests for researcher profile link resolution."""

from __future__ import annotations

from datetime import date

import pytest

from app.models import EvidenceStrength, Researcher, Signal, SignalType
from app.researcher_links import (
    accept_github_profile_for_researcher,
    accept_linkedin_profile_for_researcher,
    github_login_matches_researcher,
    normalize_github_profile_url,
    normalize_linkedin_profile_url,
    resolve_researcher_links,
)


@pytest.fixture(autouse=True)
def _github_user_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.researcher_links.github_user_exists", lambda login: True)


def test_normalize_github_from_username() -> None:
    assert normalize_github_profile_url("john-yang") == "https://github.com/john-yang"


def test_normalize_github_from_repo_url() -> None:
    assert normalize_github_profile_url("https://github.com/SWE-agent/SWE-agent") == "https://github.com/SWE-agent"


def test_normalize_linkedin_from_profile_url() -> None:
    assert (
        normalize_linkedin_profile_url("https://www.linkedin.com/in/john-yang/")
        == "https://www.linkedin.com/in/john-yang"
    )


def test_github_login_matches_researcher() -> None:
    assert github_login_matches_researcher("John Yang", "john-yang")
    assert not github_login_matches_researcher("John Yang", "SWE-agent")


def test_accept_github_rejects_org_repo_owner() -> None:
    assert (
        accept_github_profile_for_researcher(
            "John Yang",
            "https://github.com/SWE-agent/SWE-agent",
        )
        is None
    )


def test_accept_github_rejects_missing_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.researcher_links.github_user_exists", lambda login: False)
    assert accept_github_profile_for_researcher("John Yang", "john-yang") is None


def test_accept_linkedin_rejects_unrelated_slug() -> None:
    assert (
        accept_linkedin_profile_for_researcher(
            "Kehai Chen",
            "https://www.linkedin.com/in/random-person-12345",
        )
        is None
    )


def test_resolve_links_from_researcher_fields() -> None:
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Stanford University",
        role="PhD Student",
        github_username="john-yang",
        linkedin_url="https://www.linkedin.com/in/john-yang",
    )
    links = resolve_researcher_links(researcher)
    assert links.github == "https://github.com/john-yang"
    assert links.linkedin == "https://www.linkedin.com/in/john-yang"


def test_resolve_links_from_signals_when_fields_missing() -> None:
    researcher = Researcher(
        id="researcher_jane_doe",
        name="Jane Doe",
        affiliation="MIT",
        role="Postdoc",
    )
    signals = [
        Signal(
            id="sig_1",
            signal_type=SignalType.COMMERCIALIZATION,
            description="GitHub repo",
            source_url="https://github.com/janedoe/cool-project",
            evidence_strength=EvidenceStrength.MEDIUM,
            date_found=date.today(),
            researcher_name="Jane Doe",
        ),
        Signal(
            id="sig_2",
            signal_type=SignalType.POSSIBLE_FOUNDER,
            description="LinkedIn profile",
            source_url="https://linkedin.com/in/janedoe",
            evidence_strength=EvidenceStrength.HIGH,
            date_found=date.today(),
            researcher_name="Jane Doe",
        ),
    ]
    links = resolve_researcher_links(researcher, signals)
    assert links.github == "https://github.com/janedoe"
    assert links.linkedin == "https://www.linkedin.com/in/janedoe"


def test_resolve_links_rejects_mismatched_signal_github() -> None:
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Stanford University",
        role="PhD Student",
    )
    signals = [
        Signal(
            id="sig_1",
            signal_type=SignalType.COMMERCIALIZATION,
            description="Project repo",
            source_url="https://github.com/SWE-agent/SWE-agent",
            evidence_strength=EvidenceStrength.HIGH,
            date_found=date.today(),
            researcher_name="John Yang",
        ),
    ]
    links = resolve_researcher_links(researcher, signals)
    assert links.github is None
