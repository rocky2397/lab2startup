"""Streamlit dashboard for ranked candidates and reports (Step 9/14)."""

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

from app.config import get_settings
from app.models import RunStatus, VCAction
from app.pipeline_cache import cache_status
from app.report_generator import RECOMMENDATION_LABELS, render_report_markdown
from app.run_service import execute_pipeline_run
from app.run_store import (
    filter_runs_with_results,
    list_runs,
    pick_preferred_run_id,
    run_has_results,
)
from app.service import clear_cache, get_report_result, set_active_run_id
from dashboard.filters import (
    diagnose_filter_miss,
    filter_cluster_reports,
    filter_researcher_reports,
    recommendation_options,
    researcher_id_from_report_id,
)
from dashboard.leaderboard_ui import render_top_prospects_board
from dashboard.scoring_ui import (
    render_candidate_score_breakdown_expander,
    render_scoring_methodology_expander,
    render_signal_sources_expander,
    score_breakdown_dataframe,
)


@st.cache_data(show_spinner="Loading Lab2Startup pipeline...")
def load_pipeline(force_refresh: bool = False, run_id: str | None = None):
    """Load and cache the full analysis pipeline."""
    if run_id:
        set_active_run_id(run_id)
    return get_report_result(force_refresh=force_refresh, run_id=run_id)


def _active_integrations(settings) -> list[str]:
    active: list[str] = []
    if settings.perplexity_config.enabled:
        active.append("Perplexity (primary)")
    if settings.openreview_config is not None and settings.openreview_config.enabled:
        active.append("OpenReview")
    if settings.semantic_scholar_config.enabled:
        active.append("Semantic Scholar")
    if settings.github_config.enabled:
        active.append("GitHub (supplement)")
    if settings.use_mock_signals:
        active.append("Mock signals (dev)")
    if settings.paper_source != "json":
        active.append(f"Papers: {settings.paper_source}")
    elif not settings.is_production:
        active.append("Papers: mock JSON")
    return active or ["No signal sources enabled"]


def _run_label(run) -> str:
    created = run.created_at[:10] if run.created_at else "unknown"
    stats = ""
    if run.paper_count is not None:
        stats = f", {run.paper_count} papers"
    return f"{run.conference} {run.year} — {created} ({run.status.value}{stats})"


def _runs_for_selector(
    complete_runs: list,
    *,
    only_with_results: bool,
) -> list:
    if only_with_results:
        with_results = filter_runs_with_results(complete_runs)
        if with_results:
            return with_results
    return complete_runs


def _render_empty_dataset_state(
    *,
    active_run,
    complete_runs: list,
    only_with_results: bool,
) -> None:
    st.warning("No papers or candidates in the current dataset.")
    if active_run:
        st.markdown(
            f"**Selected run:** {active_run.conference} {active_run.year} "
            f"({active_run.status.value}, {active_run.paper_source})"
        )
        if active_run.error_message:
            st.error(active_run.error_message)
        elif (active_run.paper_count or 0) == 0:
            st.info(
                "This run completed but returned **0 papers** "
                "(empty fetch, fund filter, or no matching authors)."
            )

    with_results = filter_runs_with_results(complete_runs)
    if with_results and (active_run is None or not run_has_results(active_run)):
        st.success(
            f"**{len(with_results)}** stored run(s) have paper data. "
            "Turn on **Only show runs with results** in the sidebar, "
            "or pick one from **Stored run**."
        )

    if complete_runs:
        summary_rows = [
            {
                "Conference": run.conference,
                "Year": run.year,
                "Status": run.status.value,
                "Papers": run.paper_count if run.paper_count is not None else "—",
                "Researchers": run.researcher_count if run.researcher_count is not None else "—",
                "Created": run.created_at[:10] if run.created_at else "—",
            }
            for run in complete_runs[:15]
        ]
        st.subheader("Recent stored runs")
        st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)
        if only_with_results and not with_results:
            st.caption("No runs with papers yet — all recent runs are empty or failed.")


