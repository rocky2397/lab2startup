"""Dashboard UI for enrichment verification audits."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.enrichment_audit import (
    EnrichmentAudit,
    format_enriched_profile_line,
    summarize_enrichment_audit,
)


def _render_profile_list(title: str, lines: list[str], *, empty_caption: str) -> None:
    st.markdown(f"**{title}**")
    if lines:
        for line in lines:
            st.markdown(f"- {line}")
    else:
        st.caption(empty_caption)


def render_enrichment_audit_panel(audit: EnrichmentAudit | None) -> None:
    """Show whether profile/signal enrichment worked for the selected run."""
    if audit is None:
        st.info(
            "No enrichment audit saved for this run. Re-run the pipeline to capture before/after researcher snapshots."
        )
        return

    summary = summarize_enrichment_audit(audit)
    worked = summary.get("enrichment_worked")
    enriched_lines = summary.get("enriched_profile_lines") or []
    investigated_names = summary.get("investigated_profile_names") or []

    if worked and enriched_lines:
        st.success(
            f"Enrichment updated {len(enriched_lines)} profile(s): "
            + "; ".join(enriched_lines[:5])
            + (" …" if len(enriched_lines) > 5 else "")
        )
    elif worked:
        st.success("Enrichment produced results for this run.")
    else:
        st.warning(
            "Enrichment did not resolve affiliations or add signals for this run. "
            "Most researchers were likely skipped by caps or low identity confidence."
        )

    if investigated_names:
        st.caption(
            "Investigated by "
            f"{audit.mode.value}: "
            + ", ".join(investigated_names[:15])
            + (" …" if len(investigated_names) > 15 else "")
        )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Mode", audit.mode.value)
    col2.metric("Targeted", audit.targeted_count)
    col3.metric("Affiliations resolved", audit.affiliation_resolved_count)
    col4.metric("Still unknown", audit.still_unknown_count)

    with st.expander("Enrichment breakdown", expanded=not worked):
        st.caption(
            "Compares researcher state before vs after Perplexity/agentic enrichment. "
            "Use `python run_enrichment_check.py --run-id … --rerun` to verify live."
        )
        if audit.config_summary:
            st.json(audit.config_summary)

        _render_profile_list(
            "Profiles enriched",
            enriched_lines,
            empty_caption="No profiles gained affiliation, role, link, or signal updates.",
        )

        no_change = [
            format_enriched_profile_line(profile) for profile in summary.get("investigated_no_change_profiles") or []
        ]
        _render_profile_list(
            "Investigated — no change",
            no_change,
            empty_caption="No investigated profiles stayed unchanged.",
        )

        with_signals = [format_enriched_profile_line(profile) for profile in summary.get("with_signals_profiles") or []]
        _render_profile_list(
            "Profiles with signals",
            with_signals,
            empty_caption="No founder/commercialization signals attached.",
        )

        status_counts = summary.get("status_counts") or {}
        if status_counts:
            st.markdown("**Status counts**")
            st.dataframe(
                pd.DataFrame([{"status": key, "count": value} for key, value in status_counts.items()]),
                width="stretch",
                hide_index=True,
            )

        skip_reason_counts = summary.get("skip_reason_counts") or {}
        if skip_reason_counts:
            st.markdown("**Skip reasons**")
            st.dataframe(
                pd.DataFrame([{"reason": key, "count": value} for key, value in skip_reason_counts.items()]),
                width="stretch",
                hide_index=True,
            )

        table_rows = [
            {
                "Name": record.name,
                "Status": record.status,
                "Skip reason": record.skip_reason or "",
                "Pre affiliation": record.pre_affiliation,
                "Post affiliation": record.post_affiliation,
                "Pre role": record.pre_role,
                "Post role": record.post_role,
                "Signals": record.signal_count,
                "Tier": record.investigation_tier or "",
            }
            for record in audit.records[:100]
        ]
        if table_rows:
            st.markdown("**All researcher enrichment records**")
            st.dataframe(pd.DataFrame(table_rows), width="stretch", hide_index=True)
