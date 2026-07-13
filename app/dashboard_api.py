"""API endpoints backing the desktop dashboard app (Streamlit replacement)."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.models import Paper, Researcher
from app.region_hints import infer_region_hint
from app.report_generator import RECOMMENDATION_LABELS, render_report_markdown
from app.researcher_links import resolve_researcher_links
from app.run_store import list_runs, run_has_results
from app.service import clear_cache, get_report_result, set_active_run_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"])


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Pure helpers shared with the old Streamlit dashboard (no streamlit imports)
# ---------------------------------------------------------------------------


def signal_source_label(signal_id: str) -> str:
    """Map signal ID prefix to a human-readable source label."""
    for prefix, label in (
        ("agent_", "agent"),
        ("perplexity_", "perplexity"),
        ("github_", "github"),
        ("mock_", "mock"),
    ):
        if signal_id.startswith(prefix):
            return label
    return "other"


def run_uses_agentic_signals(config_json: dict[str, Any] | None) -> bool:
    """Return True when the stored run used LangGraph + Agent API."""
    if not config_json:
        return False
    integrations = config_json.get("integrations") or {}
    agentic = integrations.get("agentic_signals") or {}
    return bool(agentic.get("enabled"))


def _researcher_paper_context(
    researcher: Researcher,
    papers_by_id: dict[str, Paper],
) -> dict[str, Any]:
    researcher_papers = [papers_by_id[paper_id] for paper_id in researcher.papers if paper_id in papers_by_id]
    return {
        "conferences": sorted({paper.conference for paper in researcher_papers}),
        "years": sorted({paper.year for paper in researcher_papers}, reverse=True),
        "topics": sorted({paper.topic for paper in researcher_papers}),
        "paper_count": len(researcher_papers),
    }


def parse_trace_timeline(response_json: str | dict[str, Any] | None) -> list[dict[str, str]]:
    """Build a step timeline from a stored Agent API response payload."""
    if response_json is None:
        return []
    if isinstance(response_json, dict):
        payload: dict[str, Any] | None = response_json
    else:
        try:
            loaded = json.loads(response_json)
        except (json.JSONDecodeError, TypeError):
            return []
        payload = loaded if isinstance(loaded, dict) else None
    if not payload:
        return []

    steps: list[dict[str, str]] = []
    for item in payload.get("output") or []:
        item_type = str(item.get("type") or "")
        if item_type == "search_results":
            results = item.get("results") or []
            query = str(results[0].get("title") or results[0].get("url") or "") if results else ""
            detail = query or f"{len(results)} result(s)"
            action = "web_search"
        elif item_type == "fetch_url_results":
            urls = [str(result.get("url") or "") for result in (item.get("contents") or []) if result.get("url")]
            detail = ", ".join(urls[:3]) if urls else "fetched page(s)"
            action = "fetch_url"
        elif item_type == "function_call":
            action = str(item.get("name") or "function")
            raw_args = item.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            arg_bits = [f"{key}={value}" for key, value in list(args.items())[:2]]
            detail = ", ".join(arg_bits) if arg_bits else action
        elif item_type == "message":
            action = "output"
            detail = "Structured JSON response"
        else:
            continue
        steps.append({"step": str(len(steps) + 1), "action": action, "detail": detail})
    return steps


# ---------------------------------------------------------------------------
# Bootstrap: settings, fund scope, stored runs
# ---------------------------------------------------------------------------


def _active_integrations(settings) -> list[str]:
    active: list[str] = []
    if settings.agentic_signal_config.enabled:
        active.append("Agentic signals (LangGraph + Agent API)")
    elif settings.perplexity_config.enabled:
        active.append("Perplexity Sonar (one-shot)")
    if settings.openreview_config is not None and settings.openreview_config.enabled:
        active.append("OpenReview")
    if settings.semantic_scholar_config.enabled:
        active.append("Semantic Scholar")
    if settings.github_config.enabled:
        active.append("GitHub (supplement)")
    if settings.use_mock_signals:
        active.append("Mock signals (dev)")
    if settings.paper_source != "json":
        active.append(f"Papers: {settings.paper_source}")
    elif not settings.is_production:
        active.append("Papers: mock JSON")
    return active or ["No signal sources enabled"]


def _fund_payload(fund) -> dict[str, Any] | None:
    if fund is None:
        return None
    return {
        "id": fund.id,
        "name": fund.name,
        "description": fund.description,
        "thesis_fit": fund.thesis_fit is not None,
        "topic_scores": dict(fund.topic_scores),
        "default_paper_source": fund.default_paper_source,
        "high_priority_conferences": fund.high_priority_conferences,
        "conferences": [
            {
                "name": conference.name,
                "label": fund.conference_label(conference.name),
                "priority": conference.priority,
                "sources": list(conference.sources),
            }
            for conference in fund.conferences
        ],
    }


def _run_payload(run) -> dict[str, Any]:
    payload = run.model_dump(mode="json")
    payload["has_results"] = run_has_results(run)
    return payload


@router.get("/bootstrap")
def bootstrap() -> dict[str, Any]:
    """Everything the app shell needs on first paint."""
    settings = get_settings()
    warnings: list[str] = []
    if settings.perplexity_config.enabled and not settings.perplexity_config.api_key:
        warnings.append("Perplexity is enabled but LAB2STARTUP_PERPLEXITY_API_KEY is missing.")
    if settings.agentic_signal_config.enabled and not settings.perplexity_config.api_key:
        warnings.append("Agentic signals require LAB2STARTUP_PERPLEXITY_API_KEY.")

    return {
        "mode": settings.mode,
        "is_production": settings.is_production,
        "fund": _fund_payload(settings.fund_profile),
        "active_integrations": _active_integrations(settings),
        "warnings": warnings,
        "recommendation_labels": {action.value: label for action, label in RECOMMENDATION_LABELS.items()},
        "runs": [_run_payload(run) for run in _list_runs_safe(settings)],
    }


def _list_runs_safe(settings) -> list:
    try:
        return list_runs(db_path=settings.db_path, limit=30)
    except Exception as exc:  # pragma: no cover - transient sqlite issues
        logger.warning("Could not list stored runs: %s", exc)
        return []


@router.get("/runs")
def get_runs() -> list[dict[str, Any]]:
    """Stored pipeline runs, newest first."""
    settings = get_settings()
    return [_run_payload(run) for run in _list_runs_safe(settings)]


# ---------------------------------------------------------------------------
# Run bundle: full dataset for one stored run (or the live/latest dataset)
# ---------------------------------------------------------------------------


@router.get("/bundle")
def get_bundle(run_id: str | None = None, force_refresh: bool = False) -> dict[str, Any]:
    """Complete dataset for one run — the SPA filters and sorts client-side."""
    settings = get_settings()
    set_active_run_id(run_id)
    result = get_report_result(force_refresh=force_refresh, run_id=run_id)
    detection = result.scoring.detection
    papers = detection.papers
    papers_by_id = {paper.id: paper for paper in papers}
    signals_by_researcher: dict[str, list] = {}
    for signal in detection.signals:
        if signal.researcher_id:
            signals_by_researcher.setdefault(signal.researcher_id, []).append(signal)

    run = None
    if run_id:
        from app.run_store import get_run as load_run

        run = load_run(run_id, db_path=settings.db_path)

    links: dict[str, Any] = {}
    regions: dict[str, str | None] = {}
    contexts: dict[str, Any] = {}
    for researcher in detection.researchers:
        researcher_links = resolve_researcher_links(researcher, signals_by_researcher.get(researcher.id))
        links[researcher.id] = asdict(researcher_links)
        regions[researcher.id] = infer_region_hint(researcher.affiliation)
        contexts[researcher.id] = _researcher_paper_context(researcher, papers_by_id)

    thesis_fit = None
    diff = None
    if run is not None:
        from app.run_diff_store import load_run_diff
        from app.thesis_fit_store import load_thesis_fit

        assessments = load_thesis_fit(run.id, db_path=settings.db_path)
        if assessments:
            thesis_fit = {rid: assessment.model_dump(mode="json") for rid, assessment in assessments.items()}
        run_diff = load_run_diff(run.id, db_path=settings.db_path)
        if run_diff is not None:
            diff = run_diff.model_dump(mode="json")

    return {
        "run": _run_payload(run) if run else None,
        "agentic_enabled": run_uses_agentic_signals(run.config_json) if run else False,
        "papers": [paper.model_dump(mode="json") for paper in papers],
        "researchers": [researcher.model_dump(mode="json") for researcher in detection.researchers],
        "clusters": [cluster.model_dump(mode="json") for cluster in detection.clusters],
        "signals": [signal.model_dump(mode="json") for signal in detection.signals],
        "reports": [
            {**report.model_dump(mode="json"), "markdown": render_report_markdown(report)} for report in result.reports
        ],
        "links": links,
        "regions": regions,
        "contexts": contexts,
        "thesis_fit": thesis_fit,
        "diff": diff,
        "options": {
            "conferences": sorted({paper.conference for paper in papers}),
            "years": sorted({paper.year for paper in papers}, reverse=True),
            "topics": sorted({paper.topic for paper in papers}),
        },
    }


@router.post("/refresh")
def refresh() -> dict[str, str]:
    """Clear in-memory and disk pipeline caches (dev-mode refresh)."""
    clear_cache()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Dev tools: enrichment audit and agent traces
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/enrichment-audit")
def get_enrichment_audit(run_id: str) -> dict[str, Any]:
    from app.enrichment_audit import serialize_enrichment_audit, summarize_enrichment_audit
    from app.run_store import load_enrichment_audit

    settings = get_settings()
    audit = load_enrichment_audit(run_id, db_path=settings.db_path)
    if audit is None:
        return {"available": False}
    return {
        "available": True,
        "audit": json.loads(serialize_enrichment_audit(audit)),
        "summary": summarize_enrichment_audit(audit),
    }


@router.get("/runs/{run_id}/traces")
def get_run_traces(run_id: str) -> dict[str, Any]:
    from app.agent_trace_store import list_traces_for_run, summarize_run_traces

    settings = get_settings()
    traces = list_traces_for_run(run_id, db_path=settings.db_path)
    summary = summarize_run_traces(run_id, db_path=settings.db_path)
    return {"traces": traces, "summary": summary}


@router.get("/traces/{trace_id}")
def get_trace_detail(trace_id: str) -> dict[str, Any]:
    from app.agent_trace_store import get_trace

    settings = get_settings()
    trace = get_trace(trace_id, db_path=settings.db_path)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")
    trace["timeline"] = parse_trace_timeline(trace.get("response_json"))
    return trace


# ---------------------------------------------------------------------------
# Pipeline runs as background jobs
# ---------------------------------------------------------------------------


class PipelineRunRequest(BaseModel):
    """Launch one pipeline run per requested conference."""

    conferences: list[str] = Field(min_length=1)
    year: int = Field(ge=2000, le=2100)
    paper_source: str | None = None


_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _job_worker(job_id: str, request: PipelineRunRequest) -> None:
    from app.run_service import execute_pipeline_run

    settings = get_settings()
    fund = settings.fund_profile

    for item in _jobs[job_id]["items"]:
        conference = item["conference"]
        with _jobs_lock:
            item["status"] = "running"
        entry = fund.conference(conference) if fund else None
        source = request.paper_source if entry and request.paper_source in entry.sources else None
        try:
            run, result = execute_pipeline_run(
                conference=conference,
                year=request.year,
                paper_source=source,
                fund_profile=fund.id if fund else settings.fund_id,
                settings=settings,
            )
            paper_count = run.paper_count or len(result.scoring.detection.papers)
            with _jobs_lock:
                item["status"] = "complete"
                item["run_id"] = run.id
                item["paper_count"] = paper_count
        except Exception as exc:
            logger.exception("Pipeline job %s failed for %s", job_id, conference)
            with _jobs_lock:
                item["status"] = "failed"
                item["error"] = str(exc)

    clear_cache()
    with _jobs_lock:
        job = _jobs[job_id]
        job["finished_at"] = _utc_now_iso()
        statuses = {item["status"] for item in job["items"]}
        job["status"] = "failed" if statuses == {"failed"} else "complete"


@router.post("/pipeline/run")
def start_pipeline_run(request: PipelineRunRequest) -> dict[str, Any]:
    """Start pipeline runs in a background thread; poll the job for progress."""
    with _jobs_lock:
        active = [job for job in _jobs.values() if job["status"] == "running"]
        if active:
            raise HTTPException(status_code=409, detail="A pipeline job is already running.")
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {
            "id": job_id,
            "status": "running",
            "year": request.year,
            "started_at": _utc_now_iso(),
            "finished_at": None,
            "items": [
                {"conference": conference, "status": "pending", "run_id": None, "paper_count": None, "error": None}
                for conference in request.conferences
            ],
        }

    thread = threading.Thread(target=_job_worker, args=(job_id, request), daemon=True)
    thread.start()
    return _jobs[job_id]


@router.get("/pipeline/jobs/{job_id}")
def get_pipeline_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    with _jobs_lock:
        return json.loads(json.dumps(job))


@router.get("/pipeline/jobs")
def list_pipeline_jobs() -> list[dict[str, Any]]:
    with _jobs_lock:
        return [json.loads(json.dumps(job)) for job in _jobs.values()]
