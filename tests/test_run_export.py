"""Tests for pipeline run export bundles."""

from __future__ import annotations

import json

from app.run_export import export_run, export_runs, list_runs_matching
from app.run_service import execute_pipeline_run
from app.run_store import load_run_result


def test_export_run_writes_snapshot(tmp_path):
    run, _result = execute_pipeline_run(
        conference="NeurIPS",
        year=2024,
        paper_source="json",
        fund_profile="",
        db_path=tmp_path / "lab2startup.db",
    )
    summary = export_run(run.id, tmp_path / "exports", db_path=tmp_path / "lab2startup.db")
    run_dir = tmp_path / "exports" / run.id
    assert (run_dir / "snapshot.json").is_file()
    assert (run_dir / "run_metadata.json").is_file()
    assert summary["run_id"] == run.id
    snapshot = json.loads((run_dir / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["scoring"]["detection"]["papers"]


def test_export_runs_writes_manifest(tmp_path):
    db_path = tmp_path / "lab2startup.db"
    run, _ = execute_pipeline_run(
        conference="NeurIPS",
        year=2024,
        paper_source="json",
        fund_profile="",
        db_path=db_path,
    )
    bundle = export_runs([run.id], tmp_path / "exports", db_path=db_path, label="test_batch")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_count"] == 1
    assert manifest["runs"][0]["run_id"] == run.id
    assert load_run_result(run.id, db_path=db_path) is not None


def test_list_runs_matching_by_batch_date(tmp_path):
    db_path = tmp_path / "lab2startup.db"
    run, _ = execute_pipeline_run(
        conference="NeurIPS",
        year=2024,
        paper_source="json",
        fund_profile="",
        db_path=db_path,
    )
    date_part = run.id.split("_")[-1][:8]
    matched = list_runs_matching(batch_date=date_part, db_path=db_path)
    assert run.id in matched
