"""Tests for pipeline disk cache."""

from __future__ import annotations

from pathlib import Path

from app.agents.report_agent import run_reports
from app.config import get_settings
from app.pipeline_cache import (
    clear_pipeline_disk_cache,
    load_cached_report_result,
    save_cached_report_result,
)
from app.service import clear_cache, get_report_result


def test_pipeline_disk_cache_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LAB2STARTUP_PIPELINE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("LAB2STARTUP_PIPELINE_CACHE_ENABLED", "true")
    monkeypatch.setenv("LAB2STARTUP_PIPELINE_CACHE_TTL_HOURS", "24")
    get_settings.cache_clear()
    clear_cache()

    settings = get_settings()
    result = run_reports(include_clusters=False)
    save_cached_report_result(settings, result, cache_dir=tmp_path)

    cached = load_cached_report_result(
        settings,
        cache_dir=tmp_path,
        ttl_hours=24,
    )
    assert cached is not None
    assert cached.report_count == result.report_count

    clear_pipeline_disk_cache(cache_dir=tmp_path)
    assert load_cached_report_result(settings, cache_dir=tmp_path, ttl_hours=24) is None

    get_settings.cache_clear()
    clear_cache()


def test_get_report_result_uses_disk_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LAB2STARTUP_PIPELINE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("LAB2STARTUP_PIPELINE_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    clear_cache()

    first = get_report_result(force_refresh=True)
    second = get_report_result()
    assert second.report_count == first.report_count

    clear_pipeline_disk_cache(cache_dir=tmp_path)
    get_settings.cache_clear()
    clear_cache()
