"""Tests for dashboard filter helpers."""

from app.agents.report_agent import run_reports
from dashboard.filters import filter_researcher_reports, recommendation_options


def test_filter_researcher_reports_by_score_and_topic() -> None:
    result = run_reports(include_clusters=False)
    detection = result.scoring.detection

    filtered = filter_researcher_reports(
        result.reports,
        detection.researchers,
        detection.papers,
        min_score=70,
        topic="biotech AI",
    )

    assert filtered
    assert all(report.startup_likelihood_score >= 70 for report in filtered)
    assert any("Marinka" in report.researcher_or_cluster for report in filtered)


def test_filter_researcher_reports_by_year() -> None:
    result = run_reports(include_clusters=False)
    detection = result.scoring.detection

    filtered = filter_researcher_reports(
        result.reports,
        detection.researchers,
        detection.papers,
        year=2023,
    )

    assert filtered
    researcher_names = {report.researcher_or_cluster for report in filtered}
    assert "Shibo Hao" in researcher_names


def test_recommendation_options() -> None:
    options = recommendation_options()
    assert len(options) == 4
    assert any(value == "take_meeting" for _, value in options)
