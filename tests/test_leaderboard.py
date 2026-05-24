"""Tests for dashboard leaderboard helpers."""

from app.agents.report_agent import run_reports
from dashboard.leaderboard import (
    build_leaderboard_entries,
    count_by_recommendation,
    leaderboard_dataframe,
    take_meeting_reports,
)


def test_build_leaderboard_entries_ranks_by_score() -> None:
    result = run_reports(include_clusters=False)
    detection = result.scoring.detection

    entries = build_leaderboard_entries(
        result.reports,
        detection.researchers,
        detection.papers,
        top_n=5,
    )

    assert len(entries) == 5
    assert entries[0].rank == 1
    assert entries[0].report.startup_likelihood_score >= entries[-1].report.startup_likelihood_score


def test_leaderboard_dataframe_includes_affiliation() -> None:
    result = run_reports(include_clusters=False)
    detection = result.scoring.detection
    entries = build_leaderboard_entries(
        result.reports,
        detection.researchers,
        detection.papers,
        top_n=3,
    )

    frame = leaderboard_dataframe(entries)
    assert "Affiliation" in frame.columns
    assert "Top signal" in frame.columns
    assert len(frame) == 3


def test_take_meeting_reports_filters_recommendation() -> None:
    result = run_reports(include_clusters=False)
    detection = result.scoring.detection

    meeting_reports = take_meeting_reports(
        result.reports,
        detection.researchers,
        detection.papers,
    )

    assert all(report.recommendation.value == "take_meeting" for report in meeting_reports)


def test_count_by_recommendation() -> None:
    result = run_reports(include_clusters=False)
    counts = count_by_recommendation(result.reports)

    assert sum(counts.values()) >= 1
    assert "Take meeting" in counts
