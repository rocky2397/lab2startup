#!/usr/bin/env python3
"""Smoke-test agentic Perplexity investigations on a small researcher subset."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bootstrap_venv import reexec_with_project_venv

reexec_with_project_venv(ROOT)

from app.agents.signal_coordinator import build_investigation_plan
from app.agents.signal_graph import run_agentic_signal_graph
from app.config import AgenticSignalConfig, clear_settings_cache, get_settings
from app.integrations.agent_tools import AgentToolHandlers
from app.integrations.perplexity_agent import PerplexityAgentClient
from app.models import Researcher
from app.run_store import get_run, list_runs, load_run_result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe agentic Perplexity investigations for a few researchers from a stored run "
            "without re-running the full pipeline."
        ),
    )
    parser.add_argument(
        "--run-id",
        help="Pipeline run id (default: latest complete run with snapshot)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of researchers to investigate via the LangGraph orchestrator (default: 5)",
    )
    parser.add_argument(
        "--names",
        help="Comma-separated researcher names to probe (overrides queue order; still uses graph)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print selected researchers without calling the Agent API",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full signal JSON after the orchestration summary",
    )
    return parser


def _resolve_run_id(run_id: str | None, db_path: Path) -> str:
    if run_id:
        return run_id
    for run in list_runs(db_path=db_path, limit=20):
        if run.status != "complete":
            continue
        if load_run_result(run.id, db_path=db_path) is not None:
            return run.id
    raise SystemExit("No complete stored pipeline runs with snapshots found. Run the pipeline first.")


def _select_researcher_ids(
    researchers: list[Researcher],
    *,
    limit: int,
    names: str | None,
    queue: list[str],
) -> list[str]:
    if names:
        wanted = {name.strip().lower() for name in names.split(",") if name.strip()}
        selected = [researcher.id for researcher in researchers if researcher.name.lower() in wanted]
        if not selected:
            raise SystemExit(f"No researchers matched --names: {names}")
        return selected

    return queue[: max(limit, 1)]


def _looks_truncated(trace: dict) -> bool:
    """Detect the old bug: only lookup_prior_run, no follow-up investigation."""
    return (
        trace.get("status") == "completed"
        and int(trace.get("tool_calls_count") or 0) <= 1
        and not str(trace.get("summary") or "").strip()
    )


def _print_selection(
    *,
    run_id: str,
    researchers: list[Researcher],
    selected_ids: list[str],
    tiers: dict[str, str],
) -> None:
    by_id = {researcher.id: researcher for researcher in researchers}
    print(f"Run: {run_id}")
    print(f"Selected {len(selected_ids)} researcher(s) for graph orchestration:")
    for researcher_id in selected_ids:
        researcher = by_id[researcher_id]
        tier = tiers.get(researcher_id, "standard")
        print(f"  - {researcher.name} ({researcher.affiliation}) tier={tier}")


def main() -> int:
    args = build_arg_parser().parse_args()
    clear_settings_cache()
    settings = get_settings()
    base_config = settings.agentic_signal_config

    if not base_config.enabled:
        print("Warning: LAB2STARTUP_AGENTIC_SIGNALS is false; probing anyway.", file=sys.stderr)
    if not base_config.api_key:
        print("Set LAB2STARTUP_PERPLEXITY_API_KEY before running agentic smoke tests.", file=sys.stderr)
        return 1

    run_id = _resolve_run_id(args.run_id, settings.db_path)
    run = get_run(run_id, db_path=settings.db_path)
    result = load_run_result(run_id, db_path=settings.db_path)
    if result is None:
        print(f"No snapshot found for run: {run_id}", file=sys.stderr)
        return 1

    papers = result.scoring.detection.papers
    researchers = result.scoring.detection.researchers
    clusters = result.scoring.detection.clusters
    papers_by_id = {paper.id: paper for paper in papers}
    _, queue, tiers = build_investigation_plan(
        researchers,
        papers_by_id=papers_by_id,
        config=base_config,
        topic_scores=settings.topic_scores,
        db_path=base_config.db_path,
    )
    selected_ids = _select_researcher_ids(
        researchers,
        limit=max(args.limit, 1),
        names=args.names,
        queue=queue,
    )
    _print_selection(
        run_id=run_id,
        researchers=researchers,
        selected_ids=selected_ids,
        tiers=tiers,
    )

    if args.dry_run:
        return 0

    smoke_run_id = f"{run_id}_smoke"
    smoke_config = replace(
        base_config,
        enabled=True,
        max_agent_calls=len(selected_ids),
        max_total_steps=0,
        early_exit=False,
        db_path=settings.db_path,
    )
    if args.names:
        selected_set = set(selected_ids)
        smoke_researchers = [researcher for researcher in researchers if researcher.id in selected_set]
    else:
        smoke_researchers = researchers

    handlers = AgentToolHandlers(
        db_path=settings.db_path,
        github_config=settings.github_config,
        run_id=run_id,
    )

    conference = run.conference if run else "Unknown"
    year = run.year if run else 2024

    failures = 0
    truncated = 0
    with PerplexityAgentClient(
        api_key=smoke_config.api_key,
        request_delay_seconds=smoke_config.request_delay_seconds,
    ) as client:
        _, signals, traces = run_agentic_signal_graph(
            run_id=smoke_run_id,
            papers=papers,
            researchers=smoke_researchers,
            clusters=clusters,
            config=smoke_config,
            conference=conference,
            year=year,
            topic_scores=settings.topic_scores,
            agent_client=client,
            tool_handlers=handlers,
        )

    print("\nOrchestration summary:")
    summary = {
        "mode": "langgraph",
        "requested": len(selected_ids),
        "traces": len(traces),
        "signals": len(signals),
        "shared_client": True,
    }
    print(json.dumps(summary, indent=2))

    print("\nPer-researcher traces:")
    trace_by_id = {
        (trace["researcher_id"] if isinstance(trace, dict) else trace.researcher_id): trace
        for trace in traces
    }
    by_id = {researcher.id: researcher for researcher in researchers}
    for researcher_id in selected_ids:
        trace = trace_by_id.get(researcher_id)
        researcher = by_id[researcher_id]
        if trace is None:
            failures += 1
            print(
                json.dumps(
                    {
                        "name": researcher.name,
                        "status": "missing",
                        "error_message": "Graph did not produce a trace for this researcher",
                    },
                    indent=2,
                )
            )
            continue

        payload = trace if isinstance(trace, dict) else trace.__dict__
        line = {
            "name": researcher.name,
            "tier": payload.get("tier"),
            "status": payload.get("status"),
            "steps_used": payload.get("steps_used"),
            "tool_calls_count": payload.get("tool_calls_count"),
            "summary": payload.get("summary"),
        }
        print(json.dumps(line, indent=2))
        if payload.get("status") != "completed":
            failures += 1
        if _looks_truncated(payload):
            truncated += 1

    if len(traces) != len(selected_ids):
        failures += 1
        print(
            f"\nOrchestration error: expected {len(selected_ids)} traces, got {len(traces)}.",
            file=sys.stderr,
        )
    if truncated:
        failures += 1
        print(
            f"\nTruncated investigations detected: {truncated}/{len(selected_ids)} "
            "(likely stuck after lookup_prior_run).",
            file=sys.stderr,
        )

    if args.verbose and signals:
        print("\nSignals:")
        print(json.dumps([signal.model_dump(mode="json") for signal in signals], indent=2))

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
