"""Application configuration (Step 10a–10e)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.integrations.github import GitHubConfig
from app.integrations.perplexity import PerplexityConfig
from app.integrations.openalex import OpenAlexFetchConfig
from app.integrations.openreview import OpenReviewConfig
from app.integrations.semantic_scholar import SemanticScholarConfig
from app.database import DEFAULT_DB_PATH
from app.fund_profiles import (
    DEFAULT_FUND_ID,
    FundProfile,
    applied_topic_scores_for_fund,
    load_fund_profile,
)
from app.models import IdentityConfidence
from app.schemas import DEFAULT_PAPERS_PATH, DEFAULT_SIGNALS_PATH


@dataclass(frozen=True)
class AppSettings:
    """Runtime settings for data sources and pipeline inputs."""

    mode: str
    fund_id: str
    fund_profile: FundProfile | None
    topic_scores: dict[str, int]
    paper_source: str
    papers_path: Path | None
    signals_path: Path | None
    use_mock_signals: bool
    db_path: Path
    openalex_config: OpenAlexFetchConfig | None
    openreview_config: OpenReviewConfig | None
    semantic_scholar_config: SemanticScholarConfig
    github_config: GitHubConfig
    perplexity_config: PerplexityConfig
    pipeline_cache_enabled: bool
    pipeline_cache_dir: Path
    pipeline_cache_ttl_hours: float

    @property
    def is_production(self) -> bool:
        return self.mode == "production"


def _load_dotenv() -> None:
    """Load project-root `.env` when present (does not override existing env vars)."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


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


def _parse_identity_confidence(raw: str | None) -> IdentityConfidence:
    if raw is None:
        return IdentityConfidence.HIGH
    normalized = raw.strip().lower()
    for confidence in IdentityConfidence:
        if confidence.value == normalized:
            return confidence
    return IdentityConfidence.HIGH


@lru_cache
def get_settings() -> AppSettings:
    """Load settings from environment variables with JSON defaults."""
    _load_dotenv()
    mode = os.getenv("LAB2STARTUP_MODE", "development").strip().lower()
    default_paper_source = "openreview" if mode == "production" else "json"
    paper_source = os.getenv("LAB2STARTUP_PAPER_SOURCE", default_paper_source).strip().lower()
    papers_path = Path(os.getenv("LAB2STARTUP_PAPERS_PATH", str(DEFAULT_PAPERS_PATH)))
    signals_path = Path(os.getenv("LAB2STARTUP_SIGNALS_PATH", str(DEFAULT_SIGNALS_PATH)))
    use_mock_signals = _parse_bool(
        os.getenv("LAB2STARTUP_USE_MOCK_SIGNALS"),
        default=(mode != "production"),
    )
    db_path = Path(os.getenv("LAB2STARTUP_DB_PATH", str(DEFAULT_DB_PATH)))

    fund_id = os.getenv("LAB2STARTUP_FUND", DEFAULT_FUND_ID).strip()
    fund_profile: FundProfile | None = None
    if fund_id:
        try:
            fund_profile = load_fund_profile(fund_id)
        except FileNotFoundError:
            fund_profile = None
    topic_scores = applied_topic_scores_for_fund(fund_profile)
    fund_context = fund_profile.perplexity_context if fund_profile else None

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

    github_config = GitHubConfig(
        enabled=_parse_bool(os.getenv("LAB2STARTUP_GITHUB_ENABLED")),
        api_token=os.getenv("LAB2STARTUP_GITHUB_TOKEN") or None,
        min_stars=int(os.getenv("LAB2STARTUP_GITHUB_MIN_STARS", "5")),
        max_repos_per_paper=int(os.getenv("LAB2STARTUP_GITHUB_MAX_REPOS_PER_PAPER", "2")),
        supplement_mock_signals=_parse_bool(
            os.getenv("LAB2STARTUP_GITHUB_SUPPLEMENT_MOCK"),
            default=use_mock_signals,
        ),
        request_delay_seconds=float(os.getenv("LAB2STARTUP_GITHUB_REQUEST_DELAY", "0.5")),
    )

    perplexity_config = PerplexityConfig(
        enabled=_parse_bool(
            os.getenv("LAB2STARTUP_PERPLEXITY_ENABLED"),
            default=True,
        ),
        api_key=os.getenv("LAB2STARTUP_PERPLEXITY_API_KEY") or None,
        model=os.getenv("LAB2STARTUP_PERPLEXITY_MODEL", "sonar-pro"),
        max_researchers=int(os.getenv("LAB2STARTUP_PERPLEXITY_MAX_RESEARCHERS", "10")),
        max_signals_per_researcher=int(
            os.getenv("LAB2STARTUP_PERPLEXITY_MAX_SIGNALS_PER_RESEARCHER", "2")
        ),
        min_identity_confidence=_parse_identity_confidence(
            os.getenv("LAB2STARTUP_PERPLEXITY_MIN_IDENTITY")
        ),
        supplement_mock_signals=_parse_bool(
            os.getenv("LAB2STARTUP_PERPLEXITY_SUPPLEMENT_MOCK"),
            default=use_mock_signals,
        ),
        request_delay_seconds=float(os.getenv("LAB2STARTUP_PERPLEXITY_REQUEST_DELAY", "1.0")),
        max_workers=int(os.getenv("LAB2STARTUP_PERPLEXITY_MAX_WORKERS", "3")),
        fund_context=fund_context,
    )

    from app.pipeline_cache import DEFAULT_CACHE_DIR

    pipeline_cache_dir = Path(
        os.getenv("LAB2STARTUP_PIPELINE_CACHE_DIR", str(DEFAULT_CACHE_DIR))
    )

    return AppSettings(
        mode=mode,
        fund_id=fund_id,
        fund_profile=fund_profile,
        topic_scores=topic_scores,
        paper_source=paper_source,
        papers_path=papers_path,
        signals_path=signals_path,
        use_mock_signals=use_mock_signals,
        db_path=db_path,
        openalex_config=openalex_config,
        openreview_config=openreview_config,
        semantic_scholar_config=semantic_scholar_config,
        github_config=github_config,
        perplexity_config=perplexity_config,
        pipeline_cache_enabled=_parse_bool(
            os.getenv("LAB2STARTUP_PIPELINE_CACHE_ENABLED"),
            default=True,
        ),
        pipeline_cache_dir=pipeline_cache_dir,
        pipeline_cache_ttl_hours=float(
            os.getenv("LAB2STARTUP_PIPELINE_CACHE_TTL_HOURS", "168")
        ),
    )


def clear_settings_cache() -> None:
    """Clear cached settings (useful for tests)."""
    get_settings.cache_clear()
