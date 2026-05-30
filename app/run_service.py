"""Execute and persist full pipeline runs (Step 12)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.agents.report_agent import ReportResult, run_reports
from app.config import AgenticSignalConfig, AppSettings, get_settings
from app.fund_profiles import (
    FundProfile,
    filter_papers_for_fund,
    resolve_paper_source_for_fund,
    validate_conference_for_fund,
)
from app.integrations.openalex import OpenAlexFetchConfig
from app.integrations.openreview import OpenReviewConfig
from app.models import Paper, PipelineRun
from app.run_store import (
    create_run_record,
    find_latest_run_with_papers,
    get_run,
    load_papers_from_run,
    load_researchers_from_run,
    load_run_result,
    make_run_id,
    mark_run_failed,
    mark_run_running,
    save_enrichment_audit,
    save_run_snapshot,
)

logger = logging.getLogger(__name__)


def _run_post_pipeline_agents(
    *,
    run_id: str,
    result: ReportResult,
    conference: str,
    year: int,
    paper_source: str,
    fund: FundProfile | None,
    stored_fund_profile: str | None,
    settings: AppSettings,
    db_path: Path | str,
) -> None:
    """Run thesis fit and diff agents after snapshot persistence."""
    if settings.thesis_fit_enabled and fund and fund.thesis_fit:
        from app.agents.thesis_fit_agent import run_thesis_fit_agent
        from app.thesis_fit_store import save_thesis_fit

        assessments = run_thesis_fit_agent(
            result,
            fund=fund,
            settings=settings,
            perplexity_config=settings.perplexity_config,
            sonar_min_score=settings.thesis_sonar_min_score,
            sonar_max_calls=settings.thesis_sonar_max_calls,
            use_sonar=bool(settings.perplexity_config.api_key),
        )
        if assessments:
            save_thesis_fit(run_id, assessments, db_path=db_path)
            logger.info("Thesis fit saved for %s (%s researchers)", run_id, len(assessments))

    if settings.diff_enabled:
        from app.agents.diff_agent import compute_run_diff
        from app.run_diff_store import save_run_diff
        from app.run_store import find_prior_complete_run, get_run, load_run_result

        current_run = get_run(run_id, db_path=db_path)
        prior_run = None
        prior_result = None
        if current_run:
            prior_run = find_prior_complete_run(
                conference=conference,
                year=year,
                paper_source=paper_source,
                fund_profile=stored_fund_profile,
                exclude_run_id=run_id,
                before_created_at=current_run.created_at,
                db_path=db_path,
            )
            if prior_run:
                prior_result = load_run_result(prior_run.id, db_path=db_path)

        diff = compute_run_diff(
            result,
            prior_result,
            run_id=run_id,
            prior_run_id=prior_run.id if prior_run else None,
            conference=conference,
            year=year,
            fund_profile=stored_fund_profile,
        )
        save_run_diff(run_id, diff, db_path=db_path)
        logger.info(
            "Run diff saved for %s (%s deltas vs %s)",
            run_id,
            diff.summary.total_deltas,
            prior_run.id if prior_run else "none",
        )


@dataclass(frozen=True)
class PaperFetchResult:
    papers: list[Paper] | None
    reused_from_run_id: str | None = None


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


def _agentic_config_record(config: AgenticSignalConfig) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "max_agent_calls": config.max_agent_calls,
        "max_total_steps": config.max_total_steps,
        "early_exit": config.early_exit,
        "deep_slots": config.deep_slots,
        "standard_slots": config.standard_slots,
    }


def _redact_integration_config(config: object) -> dict[str, object]:
    """Serialize integration config for SQLite without persisting secrets."""
    data = asdict(config)  # type: ignore[arg-type]
    if data.get("api_key"):
        data["api_key"] = "***redacted***"
    return data


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
            max_results=settings.openalex_config.max_results if settings.openalex_config else 50,
            mailto=settings.openalex_config.mailto if settings.openalex_config else None,
        )
    elif paper_source == "openreview":
        base_or = settings.openreview_config
        openreview_config = OpenReviewConfig(
            enabled=True,
            fetch_as_source=True,
            conference=conference,
            year=year,
            max_results=base_or.max_results if base_or else 50,
            accepted_only=True,
            fetch_profiles=base_or.fetch_profiles if base_or else False,
            request_delay_seconds=base_or.request_delay_seconds if base_or else 1.0,
            max_retries=base_or.max_retries if base_or else 6,
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
        "agentic_signal_config": settings.agentic_signal_config,
        "use_mock_signals": settings.use_mock_signals,
        "topic_scores": settings.topic_scores,
        "papers_path": settings.papers_path,
        "signals_path": settings.signals_path,
    }


def _fetch_papers_for_run(
    configs: dict[str, object],
    fund: FundProfile | None,
    *,
    db_path: Path | str | None = None,
    force_refetch: bool = False,
    fund_profile: str | None = None,
) -> PaperFetchResult:
    """Fetch and optionally fund-filter papers before running the pipeline."""
    paper_source = configs["paper_source"]
    if paper_source == "openreview" and not force_refetch and db_path is not None:
        # Snapshots store post-fund-filter papers; match fund_profile when reusing.
        prior = find_latest_run_with_papers(
            conference=str(configs["conference"]),
            year=int(configs["year"]),  # type: ignore[arg-type]
            paper_source=paper_source,
            fund_profile=fund_profile,
            db_path=db_path,
        )
        if prior is not None:
            papers = load_papers_from_run(prior.id, db_path=db_path)
            if papers:
                logger.info(
                    "Reusing %s papers from prior run %s (%s %s via %s)",
                    len(papers),
                    prior.id,
                    prior.conference,
                    prior.year,
                    paper_source,
                )
                return PaperFetchResult(papers=papers, reused_from_run_id=prior.id)

    if paper_source == "openreview":
        from app.integrations.openreview import fetch_papers_from_openreview

        papers = fetch_papers_from_openreview(configs["openreview_config"])  # type: ignore[arg-type]
    elif paper_source == "openalex":
        from app.integrations.openalex import fetch_papers_from_openalex

        papers = fetch_papers_from_openalex(configs["openalex_config"])  # type: ignore[arg-type]
    else:
        return PaperFetchResult(papers=None)

    if fund:
        before = len(papers)
        papers = filter_papers_for_fund(papers, fund)
        logger.info(
            "Fund filter kept %s/%s papers for %s",
            len(papers),
            before,
            fund.name,
        )
    return PaperFetchResult(papers=papers)


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

    base_or = settings.openreview_config
    if base_or is not None and not base_or.fetch_profiles:
        return None

    return OpenReviewConfig(
        enabled=True,
        fetch_as_source=False,
        conference=conference,
        year=year,
        max_results=base_or.max_results if base_or else 50,
        accepted_only=True,
        fetch_profiles=base_or.fetch_profiles if base_or else False,
        request_delay_seconds=base_or.request_delay_seconds if base_or else 1.0,
        max_retries=base_or.max_retries if base_or else 6,
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
    force_refetch: bool | None = None,
) -> tuple[PipelineRun, ReportResult]:
    """Run the full pipeline and persist the snapshot to SQLite."""
    settings = settings or get_settings()
    db_path = db_path or settings.db_path
    force_refetch = settings.force_paper_refetch if force_refetch is None else force_refetch
    fund = _resolve_fund(fund_profile, settings)
    stored_fund_profile = fund.id if fund else fund_profile

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
            "semantic_scholar": _redact_integration_config(
                configs["semantic_scholar_config"]  # type: ignore[arg-type]
            ),
            "github": _redact_integration_config(configs["github_config"]),  # type: ignore[arg-type]
            "perplexity": _redact_integration_config(configs["perplexity_config"]),  # type: ignore[arg-type]
            "agentic_signals": _agentic_config_record(
                configs["agentic_signal_config"]  # type: ignore[arg-type]
            ),
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
        paper_fetch = _fetch_papers_for_run(
            configs,
            fund,
            db_path=db_path,
            force_refetch=force_refetch,
            fund_profile=stored_fund_profile,
        )
        cached_researchers = None
        if paper_fetch.reused_from_run_id:
            cached_researchers = load_researchers_from_run(
                paper_fetch.reused_from_run_id,
                db_path=db_path,
            )
            if cached_researchers:
                logger.info(
                    "Reusing enrichment for %s researchers from prior run %s",
                    len(cached_researchers),
                    paper_fetch.reused_from_run_id,
                )
        openreview_config = _enrich_openreview_config(
            configs,
            conference=conference,
            year=year,
            settings=settings,
        )

        agentic_config: AgenticSignalConfig = configs["agentic_signal_config"]  # type: ignore[assignment]
        if agentic_config.enabled:
            agentic_config = replace(agentic_config, db_path=Path(db_path))

        result = run_reports(
            papers_path=papers_path,
            signals_path=signals_path,
            papers=paper_fetch.papers,
            cached_researchers=cached_researchers,
            openalex_config=configs["openalex_config"],  # type: ignore[arg-type]
            openreview_config=openreview_config,
            semantic_scholar_config=configs["semantic_scholar_config"],  # type: ignore[arg-type]
            github_config=configs["github_config"],  # type: ignore[arg-type]
            perplexity_config=configs["perplexity_config"],  # type: ignore[arg-type]
            agentic_signal_config=agentic_config,
            use_mock_signals=bool(configs["use_mock_signals"]),
            topic_scores=configs["topic_scores"],  # type: ignore[arg-type]
            run_id=run_id,
            conference=conference,
            year=year,
            include_clusters=include_clusters,
        )
        save_run_snapshot(run_id, result, db_path=db_path)
        _run_post_pipeline_agents(
            run_id=run_id,
            result=result,
            conference=conference,
            year=year,
            paper_source=paper_source,
            fund=fund,
            stored_fund_profile=stored_fund_profile,
            settings=settings,
            db_path=db_path,
        )
        detection = result.scoring.detection
        if detection.enrichment_audit is not None:
            save_enrichment_audit(run_id, detection.enrichment_audit, db_path=db_path)
            from app.enrichment_audit import summarize_enrichment_audit

            audit_summary = summarize_enrichment_audit(detection.enrichment_audit)
            logger.info("Enrichment audit for %s: %s", run_id, audit_summary)
            enriched_lines = audit_summary.get("enriched_profile_lines") or []
            if enriched_lines:
                logger.info(
                    "Enriched profiles for %s: %s",
                    run_id,
                    "; ".join(enriched_lines),
                )
            investigated = audit_summary.get("investigated_profile_names") or []
            if investigated:
                logger.info(
                    "Investigated profiles for %s: %s",
                    run_id,
                    ", ".join(investigated),
                )
        if agentic_config.enabled:
            from app.agent_trace_store import summarize_run_traces

            trace_summary = summarize_run_traces(run_id, db_path=db_path)
            logger.info("Agentic trace summary for %s: %s", run_id, trace_summary)
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


def execute_batch_pipeline_runs(
    *,
    conferences: list[str],
    year: int,
    paper_source: str | None = None,
    fund_profile: str | None = None,
    topics: list[str] | None = None,
    db_path: Path | str | None = None,
    settings: AppSettings | None = None,
    include_clusters: bool = True,
    force_refetch: bool | None = None,
) -> list[tuple[PipelineRun, ReportResult]]:
    """Run the pipeline for multiple conferences sequentially."""
    results: list[tuple[PipelineRun, ReportResult]] = []
    failures: list[tuple[str, str]] = []
    for conference in conferences:
        logger.info("Starting batch run for %s %s", conference, year)
        try:
            run, result = execute_pipeline_run(
                conference=conference,
                year=year,
                paper_source=paper_source,
                fund_profile=fund_profile,
                topics=topics,
                db_path=db_path,
                settings=settings,
                include_clusters=include_clusters,
                force_refetch=force_refetch,
            )
            results.append((run, result))
        except Exception as exc:
            logger.exception("Batch run failed for %s %s", conference, year)
            failures.append((conference, str(exc)))
    if failures and not results:
        failed = ", ".join(f"{name}: {msg}" for name, msg in failures[:3])
        raise RuntimeError(f"All batch runs failed. {failed}") from None
    if failures:
        logger.warning(
            "Batch completed with %s failure(s): %s",
            len(failures),
            ", ".join(name for name, _ in failures),
        )
    return results


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
