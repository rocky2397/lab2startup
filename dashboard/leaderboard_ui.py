"""Streamlit UI for the highest-potential researcher leaderboard."""

from __future__ import annotations

import streamlit as st

from app.models import VCAction
from app.report_generator import RECOMMENDATION_LABELS
from dashboard.context_ui import (
    format_conference_year_label,
    infer_region_hint,
    researcher_paper_context,
)
from dashboard.leaderboard import (
    LeaderboardEntry,
    build_leaderboard_entries,
    count_by_recommendation,
    leaderboard_dataframe,
    take_meeting_reports,
)
from dashboard.researcher_links_ui import render_researcher_profile_links


def _recommendation_badge(recommendation: VCAction) -> str:
    badges = {
        VCAction.TAKE_MEETING: "🟢 Take meeting",
        VCAction.MONITOR_MONTHLY: "🟡 Monitor monthly",
        VCAction.ADD_TO_WATCHLIST: "🟠 Watchlist",
        VCAction.IGNORE_FOR_NOW: "⚪ Low priority",
    }
    return badges.get(recommendation, recommendation.value)


def render_top_prospect_cards(entries: list[LeaderboardEntry], *, columns: int = 3) -> None:
    """Render compact cards for the top few candidates."""
    if not entries:
        st.info("No ranked researchers yet. Run the pipeline to populate scores.")
        return

    podium = entries[: min(3, len(entries))]
    if len(podium) >= 1:
        cols = st.columns(len(podium))
        for col, entry in zip(cols, podium, strict=False):
            with col:
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(entry.rank, "▫️")
                st.markdown(f"### {medal} #{entry.rank} {entry.report.researcher_or_cluster}")
                st.metric("Startup score", entry.report.startup_likelihood_score)
                affiliation = entry.researcher.affiliation if entry.researcher else "Unknown"
                region = entry.region or infer_region_hint(affiliation)
                region_label = f" · {region}" if region else ""
                st.caption(f"{entry.conference_year} · {affiliation}{region_label} · {entry.top_signal_label}")
                st.write(_recommendation_badge(entry.report.recommendation))


def render_top_prospects_board(
    *,
    reports,
    researchers,
    papers,
    conference: str | None = None,
    year: int | None = None,
    topic: str | None = None,
) -> str | None:
    """Render the main highest-potential view. Returns selected report ID if any."""
    st.subheader("Highest potential researchers")
    st.caption(
        "Ranked by startup likelihood score — combine research quality, applied relevance, "
        "team network, Perplexity signals, and recency."
    )

    top_n = st.slider("Show top N researchers", min_value=5, max_value=25, value=10, step=1)
    entries = build_leaderboard_entries(
        reports,
        researchers,
        papers,
        top_n=top_n,
        conference=conference,
        year=year,
        topic=topic,
    )

    if not entries:
        st.warning("No researcher scores available for this run.")
        return None

    all_researcher_reports = [report for report in reports if report.id.startswith("report_researcher_")]
    rec_counts = count_by_recommendation(all_researcher_reports)
    metric_cols = st.columns(4)
    metric_cols[0].metric("Take meeting", rec_counts.get("Take meeting", 0))
    metric_cols[1].metric("Monitor monthly", rec_counts.get("Monitor monthly", 0))
    metric_cols[2].metric("Watchlist", rec_counts.get("Add to watchlist", 0))
    metric_cols[3].metric("Top score", entries[0].report.startup_likelihood_score)

    render_top_prospect_cards(entries)

    chart_df = leaderboard_dataframe(entries)
    st.bar_chart(
        chart_df.set_index("Name")["Score"],
        height=280,
    )

    meeting_ready = take_meeting_reports(
        reports,
        researchers,
        papers,
        conference=conference,
        year=year,
        topic=topic,
    )
    if meeting_ready:
        names = ", ".join(report.researcher_or_cluster for report in meeting_ready[:5])
        st.success(f"**Meeting-ready ({len(meeting_ready)}):** {names}")

    st.markdown("#### Full leaderboard")
    display_df = chart_df.drop(columns=["Report ID"])
    st.dataframe(display_df, width="stretch", hide_index=True)

    report_ids = [entry.report.id for entry in entries]
    selected_report_id = st.selectbox(
        "Open candidate profile",
        report_ids,
        format_func=lambda report_id: next(
            entry.report.researcher_or_cluster for entry in entries if entry.report.id == report_id
        ),
        key="top_prospect_select",
    )

    selected = next(entry for entry in entries if entry.report.id == selected_report_id)
    with st.expander(
        f"Quick view — {selected.report.researcher_or_cluster} ({selected.report.startup_likelihood_score}/100)",
        expanded=False,
    ):
        if selected.researcher:
            ctx = researcher_paper_context(
                selected.researcher,
                {paper.id: paper for paper in papers},
            )
            cy = format_conference_year_label(
                ctx["conferences"],  # type: ignore[arg-type]
                ctx["years"],  # type: ignore[arg-type]
            )
            region = infer_region_hint(selected.researcher.affiliation)
            st.write(
                f"**Recommendation:** {RECOMMENDATION_LABELS[selected.report.recommendation]}  \n"
                f"**Conference / year:** {cy}  \n"
                f"**Affiliation:** {selected.researcher.affiliation}  \n"
                f"**Region:** {region or 'Unknown'}  \n"
                f"**Role:** {selected.researcher.role}  \n"
                f"**Signals:** {len(selected.report.signals)}"
            )
            render_researcher_profile_links(
                selected.researcher,
                selected.report.signals,
                label="Links",
            )
        else:
            st.write(
                f"**Recommendation:** {RECOMMENDATION_LABELS[selected.report.recommendation]}  \n"
                f"**Signals:** {len(selected.report.signals)}"
            )
        if selected.report.signals:
            for signal in selected.report.signals[:3]:
                st.markdown(
                    f"- **{signal.signal_type.value.replace('_', ' ').title()}** — "
                    f"{signal.description[:160]}{'…' if len(signal.description) > 160 else ''}  \n"
                    f"  [Source]({signal.source_url})"
                )
        st.caption("Switch to **Explore & details** for full score breakdown and report.")

    return selected_report_id
