"""Tests for reusing OpenReview paper collections across pipeline runs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.agents.report_agent import run_reports
from app.config import clear_settings_cache, get_settings
from app.run_service import PaperFetchResult, _fetch_papers_for_run, build_run_configs
from app.run_store import (
    create_run_record,
    find_latest_run_with_papers,
    load_papers_from_run,
    save_run_snapshot,
)


def _seed_complete_run(
    *,
    db_path: Path,
    run_id: str,
    conference: str = "NeurIPS",
    year: int = 2024,
    paper_source: str = "openreview",
    fund_profile: str | None = "backtrace",
) -> list:
    result = run_reports(include_clusters=False)
    papers = result.scoring.detection.papers
    create_run_record(
        run_id=run_id,
        conference=conference,
        year=year,
        paper_source=paper_source,
        fund_profile=fund_profile,
        config_json={"conference": conference, "year": year, "paper_source": paper_source},
        db_path=db_path,
    )
    save_run_snapshot(run_id, result, db_path=db_path)
    return papers


def test_find_latest_run_with_papers_matches_fund_profile(tmp_path: Path) -> None:
    db_path = tmp_path / "reuse.db"
    _seed_complete_run(db_path=db_path, run_id="run_backtrace", fund_profile="backtrace")
    _seed_complete_run(db_path=db_path, run_id="run_other", fund_profile="other_fund")

    match = find_latest_run_with_papers(
        conference="NeurIPS",
        year=2024,
        paper_source="openreview",
        fund_profile="backtrace",
        db_path=db_path,
    )
    assert match is not None
    assert match.id == "run_backtrace"

    no_match = find_latest_run_with_papers(
        conference="NeurIPS",
        year=2024,
        paper_source="openreview",
        fund_profile="missing",
        db_path=db_path,
    )
    assert no_match is None


def test_load_papers_from_run(tmp_path: Path) -> None:
    db_path = tmp_path / "reuse.db"
    expected = _seed_complete_run(db_path=db_path, run_id="run_papers")

    papers = load_papers_from_run("run_papers", db_path=db_path)
    assert papers is not None
    assert len(papers) == len(expected)
    assert papers[0].id == expected[0].id


def test_fetch_papers_reuses_openreview_without_api_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "reuse.db"
    stored = _seed_complete_run(db_path=db_path, run_id="run_cached", fund_profile="backtrace")

    fetch_mock = MagicMock(return_value=[])
    monkeypatch.setattr(
        "app.integrations.openreview.fetch_papers_from_openreview",
        fetch_mock,
    )

    configs = build_run_configs(
        conference="NeurIPS",
        year=2024,
        paper_source="openreview",
        settings=get_settings(),
    )

    paper_fetch = _fetch_papers_for_run(
        configs,
        fund=None,
        db_path=db_path,
        force_refetch=False,
        fund_profile="backtrace",
    )

    assert paper_fetch.papers is not None
    assert len(paper_fetch.papers) == len(stored)
    assert paper_fetch.reused_from_run_id == "run_cached"
    fetch_mock.assert_not_called()


def test_fetch_papers_force_refetch_bypasses_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "reuse.db"
    _seed_complete_run(db_path=db_path, run_id="run_cached", fund_profile="backtrace")

    fetched = run_reports(include_clusters=False).scoring.detection.papers
    fetch_mock = MagicMock(return_value=fetched)
    monkeypatch.setattr(
        "app.integrations.openreview.fetch_papers_from_openreview",
        fetch_mock,
    )

    configs = build_run_configs(
        conference="NeurIPS",
        year=2024,
        paper_source="openreview",
        settings=get_settings(),
    )

    paper_fetch = _fetch_papers_for_run(
        configs,
        fund=None,
        db_path=db_path,
        force_refetch=True,
        fund_profile="backtrace",
    )

    assert paper_fetch.papers is not None
    assert len(paper_fetch.papers) == len(fetched)
    assert paper_fetch.reused_from_run_id is None
    fetch_mock.assert_called_once()


def test_force_paper_refetch_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB2STARTUP_FORCE_PAPER_REFETCH", "true")
    clear_settings_cache()
    assert get_settings().force_paper_refetch is True
    clear_settings_cache()
