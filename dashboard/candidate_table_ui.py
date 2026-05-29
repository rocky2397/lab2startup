"""Interactive candidate tables for the dashboard."""

from __future__ import annotations

import pandas as pd
import streamlit as st


def _selection_rows(state: object, key: str) -> list[int]:
    if isinstance(state, dict):
        return list(state.get("selection", {}).get("rows") or [])
    selection = getattr(state, "selection", None)
    if selection is not None:
        return list(getattr(selection, "rows", None) or [])
    widget_state = st.session_state.get(key)
    if isinstance(widget_state, dict):
        return list(widget_state.get("selection", {}).get("rows") or [])
    return []


def render_selectable_reports_table(
    rows: list[dict[str, object]],
    report_ids: list[str],
    *,
    key: str,
    selected_report_id: str | None = None,
    caption: str = "Click a row to open the quick view below.",
) -> str | None:
    """Render a ranked table; row selection returns the matching report id."""
    if not rows or not report_ids:
        return selected_report_id

    default_index = 0
    if selected_report_id in report_ids:
        default_index = report_ids.index(selected_report_id)

    st.caption(caption)
    state = st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
        selection_default={"selection": {"rows": [default_index]}},
    )

    selected_rows = _selection_rows(state, key)
    if selected_rows:
        index = selected_rows[0]
        if 0 <= index < len(report_ids):
            return report_ids[index]
    return selected_report_id
