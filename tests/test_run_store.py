"""Tests for SQLite run persistence (Step 11)."""

from __future__ import annotations

from pathlib import Path

from app.agents.report_agent import run_reports
from app.config import clear_settings_cache, get_settings
from app.models import PipelineRun, RunStatus
from app.run_service import execute_pipeline_run
from app.run_store import (
    deserialize_report_result,
    filter_runs_with_results,
    list_runs,
    load_run_result,
    pick_preferred_run_id,
    run_has_results,
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


def _make_run(
    *,
    run_id: str,
    paper_count: int | None,
    status: RunStatus = RunStatus.COMPLETE,
) -> PipelineRun:
    return PipelineRun(
        id=run_id,
        conference="NeurIPS",
        year=2024,
        status=status,
        paper_source="json",
        created_at="2024-01-02T00:00:00+00:00",
        paper_count=paper_count,
    )


def test_run_has_results_and_filter() -> None:
    with_data = _make_run(run_id="a", paper_count=3)
    empty = _make_run(run_id="b", paper_count=0)
    failed = _make_run(run_id="c", paper_count=0, status=RunStatus.FAILED)

    assert run_has_results(with_data)
    assert not run_has_results(empty)
    assert not run_has_results(failed)

    filtered = filter_runs_with_results([with_data, empty, failed])
    assert [run.id for run in filtered] == ["a"]


def test_pick_preferred_run_id() -> None:
    runs = [
        _make_run(run_id="recent_empty", paper_count=0),
        _make_run(run_id="best", paper_count=12),
        _make_run(run_id="older", paper_count=5),
    ]
    assert pick_preferred_run_id(runs, current_id="older") == "older"
    assert pick_preferred_run_id(runs, current_id=None) == "best"

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


def _make_run(
    *,
    run_id: str,
    paper_count: int | None,
    status: RunStatus = RunStatus.COMPLETE,
) -> PipelineRun:
    return PipelineRun(
        id=run_id,
        conference="NeurIPS",
        year=2024,
        status=status,
        paper_source="json",
        created_at="2024-01-02T00:00:00+00:00",
        paper_count=paper_count,
    )


def test_run_has_results_and_filter() -> None:
    with_data = _make_run(run_id="a", paper_count=3)
    empty = _make_run(run_id="b", paper_count=0)
    failed = _make_run(run_id="c", paper_count=0, status=RunStatus.FAILED)

    assert run_has_results(with_data)
    assert not run_has_results(empty)
    assert not run_has_results(failed)

    filtered = filter_runs_with_results([with_data, empty, failed])
    assert [run.id for run in filtered] == ["a"]


def test_pick_preferred_run_id() -> None:
    runs = [
        _make_run(run_id="recent_empty", paper_count=0),
        _make_run(run_id="best", paper_count=12),
        _make_run(run_id="older", paper_count=5),
    ]
    assert pick_preferred_run_id(runs, current_id="older") == "older"
    assert pick_preferred_run_id(runs, current_id=None) == "best"
