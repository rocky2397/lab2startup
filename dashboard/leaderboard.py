"""Leaderboard helpers for highest-potential researcher views."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.models import Paper, Report, Researcher, SignalType, VCAction
from app.report_generator import RECOMMENDATION_LABELS
from dashboard.context_ui import (
    format_conference_year_label,
    infer_region_hint,
    researcher_paper_context,
)
from dashboard.filters import filter_researcher_reports, researcher_id_from_report_id


@dataclass(frozen=True)
class LeaderboardEntry:
    """One row in the top-prospects leaderboard."""

    rank: int
    report: Report
    researcher: Researcher | None
    top_signal_label: str
    top_signal_url: str | None
    conference_year: str = "—"
    region: str | None = None


def _top_signal_summary(report: Report) -> tuple[str, str | None]:
    if not report.signals:
        return "No signals", None

    type_priority = {
        SignalType.CONFIRMED_FOUNDER: 0,
        SignalType.POSSIBLE_FOUNDER: 1,
        SignalType.COMMERCIALIZATION: 2,
        SignalType.NO_SIGNAL: 3,
    }
    strength_priority = {"high": 0, "medium": 1, "low": 2}
    best = sorted(
        report.signals,
        key=lambda signal: (
            type_priority.get(signal.signal_type, 9),
            strength_priority.get(signal.evidence_strength.value, 9),
        ),
    )[0]
    label = best.signal_type.value.replace("_", " ").title()
    return label, best.source_url


def build_leaderboard_entries(
    reports: list[Report],
    researchers: list[Researcher],
    papers: list[Paper],
    *,
    top_n: int = 10,
    conference: str | None = None,
    year: int | None = None,
    topic: str | None = None,
) -> list[LeaderboardEntry]:
    """Return the top-N researcher reports ranked by startup likelihood score."""
    ranked = filter_researcher_reports(
        reports,
        researchers,
        papers,
        min_score=0,
        conference=conference,
        year=year,
        topic=topic,
    )
    researchers_by_id = {researcher.id: researcher for researcher in researchers}
    papers_by_id = {paper.id: paper for paper in papers}

    entries: list[LeaderboardEntry] = []
    for index, report in enumerate(ranked[:top_n], start=1):
        researcher_id = researcher_id_from_report_id(report.id)
        researcher = researchers_by_id.get(researcher_id) if researcher_id else None
        signal_label, signal_url = _top_signal_summary(report)
        conference_year = "—"
        region = None
        if researcher:
            ctx = researcher_paper_context(researcher, papers_by_id)
            conference_year = format_conference_year_label(
                ctx["conferences"],  # type: ignore[arg-type]
                ctx["years"],  # type: ignore[arg-type]
            )
            region = infer_region_hint(researcher.affiliation)
        entries.append(
            LeaderboardEntry(
                rank=index,
                report=report,
                researcher=researcher,
                top_signal_label=signal_label,
                top_signal_url=signal_url,
                conference_year=conference_year,
                region=region,
            )
        )
    return entries


def leaderboard_dataframe(entries: list[LeaderboardEntry]) -> pd.DataFrame:
    """Build a display-friendly dataframe for the leaderboard table."""
    rows = []
    for entry in entries:
        researcher = entry.researcher
        rows.append(
            {
                "Rank": entry.rank,
                "Name": entry.report.researcher_or_cluster,
                "Score": entry.report.startup_likelihood_score,
                "Conference / year": entry.conference_year,
                "Affiliation": researcher.affiliation if researcher else "—",
                "Region": entry.region or "—",
                "Role": researcher.role if researcher else "—",
                "Recommendation": RECOMMENDATION_LABELS[entry.report.recommendation],
                "Signals": len(entry.report.signals),
                "Top signal": entry.top_signal_label,
                "Report ID": entry.report.id,
            }
        )
    return pd.DataFrame(rows)


def count_by_recommendation(reports: list[Report]) -> dict[str, int]:
    """Count researcher reports grouped by VC recommendation."""
    counts = {label: 0 for label in RECOMMENDATION_LABELS.values()}
    for report in reports:
        if not report.id.startswith("report_researcher_"):
            continue
        label = RECOMMENDATION_LABELS[report.recommendation]
        counts[label] = counts.get(label, 0) + 1
    return counts


def take_meeting_reports(
    reports: list[Report],
    researchers: list[Researcher],
    papers: list[Paper],
    *,
    conference: str | None = None,
    year: int | None = None,
    topic: str | None = None,
) -> list[Report]:
    """Return researcher reports recommended for a meeting, highest score first."""
    return filter_researcher_reports(
        reports,
        researchers,
        papers,
        min_score=0,
        recommendation=VCAction.TAKE_MEETING.value,
        conference=conference,
        year=year,
        topic=topic,
    )
