"""Serialize and persist pipeline runs to SQLite (Step 11)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.report_agent import ReportResult
from app.agents.scoring_agent import ScoringResult
from app.agents.signal_agent import SignalDetectionResult
from app.database import get_connection, init_db
from app.enrichment_audit import (
    EnrichmentAudit,
    deserialize_enrichment_audit,
    serialize_enrichment_audit,
)
from app.models import (
    Cluster,
    Paper,
    PipelineRun,
    Report,
    Researcher,
    RunStatus,
    Signal,
)
from app.scoring import EntityScore


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_run_id(*, conference: str, year: int, created_at: datetime | None = None) -> str:
    """Build a stable, human-readable run identifier."""
    moment = created_at or datetime.now(UTC)
    slug = re.sub(r"[^a-z0-9]+", "_", conference.lower()).strip("_") or "conference"
    stamp = moment.strftime("%Y%m%dT%H%M%S")
    return f"run_{year}_{slug}_{stamp}"


def _entity_score_to_dict(score: EntityScore) -> dict[str, Any]:
    return {
        "entity_id": score.entity_id,
        "entity_type": score.entity_type,
        "entity_name": score.entity_name,
        "score_breakdown": score.score_breakdown.model_dump(mode="json"),
        "startup_likelihood_score": score.startup_likelihood_score,
        "priority_band": score.priority_band.value,
        "recommendation": score.recommendation.value,
    }


def _entity_score_from_dict(payload: dict[str, Any]) -> EntityScore:
    from app.models import PriorityBand, ScoreBreakdown, VCAction

    return EntityScore(
        entity_id=payload["entity_id"],
        entity_type=payload["entity_type"],
        entity_name=payload["entity_name"],
        score_breakdown=ScoreBreakdown.model_validate(payload["score_breakdown"]),
        startup_likelihood_score=payload["startup_likelihood_score"],
        priority_band=PriorityBand(payload["priority_band"]),
        recommendation=VCAction(payload["recommendation"]),
    )


def serialize_report_result(result: ReportResult) -> str:
    """Convert a ReportResult into JSON for SQLite storage."""
    detection = result.scoring.detection
    payload = {
        "reports": [report.model_dump(mode="json") for report in result.reports],
        "scoring": {
            "researcher_scores": [_entity_score_to_dict(score) for score in result.scoring.researcher_scores],
            "cluster_scores": [_entity_score_to_dict(score) for score in result.scoring.cluster_scores],
            "detection": {
                "papers": [paper.model_dump(mode="json") for paper in detection.papers],
                "researchers": [researcher.model_dump(mode="json") for researcher in detection.researchers],
                "clusters": [cluster.model_dump(mode="json") for cluster in detection.clusters],
                "signals": [signal.model_dump(mode="json") for signal in detection.signals],
                "unmatched_researcher_names": detection.unmatched_researcher_names,
            },
        },
    }
    return json.dumps(payload)


def deserialize_report_result(payload: str | dict[str, Any]) -> ReportResult:
    """Restore a ReportResult from JSON."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    detection_data = data["scoring"]["detection"]
    detection = SignalDetectionResult(
        papers=[Paper.model_validate(item) for item in detection_data["papers"]],
        researchers=[Researcher.model_validate(item) for item in detection_data["researchers"]],
        clusters=[Cluster.model_validate(item) for item in detection_data["clusters"]],
        signals=[Signal.model_validate(item) for item in detection_data["signals"]],
        unmatched_researcher_names=detection_data.get("unmatched_researcher_names", []),
    )
    scoring = ScoringResult(
        detection=detection,
        researcher_scores=[_entity_score_from_dict(item) for item in data["scoring"]["researcher_scores"]],
        cluster_scores=[_entity_score_from_dict(item) for item in data["scoring"]["cluster_scores"]],
    )
    reports = [Report.model_validate(item) for item in data["reports"]]
    return ReportResult(scoring=scoring, reports=reports)


def _row_to_pipeline_run(row: Any) -> PipelineRun:
    return PipelineRun(
        id=row["id"],
        conference=row["conference"],
        year=row["year"],
        status=RunStatus(row["status"]),
        paper_source=row["paper_source"],
        fund_profile=row["fund_profile"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        config_json=json.loads(row["config_json"]),
        error_message=row["error_message"],
        paper_count=row["paper_count"],
        researcher_count=row["researcher_count"],
        signal_count=row["signal_count"],
        report_count=row["report_count"],
    )


def create_run_record(
    *,
    run_id: str,
    conference: str,
    year: int,
    paper_source: str,
    config_json: dict[str, object],
    fund_profile: str | None = None,
    db_path: str | Path | None = None,
) -> PipelineRun:
    """Insert a pending run row."""
    init_db(db_path)
    created_at = _utc_now_iso()
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO pipeline_runs (
                id, conference, year, fund_profile, status, paper_source,
                created_at, config_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                conference,
                year,
                fund_profile,
                RunStatus.PENDING.value,
                paper_source,
                created_at,
                json.dumps(config_json),
            ),
        )
        connection.commit()
    return PipelineRun(
        id=run_id,
        conference=conference,
        year=year,
        status=RunStatus.PENDING,
        paper_source=paper_source,
        fund_profile=fund_profile,
        created_at=created_at,
        config_json=config_json,
    )


