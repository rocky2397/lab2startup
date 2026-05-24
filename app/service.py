"""Cached application state for the FastAPI backend and dashboard."""

from __future__ import annotations

from app.agents.report_agent import ReportResult, run_reports
from app.config import get_settings
from app.pipeline_cache import (
    clear_pipeline_disk_cache,
    load_cached_report_result,
    save_cached_report_result,
)
from app.run_service import get_stored_report_result

_memory_result: ReportResult | None = None
_memory_fingerprint: str | None = None
_active_run_id: str | None = None


def _fingerprint() -> str:
    from app.pipeline_cache import build_pipeline_fingerprint

    fingerprint = build_pipeline_fingerprint(get_settings())
    if _active_run_id:
        return f"{fingerprint}:{_active_run_id}"
    return fingerprint


def set_active_run_id(run_id: str | None) -> None:
    """Select which stored run the dashboard/API should load."""
    global _active_run_id, _memory_result, _memory_fingerprint
    _active_run_id = run_id
    _memory_result = None
    _memory_fingerprint = None


def get_active_run_id() -> str | None:
    return _active_run_id


def get_report_result(*, force_refresh: bool = False, run_id: str | None = None) -> ReportResult:
    """Load pipeline results from SQLite (production) or live pipeline (development)."""
    global _memory_result, _memory_fingerprint

    settings = get_settings()
    selected_run_id = run_id or _active_run_id
    fingerprint = _fingerprint()

    if not force_refresh:
        if _memory_result is not None and _memory_fingerprint == fingerprint:
            return _memory_result

        if settings.is_production or selected_run_id:
            stored = get_stored_report_result(run_id=selected_run_id, settings=settings)
            if stored is not None:
                _memory_result = stored
                _memory_fingerprint = fingerprint
                return stored

        if settings.pipeline_cache_enabled and not settings.is_production:
            cached = load_cached_report_result(
                settings,
                cache_dir=settings.pipeline_cache_dir,
                ttl_hours=settings.pipeline_cache_ttl_hours,
            )
            if cached is not None:
                _memory_result = cached
                _memory_fingerprint = fingerprint
                return cached

    signals_path = settings.signals_path if settings.use_mock_signals else None
    result = run_reports(
        papers_path=settings.papers_path,
        signals_path=signals_path,
        openalex_config=settings.openalex_config,
        openreview_config=settings.openreview_config,
        semantic_scholar_config=settings.semantic_scholar_config,
        github_config=settings.github_config,
        perplexity_config=settings.perplexity_config,
        use_mock_signals=settings.use_mock_signals,
        topic_scores=settings.topic_scores,
    )

    if settings.pipeline_cache_enabled and not settings.is_production:
        save_cached_report_result(
            settings,
            result,
            cache_dir=settings.pipeline_cache_dir,
        )

    _memory_result = result
    _memory_fingerprint = fingerprint
    return result


def clear_cache() -> None:
    """Clear in-memory and disk pipeline cache (useful for tests and refresh)."""
    global _memory_result, _memory_fingerprint
    _memory_result = None
    _memory_fingerprint = None
    settings = get_settings()
    clear_pipeline_disk_cache(cache_dir=settings.pipeline_cache_dir)
