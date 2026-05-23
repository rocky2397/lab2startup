"""Founder-monitoring report generation (Step 7)."""

from __future__ import annotations

from app.models import (
    Cluster,
    IdentityConfidence,
    Paper,
    PriorityBand,
    Report,
    Researcher,
    ScoreBreakdown,
    Signal,
    SignalType,
    VCAction,
)
from app.scoring import EntityScore


PRIORITY_LABELS = {
    PriorityBand.HIGH_PRIORITY: "High priority",
    PriorityBand.MONITOR_CLOSELY: "Monitor closely",
    PriorityBand.WEAK_SIGNAL: "Weak signal",
    PriorityBand.LOW_PRIORITY: "Low priority",
}

RECOMMENDATION_LABELS = {
    VCAction.TAKE_MEETING: "Take meeting",
    VCAction.MONITOR_MONTHLY: "Monitor monthly",
    VCAction.ADD_TO_WATCHLIST: "Add to watchlist",
    VCAction.IGNORE_FOR_NOW: "Ignore for now",
}

SIGNAL_TYPE_LABELS = {
    SignalType.CONFIRMED_FOUNDER: "Confirmed founder",
    SignalType.POSSIBLE_FOUNDER: "Possible founder",
    SignalType.COMMERCIALIZATION: "Commercialization",
    SignalType.NO_SIGNAL: "No signal",
}


def _format_recommendation(recommendation: VCAction) -> str:
    return RECOMMENDATION_LABELS[recommendation]


def _format_priority(priority_band: PriorityBand) -> str:
    return PRIORITY_LABELS[priority_band]


def _paper_titles(researcher: Researcher, papers_by_id: dict[str, Paper]) -> list[str]:
    return [papers_by_id[paper_id].title for paper_id in researcher.papers if paper_id in papers_by_id]


def _build_open_questions(
    entity_score: EntityScore,
    researcher: Researcher | None,
    signals: list[Signal],
) -> list[str]:
    """Suggest follow-up diligence questions based on profile gaps."""
    questions: list[str] = []

    if researcher and researcher.identity_confidence != IdentityConfidence.HIGH:
        questions.append(
            "Verify identity match: "
            + researcher.identity_confidence_explanation
        )

    if not signals:
        questions.append("No public commercialization signals found yet. Monitor for new project pages or founder announcements.")
    elif all(signal.signal_type == SignalType.NO_SIGNAL for signal in signals):
        questions.append("Public activity appears academic-only. Confirm whether any stealth startup involvement exists.")

    if entity_score.score_breakdown.commercialization_signal_strength < 10:
        questions.append("What is the clearest path from recent research to a product or company?")

    if entity_score.score_breakdown.open_source_or_project_momentum < 5:
        questions.append("Is there an open-source repo, demo, or product page beyond conference publications?")

    if entity_score.startup_likelihood_score >= 60 and not any(
        signal.signal_type == SignalType.CONFIRMED_FOUNDER for signal in signals
    ):
        questions.append("Is there direct evidence of a founding role, or only adjacent commercialization activity?")

    if not questions:
        questions.append("Validate signal sources and check for recent affiliation or company-domain changes.")

    return questions


def _build_summary(
    entity_score: EntityScore,
    researcher: Researcher | None,
    cluster: Cluster | None,
    signals: list[Signal],
    paper_titles: list[str],
) -> str:
    """Write a short narrative summary for the report."""
    score = entity_score.startup_likelihood_score
    recommendation = _format_recommendation(entity_score.recommendation)

    if researcher:
        subject = f"{researcher.name} ({researcher.affiliation})"
        paper_phrase = (
            f"Recent work includes {paper_titles[0]}."
            if len(paper_titles) == 1
            else f"Recent work spans {len(paper_titles)} tracked papers, including {paper_titles[0]}."
        )
    else:
        subject = cluster.name if cluster else entity_score.entity_name
        paper_phrase = (
            f"The team shares {len(cluster.shared_papers)} tracked paper(s) on {cluster.topic}."
            if cluster
            else "This cluster has shared publication history in the dataset."
        )

    signal_phrase = (
        f"{len(signals)} public signal(s) were detected."
        if signals
        else "No public commercialization signals were detected."
    )

    scholar_phrase = ""
    if researcher and researcher.semantic_scholar_id:
        scholar_phrase = (
            f" Semantic Scholar profile: h-index {researcher.h_index or 'n/a'}, "
            f"{researcher.citation_count or 0} total citations."
        )

    openreview_phrase = ""
    if researcher and researcher.openreview_url:
        openreview_phrase = f" OpenReview profile: {researcher.openreview_url}."

    return (
        f"{subject} received a startup likelihood score of {score}/100. "
        f"{paper_phrase} {signal_phrase}{scholar_phrase}{openreview_phrase} "
        f"Recommended VC action: {recommendation}."
    )