def _conference_scope_table(fund) -> pd.DataFrame:
    """Build a dataframe summarizing fund conference coverage."""
    if not fund:
        return pd.DataFrame()
    rows = [
        {
            "Conference": conference.name,
            "Priority": conference.priority.title(),
            "Paper source": " / ".join(conference.sources),
        }
        for conference in fund.conferences
    ]
    return pd.DataFrame(rows)


def main() -> None:
    st.set_page_config(
        page_title="Lab2Startup",
        page_icon="🔬",
        layout="wide",
    )

    st.title("Lab2Startup")
    st.caption("Founder signal monitoring for academic AI researchers")

    settings = get_settings()
    fund = settings.fund_profile
    fund_conferences = fund.conference_names if fund else ["NeurIPS"]
    default_conference = fund_conferences[0] if fund_conferences else "NeurIPS"

    stored_runs = list_runs(db_path=settings.db_path, limit=30)
    complete_runs = [run for run in stored_runs if run.status == RunStatus.COMPLETE]

    if "selected_run_id" not in st.session_state:
        st.session_state.selected_run_id = complete_runs[0].id if complete_runs else None

    with st.sidebar:
        st.header("Pipeline")
        st.caption(f"Mode: **{settings.mode}**")
        if fund:
            st.markdown(f"**Fund:** {fund.name}")
            st.caption(fund.description[:180] + ("…" if len(fund.description) > 180 else ""))
            with st.expander(f"Conferences in scope ({len(fund.conferences)})", expanded=False):
                st.dataframe(_conference_scope_table(fund), width="stretch", hide_index=True)
                st.caption(
                    "OpenReview: NeurIPS, ICML, ICLR · OpenAlex: systems, security, devtools, data infra"
                )
        st.caption("Active sources: " + ", ".join(_active_integrations(settings)))

        only_with_results = st.checkbox(
            "Only show runs with results",
            value=st.session_state.get("only_runs_with_results", False),
            help="Hide stored runs that completed with 0 papers.",
        )
        st.session_state.only_runs_with_results = only_with_results

        display_runs = _runs_for_selector(complete_runs, only_with_results=only_with_results)
        if only_with_results and not filter_runs_with_results(complete_runs) and complete_runs:
            st.caption("No runs with papers yet — showing all complete runs.")

        if display_runs:
            if st.session_state.selected_run_id not in {run.id for run in display_runs}:
                st.session_state.selected_run_id = pick_preferred_run_id(
                    display_runs,
                    current_id=None,
                )

            run_options = {_run_label(run): run.id for run in display_runs}
            labels = list(run_options.keys())
            current_id = st.session_state.selected_run_id
            default_index = 0
            if current_id in run_options.values():
                default_index = list(run_options.values()).index(current_id)
            selected_label = st.selectbox("Stored run", labels, index=default_index)
            st.session_state.selected_run_id = run_options[selected_label]
            if st.session_state.get("loaded_run_id") != st.session_state.selected_run_id:
                st.session_state.loaded_run_id = st.session_state.selected_run_id
                st.session_state.pop("dashboard_min_score", None)
        elif settings.is_production:
            st.warning(
                "No stored runs yet. Start one below or run:\n"
                "`python run_pipeline.py --conference NeurIPS --year 2024`"
            )
        else:
            status = cache_status(
                settings,
                cache_dir=settings.pipeline_cache_dir,
                ttl_hours=settings.pipeline_cache_ttl_hours,
            )
            if status.get("hit"):
                st.success(
                    f"Disk cache available ({status.get('age_hours', '?')}h old)."
                )
            elif settings.pipeline_cache_enabled:
                st.caption(status.get("message", "No cache yet — first load may be slow."))

        if settings.is_production:
            st.subheader("New conference run")
            run_year = st.number_input("Year", min_value=2000, max_value=2100, value=2024)
            run_scope = st.radio(
                "Run scope",
                ["Single conference", "All high-priority", "Custom selection"],
                index=0,
            )

            if run_scope == "Single conference":
                run_targets = [
                    st.selectbox(
                        "Conference",
                        fund_conferences,
                        index=0,
                        format_func=lambda name: fund.conference_label(name) if fund else name,
                    )
                ]
            elif run_scope == "All high-priority":
                run_targets = fund.high_priority_conferences if fund else fund_conferences[:4]
                st.caption(
                    "Will run: "
                    + ", ".join(run_targets[:8])
                    + ("…" if len(run_targets) > 8 else "")
                )
            else:
                run_targets = st.multiselect(
                    "Conferences",
                    fund_conferences,
                    default=fund.high_priority_conferences[:3] if fund else fund_conferences[:3],
                    format_func=lambda name: fund.conference_label(name) if fund else name,
                )

            fund_entry = fund.conference(run_targets[0]) if fund and run_targets else None
            source_options = list(fund_entry.sources) if fund_entry else ["openreview", "openalex", "json"]
            paper_source = st.selectbox(
                "Paper source",
                source_options,
                index=0,
                help="Auto-selected per conference when running a batch (OpenReview vs OpenAlex).",
            )
            if run_scope != "Single conference":
                st.caption("Paper source applies only when a conference supports it; others use their default.")

            if st.button("Run pipeline & save", type="primary", disabled=not run_targets):
                progress = st.progress(0.0, text="Starting pipeline run...")
                completed: list[str] = []
                runs_with_data: list[str] = []
                failures: list[tuple[str, str]] = []

                for index, run_conference in enumerate(run_targets, start=1):
                    progress.progress(
                        (index - 1) / len(run_targets),
                        text=f"Running {run_conference} {int(run_year)} ({index}/{len(run_targets)})...",
                    )
                    entry = fund.conference(run_conference) if fund else None
                    source = paper_source if entry and paper_source in entry.sources else None
                    try:
                        run, result = execute_pipeline_run(
                            conference=run_conference,
                            year=int(run_year),
                            paper_source=source,
                            fund_profile=fund.id if fund else settings.fund_id,
                            settings=settings,
                        )
                        completed.append(run.id)
                        paper_count = run.paper_count or len(result.scoring.detection.papers)
                        if paper_count > 0:
                            runs_with_data.append(run.id)
                    except Exception as exc:
                        failures.append((run_conference, str(exc)))

                progress.progress(1.0, text=f"Finished {len(run_targets)} conference run(s).")

                if not completed and failures:
                    st.error(f"All pipeline runs failed: {failures[0][1]}")
                    st.info(
                        "OpenReview paper fetch failed (often **429 Too Many Requests** on profile lookups). "
                        "Affiliations are now resolved by **Perplexity** — ensure "
                        "`LAB2STARTUP_OPENREVIEW_FETCH_PROFILES=false` and your Perplexity API key is set.\n\n"
                        "If paper fetch still fails, wait a few minutes and retry, or increase:\n\n"
                        "`LAB2STARTUP_OPENREVIEW_REQUEST_DELAY=2.0`"
                    )
                else:
                    preferred_id = runs_with_data[0] if runs_with_data else completed[-1]
                    st.session_state.selected_run_id = preferred_id
                    if runs_with_data:
                        st.session_state.only_runs_with_results = True
                    if failures:
                        failure_lines = []
                        for name, msg in failures[:5]:
                            short = msg if len(msg) <= 80 else msg[:77] + "..."
                            failure_lines.append(f"{name} ({short})")
                        st.warning(
                            f"{len(failures)} run(s) failed: "
                            + "; ".join(failure_lines)
                            + ("…" if len(failures) > 5 else "")
                        )
                    if len(completed) > 1:
                        st.success(
                            f"Saved {len(completed)} run(s)"
                            + (f", {len(runs_with_data)} with papers" if runs_with_data else "")
                            + ". Viewing "
                            + ("first run with data." if runs_with_data else "last completed run.")
                        )
                    elif completed:
                        if runs_with_data:
                            st.success("Run saved with paper data.")
                        else:
                            st.warning("Run saved but returned 0 papers.")
                    load_pipeline.clear()
                    clear_cache()
                    st.rerun()
        else:
            st.caption(
                "Development mode: refresh runs the live pipeline against mock JSON papers "
                "unless `LAB2STARTUP_PAPER_SOURCE=openreview`. Perplexity is the primary signal source."
            )
            if st.button("Refresh live data", help="Re-run enabled integrations (Perplexity-first)"):
                clear_cache()
                load_pipeline.clear()
                st.session_state.do_refresh = True
                st.rerun()

        if settings.perplexity_config.enabled and not settings.perplexity_config.api_key:
            st.error("Perplexity is enabled but LAB2STARTUP_PERPLEXITY_API_KEY is missing.")

        st.divider()
        st.header("Filters")

    result = load_pipeline(
        force_refresh=st.session_state.pop("do_refresh", False),
        run_id=st.session_state.selected_run_id,
    )
    detection = result.scoring.detection
    papers = detection.papers

    if not papers and settings.is_production and not complete_runs:
        st.info(
            "No pipeline data yet. Use **Run pipeline & save** in the sidebar, or from the CLI:\n\n"
            f"`python run_pipeline.py --conference {default_conference} --year 2024`"
        )
        return

    if not papers and not result.reports:
        active_run = next(
            (run for run in complete_runs if run.id == st.session_state.selected_run_id),
            None,
        )
        _render_empty_dataset_state(
            active_run=active_run,
            complete_runs=complete_runs,
            only_with_results=st.session_state.get("only_runs_with_results", False),
        )
        return

    conferences = sorted({paper.conference for paper in papers})
    years = sorted({paper.year for paper in papers}, reverse=True)
    topics = sorted({paper.topic for paper in papers})
    rec_options = recommendation_options()
    active_run = next(
        (run for run in complete_runs if run.id == st.session_state.selected_run_id),
        None,
    )

    if active_run:
        st.caption(
            f"Viewing stored run: **{active_run.conference} {active_run.year}** "
            f"({active_run.paper_source})"
        )

    filter_ns = st.session_state.get("loaded_run_id") or "default"

    with st.sidebar:
        view_mode = st.radio("View", ["Researchers", "Clusters"], horizontal=True)
        min_score = st.slider(
            "Minimum score",
            min_value=0,
            max_value=100,
            value=st.session_state.get("dashboard_min_score", 40),
            key=f"min_score_{filter_ns}",
        )
        st.session_state.dashboard_min_score = min_score
        if min_score >= 60:
            st.caption("Tip: lower to 40 or 0 if this run has few/no Perplexity signals yet.")
        recommendation_label = st.selectbox(
            "Recommendation",
            ["All"] + [label for label, _ in rec_options],
        )
        recommendation = None
        if recommendation_label != "All":
            recommendation = next(
                value for label, value in rec_options if label == recommendation_label
            )

        conference = st.selectbox(
            "Conference",
            ["All"] + conferences,
            index=0,
            key=f"conference_{filter_ns}",
        )
        year_label = st.selectbox(
            "Year",
            ["All"] + [str(y) for y in years],
            index=0,
            key=f"year_{filter_ns}",
        )
        topic = st.selectbox(
            "Topic",
            ["All"] + topics,
            index=0,
            key=f"topic_{filter_ns}",
        )

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
        diagnosis = diagnose_filter_miss(
            result.reports,
            detection.researchers,
            papers,
            min_score=min_score,
            recommendation=recommendation,
            conference=conference_filter,
            year=year_filter,
            topic=topic_filter,
        )
        st.warning("No candidates match the current sidebar filters.")
        st.info(
            f"**{diagnosis['total_researchers']}** researchers in this run · "
            f"**{diagnosis['above_min_score']}** at or above min score **{min_score}** · "
            f"**{diagnosis['after_metadata_filters']}** after conference/year/topic filters.\n\n"
            "Try lowering **Minimum score** to **0** in the sidebar, set filters to **All**, "
            "or check that Perplexity signals finished for this run."
        )

    researcher_reports = [
        report for report in result.reports if report.id.startswith("report_researcher_")
    ]

    tab_top, tab_explore = st.tabs(["Top prospects", "Explore & details"])

    with tab_top:
        if view_mode == "Researchers":
            if researcher_reports:
                top_selection = render_top_prospects_board(
                    reports=researcher_reports,
                    researchers=detection.researchers,
                    papers=papers,
                    conference=conference_filter,
                    year=year_filter,
                    topic=topic_filter,
                )
                if top_selection:
                    st.session_state.selected_report_id = top_selection
            else:
                st.warning("This run has no researcher scores yet.")
        else:
            st.info("Switch to **Researchers** in the sidebar to see the highest-potential leaderboard.")
            if filtered_reports:
                cluster_rows = [
                    {
                        "Name": report.researcher_or_cluster,
                        "Score": report.startup_likelihood_score,
                        "Recommendation": RECOMMENDATION_LABELS[report.recommendation],
                        "Signals": len(report.signals),
                    }
                    for report in filtered_reports[:15]
                ]
                st.dataframe(pd.DataFrame(cluster_rows), width="stretch", hide_index=True)

    with tab_explore:
        if not filtered_reports:
            st.caption("Adjust sidebar filters to explore individual candidate reports.")
            return
        _render_explore_tab(
            filtered_reports=filtered_reports,
            view_mode=view_mode,
            detection=detection,
            fund=fund,
            topic_scores=settings.topic_scores,
        )


