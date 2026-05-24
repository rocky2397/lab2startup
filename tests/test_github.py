"""Tests for GitHub integration (Step 10d)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.integrations.github import (
    GitHubClient,
    GitHubConfig,
    detect_github_signals,
    extract_search_terms,
    merge_github_signals,
    pick_researcher_for_repo,
    repo_to_signal,
)
from app.models import EvidenceStrength, Paper, PaperAuthor, Researcher, Signal, SignalType

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SEARCH_FIXTURE = FIXTURES_DIR / "github_search_swe_agent.json"


@pytest.fixture
def swe_repo_search() -> dict:
    return json.loads(SEARCH_FIXTURE.read_text(encoding="utf-8"))


def test_extract_search_terms_from_paper_title() -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[],
    )
    terms = extract_search_terms(paper)
    assert "SWE-agent" in terms


def test_repo_to_signal() -> None:
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Stanford University",
        role="PhD Student",
    )
    paper = Paper(
        id="paper_001",
        title="SWE-agent",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[],
    )
    repo = {
        "id": 780737106,
        "name": "SWE-agent",
        "full_name": "SWE-agent/SWE-agent",
        "html_url": "https://github.com/SWE-agent/SWE-agent",
        "description": "NeurIPS 2024 software engineering agent",
        "stargazers_count": 1200,
        "pushed_at": "2026-01-01T00:00:00Z",
        "owner": {"login": "SWE-agent", "type": "Organization"},
    }
    signal = repo_to_signal(repo, researcher=researcher, paper=paper)
    assert signal.signal_type == SignalType.COMMERCIALIZATION
    assert signal.evidence_strength == EvidenceStrength.HIGH
    assert signal.source_url == "https://github.com/SWE-agent/SWE-agent"


def test_merge_github_signals_deduplicates_urls() -> None:
    existing = [
        Signal(
            id="sig_002",
            signal_type=SignalType.COMMERCIALIZATION,
            description="existing",
            source_url="https://github.com/SWE-agent/SWE-agent",
            evidence_strength=EvidenceStrength.HIGH,
            date_found="2025-05-22",
            researcher_name="Carlos E. Jimenez",
        )
    ]
    new = [
        Signal(
            id="github_swe_agent_swe_agent",
            signal_type=SignalType.COMMERCIALIZATION,
            description="duplicate",
            source_url="https://github.com/SWE-agent/SWE-agent/",
            evidence_strength=EvidenceStrength.HIGH,
            date_found="2025-05-22",
            researcher_name="John Yang",
        ),
        Signal(
            id="github_bytarnish_agile",
            signal_type=SignalType.COMMERCIALIZATION,
            description="new",
            source_url="https://github.com/bytarnish/AGILE",
            evidence_strength=EvidenceStrength.MEDIUM,
            date_found="2025-05-22",
            researcher_name="Peiyuan Feng",
        ),
    ]
    merged = merge_github_signals(existing, new)
    assert len(merged) == 2
    assert merged[1].source_url.endswith("/AGILE")


def test_detect_github_signals(
    monkeypatch: pytest.MonkeyPatch,
    swe_repo_search: dict,
) -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="SWE-agent project for software engineering agents.",
        authors=[PaperAuthor(name="John Yang", affiliation="Stanford", role="PhD Student")],
    )
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Stanford University",
        role="PhD Student",
        papers=[paper.id],
    )

    def fake_search_repositories(self, query: str, *, per_page: int = 5) -> list[dict]:
        if "SWE-agent" not in query:
            return []
        return swe_repo_search["items"]

    monkeypatch.setattr(GitHubClient, "search_repositories", fake_search_repositories)

    signals = detect_github_signals(
        [paper],
        [researcher],
        GitHubConfig(enabled=True, min_stars=5, request_delay_seconds=0),
    )
    assert len(signals) == 1
    assert signals[0].researcher_name == "John Yang"
    assert "SWE-agent" in signals[0].description


def test_pick_researcher_for_user_owned_repo() -> None:
    paper = Paper(
        id="paper_004",
        title="ToolkenGPT: Augmenting Frozen Language Models with Massive Tools via Tool Embeddings",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[PaperAuthor(name="Shibo Hao", affiliation="Unknown", role="Researcher")],
    )
    researchers = {
        "Shibo Hao": Researcher(
            id="researcher_shibo_hao",
            name="Shibo Hao",
            affiliation="Unknown",
            role="Researcher",
        )
    }
    repo = {
        "name": "ToolkenGPT",
        "owner": {"login": "HaoShao911", "type": "User"},
    }
    picked = pick_researcher_for_repo(repo, paper, researchers)
    assert picked is not None
    assert picked.name == "Shibo Hao"
