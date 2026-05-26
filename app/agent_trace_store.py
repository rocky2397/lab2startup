"""SQLite persistence for agent investigation traces and researcher history."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database import get_connection, init_db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentTraceRow:
    """Row payload for agent_traces table."""

    id: str
    run_id: str
    researcher_id: str
    researcher_name: str
    tier: str
    max_steps: int
    status: str
    steps_used: int | None = None
    preset: str | None = None
    model: str | None = None
    tool_calls_count: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    summary: str | None = None
    request_json: str | None = None
    response_json: str | None = None
    signals_emitted: int = 0
    error_message: str | None = None
    created_at: str | None = None


@dataclass
class ResearcherHistoryRow:
    """Row payload for researcher_history table."""

    researcher_id: str
    canonical_name: str
    last_run_id: str | None = None
    last_investigated_at: str | None = None
    last_conference: str | None = None
    last_year: int | None = None
    last_tier: str | None = None
    last_signal_count: int = 0
    last_best_signal_type: str | None = None
    last_identity_confidence: str | None = None
    affiliation: str | None = None
    profile_url: str | None = None
    notes_json: str | None = None
    updated_at: str | None = None


def make_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex[:12]}"


def save_agent_trace(trace: AgentTraceRow, *, db_path: Path | str | None = None) -> str:
    """Insert one agent trace row."""
    init_db(db_path)
    created_at = trace.created_at or _utc_now_iso()
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO agent_traces (
                id, run_id, researcher_id, researcher_name, tier, max_steps,
                steps_used, preset, model, status, tool_calls_count,
                input_tokens, output_tokens, estimated_cost_usd, summary,
                request_json, response_json, signals_emitted, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.id,
                trace.run_id,
                trace.researcher_id,
                trace.researcher_name,
                trace.tier,
                trace.max_steps,
                trace.steps_used,
                trace.preset,
                trace.model,
                trace.status,
                trace.tool_calls_count,
                trace.input_tokens,
                trace.output_tokens,
                trace.estimated_cost_usd,
                trace.summary,
                trace.request_json,
                trace.response_json,
                trace.signals_emitted,
                trace.error_message,
                created_at,
            ),
        )
        connection.commit()
    return trace.id


def list_traces_for_run(run_id: str, *, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Return all traces for a pipeline run."""
    init_db(db_path)
    with get_connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, run_id, researcher_id, researcher_name, tier, max_steps,
                   steps_used, preset, model, status, tool_calls_count,
                   input_tokens, output_tokens, estimated_cost_usd, summary,
                   signals_emitted, error_message, created_at
            FROM agent_traces
            WHERE run_id = ?
            ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_trace(trace_id: str, *, db_path: Path | str | None = None) -> dict[str, Any] | None:
    """Load a single trace including raw JSON payloads."""
    init_db(db_path)
    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM agent_traces WHERE id = ?",
            (trace_id,),
        ).fetchone()
    return dict(row) if row else None


def upsert_researcher_history(
    history: ResearcherHistoryRow,
    *,
    db_path: Path | str | None = None,
) -> None:
    """Insert or update cross-run researcher memory."""
    init_db(db_path)
    updated_at = history.updated_at or _utc_now_iso()
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO researcher_history (
                researcher_id, canonical_name, last_run_id, last_investigated_at,
                last_conference, last_year, last_tier, last_signal_count,
                last_best_signal_type, last_identity_confidence, affiliation,
                profile_url, notes_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(researcher_id) DO UPDATE SET
                canonical_name = excluded.canonical_name,
                last_run_id = excluded.last_run_id,
                last_investigated_at = excluded.last_investigated_at,
                last_conference = excluded.last_conference,
                last_year = excluded.last_year,
                last_tier = excluded.last_tier,
                last_signal_count = excluded.last_signal_count,
                last_best_signal_type = excluded.last_best_signal_type,
                last_identity_confidence = excluded.last_identity_confidence,
                affiliation = excluded.affiliation,
                profile_url = excluded.profile_url,
                notes_json = excluded.notes_json,
                updated_at = excluded.updated_at
            """,
            (
                history.researcher_id,
                history.canonical_name,
                history.last_run_id,
                history.last_investigated_at,
                history.last_conference,
                history.last_year,
                history.last_tier,
                history.last_signal_count,
                history.last_best_signal_type,
                history.last_identity_confidence,
                history.affiliation,
                history.profile_url,
                history.notes_json,
                updated_at,
            ),
        )
        connection.commit()


def lookup_researcher_history(
    *,
    researcher_id: str | None = None,
    researcher_name: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Fetch prior investigation summary for coordinator or custom tools."""
    init_db(db_path)
    with get_connection(db_path) as connection:
        if researcher_id:
            row = connection.execute(
                "SELECT * FROM researcher_history WHERE researcher_id = ?",
                (researcher_id,),
            ).fetchone()
            if row:
                return dict(row)
        if researcher_name:
            row = connection.execute(
                """
                SELECT * FROM researcher_history
                WHERE canonical_name = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (researcher_name,),
            ).fetchone()
            if row:
                return dict(row)
    return None


def summarize_run_traces(run_id: str, *, db_path: Path | str | None = None) -> dict[str, Any]:
    """Aggregate token and cost stats for a run."""
    traces = list_traces_for_run(run_id, db_path=db_path)
    total_input = sum(row.get("input_tokens") or 0 for row in traces)
    total_output = sum(row.get("output_tokens") or 0 for row in traces)
    total_cost = sum(row.get("estimated_cost_usd") or 0.0 for row in traces)
    completed = sum(1 for row in traces if row.get("status") == "completed")
    failed = sum(1 for row in traces if row.get("status") == "failed")
    return {
        "trace_count": len(traces),
        "completed_count": completed,
        "failed_count": failed,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "estimated_cost_usd": round(total_cost, 4),
    }
