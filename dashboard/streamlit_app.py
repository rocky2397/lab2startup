"""Streamlit dashboard for ranked candidates and reports (Step 9)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Streamlit puts `dashboard/` on sys.path, which hides the project-root `app` package.
# Always prepend the project root before importing local packages.
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
os.chdir(ROOT_DIR)

import pandas as pd
import streamlit as st

from app.models import VCAction
from app.report_generator import RECOMMENDATION_LABELS, render_report_markdown
from app.service import get_report_result
from dashboard.filters import (
    filter_cluster_reports,
    filter_researcher_reports,
    recommendation_options,
    researcher_id_from_report_id,
)


@st.cache_data(show_spinner="Loading Lab2Startup pipeline...")
def load_pipeline():
    """Load and cache the full analysis pipeline."""
    return get_report_result()


def score_breakdown_dataframe(report) -> pd.DataFrame:
    """Convert a score breakdown into a chart-friendly dataframe."""
    breakdown = report.score_breakdown
    return pd.DataFrame(
        {
            "Component": [
                "Research quality",
                "Applied relevance",
                "Team continuity",
                "Project momentum",
                "Signal strength",
                "Recency",
            ],
            "Score": [
                breakdown.research_quality,
                breakdown.applied_relevance,
                breakdown.team_continuity,
                breakdown.open_source_or_project_momentum,
                breakdown.commercialization_signal_strength,
                breakdown.recency,
            ],
            "Max": [20, 20, 15, 15, 20, 10],
        }
    )


def main() -> None:
    st.set_page_config(
        page_title="Lab2Startup",
        page_icon="🔬",
        layout="wide",
    )

    st.title("Lab2Startup")
    st.caption("Founder signal monitoring for academic AI researchers")

    result = load_pipeline()
    detection = result.scoring.detection
    papers = detection.papers

    conferences = sorted({paper.conference for paper in papers})
    years = sorted({paper.year for paper in papers}, reverse=True)
    topics = sorted({paper.topic for paper in papers})
    rec_options = recommendation_options()

    with st.sidebar:
        st.header("Filters")
        view_mode = st.radio("View", ["Researchers", "Clusters"], horizontal=True)
        min_score = st.slider("Minimum score", min_value=0, max_value=100, value=60)
        recommendation_label = st.selectbox(
            "Recommendation",
            ["All"] + [label for label, _ in rec_options],
        )
        recommendation = None
        if recommendation_label != "All":
            recommendation = next(
                value for label, value in rec_options if label == recommendation_label
            )

        conference = st.selectbox("Conference", ["All"] + conferences)
        year_label = st.selectbox("Year", ["All"] + [str(y) for y in years])
        topic = st.selectbox("Topic", ["All"] + topics)

        conference_filter = None if conference == "All" else conference
        year_filter = None if year_label == "All" else int(year_label)
        topic_filter = None if topic == "All" else topic

        st.divider()
        st.markdown("**Dataset**")
        st.write(f"Papers: {len(papers)}")
        st.write(f"Researchers: {len(detection.researchers)}")
        st.write(f"Signals: {len(detection.signals)}")

    if view_mode == "Researchers":
        filtered_reports = filter_researcher_reports(
            result.reports,
            detection.researchers,
            papers,
            min_score=min_score,
            recommendation=recommendation,
            conference=conference_filter,
            year=year_filter,
            topic=topic_filter,
        )
    else:
        filtered_reports = filter_cluster_reports(
            result.reports,
            detection.clusters,
            min_score=min_score,
            recommendation=recommendation,
            topic=topic_filter,
        )

    if not filtered_reports:
        st.warning("No candidates match the current filters.")
        return

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Candidates", len(filtered_reports))
    col2.metric("Top score", filtered_reports[0].startup_likelihood_score)
    col3.metric(
        "Avg score",
        round(
            sum(report.startup_likelihood_score for report in filtered_reports)
            / len(filtered_reports),
            1,
        ),
    )
    col4.metric(
        "Take meeting",
        sum(
            1
            for report in filtered_reports
            if report.recommendation == VCAction.TAKE_MEETING
        ),
    )

    table_rows = [
        {
            "Name": report.researcher_or_cluster,
            "Score": report.startup_likelihood_score,
            "Priority": report.priority_band.value.replace("_", " ").title(),
            "Recommendation": RECOMMENDATION_LABELS[report.recommendation],
            "Signals": len(report.signals),
            "Report ID": report.id,
        }
        for report in filtered_reports
    ]
    st.subheader("Ranked candidates")
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

    report_ids = [report.id for report in filtered_reports]
    selected_report_id = st.selectbox(
        "Select candidate for details",
        report_ids,
        format_func=lambda report_id: next(
            report.researcher_or_cluster
            for report in filtered_reports
            if report.id == report_id
        ),
    )
    selected_report = next(
        report for report in filtered_reports if report.id == selected_report_id
    )

    left, right = st.columns([1, 1])

    with left:
        st.subheader("Score breakdown")
        breakdown_df = score_breakdown_dataframe(selected_report)
        st.bar_chart(breakdown_df.set_index("Component")["Score"])
        st.dataframe(breakdown_df, use_container_width=True, hide_index=True)

    with right:
        st.subheader("Detected signals")
        if selected_report.signals:
            for signal in selected_report.signals:
                st.markdown(
                    f"**{signal.signal_type.value.replace('_', ' ').title()}** "
                    f"({signal.evidence_strength.value})  \n"
                    f"{signal.description}  \n"
                    f"[Source]({signal.source_url})"
                )
        else:
            st.info("No signals attached to this candidate.")

        if view_mode == "Researchers":
            researcher_id = researcher_id_from_report_id(selected_report.id)
            if researcher_id:
                researcher = next(
                    r for r in detection.researchers if r.id == researcher_id
                )
                st.markdown("**Profile**")
                st.write(f"Affiliation: {researcher.affiliation}")
                st.write(f"Role: {researcher.role}")
                st.write(f"Identity confidence: {researcher.identity_confidence.value}")

    st.subheader("Generated report")
    st.markdown(render_report_markdown(selected_report))


if __name__ == "__main__":
    main()