def build_researcher_report(
    entity_score: EntityScore,
    researcher: Researcher,
    signals: list[Signal],
    papers_by_id: dict[str, Paper],
) -> Report:
    """Build a structured report for one researcher."""
    return Report(
        id=f"report_{entity_score.entity_id}",
        researcher_or_cluster=researcher.name,
        summary=_build_summary(
            entity_score,
            researcher,
            None,
            signals,
            _paper_titles(researcher, papers_by_id),
        ),
        signals=signals,
        score_breakdown=entity_score.score_breakdown,
        startup_likelihood_score=entity_score.startup_likelihood_score,
        priority_band=entity_score.priority_band,
        recommendation=entity_score.recommendation,
        open_questions=_build_open_questions(entity_score, researcher, signals),
    )


def build_cluster_report(
    entity_score: EntityScore,
    cluster: Cluster,
    member_names: list[str],
    signals: list[Signal],
) -> Report:
    """Build a structured report for one coauthor cluster."""
    pseudo_researcher = Researcher(
        id=cluster.id,
        name=cluster.name,
        affiliation=cluster.topic,
        role="Research team",
    )
    return Report(
        id=f"report_{entity_score.entity_id}",
        researcher_or_cluster=cluster.name,
        summary=_build_summary(entity_score, None, cluster, signals, []),
        signals=signals,
        score_breakdown=entity_score.score_breakdown,
        startup_likelihood_score=entity_score.startup_likelihood_score,
        priority_band=entity_score.priority_band,
        recommendation=entity_score.recommendation,
        open_questions=_build_open_questions(entity_score, pseudo_researcher, signals)
        + ([f"Team members: {', '.join(member_names)}."] if member_names else []),
    )


def render_score_breakdown_markdown(breakdown: ScoreBreakdown) -> str:
    """Render score components as a markdown table."""
    rows = [
        ("Research quality", breakdown.research_quality, 20),
        ("Applied relevance", breakdown.applied_relevance, 20),
        ("Team continuity", breakdown.team_continuity, 15),
        ("Open source / project momentum", breakdown.open_source_or_project_momentum, 15),
        ("Commercialization signal strength", breakdown.commercialization_signal_strength, 20),
        ("Recency", breakdown.recency, 10),
    ]
    lines = ["| Component | Score | Max |", "|---|---:|---:|"]
    lines.extend(f"| {label} | {score} | {max_score} |" for label, score, max_score in rows)
    lines.append(f"| **Total** | **{breakdown.startup_likelihood_score}** | **100** |")
    return "\n".join(lines)


def render_signals_markdown(signals: list[Signal]) -> str:
    """Render detected signals as markdown bullets."""
    if not signals:
        return "_No signals attached._"

    lines: list[str] = []
    for signal in signals:
        label = SIGNAL_TYPE_LABELS[signal.signal_type]
        lines.append(
            f"- **{label}** ({signal.evidence_strength.value}): {signal.description} "
            f"[Source]({signal.source_url})"
        )
    return "\n".join(lines)


def render_report_markdown(report: Report) -> str:
    """Render a full founder-monitoring report as markdown."""
    lines = [
        f"# Founder Monitoring Report: {report.researcher_or_cluster}",
        "",
        "## Summary",
        report.summary,
        "",
        "## Score",
        f"- **Startup likelihood:** {report.startup_likelihood_score}/100",
        f"- **Priority band:** {_format_priority(report.priority_band)}",
        f"- **Recommended action:** {_format_recommendation(report.recommendation)}",
        "",
        "## Score Breakdown",
        render_score_breakdown_markdown(report.score_breakdown),
        "",
        "## Detected Signals",
        render_signals_markdown(report.signals),
        "",
        "## Open Questions",
    ]
    lines.extend(f"- {question}" for question in report.open_questions)
    return "\n".join(lines) + "\n"
