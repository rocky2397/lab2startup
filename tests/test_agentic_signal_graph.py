"""Integration tests for agentic signal LangGraph (mocked Agent API)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from app.agents.profile_agent import build_profiles
from app.agents.signal_agent import detect_signals
from app.agents.signal_graph import _config_from_state, initialize_node, investigate_researcher_node, run_agentic_signal_graph
from app.config import AgenticSignalConfig, clear_settings_cache
from app.integrations.perplexity_agent import AgentInvestigationResult, PerplexityAgentClient
from app.models import IdentityConfidence, Researcher, Signal, SignalType
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


def test_config_from_state_preserves_zero_as_unlimited() -> None:
    config = _config_from_state({"max_agent_calls": 0, "max_total_steps": 0})
    assert config.max_agent_calls == 0
    assert config.max_total_steps == 0

    defaults = _config_from_state({})
    assert defaults.max_agent_calls == 10
    assert defaults.max_total_steps == 40


def _neurips_like_researchers(count: int = 78) -> list[Researcher]:
    return [
        Researcher(
            id=f"researcher_{index}",
            name=f"Researcher {index}",
            affiliation="Stanford",
            role="PhD Student",
            papers=[f"paper_{index}_{paper_index}" for paper_index in range(4)],
            identity_confidence=IdentityConfidence.HIGH,
        )
        for index in range(count)
    ]


def test_initialize_node_unlimited_queues_all_non_skip_researchers() -> None:
    """Regression: LAB2STARTUP_AGENTIC_MAX_CALLS=0 must queue all 78, not 15."""
    researchers = _neurips_like_researchers()
    result = initialize_node(
        {
            "researchers": researchers,
            "papers": [],
            "max_agent_calls": 0,
            "max_total_steps": 0,
            "early_exit_enabled": False,
        }
    )
    assert len(result["investigation_queue"]) == 78


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


def test_investigate_researcher_node_falls_back_to_light_on_standard_400(
    tmp_path: Path,
    agent_completed_body: dict,
) -> None:
    profile = build_profiles()
    researcher = profile.researchers[0]
    config = AgenticSignalConfig(
        enabled=True,
        api_key="test-key",
        db_path=tmp_path / "fallback.db",
    )

    failed_result = AgentInvestigationResult(
        payload=None,
        citations=[],
        signals=[],
        researcher=researcher,
        status="failed",
        steps_used=0,
        tool_calls_count=0,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=None,
        summary="Agent investigation failed.",
        request_json={"preset": "pro-search"},
        response_json=None,
        error_message="Agent API error 400: invalid tools configuration",
    )
    success_result = AgentInvestigationResult(
        payload={},
        citations=["https://example.com/founder"],
        signals=[
            Signal(
                id="agent_fallback_1",
                signal_type=SignalType.POSSIBLE_FOUNDER,
                description="Founder evidence",
                source_url="https://example.com/founder",
                evidence_strength="medium",
                date_found="2025-05-22",
                researcher_name=researcher.name,
            )
        ],
        researcher=researcher.model_copy(update={"affiliation": "Stanford University"}),
        status="completed",
        steps_used=1,
        tool_calls_count=1,
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=0.01,
        summary="Investigated with light fallback.",
        request_json={"preset": "fast-search"},
        response_json=agent_completed_body,
    )

    mock_client = MagicMock(spec=PerplexityAgentClient)
    mock_client.investigate_researcher.side_effect = [failed_result, success_result]

    state = {
        "run_id": "run_fallback_test",
        "current_researcher_id": researcher.id,
        "researchers": profile.researchers[:3],
        "papers": profile.papers,
        "tier_by_researcher": {researcher.id: "standard"},
        "agent_calls_used": 0,
        "steps_used_total": 0,
        "investigated_ids": [],
        "researcher_updates": {},
        "conference": "NeurIPS",
        "year": 2025,
        "db_path": config.db_path,
        "api_key": config.api_key,
    }

    result = investigate_researcher_node(state, agent_client=mock_client, agentic_config=config)

    assert mock_client.investigate_researcher.call_count == 2
    assert len(result["traces"]) == 2
    assert result["traces"][0]["status"] == "failed"
    assert result["traces"][0]["tier"] == "standard"
    assert result["traces"][1]["status"] == "completed"
    assert result["traces"][1]["tier"] == "light"
    assert "Light fallback after standard failed" in result["traces"][1]["summary"]
    assert result["signals"]
    assert result["errors"] == []
