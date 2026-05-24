"""Execute and persist full pipeline runs (Step 12)."""

from __future__ import annotations

import logging
from dataclasses import asdict, replace
from pathlib import Path

from app.agents.report_agent import ReportResult, run_reports
from app.config import AppSettings, get_settings
from app.fund_profiles import (
    FundProfile,
    filter_papers_for_fund,
    resolve_paper_source_for_fund,
    validate_conference_for_fund,
)
from app.integrations.github import GitHubConfig
from app.integrations.openalex import OpenAlexFetchConfig
from app.integrations.openreview import OpenReviewConfig
from app.integrations.perplexity import PerplexityConfig
from app.integrations.semantic_scholar import SemanticScholarConfig
from app.models import Paper, PipelineRun, RunStatus
from app.run_store import (
    create_run_record,
    get_run,
    load_run_result,
    make_run_id,
    mark_run_failed,
    mark_run_running,
    save_run_snapshot,
)

logger = logging.getLogger(__name__)


def _resolve_fund(
    fund_profile: str | None,
    settings: AppSettings,
) -> FundProfile | None:
    if fund_profile == "":
        return None
    if fund_profile:
        from app.fund_profiles import load_fund_profile

        return load_fund_profile(fund_profile)
    return settings.fund_profile


def build_run_configs(
    *,
    conference: str,
    year: int,
    paper_source: str,
    topics: list[str] | None = None,
    fund: FundProfile | None = None,
    settings: AppSettings | None = None,
) -> dict[str, object]:
    """Build integration configs for a conference run."""
    settings = settings or get_settings()
    topics = topics or []

    openalex_config: OpenAlexFetchConfig | None = None
    openreview_config: OpenReviewConfig | None = None

    if paper_source == "openalex":
        topic_keywords = topics or (list(fund.topic_keywords) if fund else [])
        openalex_config = OpenAlexFetchConfig(
            conference=conference,
            year=year,
            topic_keywords=topic_keywords,
            max_results=settings.openalex_config.max_results
            if settings.openalex_config
            else 50,
            mailto=settings.openalex_config.mailto if settings.openalex_config else None,
        )
    elif paper_source == "openreview":
        openreview_config = OpenReviewConfig(
            enabled=True,
            fetch_as_source=True,
            conference=conference,
            year=year,
            max_results=settings.openreview_config.max_results
            if settings.openreview_config
            else 50,
            accepted_only=True,
            fetch_profiles=True,
            request_delay_seconds=settings.openreview_config.request_delay_seconds
            if settings.openreview_config
            else 0.5,
        )
    elif paper_source == "json":
        openreview_config = settings.openreview_config

    perplexity_config = settings.perplexity_config
    if fund and fund.perplexity_context:
        perplexity_config = replace(
            settings.perplexity_config,
            fund_context=fund.perplexity_context,
        )

    return {
        "paper_source": paper_source,
        "conference": conference,
        "year": year,
        "topics": topics,
        "fund_id": fund.id if fund else None,
        "openalex_config": openalex_config,
        "openreview_config": openreview_config,
        "semantic_scholar_config": settings.semantic_scholar_config,
        "github_config": settings.github_config,
        "perplexity_config": perplexity_config,
        "use_mock_signals": settings.use_mock_signals,
        "topic_scores": settings.topic_scores,
        "papers_path": settings.papers_path,
        "signals_path": settings.signals_path,
    }


def _fetch_papers_for_run(configs: dict[str, object], fund: FundProfile | None) -> list[Paper] | None:
    """Fetch and optionally fund-filter papers before running the pipeline."""
    paper_source = configs["paper_source"]
    if paper_source == "openreview":
        from app.integrations.openreview import fetch_papers_from_openreview

        papers = fetch_papers_from_openreview(configs["openreview_config"])  # type: ignore[arg-type]
    elif paper_source == "openalex":
        from app.integrations.openalex import fetch_papers_from_openalex

        papers = fetch_papers_from_openalex(configs["openalex_config"])  # type: ignore[arg-type]
    else:
        return None

    if fund:
        before = len(papers)
        papers = filter_papers_for_fund(papers, fund)
        logger.info(
            "Fund filter kept %s/%s papers for %s",
            len(papers),
            before,
            fund.name,
        )
    return papers


