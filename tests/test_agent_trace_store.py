"""Tests for agent trace SQLite persistence."""

from __future__ import annotations

from pathlib import Path

from app.agent_trace_store import (
    AgentTraceRow,
    ResearcherHistoryRow,
    list_traces_for_run,
    lookup_researcher_history,
    save_agent_trace,
    summarize_run_traces,
    upsert_researcher_history,
)
from app.database import init_db


def test_agent_trace_store_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "traces.db"
    init_db(db_path)

    save_agent_trace(
        AgentTraceRow(
            id="trace_test_1",
            run_id="run_test",
            researcher_id="researcher_john_yang",
            researcher_name="John Yang",
            tier="standard",
            max_steps=3,
            status="completed",
            steps_used=3,
            tool_calls_count=2,
            input_tokens=100,
            output_tokens=50,
            signals_emitted=1,
            summary="Test investigation",
        ),
        db_path=db_path,
    )

    traces = list_traces_for_run("run_test", db_path=db_path)
    assert len(traces) == 1
    assert traces[0]["researcher_name"] == "John Yang"
    assert traces[0]["status"] == "completed"

    upsert_researcher_history(
        ResearcherHistoryRow(
            researcher_id="researcher_john_yang",
            canonical_name="John Yang",
            last_run_id="run_test",
            last_signal_count=1,
            last_tier="standard",
        ),
        db_path=db_path,
    )
    history = lookup_researcher_history(
        researcher_id="researcher_john_yang",
        db_path=db_path,
    )
    assert history is not None
    assert history["last_signal_count"] == 1

    summary = summarize_run_traces("run_test", db_path=db_path)
    assert summary["trace_count"] == 1
    assert summary["total_input_tokens"] == 100