def _render_explore_tab(
    *,
    filtered_reports,
    view_mode: str,
    detection,
    fund,
    topic_scores,
) -> None:
    if "selected_report_id" in st.session_state:
        preferred = st.session_state.selected_report_id
        if preferred in [report.id for report in filtered_reports]:
            default_index = [report.id for report in filtered_reports].index(preferred)
        else:
            default_index = 0
    else:
        default_index = 0

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
    st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True)

    report_ids = [report.id for report in filtered_reports]
    selected_report_id = st.selectbox(
        "Select candidate for details",
        report_ids,
        index=default_index,
        format_func=lambda report_id: next(
            report.researcher_or_cluster
            for report in filtered_reports
            if report.id == report_id
        ),
    )
    st.session_state.selected_report_id = selected_report_id
    selected_report = next(
        report for report in filtered_reports if report.id == selected_report_id
    )

    detail_col1, detail_col2, detail_col3 = st.columns(3)
    detail_col1.metric("Score", selected_report.startup_likelihood_score)
    detail_col2.metric(
        "Priority",
        selected_report.priority_band.value.replace("_", " ").title(),
    )
    detail_col3.metric(
        "Recommendation",
        RECOMMENDATION_LABELS[selected_report.recommendation],
    )

    breakdown_df = score_breakdown_dataframe(selected_report)
    preview_col1, preview_col2 = st.columns([1, 1])
    with preview_col1:
        st.caption("Score components (expand below for full methodology)")
        st.bar_chart(breakdown_df.set_index("Component")["Score"], height=220)
    with preview_col2:
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
                st.write(f"Signals attached: {len(selected_report.signals)}")
        else:
            st.markdown("**Cluster view**")
            st.write(f"Signals attached: {len(selected_report.signals)}")
            st.caption("Expand **Sources & detected signals** below for links.")

    st.subheader("Generated report")
    st.markdown(render_report_markdown(selected_report))

    st.divider()
    st.subheader("Details")
    render_scoring_methodology_expander(
        fund=fund,
        topic_scores=topic_scores,
    )
    render_candidate_score_breakdown_expander(selected_report)
    render_signal_sources_expander(selected_report.signals)


if __name__ == "__main__":
    main()
