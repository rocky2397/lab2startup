"""LangGraph coordinator for agentic signal detection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from app.agent_trace_store import (
    AgentTraceRow,
    ResearcherHistoryRow,
    make_trace_id,
    save_agent_trace,
    upsert_researcher_history,
)
from app.agents.signal_coordinator import (
    build_investigation_plan,
    evaluate_continue,
    tier_max_steps,
)
from app.agents.signal_graph_state import (
    AgenticSignalState,
    AgentTraceRecord,
    InvestigationTier,
)
from app.config import AgenticSignalConfig
from app.integrations.agent_tools import AgentToolHandlers
from app.integrations.perplexity import apply_perplexity_researcher_updates
from app.integrations.perplexity_agent import (
    AgentInvestigationConfig,
    AgentInvestigationResult,
    PerplexityAgentClient,
    should_fallback_to_light,
    tier_investigation_config,
)
from app.models import Cluster, Paper, Researcher, Signal


def _researchers_by_id(researchers: list[Researcher]) -> dict[str, Researcher]:
    return {researcher.id: researcher for researcher in researchers}


def _int_or_default(state: AgenticSignalState, key: str, default: int) -> int:
    """Read an int from graph state; preserve 0 (e.g. unlimited caps)."""
    if key not in state:
        return default
    value = state[key]
    if value is None:
        return default
    return int(value)


def _float_or_default(state: AgenticSignalState, key: str, default: float) -> float:
    """Read a float from graph state; preserve 0.0."""
    if key not in state:
        return default
    value = state[key]
    if value is None:
        return default
    return float(value)


def initialize_node(state: AgenticSignalState) -> dict[str, Any]:
    """Seed candidate scores; preserve preloaded queue in tests."""
    if state.get("investigation_queue"):
        return {}
    papers_by_id = {paper.id: paper for paper in state.get("papers") or []}
    scores, queue, tiers = build_investigation_plan(
        state.get("researchers") or [],
        papers_by_id=papers_by_id,
        config=_config_from_state(state),
        topic_scores=state.get("topic_scores"),
        db_path=state.get("db_path"),  # type: ignore[arg-type]
    )
    return {
        "candidate_scores": scores,
        "investigation_queue": queue,
        "tier_by_researcher": tiers,
        "investigated_ids": [],
        "agent_calls_used": 0,
        "steps_used_total": 0,
        "researcher_updates": {},
        "should_continue": True,
        "stop_reason": None,
    }


def plan_investigation_node(state: AgenticSignalState) -> dict[str, Any]:
    """Assign tiers when queue was preloaded without tiers."""
    if state.get("tier_by_researcher"):
        return {}
    papers_by_id = {paper.id: paper for paper in state.get("papers") or []}
    _, queue, tiers = build_investigation_plan(
        state.get("researchers") or [],
        papers_by_id=papers_by_id,
        config=_config_from_state(state),
        topic_scores=state.get("topic_scores"),
        db_path=state.get("db_path"),  # type: ignore[arg-type]
    )
    return {
        "investigation_queue": queue or state.get("investigation_queue") or [],
        "tier_by_researcher": tiers,
    }


def pick_next_node(state: AgenticSignalState) -> dict[str, Any]:
    """Pop next researcher from queue when budget remains."""
    queue = list(state.get("investigation_queue") or [])
    agent_calls_used = int(state.get("agent_calls_used") or 0)
    max_agent_calls = int(state.get("max_agent_calls") or 0)

    if (max_agent_calls > 0 and agent_calls_used >= max_agent_calls) or not queue:
        return {"current_researcher_id": None}

    next_id = queue[0]
    return {
        "current_researcher_id": next_id,
        "investigation_queue": queue[1:],
    }


def _tier_investigation_config_from_agentic(
    tier: InvestigationTier,
    config: AgenticSignalConfig,
) -> AgentInvestigationConfig:
    return tier_investigation_config(
        tier,
        preset_standard=config.preset_standard,
        preset_deep=config.preset_deep,
        model=config.model,
        max_signals_per_researcher=config.max_signals_per_researcher,
        enrich_profiles=config.enrich_profiles,
        fund_context=config.fund_context,
    )


def _save_investigation_trace(
    *,
    result: AgentInvestigationResult,
    tier: InvestigationTier,
    investigation_config: AgentInvestigationConfig,
    researcher: Researcher,
    run_id: str,
    db_path: object,
) -> tuple[str, AgentTraceRecord]:
    trace_id = make_trace_id()
    save_agent_trace(
        AgentTraceRow(
            id=trace_id,
            run_id=run_id,
            researcher_id=researcher.id,
            researcher_name=researcher.name,
            tier=tier,
            max_steps=tier_max_steps(tier),
            steps_used=result.steps_used,
            preset=investigation_config.preset,
            model=investigation_config.model,
            status=result.status,
            tool_calls_count=result.tool_calls_count,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_usd=result.estimated_cost_usd,
            summary=result.summary,
            request_json=json.dumps(result.request_json),
            response_json=json.dumps(result.response_json) if result.response_json else None,
            signals_emitted=len(result.signals),
            error_message=result.error_message,
        ),
        db_path=db_path,
    )
    trace_record: AgentTraceRecord = {
        "trace_id": trace_id,
        "researcher_id": researcher.id,
        "tier": tier,
        "max_steps": tier_max_steps(tier),
        "status": result.status,
        "tool_calls_count": result.tool_calls_count,
        "steps_used": result.steps_used,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "summary": result.summary,
    }
    return trace_id, trace_record


def investigate_researcher_node(
    state: AgenticSignalState,
    *,
    agent_client: PerplexityAgentClient | None = None,
    tool_handlers: AgentToolHandlers | None = None,
    agentic_config: AgenticSignalConfig | None = None,
) -> dict[str, Any]:
    """Call Perplexity Agent API for the current researcher."""
    researcher_id = state.get("current_researcher_id")
    if not researcher_id:
        return {}

    researchers = _researchers_by_id(state.get("researchers") or [])
    researcher = researchers.get(researcher_id)
    if researcher is None:
        return {"errors": [f"Unknown researcher id: {researcher_id}"]}

    config = agentic_config or _config_from_state(state)
    tier: InvestigationTier = (state.get("tier_by_researcher") or {}).get(researcher_id, "standard")
    papers_by_id = {paper.id: paper for paper in state.get("papers") or []}
    run_id = state.get("run_id") or "agentic_local"

    handlers = tool_handlers or AgentToolHandlers(
        db_path=Path(config.db_path) if config.db_path else None,
        github_config=config.github_config,
        run_id=run_id,
    )
    investigation_config = _tier_investigation_config_from_agentic(tier, config)

    client = agent_client
    owns_client = False
    if client is None:
        if not config.api_key:
            return {
                "errors": ["Perplexity API key required for agentic signals."],
                "should_continue": False,
                "stop_reason": "error",
            }
        client = PerplexityAgentClient(
            api_key=config.api_key,
            request_delay_seconds=config.request_delay_seconds,
        )
        owns_client = True

    try:
        result = client.investigate_researcher(
            researcher,
            papers_by_id,
            tier=tier,
            config=investigation_config,
            tool_handlers=handlers,
            researchers_by_id=researchers,
        )
        attempts: list[tuple[InvestigationTier, AgentInvestigationConfig, AgentInvestigationResult]] = [
            (tier, investigation_config, result)
        ]

        if should_fallback_to_light(tier, result.error_message):
            fallback_config = _tier_investigation_config_from_agentic("light", config)
            fallback_result = client.investigate_researcher(
                researcher,
                papers_by_id,
                tier="light",
                config=fallback_config,
                tool_handlers=handlers,
                researchers_by_id=researchers,
            )
            fallback_result = AgentInvestigationResult(
                payload=fallback_result.payload,
                citations=fallback_result.citations,
                signals=fallback_result.signals,
                researcher=fallback_result.researcher,
                status=fallback_result.status,
                steps_used=fallback_result.steps_used,
                tool_calls_count=fallback_result.tool_calls_count,
                input_tokens=fallback_result.input_tokens,
                output_tokens=fallback_result.output_tokens,
                estimated_cost_usd=fallback_result.estimated_cost_usd,
                summary=(
                    f"Light fallback after {tier} failed ({result.error_message}): "
                    f"{fallback_result.summary}"
                ),
                request_json=fallback_result.request_json,
                response_json=fallback_result.response_json,
                error_message=fallback_result.error_message,
            )
            attempts.append(("light", fallback_config, fallback_result))
            if fallback_result.status == "completed":
                result = fallback_result
    finally:
        if owns_client and client is not None:
            client.close()

    trace_records: list[AgentTraceRecord] = []
    trace_ids: list[str] = []
    for attempt_tier, attempt_config, attempt_result in attempts:
        trace_id, trace_record = _save_investigation_trace(
            result=attempt_result,
            tier=attempt_tier,
            investigation_config=attempt_config,
            researcher=researcher,
            run_id=run_id,
            db_path=config.db_path,
        )
        trace_ids.append(trace_id)
        trace_records.append(trace_record)

    if result.status == "completed":
        best_signal = None
        if result.signals:
            best_signal = max(
                result.signals,
                key=lambda signal: signal.evidence_strength.value,
            )
        upsert_researcher_history(
            ResearcherHistoryRow(
                researcher_id=researcher.id,
                canonical_name=researcher.name,
                last_run_id=run_id,
                last_investigated_at=None,
                last_conference=state.get("conference"),
                last_year=state.get("year"),
                last_tier=attempts[-1][0],
                last_signal_count=len(result.signals),
                last_best_signal_type=best_signal.signal_type.value if best_signal else None,
                last_identity_confidence=result.researcher.identity_confidence.value,
                affiliation=result.researcher.affiliation,
                profile_url=result.researcher.openreview_url,
                notes_json=json.dumps({"last_trace_id": trace_ids[-1], "trace_ids": trace_ids}),
            ),
            db_path=config.db_path,
        )

    updates = dict(state.get("researcher_updates") or {})
    if result.researcher:
        updates[researcher.id] = result.researcher

    investigated = list(state.get("investigated_ids") or [])
    investigated.append(researcher_id)

    steps_used_delta = sum(attempt_result.steps_used for _, _, attempt_result in attempts)
    error_messages = [attempt_result.error_message for _, _, attempt_result in attempts if attempt_result.error_message]

    return {
        "signals": result.signals,
        "researcher_updates": updates,
        "traces": trace_records,
        "agent_calls_used": int(state.get("agent_calls_used") or 0) + 1,
        "steps_used_total": int(state.get("steps_used_total") or 0) + steps_used_delta,
        "investigated_ids": investigated,
        "errors": error_messages if result.status == "failed" else [],
    }


def evaluate_continue_node(state: AgenticSignalState) -> dict[str, Any]:
    return evaluate_continue(state)


def finalize_node(state: AgenticSignalState) -> dict[str, Any]:
    """Apply profile patches to researchers."""
    researchers = state.get("researchers") or []
    updates = state.get("researcher_updates") or {}
    if updates:
        researchers = apply_perplexity_researcher_updates(researchers, updates)
    return {"researchers": researchers, "should_continue": False}


def route_after_pick(state: AgenticSignalState) -> str:
    max_agent_calls = int(state.get("max_agent_calls") or 0)
    agent_calls_used = int(state.get("agent_calls_used") or 0)
    if max_agent_calls > 0 and agent_calls_used >= max_agent_calls:
        return "finalize"
    if not state.get("current_researcher_id"):
        return "finalize"
    return "investigate_researcher"


def route_after_evaluate(state: AgenticSignalState) -> str:
    return "pick_next" if state.get("should_continue") else "finalize"


def _config_from_state(state: AgenticSignalState) -> AgenticSignalConfig:
    from app.config import AgenticSignalConfig

    return AgenticSignalConfig(
        enabled=True,
        api_key=state.get("api_key"),  # type: ignore[arg-type]
        max_agent_calls=_int_or_default(state, "max_agent_calls", 10),
        max_total_steps=_int_or_default(state, "max_total_steps", 40),
        early_exit=bool(state.get("early_exit_enabled", True)),
        deep_slots=_int_or_default(state, "deep_slots", 3),
        standard_slots=_int_or_default(state, "standard_slots", 7),
        prefilter_min_score=_float_or_default(state, "prefilter_min_score", 20.0),
        queue_reserve=5,
        db_path=state.get("db_path"),  # type: ignore[arg-type]
    )


def build_agentic_signal_graph(
    *,
    agent_client: PerplexityAgentClient | None = None,
    tool_handlers: AgentToolHandlers | None = None,
    agentic_config: AgenticSignalConfig | None = None,
):
    """Compile the LangGraph state machine."""
    graph = StateGraph(AgenticSignalState)

    def _investigate(state: AgenticSignalState) -> dict[str, Any]:
        return investigate_researcher_node(
            state,
            agent_client=agent_client,
            tool_handlers=tool_handlers,
            agentic_config=agentic_config,
        )

    graph.add_node("initialize", initialize_node)
    graph.add_node("plan_investigation", plan_investigation_node)
    graph.add_node("pick_next", pick_next_node)
    graph.add_node("investigate_researcher", _investigate)
    graph.add_node("evaluate_continue", evaluate_continue_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("initialize")
    graph.add_edge("initialize", "plan_investigation")
    graph.add_edge("plan_investigation", "pick_next")
    graph.add_conditional_edges(
        "pick_next",
        route_after_pick,
        {
            "investigate_researcher": "investigate_researcher",
            "finalize": "finalize",
        },
    )
    graph.add_edge("investigate_researcher", "evaluate_continue")
    graph.add_conditional_edges(
        "evaluate_continue",
        route_after_evaluate,
        {
            "pick_next": "pick_next",
            "finalize": "finalize",
        },
    )
    graph.add_edge("finalize", END)
    return graph.compile()


def run_agentic_signal_graph(
    *,
    run_id: str,
    papers: list[Paper],
    researchers: list[Researcher],
    clusters: list[Cluster],
    config: AgenticSignalConfig,
    conference: str = "Unknown",
    year: int = 2024,
    topic_scores: dict[str, int] | None = None,
    agent_client: PerplexityAgentClient | None = None,
    tool_handlers: AgentToolHandlers | None = None,
) -> tuple[list[Researcher], list[Signal], list[AgentTraceRecord]]:
    """Execute the agentic signal graph end-to-end."""
    if config.enabled and not config.api_key:
        raise ValueError("LAB2STARTUP_PERPLEXITY_API_KEY is required when agentic signals are enabled.")

    initial: AgenticSignalState = {
        "run_id": run_id,
        "conference": conference,
        "year": year,
        "fund_context": config.fund_context,
        "papers": papers,
        "researchers": researchers,
        "clusters": clusters,
        "signals": [],
        "traces": [],
        "errors": [],
        "max_agent_calls": config.max_agent_calls,
        "max_total_steps": config.max_total_steps,
        "early_exit_enabled": config.early_exit,
        "deep_slots": config.deep_slots,
        "standard_slots": config.standard_slots,
        "prefilter_min_score": config.prefilter_min_score,
        "topic_scores": topic_scores or {},
        "db_path": config.db_path,
        "api_key": config.api_key,
    }

    compiled = build_agentic_signal_graph(
        agent_client=agent_client,
        tool_handlers=tool_handlers,
        agentic_config=config,
    )
    final_state = compiled.invoke(initial)

    merged_researchers = final_state.get("researchers") or researchers
    signals = list(final_state.get("signals") or [])
    traces = list(final_state.get("traces") or [])
    return merged_researchers, signals, traces
