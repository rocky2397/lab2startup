"""Integration tests for agentic signal LangGraph (mocked Agent API)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from app.agents.signal_agent import detect_signals
from app.agents.signal_graph import run_agentic_signal_graph
from app.config import AgenticSignalConfig, clear_settings_cache
from app.integrations.perplexity_agent import AgentInvestigationResult, PerplexityAgentClient
from app.models import IdentityConfidence, Paper, Researcher, Signal, SignalType
from app.service import clear_cache

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "agent_responses"
COMPLETED_FIXTURE = FIXTURES_DIR / "standard_completed.json"


@pytest.fixture
def agent_completed_body() -> dict:
    return json.loads(COMPLETED_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def mock_agent_client(agent_completed_body: dict) -> PerplexityAgentClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=agent_completed_body)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://api.perplexity.ai")
    client = PerplexityAgentClient(api_key="test-key", request_delay_seconds=0)
    client._client = http_client
    return client


def test_run_agentic_signal_graph_emits_agent_signals(
    tmp_path: Path,
    mock_agent_client: PerplexityAgentClient,
) -> None:
    from app.agents.profile_agent import build_profiles

    profile = build_profiles()
    config = AgenticSignalConfig(
        enabled=True,
        api_key="test-key",
        max_agent_calls=1,
        max_total_steps=10,
        db_path=tmp_path / "agent.db",
    )

    researchers, signals, traces = run_agentic_signal_graph(
        run_id="run_graph_test",
        papers=profile.papers,
        researchers=profile.researchers[:3],
        clusters=profile.clusters,
        config=config,
        agent_client=mock_agent_client,
    )

    assert len(traces) == 1
    assert any(signal.id.startswith("agent_") for signal in signals)
    assert researchers


def test_detect_signals_agentic_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_completed_body: dict,
) -> None:
    monkeypatch.setenv("LAB2STARTUP_AGENTIC_SIGNALS", "true")
    monkeypatch.setenv("LAB2STARTUP_USE_MOCK_SIGNALS", "false")
    monkeypatch.setenv("LAB2STARTUP_PERPLEXITY_ENABLED", "true")
    monkeypatch.setenv("LAB2STARTUP_PERPLEXITY_API_KEY", "test-key")
    monkeypatch.setenv("LAB2STARTUP_AGENTIC_MAX_CALLS", "1")
    monkeypatch.setenv("LAB2STARTUP_DB_PATH", str(tmp_path / "detect.db"))
    clear_settings_cache()
    clear_cache()

    mock_result = AgentInvestigationResult(
        payload={},
        citations=["https://john-b-yang.github.io/"],
        signals=[
            Signal(
                id="agent_john_yang_1",
                signal_type=SignalType.POSSIBLE_FOUNDER,
                description="Stealth startup mention",
                source_url="https://john-b-yang.github.io/",
                evidence_strength="medium",
                date_found="2025-05-22",
                researcher_name="John Yang",
            )
        ],
        researcher=Researcher(
            id="researcher_john_yang",
            name="John Yang",
            affiliation="Stanford University",
            role="PhD Student",
            identity_confidence=IdentityConfidence.HIGH,
        ),
        status="completed",
        steps_used=3,
        tool_calls_count=1,
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=0.01,
        summary="ok",
        request_json={},
        response_json=agent_completed_body,
    )

    mock_client = MagicMock(spec=PerplexityAgentClient)
    mock_client.investigate_researcher.return_value = mock_result

    from app.config import get_settings

    settings = get_settings()
    config = settings.agentic_signal_config

    def _mock_graph(**kwargs):
        kwargs["agent_client"] = mock_client
        return run_agentic_signal_graph(**kwargs)

    monkeypatch.setattr(
        "app.agents.signal_graph.run_agentic_signal_graph",
        _mock_graph,
    )

    result = detect_signals(
        agentic_signal_config=config,
        use_mock_signals=False,
        perplexity_config=settings.perplexity_config,
        run_id="run_detect_test",
    )
    agent_signals = [signal for signal in result.signals if signal.id.startswith("agent_")]
    assert agent_signals


def test_agentic_graph_scoring_unchanged(
    tmp_path: Path,
    mock_agent_client: PerplexityAgentClient,
) -> None:
    from app.agents.profile_agent import build_profiles
    from app.agents.scoring_agent import compute_scores
    from app.agents.signal_agent import SignalDetectionResult, attach_signals

    profile = build_profiles()
    config = AgenticSignalConfig(
        enabled=True,
        api_key="test-key",
        max_agent_calls=1,
        db_path=tmp_path / "score.db",
    )

    researchers, signals, _traces = run_agentic_signal_graph(
        run_id="run_score_test",
        papers=profile.papers,
        researchers=profile.researchers,
        clusters=profile.clusters,
        config=config,
        agent_client=mock_agent_client,
    )
    resolved, _ = attach_signals(signals, researchers, profile.clusters)
    detection = SignalDetectionResult(
        papers=profile.papers,
        researchers=researchers,
        clusters=profile.clusters,
        signals=resolved,
    )
    scoring = compute_scores(detection)
    assert scoring.researcher_scores
    assert scoring.detection.signals


def test_agentic_graph_early_exit_stops_queue(
    tmp_path: Path,
    agent_completed_body: dict,
) -> None:
    from app.agents.profile_agent import build_profiles
    from app.models import EvidenceStrength

    profile = build_profiles()
    config = AgenticSignalConfig(
        enabled=True,
        api_key="test-key",
        max_agent_calls=5,
        early_exit=True,
        db_path=tmp_path / "early_exit.db",
    )

    high_founder_result = AgentInvestigationResult(
        payload={},
        citations=["https://example.com/founder"],
        signals=[
            Signal(
                id="agent_founder_1",
                signal_type=SignalType.CONFIRMED_FOUNDER,
                description="Founded Acme AI",
                source_url="https://example.com/founder",
                evidence_strength=EvidenceStrength.HIGH,
                date_found="2025-05-22",
                researcher_name="Jane Doe",
            )
        ],
        researcher=profile.researchers[0],
        status="completed",
        steps_used=3,
        tool_calls_count=1,
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=0.01,
        summary="High-confidence founder",
        request_json={},
        response_json=agent_completed_body,
    )

    mock_client = MagicMock(spec=PerplexityAgentClient)
    mock_client.investigate_researcher.return_value = high_founder_result

    _researchers, _signals, traces = run_agentic_signal_graph(
        run_id="run_early_exit",
        papers=profile.papers,
        researchers=profile.researchers[:5],
        clusters=profile.clusters,
        config=config,
        agent_client=mock_client,
    )

    assert len(traces) == 1
    mock_client.investigate_researcher.assert_called_once()


def test_agentic_graph_early_exit_stops_queue(
    tmp_path: Path,
    agent_completed_body: dict,
) -> None:
    from app.agents.profile_agent import build_profiles
    from app.models import EvidenceStrength

    profile = build_profiles()
    config = AgenticSignalConfig(
        enabled=True,
        api_key="test-key",
        max_agent_calls=5,
        early_exit=True,
        db_path=tmp_path / "early_exit.db",
    )

    high_founder_result = AgentInvestigationResult(
        payload={},
        citations=["https://example.com/founder"],
        signals=[
            Signal(
                id="agent_founder_1",
                signal_type=SignalType.CONFIRMED_FOUNDER,
                description="Founded Acme AI",
                source_url="https://example.com/founder",
                evidence_strength=EvidenceStrength.HIGH,
                date_found="2025-05-22",
                researcher_name="Jane Doe",
            )
        ],
        researcher=profile.researchers[0],
        status="completed",
        steps_used=3,
        tool_calls_count=1,
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=0.01,
        summary="High-confidence founder",
        request_json={},
        response_json=agent_completed_body,
    )

    mock_client = MagicMock(spec=PerplexityAgentClient)
    mock_client.investigate_researcher.return_value = high_founder_result

    _researchers, _signals, traces = run_agentic_signal_graph(
        run_id="run_early_exit",
        papers=profile.papers,
        researchers=profile.researchers[:5],
        clusters=profile.clusters,
        config=config,
        agent_client=mock_client,
    )

    assert len(traces) == 1
    mock_client.investigate_researcher.assert_called_once()
