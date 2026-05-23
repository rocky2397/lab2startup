"""Cached application state for the FastAPI backend."""

from __future__ import annotations

from functools import lru_cache

from app.agents.report_agent import ReportResult, run_reports
from app.config import get_settings


@lru_cache
def get_report_result() -> ReportResult:
    """Load and cache the full pipeline result for API requests."""
    settings = get_settings()
    return run_reports(
        papers_path=settings.papers_path,
        signals_path=settings.signals_path,
        openalex_config=settings.openalex_config,
    )


def clear_cache() -> None:
    """Clear cached pipeline state (useful for tests)."""
    get_report_result.cache_clear()
