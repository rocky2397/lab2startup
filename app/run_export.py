"""Export stored pipeline runs to portable JSON bundles."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent_trace_store import list_traces_for_run, summarize_run_traces
from app.database import get_connection, init_db
from app.enrichment_audit import serialize_enrichment_audit
from app.models import RunStatus
from app.run_store import get_run, list_runs, load_enrichment_audit, load_run_result


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def export_run(
    run_id: str,
    output_dir: Path | str,
    *,
    db_path: Path | str | None = None,
    include_trace_payloads: bool = False,
) -> dict[str, Any]:
    """Export one pipeline run to a directory."""
    output_dir = Path(output_dir)
    run = get_run(run_id, db_path=db_path)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")

    result = load_run_result(run_id, db_path=db_path)
    if result is None:
        raise ValueError(f"No snapshot found for run: {run_id}")

    run_dir = output_dir / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    audit = load_enrichment_audit(run_id, db_path=db_path)
    traces = list_traces_for_run(run_id, db_path=db_path)
    cost_summary = summarize_run_traces(run_id, db_path=db_path)

    run_record = {
        "id": run.id,
        "conference": run.conference,
        "year": run.year,
        "status": run.status.value,
        "paper_source": run.paper_source,
        "fund_profile": run.fund_profile,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "paper_count": run.paper_count,
        "researcher_count": run.researcher_count,
        "signal_count": run.signal_count,
        "report_count": run.report_count,
        "config_json": run.config_json,
    }

    _write_json(run_dir / "run_metadata.json", run_record)
    _write_json(run_dir / "snapshot.json", _report_result_to_dict(result))
    _write_json(run_dir / "agent_traces.json", traces)
    _write_json(run_dir / "cost_summary.json", cost_summary)
    if audit is not None:
        (run_dir / "enrichment_audit.json").write_text(serialize_enrichment_audit(audit), encoding="utf-8")

    if include_trace_payloads:
        trace_payloads_dir = run_dir / "trace_payloads"
        trace_payloads_dir.mkdir(exist_ok=True)
        init_db(db_path)
        with get_connection(db_path) as connection:
            rows = connection.execute(
                "SELECT id, request_json, response_json FROM agent_traces WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        for row in rows:
            trace_id = row["id"]
            if row["request_json"]:
                (trace_payloads_dir / f"{trace_id}_request.json").write_text(
                    row["request_json"],
                    encoding="utf-8",
                )
            if row["response_json"]:
                (trace_payloads_dir / f"{trace_id}_response.json").write_text(
                    row["response_json"],
                    encoding="utf-8",
                )

    return {
        "run_id": run_id,
        "conference": run.conference,
        "year": run.year,
        "paper_count": run.paper_count,
        "researcher_count": run.researcher_count,
        "signal_count": run.signal_count,
        "report_count": run.report_count,
        "estimated_cost_usd": cost_summary.get("estimated_cost_usd"),
        "trace_count": cost_summary.get("trace_count"),
        "export_path": str(run_dir),
    }


def _report_result_to_dict(result: Any) -> dict[str, Any]:
    detection = result.scoring.detection
    return {
        "reports": [report.model_dump(mode="json") for report in result.reports],
        "scoring": {
            "researcher_scores": [
                {
                    "entity_id": score.entity_id,
                    "entity_name": score.entity_name,
                    "startup_likelihood_score": score.startup_likelihood_score,
                    "priority_band": score.priority_band.value,
                    "recommendation": score.recommendation.value,
                    "score_breakdown": score.score_breakdown.model_dump(mode="json")
                    if hasattr(score.score_breakdown, "model_dump")
                    else score.score_breakdown,
                }
                for score in result.scoring.researcher_scores
            ],
            "cluster_scores": [
                {
                    "entity_id": score.entity_id,
                    "entity_name": score.entity_name,
                    "startup_likelihood_score": score.startup_likelihood_score,
                    "priority_band": score.priority_band.value,
                    "recommendation": score.recommendation.value,
                    "score_breakdown": score.score_breakdown.model_dump(mode="json")
                    if hasattr(score.score_breakdown, "model_dump")
                    else score.score_breakdown,
                }
                for score in result.scoring.cluster_scores
            ],
            "detection": {
                "papers": [paper.model_dump(mode="json") for paper in detection.papers],
                "researchers": [researcher.model_dump(mode="json") for researcher in detection.researchers],
                "clusters": [cluster.model_dump(mode="json") for cluster in detection.clusters],
                "signals": [signal.model_dump(mode="json") for signal in detection.signals],
                "unmatched_researcher_names": detection.unmatched_researcher_names,
            },
        },
    }


def list_runs_matching(
    *,
    run_id: str | None = None,
    run_ids: list[str] | None = None,
    batch_date: str | None = None,
    latest_per_conference: bool = False,
    db_path: Path | str | None = None,
    limit: int = 200,
) -> list[str]:
    """Resolve run ids to export."""
    if run_id:
        return [run_id]
    if run_ids:
        return run_ids

    runs = list_runs(db_path=db_path, limit=limit)
    if batch_date:
        filtered = [
            run
            for run in runs
            if batch_date in run.id and run.status == RunStatus.COMPLETE and run.paper_count is not None
        ]
    else:
        filtered = [run for run in runs if run.status == RunStatus.COMPLETE and run.paper_count is not None]

    if latest_per_conference:
        latest_by_conference: dict[str, Any] = {}
        for run in filtered:
            existing = latest_by_conference.get(run.conference)
            if existing is None or run.created_at > existing.created_at:
                latest_by_conference[run.conference] = run
        filtered = list(latest_by_conference.values())

    filtered.sort(key=lambda run: (run.conference, run.created_at))
    return [run.id for run in filtered]


def export_runs(
    run_ids: list[str],
    output_dir: Path | str,
    *,
    db_path: Path | str | None = None,
    label: str | None = None,
    include_trace_payloads: bool = False,
) -> Path:
    """Export multiple runs and write a batch manifest."""
    output_dir = Path(output_dir)
    if label:
        bundle_dir = output_dir / label
    elif len(run_ids) == 1:
        bundle_dir = output_dir / run_ids[0]
    else:
        bundle_dir = output_dir / f"batch_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"

    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    exported: list[dict[str, Any]] = []
    total_cost = 0.0
    for run_id in run_ids:
        summary = export_run(
            run_id,
            bundle_dir / "runs",
            db_path=db_path,
            include_trace_payloads=include_trace_payloads,
        )
        exported.append(summary)
        cost = summary.get("estimated_cost_usd")
        if isinstance(cost, (int, float)):
            total_cost += float(cost)

    manifest = {
        "label": label or bundle_dir.name,
        "exported_at": _utc_now_iso(),
        "run_count": len(exported),
        "total_estimated_cost_usd": round(total_cost, 4),
        "runs": exported,
    }

    init_db(db_path)
    db_file = Path(db_path) if db_path else Path(__file__).resolve().parents[1] / ".cache" / "lab2startup.db"
    if db_file.is_file() and len(run_ids) > 1:
        archive_db = bundle_dir / "lab2startup_source.db"
        shutil.copy2(db_file, archive_db)
        manifest["source_db_copy"] = archive_db.name

    _write_json(bundle_dir / "manifest.json", manifest)
    return bundle_dir
