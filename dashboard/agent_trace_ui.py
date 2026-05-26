"""Dashboard UI for agent investigation traces (Week 2)."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from app.agent_trace_store import get_trace, list_traces_for_run, summarize_run_traces
from app.models import Signal


def signal_source_label(signal_id: str) -> str:
    """Map signal ID prefix to a human-readable source label."""
    if signal_id.startswith("agent_"):
        return "agent"
    if signal_id.startswith("perplexity_"):
        return "perplexity"
    if signal_id.startswith("github_"):
        return "github"
    if signal_id.startswith("mock_"):
        return "mock"
    return "other"


def run_uses_agentic_signals(config_json: dict[str, Any] | None) -> bool:
    """Return True when the stored run used LangGraph + Agent API."""
    if not config_json:
        return False
    integrations = config_json.get("integrations") or {}
    agentic = integrations.get("agentic_signals") or {}
    return bool(agentic.get("enabled"))


def _load_response_json(raw: str | dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def parse_trace_timeline(response_json: str | dict[str, Any] | None) -> list[dict[str, str]]:
    """Build a step timeline from a stored Agent API response payload."""
    payload = _load_response_json(response_json)
    if not payload:
        return []

    steps: list[dict[str, str]] = []
    for item in payload.get("output") or []:
        item_type = str(item.get("type") or "")
        if item_type == "search_results":
            query = ""
            results = item.get("results") or []
            if results:
                query = str(results[0].get("title") or results[0].get("url") or "")
            steps.append(
                {
                    "step": str(len(steps) + 1),
                    "action": "web_search",
                    "detail": query or f"{len(results)} result(s)",
                }
            )
        elif item_type == "fetch_url_results":
            urls = [
                str(result.get("url") or "")
                for result in (item.get("contents") or [])
                if result.get("url")
            ]
            steps.append(
                {
                    "step": str(len(steps) + 1),
                    "action": "fetch_url",
                    "detail": ", ".join(urls[:3]) if urls else "fetched page(s)",
                }
            )
        elif item_type == "function_call":
            name = str(item.get("name") or "function")
            raw_args = item.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            arg_bits = [f"{key}={value}" for key, value in list(args.items())[:2]]
            steps.append(
                {
                    "step": str(len(steps) + 1),
                    "action": name,
                    "detail": ", ".join(arg_bits) if arg_bits else name,
                }
            )
        elif item_type == "message":
            steps.append(
                {
                    "step": str(len(steps) + 1),
                    "action": "output",
                    "detail": "Structured JSON response",
                }
            )
    return steps


def trace_summary_rows(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert trace rows into dashboard table rows."""
    rows: list[dict[str, Any]] = []
    for trace in traces:
        rows.append(
            {
                "Researcher": trace.get("researcher_name") or trace.get("researcher_id"),
                "Tier": trace.get("tier"),
                "Steps": f"{trace.get('steps_used') or '—'}/{trace.get('max_steps') or '—'}",
                "Tools": trace.get("tool_calls_count") or 0,
                "Status": trace.get("status"),
                "Signals": trace.get("signals_emitted") or 0,
                "Cost (est.)": trace.get("estimated_cost_usd"),
            }
        )
    return rows


def find_trace_for_researcher(
    traces: list[dict[str, Any]],
    researcher_id: str,
) -> dict[str, Any] | None:
    """Return the trace row for a researcher in a run, if any."""
    for trace in traces:
        if trace.get("researcher_id") == researcher_id:
            return trace
    return None


def format_cost_caption(summary: dict[str, Any]) -> str:
    """One-line cost/token summary for sidebar metrics."""
    tokens = int(summary.get("total_input_tokens") or 0) + int(
        summary.get("total_output_tokens") or 0
    )
    cost = summary.get("estimated_cost_usd")
    cost_text = f"~${cost:.2f}" if cost is not None else "—"
    return (
        f"{summary.get('trace_count', 0)} investigations · "
        f"{tokens:,} tokens · {cost_text} est."
    )


def render_run_trace_summary(
    run_id: str,
    *,
    db_path,
    agentic_enabled: bool,
) -> None:
    """Show investigation summary table above candidate selection."""
    if not agentic_enabled:
        st.caption("No agent traces (Sonar mode was used for this run).")
        return

    traces = list_traces_for_run(run_id, db_path=db_path)
    if not traces:
        st.info("Agentic mode was enabled but no investigation traces were stored for this run.")
        return

    summary = summarize_run_traces(run_id, db_path=db_path)
    st.subheader("Investigation traces")
    st.caption(format_cost_caption(summary))
    st.dataframe(
        pd.DataFrame(trace_summary_rows(traces)),
        width="stretch",
        hide_index=True,
    )


def render_researcher_trace_expander(
    researcher_id: str,
    run_id: str,
    *,
    db_path,
    agentic_enabled: bool,
) -> None:
    """Expandable per-candidate investigation trace detail."""
    if not agentic_enabled:
        return

    traces = list_traces_for_run(run_id, db_path=db_path)
    trace_row = find_trace_for_researcher(traces, researcher_id)
    if trace_row is None:
        st.caption("No agent investigation trace for this candidate in the selected run.")
        return

    trace_id = trace_row.get("id")
    full_trace = get_trace(trace_id, db_path=db_path) if trace_id else None
    tier = trace_row.get("tier") or "standard"
    steps_used = trace_row.get("steps_used")
    max_steps = trace_row.get("max_steps")
    status = trace_row.get("status") or "unknown"
    title = (
        f"Investigation trace — {trace_row.get('researcher_name')} "
        f"({tier}, {steps_used}/{max_steps} steps, {status})"
    )

    with st.expander(title, expanded=False):
        if trace_row.get("summary"):
            st.markdown(f"**Summary:** {trace_row['summary']}")
        if trace_row.get("error_message"):
            st.error(trace_row["error_message"])

        timeline = parse_trace_timeline(
            full_trace.get("response_json") if full_trace else None
        )
        if timeline:
            st.markdown("#### Step timeline")
            for step in timeline:
                st.markdown(
                    f"- **Step {step['step']}:** `{step['action']}` — {step['detail']}"
                )
        elif status == "failed":
            st.caption("No step timeline available (investigation failed before output).")
        else:
            st.caption("No step timeline in stored response.")

        metrics_col1, metrics_col2, metrics_col3 = st.columns(3)
        metrics_col1.metric("Tool calls", trace_row.get("tool_calls_count") or 0)
        metrics_col2.metric(
            "Tokens",
            (trace_row.get("input_tokens") or 0) + (trace_row.get("output_tokens") or 0),
        )
        metrics_col3.metric(
            "Est. cost",
            f"${trace_row['estimated_cost_usd']:.3f}"
            if trace_row.get("estimated_cost_usd") is not None
            else "—",
        )

        if full_trace and full_trace.get("response_json"):
            st.download_button(
                "Download full trace JSON",
                data=full_trace["response_json"],
                file_name=f"{trace_id or 'trace'}.json",
                mime="application/json",
            )


def summarize_agent_signals(signals: list[Signal]) -> dict[str, int]:
    """Count signals by source prefix for dashboard badges."""
    counts: dict[str, int] = {}
    for signal in signals:
        label = signal_source_label(signal.id)
        counts[label] = counts.get(label, 0) + 1
    return counts
