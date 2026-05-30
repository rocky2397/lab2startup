"""Dashboard panel for run-to-run diffs."""

from __future__ import annotations

import streamlit as st

from app.run_diff_models import RunDiff


def render_changes_since_last_run(
    diff: RunDiff | None,
    *,
    selected_report_id: str | None = None,
) -> str | None:
    """Show diff summary and table; return report id if user picks a delta row."""
    st.subheader("Changes since last run")
    if diff is None:
        st.caption("No diff computed for this run yet.")
        return selected_report_id

    if diff.prior_run_id is None:
        st.info("First run for this conference — no prior comparison.")
        return selected_report_id

    summary = diff.summary
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total changes", summary.total_deltas)
    col2.metric("New take meeting", summary.new_take_meeting)
    col3.metric("New researchers", summary.new_researchers)
    col4.metric("Score increases", summary.score_increases)

    st.caption(f"Compared to prior run `{diff.prior_run_id}`")

    if not diff.deltas:
        st.success("No material changes vs the prior run.")
        return selected_report_id

    rows = []
    report_ids = []
    for delta in diff.deltas:
        report_id = f"report_{delta.researcher_id}"
        report_ids.append(report_id)
        rows.append(
            {
                "Name": delta.name,
                "Change": delta.change_type.replace("_", " ").title(),
                "Detail": delta.detail[:120],
                "Before": delta.before if delta.before is not None else "—",
                "After": delta.after if delta.after is not None else "—",
            }
        )

    st.caption("Click a row to open that candidate in Explore & details.")
    from dashboard.candidate_table_ui import render_selectable_reports_table

    return render_selectable_reports_table(
        rows,
        report_ids,
        key="run_diff_table",
        selected_report_id=selected_report_id,
        caption="Highlighted: new take-meeting, new researchers, score jumps, new signals.",
    ) or selected_report_id
