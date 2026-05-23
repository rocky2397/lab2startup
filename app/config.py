"""Application configuration (Step 10a–10c)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.integrations.openalex import OpenAlexFetchConfig
from app.integrations.openreview import OpenReviewConfig
from app.integrations.semantic_scholar import SemanticScholarConfig
from app.schemas import DEFAULT_PAPERS_PATH, DEFAULT_SIGNALS_PATH


@dataclass(frozen=True)
class AppSettings:
    """Runtime settings for data sources and pipeline inputs."""

    paper_source: str
    papers_path: Path | None
    signals_path: Path | None
    openalex_config: OpenAlexFetchConfig | None
    openreview_config: OpenReviewConfig | None
    semantic_scholar_config: SemanticScholarConfig


def _parse_topic_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_work_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_openreview_config(*, fetch_as_source: bool) -> OpenReviewConfig | None:
    if fetch_as_source:
        return OpenReviewConfig(
            enabled=True,
            fetch_as_source=True,
            conference=os.getenv("LAB2STARTUP_OPENREVIEW_CONFERENCE", "NeurIPS"),
            year=int(os.getenv("LAB2STARTUP_OPENREVIEW_YEAR", "2024")),
            max_results=int(os.getenv("LAB2STARTUP_OPENREVIEW_MAX_RESULTS", "50")),
            accepted_only=_parse_bool(os.getenv("LAB2STARTUP_OPENREVIEW_ACCEPTED_ONLY"), True),
            fetch_profiles=_parse_bool(os.getenv("LAB2STARTUP_OPENREVIEW_FETCH_PROFILES"), True),
            request_delay_seconds=float(os.getenv("LAB2STARTUP_OPENREVIEW_REQUEST_DELAY", "0.5")),
        )

    if not _parse_bool(os.getenv("LAB2STARTUP_OPENREVIEW_ENABLED")):
        return None

    return OpenReviewConfig(
        enabled=True,
        fetch_as_source=False,
        conference=os.getenv("LAB2STARTUP_OPENREVIEW_CONFERENCE", "NeurIPS"),
        year=int(os.getenv("LAB2STARTUP_OPENREVIEW_YEAR", "2024")),
        max_results=int(os.getenv("LAB2STARTUP_OPENREVIEW_MAX_RESULTS", "1000")),
        accepted_only=_parse_bool(os.getenv("LAB2STARTUP_OPENREVIEW_ACCEPTED_ONLY"), True),
        fetch_profiles=_parse_bool(os.getenv("LAB2STARTUP_OPENREVIEW_FETCH_PROFILES"), True),
        request_delay_seconds=float(os.getenv("LAB2STARTUP_OPENREVIEW_REQUEST_DELAY", "0.5")),
    )


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

    openreview_config = _build_openreview_config(fetch_as_source=paper_source == "openreview")

    semantic_scholar_config = SemanticScholarConfig(
        enabled=_parse_bool(os.getenv("LAB2STARTUP_SEMANTIC_SCHOLAR_ENABLED")),
        api_key=os.getenv("LAB2STARTUP_S2_API_KEY") or None,
        fetch_author_profiles=_parse_bool(
            os.getenv("LAB2STARTUP_S2_FETCH_AUTHORS"),
            default=True,
        ),
        request_delay_seconds=float(os.getenv("LAB2STARTUP_S2_REQUEST_DELAY", "1.1")),
    )

    return AppSettings(
        paper_source=paper_source,
        papers_path=papers_path,
        signals_path=signals_path,
        openalex_config=openalex_config,
        openreview_config=openreview_config,
        semantic_scholar_config=semantic_scholar_config,
    )


def clear_settings_cache() -> None:
    """Clear cached settings (useful for tests)."""
    get_settings.cache_clear()
