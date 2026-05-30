"""Dashboard badges and filters for thesis fit."""

from __future__ import annotations

import streamlit as st

from app.thesis_fit_models import ThesisFitAssessment, ThesisFitLevel

_FIT_COLORS = {
    ThesisFitLevel.STRONG: "green",
    ThesisFitLevel.MODERATE: "blue",
    ThesisFitLevel.WEAK: "orange",
    ThesisFitLevel.UNCLEAR: "gray",
}


def thesis_fit_label(assessment: ThesisFitAssessment | None) -> str:
    if assessment is None:
        return "—"
    return assessment.fit_level.value.title()


def render_thesis_fit_badges(assessment: ThesisFitAssessment | None) -> None:
    """Compact badges for researcher cards."""
    if assessment is None:
        return
    fit = assessment.fit_level.value.title()
    europe = assessment.europe_nexus.title()
    layer = assessment.infra_layer.title()
    st.markdown(
        f"**Backtrace fit:** :{_FIT_COLORS.get(assessment.fit_level, 'gray')}[{fit}] · "
        f"**EU:** {europe} · **Layer:** {layer}"
    )
    if assessment.reasons:
        st.caption(" · ".join(assessment.reasons[:3]))


def render_thesis_fit_dev_panel(assessments: dict[str, ThesisFitAssessment] | None) -> None:
    if not assessments:
        st.caption("No thesis fit data for this run.")
        return
    st.json({rid: a.model_dump(mode="json") for rid, a in list(assessments.items())[:5]})


def thesis_fit_filter_options() -> list[str]:
    return ["All", "Strong+Moderate", "Strong only"]


def europe_nexus_filter_options() -> list[str]:
    return ["All", "Yes", "Unknown"]


def report_passes_thesis_fit_filter(
    researcher_id: str,
    assessments: dict[str, ThesisFitAssessment] | None,
    *,
    fit_filter: str,
    europe_filter: str,
) -> bool:
    if fit_filter == "All" and europe_filter == "All":
        return True
    assessment = (assessments or {}).get(researcher_id)
    if assessment is None:
        return fit_filter == "All" and europe_filter in ("All", "Unknown")

    if fit_filter == "Strong only" and assessment.fit_level != ThesisFitLevel.STRONG:
        return False
    if fit_filter == "Strong+Moderate" and assessment.fit_level not in (
        ThesisFitLevel.STRONG,
        ThesisFitLevel.MODERATE,
    ):
        return False
    if europe_filter == "Yes" and assessment.europe_nexus != "yes":
        return False
    if europe_filter == "Unknown" and assessment.europe_nexus != "unclear":
        return False
    return True