def _enrich_openreview_config(
    configs: dict[str, object],
    *,
    conference: str,
    year: int,
    settings: AppSettings,
) -> OpenReviewConfig | None:
    """Use OpenReview for affiliation enrichment when papers were pre-fetched."""
    if configs["paper_source"] != "openreview":
        return configs["openreview_config"]  # type: ignore[return-value]

    return OpenReviewConfig(
        enabled=True,
        fetch_as_source=False,
        conference=conference,
        year=year,
        max_results=settings.openreview_config.max_results
        if settings.openreview_config
        else 50,
        accepted_only=True,
        fetch_profiles=True,
        request_delay_seconds=settings.openreview_config.request_delay_seconds
        if settings.openreview_config
        else 0.5,
    )


def execute_pipeline_run(
    *,
    conference: str,
    year: int,
    paper_source: str | None = None,
    fund_profile: str | None = None,
    topics: list[str] | None = None,
    run_id: str | None = None,
    db_path: Path | str | None = None,
    settings: AppSettings | None = None,
    include_clusters: bool = True,
) -> tuple[PipelineRun, ReportResult]:
    """Run the full pipeline and persist the snapshot to SQLite."""
    settings = settings or get_settings()
    db_path = db_path or settings.db_path
    fund = _resolve_fund(fund_profile, settings)

    if fund:
        validate_conference_for_fund(conference, fund)
        paper_source = resolve_paper_source_for_fund(
            conference=conference,
            fund=fund,
            requested_source=paper_source,
        )
    elif paper_source is None:
        paper_source = "openreview" if settings.is_production else "json"

    configs = build_run_configs(
        conference=conference,
        year=year,
        paper_source=paper_source,
        topics=topics,
        fund=fund,
        settings=settings,
    )

    run_id = run_id or make_run_id(conference=conference, year=year)
    config_record = {
        "conference": conference,
        "year": year,
        "paper_source": paper_source,
        "fund_profile": fund.id if fund else fund_profile,
        "topics": topics or [],
        "mode": settings.mode,
        "integrations": {
            "semantic_scholar": asdict(configs["semantic_scholar_config"]),  # type: ignore[arg-type]
            "github": asdict(configs["github_config"]),  # type: ignore[arg-type]
            "perplexity": asdict(configs["perplexity_config"]),  # type: ignore[arg-type]
        },
    }

    run = create_run_record(
        run_id=run_id,
        conference=conference,
        year=year,
        paper_source=paper_source,
        fund_profile=fund.id if fund else fund_profile,
        config_json=config_record,
        db_path=db_path,
    )

    try:
        mark_run_running(run_id, db_path=db_path)
        logger.info(
            "Running pipeline %s (%s %s via %s%s)",
            run_id,
            conference,
            year,
            paper_source,
            f" for {fund.name}" if fund else "",
        )

        papers_path = settings.papers_path if paper_source == "json" else None
        signals_path = settings.signals_path if settings.use_mock_signals else None
        prefetched_papers = _fetch_papers_for_run(configs, fund)
        openreview_config = _enrich_openreview_config(
            configs,
            conference=conference,
            year=year,
            settings=settings,
        )

        result = run_reports(
            papers_path=papers_path,
            signals_path=signals_path,
            papers=prefetched_papers,
            openalex_config=configs["openalex_config"],  # type: ignore[arg-type]
            openreview_config=openreview_config,
            semantic_scholar_config=configs["semantic_scholar_config"],  # type: ignore[arg-type]
            github_config=configs["github_config"],  # type: ignore[arg-type]
            perplexity_config=configs["perplexity_config"],  # type: ignore[arg-type]
            use_mock_signals=bool(configs["use_mock_signals"]),
            topic_scores=configs["topic_scores"],  # type: ignore[arg-type]
            include_clusters=include_clusters,
        )
        save_run_snapshot(run_id, result, db_path=db_path)
        logger.info(
            "Run complete: %s papers, %s researchers, %s signals, %s reports",
            len(result.scoring.detection.papers),
            len(result.scoring.detection.researchers),
            len(result.scoring.detection.signals),
            result.report_count,
        )
        updated = get_run(run_id, db_path=db_path)
        return updated or run, result
    except Exception as exc:
        mark_run_failed(run_id, str(exc), db_path=db_path)
        logger.exception("Pipeline run failed: %s", run_id)
        raise


def get_stored_report_result(
    *,
    run_id: str | None = None,
    db_path: Path | str | None = None,
    settings: AppSettings | None = None,
) -> ReportResult | None:
    """Load a stored run snapshot by ID or latest complete run."""
    settings = settings or get_settings()
    db_path = db_path or settings.db_path

    if run_id:
        return load_run_result(run_id, db_path=db_path)

    from app.run_store import load_latest_run_result

    return load_latest_run_result(db_path=db_path)
