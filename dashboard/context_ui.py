"""Dashboard context helpers — run scope and researcher affiliation display."""

from __future__ import annotations

import streamlit as st

from app.models import Paper, PipelineRun, Report, Researcher
from app.region_hints import infer_region_hint  # re-exported for dashboard/tests

__all__ = ["infer_region_hint", "format_conference_year_label", "researcher_paper_context"]


def researcher_paper_context(
    researcher: Researcher,
    papers_by_id: dict[str, Paper],
) -> dict[str, object]:
    """Summarize conference/year/topic coverage for one researcher in the current run."""
    researcher_papers = [papers_by_id[paper_id] for paper_id in researcher.papers if paper_id in papers_by_id]
    conferences = sorted({paper.conference for paper in researcher_papers})
    years = sorted({paper.year for paper in researcher_papers}, reverse=True)
    topics = sorted({paper.topic for paper in researcher_papers})
    return {
        "papers": researcher_papers,
        "conferences": conferences,
        "years": years,
        "topics": topics,
        "paper_count": len(researcher_papers),
    }


def format_conference_year_label(
    conferences: list[str],
    years: list[int],
) -> str:
    """Compact label like 'NeurIPS 2024' or 'NeurIPS 2024 · ICML 2023'."""
    if not conferences and not years:
        return "Unknown conference / year"

    if len(conferences) == 1 and len(years) == 1:
        return f"{conferences[0]} {years[0]}"

    parts: list[str] = []
    if conferences:
        parts.append(", ".join(conferences))
    if years:
        parts.append(", ".join(str(year) for year in years))
    return " · ".join(parts)


def render_run_context_header(
    *,
    active_run: PipelineRun | None,
    papers: list[Paper],
    researcher_count: int,
    signal_count: int,
    conference_filter: str | None,
    year_filter: int | None,
    topic_filter: str | None,
) -> None:
    """Prominent banner: which conference/year run you are viewing."""
    run_conference = active_run.conference if active_run else (papers[0].conference if papers else "—")
    run_year = active_run.year if active_run else (papers[0].year if papers else "—")
    paper_source = active_run.paper_source if active_run else "—"

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Conference", conference_filter or run_conference)
    col2.metric("Year", year_filter or run_year)
    col3.metric("Researchers", researcher_count)
    col4.metric("Papers in run", len(papers))

    filter_bits: list[str] = []
    if conference_filter:
        filter_bits.append(f"conference **{conference_filter}**")
    if year_filter:
        filter_bits.append(f"year **{year_filter}**")
    if topic_filter:
        filter_bits.append(f"topic **{topic_filter}**")

    if active_run:
        scope = f"Stored run **{active_run.conference} {active_run.year}** via `{active_run.paper_source}`"
    else:
        scope = f"Live dataset · papers from **{run_conference} {run_year}** via `{paper_source}`"

    if filter_bits:
        st.caption(f"{scope} · Filtered by {', '.join(filter_bits)}.")
    else:
        st.caption(scope)


def render_researcher_context_card(
    *,
    researcher: Researcher,
    report: Report,
    papers_by_id: dict[str, Paper],
) -> None:
    """Clear profile header for the selected candidate."""
    context = researcher_paper_context(researcher, papers_by_id)
    region = infer_region_hint(researcher.affiliation)
    conference_year = format_conference_year_label(
        context["conferences"],  # type: ignore[arg-type]
        context["years"],  # type: ignore[arg-type]
    )

    st.markdown(f"### {report.researcher_or_cluster}")
    subtitle_parts = [researcher.affiliation or "Unknown affiliation", researcher.role or "Unknown role"]
    if region:
        subtitle_parts.append(region)
    st.markdown(" · ".join(subtitle_parts))

    info_col1, info_col2, info_col3 = st.columns(3)
    info_col1.metric("Conference / year", conference_year.split(" · ")[0][:40])
    if len(context["years"]) == 1:  # type: ignore[index]
        info_col2.metric("Paper year", context["years"][0])  # type: ignore[index]
    elif context["years"]:  # type: ignore[index]
        info_col2.metric("Paper years", f"{context['years'][-1]}–{context['years'][0]}")  # type: ignore[index]
    else:
        info_col2.metric("Paper year", "—")
    info_col3.metric("Papers in run", context["paper_count"])  # type: ignore[arg-type]

    if context["topics"]:  # type: ignore[index]
        topics = context["topics"]  # type: ignore[index]
        st.caption("Research topics: " + ", ".join(topics[:4]) + ("…" if len(topics) > 4 else ""))

    if conference_year and len(context["conferences"]) != 1:  # type: ignore[index]
        st.caption(f"Conference coverage: {conference_year}")


def render_researcher_quick_view(
    *,
    report: Report,
    researcher: Researcher | None,
    papers_by_id: dict[str, Paper],
    expanded: bool = True,
) -> None:
    """Compact profile card with signals — used from tables and leaderboard."""
    from app.report_generator import RECOMMENDATION_LABELS
    from dashboard.researcher_links_ui import render_researcher_profile_links

    title = f"Quick view — {report.researcher_or_cluster} ({report.startup_likelihood_score}/100)"
    with st.expander(title, expanded=expanded):
        if researcher:
            ctx = researcher_paper_context(researcher, papers_by_id)
            conference_year = format_conference_year_label(
                ctx["conferences"],  # type: ignore[arg-type]
                ctx["years"],  # type: ignore[arg-type]
            )
            region = infer_region_hint(researcher.affiliation)
            st.write(
                f"**Recommendation:** {RECOMMENDATION_LABELS[report.recommendation]}  \n"
                f"**Conference / year:** {conference_year}  \n"
                f"**Affiliation:** {researcher.affiliation}  \n"
                f"**Region:** {region or 'Unknown'}  \n"
                f"**Role:** {researcher.role}  \n"
                f"**Signals:** {len(report.signals)}"
            )
            render_researcher_profile_links(
                researcher,
                report.signals,
                label="Links",
            )
        else:
            st.write(
                f"**Recommendation:** {RECOMMENDATION_LABELS[report.recommendation]}  \n"
                f"**Signals:** {len(report.signals)}"
            )

        if report.signals:
            for signal in report.signals[:3]:
                st.markdown(
                    f"- **{signal.signal_type.value.replace('_', ' ').title()}** — "
                    f"{signal.description[:160]}{'…' if len(signal.description) > 160 else ''}  \n"
                    f"  [Source]({signal.source_url})"
                )
        elif researcher:
            st.caption("No commercialization signals detected for this researcher in this run.")

        st.caption("Open **Explore & details** for the full report and score breakdown.")
