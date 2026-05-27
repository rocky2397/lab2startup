"""Tests for dashboard agent trace helpers (no Streamlit runtime)."""

from __future__ import annotations

import json
from pathlib import Path

from app.agent_trace_store import AgentTraceRow, save_agent_trace
from app.database import init_db
from app.models import Signal, SignalType
from dashboard.agent_trace_ui import (
    find_trace_for_researcher,
    format_cost_caption,
    parse_trace_timeline,
    run_uses_agentic_signals,
    signal_source_label,
    summarize_agent_signals,
    trace_summary_rows,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "agent_responses"


def test_signal_source_label_prefixes() -> None:
    assert signal_source_label("agent_jane_1") == "agent"
    assert signal_source_label("perplexity_jane_1") == "perplexity"
    assert signal_source_label("github_repo_1") == "github"
    assert signal_source_label("mock_signal_1") == "mock"


def test_run_uses_agentic_signals_from_config() -> None:
    assert run_uses_agentic_signals({"integrations": {"agentic_signals": {"enabled": True}}})
    assert not run_uses_agentic_signals({"integrations": {"agentic_signals": {"enabled": False}}})
    assert not run_uses_agentic_signals({})


def test_parse_trace_timeline_from_fixture() -> None:
    payload = json.loads((FIXTURES_DIR / "standard_completed.json").read_text(encoding="utf-8"))
    steps = parse_trace_timeline(payload)
    assert len(steps) >= 2
    assert steps[0]["action"] == "web_search"
    assert steps[-1]["action"] == "output"


def test_trace_summary_rows_and_lookup(tmp_path: Path) -> None:
    db_path = tmp_path / "trace_ui.db"
    init_db(db_path)
    save_agent_trace(
        AgentTraceRow(
            id="trace_ui_1",
            run_id="run_ui",
            researcher_id="researcher_a",
            researcher_name="Alice",
            tier="deep",
            max_steps=8,
            status="completed",
            steps_used=6,
            tool_calls_count=3,
            input_tokens=100,
            output_tokens=40,
            estimated_cost_usd=0.12,
            signals_emitted=1,
            summary="Found possible founder evidence.",
            response_json=json.dumps(
                json.loads((FIXTURES_DIR / "standard_completed.json").read_text(encoding="utf-8"))
            ),
        ),
        db_path=db_path,
    )

    from app.agent_trace_store import list_traces_for_run

    traces = list_traces_for_run("run_ui", db_path=db_path)
    rows = trace_summary_rows(traces)
    assert rows[0]["Researcher"] == "Alice"
    assert rows[0]["Tier"] == "deep"
    assert find_trace_for_researcher(traces, "researcher_a") is not None


def test_format_cost_caption_and_signal_counts() -> None:
    caption = format_cost_caption(
        {
            "trace_count": 2,
            "total_input_tokens": 100,
            "total_output_tokens": 50,
            "estimated_cost_usd": 0.25,
        }
    )
    assert "2 investigations" in caption
    assert "$0.25" in caption

    signals = [
        Signal(
            id="agent_a_1",
            signal_type=SignalType.POSSIBLE_FOUNDER,
            description="x",
            source_url="https://example.com/a",
            evidence_strength="medium",
            date_found="2025-05-22",
        ),
        Signal(
            id="perplexity_b_1",
            signal_type=SignalType.COMMERCIALIZATION,
            description="y",
            source_url="https://example.com/b",
            evidence_strength="low",
            date_found="2025-05-22",
        ),
    ]
    assert summarize_agent_signals(signals) == {"agent": 1, "perplexity": 1}
