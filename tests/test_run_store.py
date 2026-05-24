"""Tests for SQLite run persistence (Step 11)."""

from __future__ import annotations

from pathlib import Path

from app.agents.report_agent import run_reports
from app.config import clear_settings_cache, get_settings
from app.models import RunStatus
from app.run_service import execute_pipeline_run
from app.run_store import (
    deserialize_report_result,
    list_runs,
    load_run_result,
    serialize_report_result,
)
from app.service import clear_cache


def test_serialize_report_result_roundtrip() -> None:
    result = run_reports(include_clusters=False)
    restored = deserialize_report_result(serialize_report_result(result))
    assert restored.report_count == result.report_count
    assert len(restored.scoring.detection.papers) == len(result.scoring.detection.papers)
    assert len(restored.scoring.detection.signals) == len(result.scoring.detection.signals)


def test_execute_pipeline_run_json_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LAB2STARTUP_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LAB2STARTUP_PAPER_SOURCE", "json")
    monkeypatch.setenv("LAB2STARTUP_USE_MOCK_SIGNALS", "true")
    monkeypatch.setenv("LAB2STARTUP_PERPLEXITY_ENABLED", "false")
    monkeypatch.setenv("LAB2STARTUP_GITHUB_ENABLED", "false")
    clear_settings_cache()
    clear_cache()

    run, result = execute_pipeline_run(
        conference="NeurIPS",
        year=2024,
        paper_source="json",
        fund_profile="",
        settings=get_settings(),
        include_clusters=False,
    )
    assert run.status == RunStatus.COMPLETE
    assert result.report_count > 0

    stored = load_run_result(run.id, db_path=get_settings().db_path)
    assert stored is not None
    assert stored.report_count == result.report_count

    runs = list_runs(db_path=get_settings().db_path)
    assert len(runs) == 1
    assert runs[0].paper_count == len(result.scoring.detection.papers)

    clear_settings_cache()
    clear_cache()
