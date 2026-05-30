"""Diff agent — compare pipeline runs without LLM calls (Step 16)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.agents.report_agent import ReportResult
from app.models import Report, Researcher, Signal, VCAction
from app.run_diff_models import ResearcherDelta, RunDiff, summarize_deltas


def _report_by_researcher_id(reports: list[Report]) -> dict[str, Report]:
    mapping: dict[str, Report] = {}
    for report in reports:
        if report.id.startswith("report_researcher_"):
            researcher_id = report.id.removeprefix("report_")
            mapping[researcher_id] = report
    return mapping


def _researchers_by_id(researchers: list[Researcher]) -> dict[str, Researcher]:
    return {researcher.id: researcher for researcher in researchers}


def _signal_urls_by_researcher(signals: list[Signal]) -> dict[str, set[str]]:
    by_researcher: dict[str, set[str]] = {}
    for signal in signals:
        if not signal.researcher_id:
            continue
        by_researcher.setdefault(signal.researcher_id, set()).add(signal.source_url)
    return by_researcher


def compute_run_diff(
    current: ReportResult,
    prior: ReportResult | None,
    *,
    run_id: str,
    prior_run_id: str | None = None,
    conference: str,
    year: int,
    fund_profile: str | None = None,
    score_threshold: int = 5,
) -> RunDiff:
    """Compare two report snapshots and return structured deltas."""
    computed_at = datetime.now(UTC)
    if prior is None:
        return RunDiff(
            run_id=run_id,
            prior_run_id=prior_run_id,
            conference=conference,
            year=year,
            fund_profile=fund_profile,
            computed_at=computed_at,
            deltas=[],
            summary=summarize_deltas([]),
        )

    current_detection = current.scoring.detection
    prior_detection = prior.scoring.detection

    current_reports = _report_by_researcher_id(current.reports)
    prior_reports = _report_by_researcher_id(prior.reports)
    current_researchers = _researchers_by_id(current_detection.researchers)
    prior_researchers = _researchers_by_id(prior_detection.researchers)

    current_signals = _signal_urls_by_researcher(current_detection.signals)
    prior_signals = _signal_urls_by_researcher(prior_detection.signals)

    deltas: list[ResearcherDelta] = []

    for researcher_id, report in current_reports.items():
        researcher = current_researchers.get(researcher_id)
        name = researcher.name if researcher else report.researcher_or_cluster
        prior_report = prior_reports.get(researcher_id)
        prior_researcher = prior_researchers.get(researcher_id)

        if prior_report is None:
            deltas.append(
                ResearcherDelta(
                    researcher_id=researcher_id,
                    name=name,
                    change_type="new_researcher",
                    before=None,
                    after=report.startup_likelihood_score,
                    detail=f"New researcher in run (score {report.startup_likelihood_score})",
                )
            )
            if report.recommendation == VCAction.TAKE_MEETING:
                deltas.append(
                    ResearcherDelta(
                        researcher_id=researcher_id,
                        name=name,
                        change_type="new_take_meeting",
                        before=None,
                        after=report.recommendation.value,
                        detail="Recommendation is Take meeting",
                    )
                )
            continue

        score_delta = report.startup_likelihood_score - prior_report.startup_likelihood_score
        if score_delta >= score_threshold:
            deltas.append(
                ResearcherDelta(
                    researcher_id=researcher_id,
                    name=name,
                    change_type="score_increased",
                    before=prior_report.startup_likelihood_score,
                    after=report.startup_likelihood_score,
                    detail=f"Score increased by {score_delta} ({prior_report.startup_likelihood_score} → {report.startup_likelihood_score})",
                )
            )
        elif score_delta <= -score_threshold:
            deltas.append(
                ResearcherDelta(
                    researcher_id=researcher_id,
                    name=name,
                    change_type="score_decreased",
                    before=prior_report.startup_likelihood_score,
                    after=report.startup_likelihood_score,
                    detail=f"Score decreased by {abs(score_delta)} ({prior_report.startup_likelihood_score} → {report.startup_likelihood_score})",
                )
            )

        if report.recommendation != prior_report.recommendation:
            deltas.append(
                ResearcherDelta(
                    researcher_id=researcher_id,
                    name=name,
                    change_type="recommendation_changed",
                    before=prior_report.recommendation.value,
                    after=report.recommendation.value,
                    detail=f"Recommendation {prior_report.recommendation.value} → {report.recommendation.value}",
                )
            )
            if (
                report.recommendation == VCAction.TAKE_MEETING
                and prior_report.recommendation != VCAction.TAKE_MEETING
            ):
                deltas.append(
                    ResearcherDelta(
                        researcher_id=researcher_id,
                        name=name,
                        change_type="new_take_meeting",
                        before=prior_report.recommendation.value,
                        after=report.recommendation.value,
                        detail="Now recommended Take meeting",
                    )
                )

        prior_urls = prior_signals.get(researcher_id, set())
        current_urls = current_signals.get(researcher_id, set())
        new_urls = current_urls - prior_urls
        for url in sorted(new_urls):
            deltas.append(
                ResearcherDelta(
                    researcher_id=researcher_id,
                    name=name,
                    change_type="new_signal",
                    before=None,
                    after=url,
                    detail=f"New signal source: {url}",
                )
            )

        if researcher and prior_researcher:
            if researcher.affiliation != prior_researcher.affiliation:
                deltas.append(
                    ResearcherDelta(
                        researcher_id=researcher_id,
                        name=name,
                        change_type="affiliation_changed",
                        before=prior_researcher.affiliation,
                        after=researcher.affiliation,
                        detail=f"Affiliation: {prior_researcher.affiliation} → {researcher.affiliation}",
                    )
                )
            if researcher.role != prior_researcher.role:
                deltas.append(
                    ResearcherDelta(
                        researcher_id=researcher_id,
                        name=name,
                        change_type="role_changed",
                        before=prior_researcher.role,
                        after=researcher.role,
                        detail=f"Role: {prior_researcher.role} → {researcher.role}",
                    )
                )

    return RunDiff(
        run_id=run_id,
        prior_run_id=prior_run_id,
        conference=conference,
        year=year,
        fund_profile=fund_profile,
        computed_at=computed_at,
        deltas=deltas,
        summary=summarize_deltas(deltas),
    )
