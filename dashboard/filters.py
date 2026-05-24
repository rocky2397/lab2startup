"""Filtering helpers for the Streamlit dashboard."""

from __future__ import annotations

from app.models import Cluster, Paper, Report, Researcher, VCAction


def researcher_id_from_report_id(report_id: str) -> str | None:
    """Extract researcher ID from a report ID."""
    if report_id.startswith("report_researcher_"):
        return report_id.removeprefix("report_")
    return None


def filter_researcher_reports(
    reports: list[Report],
    researchers: list[Researcher],
    papers: list[Paper],
    *,
    min_score: int = 0,
    recommendation: str | None = None,
    conference: str | None = None,
    year: int | None = None,
    topic: str | None = None,
) -> list[Report]:
    """Filter researcher reports using score and paper metadata."""
    researchers_by_id = {researcher.id: researcher for researcher in researchers}
    papers_by_id = {paper.id: paper for paper in papers}

    filtered: list[Report] = []
    for report in reports:
        if not report.id.startswith("report_researcher_"):
            continue
        if report.startup_likelihood_score < min_score:
            continue
        if recommendation and report.recommendation.value != recommendation:
            continue

        researcher_id = researcher_id_from_report_id(report.id)
        if not researcher_id or researcher_id not in researchers_by_id:
            continue

        researcher = researchers_by_id[researcher_id]
        researcher_papers = [
            papers_by_id[paper_id]
            for paper_id in researcher.papers
            if paper_id in papers_by_id
        ]

        if conference and not any(
            paper.conference.lower() == conference.lower() for paper in researcher_papers
        ):
            continue
        if year is not None and not any(paper.year == year for paper in researcher_papers):
            continue
        if topic and not any(paper.topic.lower() == topic.lower() for paper in researcher_papers):
            continue

        filtered.append(report)

    return sorted(
        filtered,
        key=lambda report: (-report.startup_likelihood_score, report.researcher_or_cluster),
    )


def filter_cluster_reports(
    reports: list[Report],
    clusters: list[Cluster],
    *,
    min_score: int = 0,
    recommendation: str | None = None,
    topic: str | None = None,
) -> list[Report]:
    """Filter cluster reports using score, recommendation, and topic."""
    clusters_by_id = {cluster.id: cluster for cluster in clusters}
    filtered: list[Report] = []

    for report in reports:
        if not report.id.startswith("report_cluster_"):
            continue
        if report.startup_likelihood_score < min_score:
            continue
        if recommendation and report.recommendation.value != recommendation:
            continue

        cluster_id = report.id.removeprefix("report_")
        cluster = clusters_by_id.get(cluster_id)
        if topic and cluster and cluster.topic.lower() != topic.lower():
            continue

        filtered.append(report)

    return sorted(
        filtered,
        key=lambda report: (-report.startup_likelihood_score, report.researcher_or_cluster),
    )


def recommendation_options() -> list[tuple[str, str]]:
    """Return display/value pairs for recommendation filters."""
    labels = {
        VCAction.TAKE_MEETING: "Take meeting",
        VCAction.MONITOR_MONTHLY: "Monitor monthly",
        VCAction.ADD_TO_WATCHLIST: "Add to watchlist",
        VCAction.IGNORE_FOR_NOW: "Ignore for now",
    }
    return [(labels[action], action.value) for action in VCAction]


def count_researcher_reports(
    reports: list[Report],
    *,
    min_score: int = 0,
) -> int:
    """Count researcher reports at or above a score threshold."""
    return sum(
        1
        for report in reports
        if report.id.startswith("report_researcher_")
        and report.startup_likelihood_score >= min_score
    )


def diagnose_filter_miss(
    reports: list[Report],
    researchers: list[Researcher],
    papers: list[Paper],
    *,
    min_score: int,
    recommendation: str | None = None,
    conference: str | None = None,
    year: int | None = None,
    topic: str | None = None,
) -> dict[str, int | str | None]:
    """Explain why sidebar filters may hide all candidates."""
    researcher_reports = [
        report for report in reports if report.id.startswith("report_researcher_")
    ]
    total = len(researcher_reports)
    above_min = count_researcher_reports(reports, min_score=min_score)

    without_score = filter_researcher_reports(
        reports,
        researchers,
        papers,
        min_score=0,
        recommendation=recommendation,
        conference=conference,
        year=year,
        topic=topic,
    )
    without_rec = filter_researcher_reports(
        reports,
        researchers,
        papers,
        min_score=min_score,
        conference=conference,
        year=year,
        topic=topic,
    )

    return {
        "total_researchers": total,
        "above_min_score": above_min,
        "after_metadata_filters": len(without_score),
        "after_all_filters": len(without_rec),
        "conference_filter": conference,
        "year_filter": year,
        "topic_filter": topic,
        "min_score": min_score,
    }
