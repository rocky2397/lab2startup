"""Dashboard helpers for researcher profile links."""

from __future__ import annotations

import streamlit as st

from app.models import Researcher, Signal
from app.researcher_links import ResearcherLinks, resolve_researcher_links


def render_researcher_profile_links(
    researcher: Researcher,
    signals: list[Signal] | None = None,
    *,
    label: str = "Profiles",
) -> ResearcherLinks:
    """Show clickable GitHub / LinkedIn / OpenReview links when available."""
    links = resolve_researcher_links(researcher, signals)
    visible = [
        name
        for name, url in (
            ("GitHub", links.github),
            ("LinkedIn", links.linkedin),
            ("OpenReview", links.openreview),
            ("Website", links.website),
        )
        if url
    ]

    if not visible:
        st.caption("No GitHub or LinkedIn profile found yet — rerun with Perplexity enrichment.")
        return links

    st.markdown(f"**{label}**")
    columns = st.columns(min(len(visible), 4))
    for column, (name, url) in zip(
        columns,
        (
            ("GitHub", links.github),
            ("LinkedIn", links.linkedin),
            ("OpenReview", links.openreview),
            ("Website", links.website),
        ),
        strict=False,
    ):
        if not url:
            continue
        column.link_button(name, url, use_container_width=True)

    return links