def mark_run_running(run_id: str, *, db_path: str | Path | None = None) -> None:
    with get_connection(db_path) as connection:
        connection.execute(
            "UPDATE pipeline_runs SET status = ? WHERE id = ?",
            (RunStatus.RUNNING.value, run_id),
        )
        connection.commit()


def save_run_snapshot(
    run_id: str,
    result: ReportResult,
    *,
    db_path: str | Path | None = None,
) -> None:
    """Persist the full pipeline snapshot and mark the run complete."""
    init_db(db_path)
    detection = result.scoring.detection
    completed_at = _utc_now_iso()
    snapshot_json = serialize_report_result(result)

    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO run_snapshots (run_id, snapshot_json)
            VALUES (?, ?)
            ON CONFLICT(run_id) DO UPDATE SET snapshot_json = excluded.snapshot_json
            """,
            (run_id, snapshot_json),
        )
        connection.execute(
            """
            UPDATE pipeline_runs
            SET status = ?, completed_at = ?, error_message = NULL,
                paper_count = ?, researcher_count = ?, signal_count = ?, report_count = ?
            WHERE id = ?
            """,
            (
                RunStatus.COMPLETE.value,
                completed_at,
                len(detection.papers),
                len(detection.researchers),
                len(detection.signals),
                result.report_count,
                run_id,
            ),
        )
        connection.commit()


def save_enrichment_audit(
    run_id: str,
    audit: EnrichmentAudit,
    *,
    db_path: str | Path | None = None,
) -> None:
    """Persist enrichment verification data for a pipeline run."""
    init_db(db_path)
    created_at = audit.created_at or _utc_now_iso()
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO run_enrichment_audits (run_id, audit_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                audit_json = excluded.audit_json,
                created_at = excluded.created_at
            """,
            (run_id, serialize_enrichment_audit(audit), created_at),
        )
        connection.commit()


def load_enrichment_audit(
    run_id: str,
    *,
    db_path: str | Path | None = None,
) -> EnrichmentAudit | None:
    init_db(db_path)
    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT audit_json FROM run_enrichment_audits WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return deserialize_enrichment_audit(row["audit_json"])


def mark_run_failed(
    run_id: str,
    error_message: str,
    *,
    db_path: str | Path | None = None,
) -> None:
    with get_connection(db_path) as connection:
        connection.execute(
            """
            UPDATE pipeline_runs
            SET status = ?, completed_at = ?, error_message = ?
            WHERE id = ?
            """,
            (RunStatus.FAILED.value, _utc_now_iso(), error_message, run_id),
        )
        connection.commit()


def run_has_results(run: PipelineRun) -> bool:
    """True when a completed run persisted at least one paper."""
    return run.status == RunStatus.COMPLETE and (run.paper_count or 0) > 0


def filter_runs_with_results(runs: list[PipelineRun]) -> list[PipelineRun]:
    """Keep only complete runs that have paper data."""
    return [run for run in runs if run_has_results(run)]


def pick_preferred_run_id(
    runs: list[PipelineRun],
    *,
    current_id: str | None = None,
) -> str | None:
    """Pick a run for the dashboard — prefer current selection, then most papers."""
    if not runs:
        return None
    run_ids = {run.id for run in runs}
    if current_id in run_ids:
        return current_id
    best = max(runs, key=lambda run: ((run.paper_count or 0), run.created_at))
    return best.id


def list_runs(*, db_path: str | Path | None = None, limit: int = 50) -> list[PipelineRun]:
    init_db(db_path)
    with get_connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM pipeline_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_pipeline_run(row) for row in rows]


def get_run(run_id: str, *, db_path: str | Path | None = None) -> PipelineRun | None:
    init_db(db_path)
    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM pipeline_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    return _row_to_pipeline_run(row) if row else None


def get_latest_run(*, db_path: str | Path | None = None) -> PipelineRun | None:
    runs = list_runs(db_path=db_path, limit=1)
    return runs[0] if runs else None


def load_run_result(run_id: str, *, db_path: str | Path | None = None) -> ReportResult | None:
    init_db(db_path)
    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT snapshot_json FROM run_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return deserialize_report_result(row["snapshot_json"])


def load_latest_run_result(*, db_path: str | Path | None = None) -> ReportResult | None:
    latest = get_latest_run(db_path=db_path)
    if latest is None or latest.status != RunStatus.COMPLETE:
        return None
    return load_run_result(latest.id, db_path=db_path)
