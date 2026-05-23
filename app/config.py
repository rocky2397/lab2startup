"""Application configuration (Step 10a)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.integrations.openalex import OpenAlexFetchConfig
from app.schemas import DEFAULT_PAPERS_PATH, DEFAULT_SIGNALS_PATH


@dataclass(frozen=True)
class AppSettings:
    """Runtime settings for data sources and pipeline inputs."""

    paper_source: str
    papers_path: Path | None
    signals_path: Path | None
    openalex_config: OpenAlexFetchConfig | None


def _parse_topic_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_work_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


@lru_cache
def get_settings() -> AppSettings:
    """Load settings from environment variables with JSON defaults."""
    paper_source = os.getenv("LAB2STARTUP_PAPER_SOURCE", "json").strip().lower()
    papers_path = Path(os.getenv("LAB2STARTUP_PAPERS_PATH", str(DEFAULT_PAPERS_PATH)))
    signals_path = Path(os.getenv("LAB2STARTUP_SIGNALS_PATH", str(DEFAULT_SIGNALS_PATH)))

    openalex_config: OpenAlexFetchConfig | None = None
    if paper_source == "openalex":
        openalex_config = OpenAlexFetchConfig(
            conference=os.getenv("LAB2STARTUP_OPENALEX_CONFERENCE", "NeurIPS"),
            year=int(os.getenv("LAB2STARTUP_OPENALEX_YEAR", "2024")),
            search_query=os.getenv("LAB2STARTUP_OPENALEX_SEARCH") or None,
            topic_keywords=_parse_topic_keywords(os.getenv("LAB2STARTUP_OPENALEX_TOPICS")),
            openalex_work_ids=_parse_work_ids(os.getenv("LAB2STARTUP_OPENALEX_WORK_IDS")),
            max_results=int(os.getenv("LAB2STARTUP_OPENALEX_MAX_RESULTS", "50")),
            mailto=os.getenv("LAB2STARTUP_OPENALEX_MAILTO") or None,
        )

    return AppSettings(
        paper_source=paper_source,
        papers_path=papers_path,
        signals_path=signals_path,
        openalex_config=openalex_config,
    )


def clear_settings_cache() -> None:
    """Clear cached settings (useful for tests)."""
    get_settings.cache_clear()
