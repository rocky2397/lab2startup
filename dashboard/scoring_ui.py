"""Scoring methodology and expandable UI blocks for the dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

from app.models import Signal, SignalType
from dashboard.agent_trace_ui import signal_source_label

if TYPE_CHECKING:
    from app.fund_profiles import FundProfile

SIGNAL_TYPE_POINTS: dict[str, dict[str, int]] = {
    "confirmed_founder": {"high": 20, "medium": 16, "low": 12},
    "possible_founder": {"high": 14, "medium": 11, "low": 8},
    "commercialization": {"high": 12, "medium": 9, "low": 5},
}

SCORE_COMPONENTS: list[tuple[str, str, int, str]] = [
    (
        "Research quality",
        "research_quality",
        20,
        "Best paper score among the candidate's publications: conference tier (NeurIPS = 12, "
        "others = 8), recency bonus (+6 for 2024+, +4 for 2023+, +2 older), plus up to +2 from "
        "Semantic Scholar citations (100+ = +1, 500+ = +2). Capped at 20.",
    ),
    (
        "Applied relevance",
        "applied_relevance",
        20,
        "Highest topic score across the candidate's papers. Default topics: AI agents 18, "
        "robotics 17, biotech AI 19; unknown topics = 10. Fund profiles can "
        "override via `topic_scores` in the fund YAML.",
    ),
    (
        "Team continuity",
        "team_continuity",
        15,
        "Coauthor network size as a proxy for team-formation potential: 6+ coauthors = 15, "
        "4–5 = 12, 2–3 = 9, 1 = 6, solo = 3.",
    ),
    (
        "Project momentum",
        "open_source_or_project_momentum",
        15,
        "Points from `commercialization` signals only: GitHub URL +8, OpenReview/arXiv +5, "
        "otherwise +7/+5/+3 by evidence strength (high/medium/low). Capped at 15.",
    ),
    (
        "Signal strength",
        "commercialization_signal_strength",
        20,
        "Founder/commercialization evidence from attached signals (Perplexity, etc.). Each signal "
        "maps type × strength to points; total uses max(best single signal, sum of half-points). "
        "Capped at 20.",
    ),
    (
        "Recency",
        "recency",
        10,
        "Most recent paper year: 2024+ = 10, 2023 = 7, older = 4.",
    ),
]


def score_breakdown_dataframe(report) -> pd.DataFrame:
    """Convert a score breakdown into a chart-friendly dataframe."""
    breakdown = report.score_breakdown
    return pd.DataFrame(
        {
            "Component": [label for label, _, _, _ in SCORE_COMPONENTS],
            "Score": [
                breakdown.research_quality,
                breakdown.applied_relevance,
                breakdown.team_continuity,
                breakdown.open_source_or_project_momentum,
                breakdown.commercialization_signal_strength,
                breakdown.recency,
            ],
            "Max": [max_pts for _, _, max_pts, _ in SCORE_COMPONENTS],
        }
    )


def _format_signal_points(signal: Signal) -> str:
    points = SIGNAL_TYPE_POINTS.get(signal.signal_type.value, {}).get(signal.evidence_strength.value, 0)
    return f"{points} pts ({signal.signal_type.value}, {signal.evidence_strength.value})"


def _signal_expander_label(signal: Signal, index: int) -> str:
    signal_label = signal.signal_type.value.replace("_", " ").title()
    source = signal_source_label(signal.id)
    host = signal.source_url.split("/")[2] if "://" in signal.source_url else signal.source_url
    return f"{index}. [{source}] {signal_label} — {host}"


def render_scoring_methodology_expander(
    *,
    fund: FundProfile | None = None,
    topic_scores: dict[str, int] | None = None,
) -> None:
    """Expandable reference for how the 0–100 score is built."""
    with st.expander("How startup likelihood is calculated", expanded=False):
        st.markdown(
            "Each researcher (or cluster) gets a **rule-based score from 0–100**. "
            "Six components are summed, then a small **identity penalty** may apply "
            "when profile matching is uncertain (−3 medium, −6 low confidence)."
        )

        st.markdown("#### Components (max 100 before penalty)")
        for label, _field, max_pts, description in SCORE_COMPONENTS:
            st.markdown(f"**{label}** (0–{max_pts})  \n{description}")

        st.markdown("#### Signal type × evidence strength")
        rows = []
        for signal_type, strengths in SIGNAL_TYPE_POINTS.items():
            for strength, points in strengths.items():
                rows.append(
                    {
                        "Signal type": signal_type.replace("_", " "),
                        "Evidence": strength,
                        "Points": points,
                    }
                )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        st.markdown(
            "#### Priority bands & recommendations\n"
            "| Score | Band | VC action |\n"
            "|---|---|---|\n"
            "| 80+ | High priority | Take meeting |\n"
            "| 60–79 | Monitor closely | Monitor monthly |\n"
            "| 40–59 | Weak signal | Add to watchlist |\n"
            "| &lt;40 | Low priority | Ignore for now |"
        )

        if fund:
            st.markdown(f"#### Fund overrides ({fund.name})")
            scores = topic_scores or fund.topic_scores
            if scores:
                st.markdown("**Topic scores** used for applied relevance:")
                st.dataframe(
                    pd.DataFrame([{"Topic": topic, "Score": score} for topic, score in sorted(scores.items())]),
                    width="stretch",
                    hide_index=True,
                )
            st.caption(fund.description[:400])

        st.markdown(
            "**Clusters:** member researcher scores are averaged per component; "
            "team continuity and signal strength get a +2 boost (capped at their maxima)."
        )


def render_candidate_score_breakdown_expander(report) -> None:
    """Expandable per-candidate score chart, table, and component detail."""
    total = report.startup_likelihood_score
    raw_total = report.score_breakdown.startup_likelihood_score
    penalty = raw_total - total
    title = f"Score breakdown — {report.researcher_or_cluster} ({total}/100)"
    if penalty > 0:
        title += f", −{penalty} identity penalty"

    with st.expander(title, expanded=False):
        breakdown_df = score_breakdown_dataframe(report)
        st.bar_chart(breakdown_df.set_index("Component")["Score"])
        st.dataframe(breakdown_df, width="stretch", hide_index=True)

        if penalty > 0:
            st.caption(f"Component sum: **{raw_total}**. Final score after identity penalty: **{total}**.")

        st.markdown("#### Component detail")
        breakdown = report.score_breakdown
        for label, field, max_pts, description in SCORE_COMPONENTS:
            value = getattr(breakdown, field)
            pct = round(100 * value / max_pts) if max_pts else 0
            st.markdown(f"**{label}: {value}/{max_pts}** ({pct}% of max)  \n{description}")


def render_signal_sources_expander(signals: list[Signal]) -> None:
    """Expandable list of detected signals with full source URLs."""
    count = len(signals)
    with st.expander(f"Sources & detected signals ({count})", expanded=False):
        if not signals:
            st.info("No public founder or commercialization signals attached to this candidate.")
            return

        st.caption(
            "Signal sources: **agent** (LangGraph investigation), **perplexity** (Sonar), "
            "**github**, or **mock**. Expand a row for the full description and source link."
        )

        for index, signal in enumerate(signals, start=1):
            with st.expander(_signal_expander_label(signal, index), expanded=False):
                st.markdown(
                    f"**Source:** {signal_source_label(signal.id)}  \n"
                    f"**Type:** {signal.signal_type.value.replace('_', ' ').title()}  \n"
                    f"**Evidence strength:** {signal.evidence_strength.value}  \n"
                    f"**Scoring weight:** {_format_signal_points(signal)}"
                )
                if signal.signal_type == SignalType.COMMERCIALIZATION:
                    st.caption("Also contributes to **Project momentum** if URL matches GitHub/OpenReview/arXiv.")
                st.markdown(f"**Description**  \n{signal.description}")
                st.markdown(f"**Source URL**  \n[{signal.source_url}]({signal.source_url})")
                if signal.date_found:
                    st.caption(f"Date found: {signal.date_found}")
