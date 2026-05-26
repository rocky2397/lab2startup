"""Tests for Perplexity Agent API client (mocked HTTP)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.integrations.agent_tools import AgentToolHandlers
from app.integrations.perplexity_agent import (
    PerplexityAgentClient,
    _extract_citations,
    _extract_output_text,
    tier_investigation_config,
)
from app.models import IdentityConfidence, Paper, Researcher

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "agent_responses"
COMPLETED_FIXTURE = FIXTURES_DIR / "standard_completed.json"
REQUIRES_ACTION_FIXTURE = FIXTURES_DIR / "requires_action_github.json"


@pytest.fixture
def agent_completed_body() -> dict:
    return json.loads(COMPLETED_FIXTURE.read_text(encoding="utf-8"))


def test_extract_agent_response_text(agent_completed_body: dict) -> None:
    text = _extract_output_text(agent_completed_body)
    assert "Stanford University" in text
    citations = _extract_citations(agent_completed_body)
    assert "https://john-b-yang.github.io/" in citations


def test_investigate_researcher_mock_transport(agent_completed_body: dict) -> None:
    paper = Paper(
        id="paper_001",
        title="SWE-agent",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[],
    )
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Unknown",
        role="Researcher",
        papers=[paper.id],
        identity_confidence=IdentityConfidence.LOW,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/agent":
            return httpx.Response(200, json=agent_completed_body)
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, base_url="https://api.perplexity.ai") as http_client:
        client = PerplexityAgentClient(api_key="test-key", request_delay_seconds=0)
        client._client = http_client
        result = client.investigate_researcher(
            researcher,
            {paper.id: paper},
            tier="standard",
            config=tier_investigation_config("standard"),
            tool_handlers=AgentToolHandlers(),
        )

    assert result.status == "completed"
    assert len(result.signals) == 1
    assert result.signals[0].id.startswith("agent_")
    assert result.researcher.affiliation == "Stanford University"


def test_requires_action_tool_loop(agent_completed_body: dict) -> None:
    requires_body = json.loads(REQUIRES_ACTION_FIXTURE.read_text(encoding="utf-8"))
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(404)
        calls.append("post")
        body = json.loads(request.content.decode())
        if body.get("previous_response_id"):
            return httpx.Response(200, json=agent_completed_body)
        return httpx.Response(200, json=requires_body)

    transport = httpx.MockTransport(handler)
    paper = Paper(
        id="paper_001",
        title="SWE-agent",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="test",
        authors=[],
    )
    researcher = Researcher(
        id="researcher_john_yang",
        name="John Yang",
        affiliation="Unknown",
        role="Researcher",
        papers=[paper.id],
    )

    with httpx.Client(transport=transport, base_url="https://api.perplexity.ai") as http_client:
        client = PerplexityAgentClient(api_key="test-key", request_delay_seconds=0)
        client._client = http_client
        result = client.investigate_researcher(
            researcher,
            {paper.id: paper},
            tier="standard",
            config=tier_investigation_config("standard"),
            tool_handlers=AgentToolHandlers(),
        )

    assert len(calls) == 2
    assert result.status == "completed"


def test_cli_main_mock_transport(agent_completed_body: dict, monkeypatch) -> None:
    import httpx

    from app.integrations import perplexity_agent as agent_module

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=agent_completed_body)

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("LAB2STARTUP_PERPLEXITY_API_KEY", "test-key")
    from app.config import clear_settings_cache

    clear_settings_cache()

    with httpx.Client(transport=transport, base_url="https://api.perplexity.ai") as http_client:
        original_client = agent_module.PerplexityAgentClient

        class _PatchedClient(original_client):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._client = http_client

        monkeypatch.setattr(agent_module, "PerplexityAgentClient", _PatchedClient)
        exit_code = agent_module.main(
            ["--name", "John Yang", "--tier", "standard", "--paper-title", "SWE-agent"]
        )

    assert exit_code == 0


def test_cli_main_mock_transport(agent_completed_body: dict, monkeypatch) -> None:
    import httpx

    from app.integrations import perplexity_agent as agent_module

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=agent_completed_body)

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("LAB2STARTUP_PERPLEXITY_API_KEY", "test-key")
    from app.config import clear_settings_cache

    clear_settings_cache()

    with httpx.Client(transport=transport, base_url="https://api.perplexity.ai") as http_client:
        original_client = agent_module.PerplexityAgentClient

        class _PatchedClient(original_client):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._client = http_client

        monkeypatch.setattr(agent_module, "PerplexityAgentClient", _PatchedClient)
        exit_code = agent_module.main(
            ["--name", "John Yang", "--tier", "standard", "--paper-title", "SWE-agent"]
        )

    assert exit_code == 0
