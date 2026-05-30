"""Deterministic Backtrace thesis fit rules (Step 17)."""

from __future__ import annotations

from app.fund_profiles import FundProfile, ThesisFitConfig
from app.models import Paper, Report, Researcher, Signal
from app.region_hints import infer_region_hint
from app.thesis_fit_models import EuropeNexus, InfraLayer, ThesisFitAssessment, ThesisFitLevel


def _collect_text(
    researcher: Researcher,
    report: Report,
    signals: list[Signal],
    papers_by_id: dict[str, Paper],
) -> str:
    parts = [
        researcher.affiliation,
        researcher.role,
        report.summary,
    ]
    for paper_id in researcher.papers:
        paper = papers_by_id.get(paper_id)
        if paper:
            parts.extend([paper.title, paper.abstract, paper.topic])
    for signal in signals:
        parts.append(signal.description)
    return " ".join(part for part in parts if part).lower()


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for keyword in keywords:
        if keyword.lower() in text:
            hits.append(keyword)
    return hits


def assess_europe_nexus(
    researcher: Researcher,
    config: ThesisFitConfig,
) -> tuple[EuropeNexus, list[str]]:
    region = infer_region_hint(researcher.affiliation)
    if region and region in config.europe_regions:
        return "yes", [f"Affiliation maps to {region} (European nexus)"]
    if region == "United States":
        return "no", ["Affiliation maps to United States with no EU signal"]
    if region:
        if region in ("China", "Japan", "South Korea", "India", "Australia", "Canada", "Israel", "Singapore"):
            return "no", [f"Affiliation maps to {region}"]
    if region:
        return "unclear", [f"Region hint {region} not in fund Europe list"]
    return "unclear", ["Could not infer region from affiliation"]


def assess_infra_layer(
    text: str,
    config: ThesisFitConfig,
) -> tuple[InfraLayer, list[str], bool]:
    """Return infra layer, reason bullets, and whether hard-excluded."""
    exclude_hits = _keyword_hits(text, config.hard_exclude_keywords)
    if exclude_hits:
        return "unclear", [f"Hard exclude: {', '.join(exclude_hits)}"], True

    infra_hits = _keyword_hits(text, config.infra_keywords)
    app_hits = _keyword_hits(text, config.application_keywords)

    if infra_hits and app_hits:
        return "mixed", [f"Infra signals: {', '.join(infra_hits[:3])}", f"Application signals: {', '.join(app_hits[:2])}"], False
    if infra_hits:
        return "infra", [f"Infrastructure layer: {', '.join(infra_hits[:4])}"], False
    if app_hits:
        return "application", [f"Application-layer keywords: {', '.join(app_hits[:3])}"], False
    return "unclear", ["No strong infra or application keyword match"], False


def _combine_fit_level(
    *,
    europe: EuropeNexus,
    infra: InfraLayer,
    hard_excluded: bool,
    europe_reasons: list[str],
    infra_reasons: list[str],
) -> tuple[ThesisFitLevel, list[str]]:
    reasons = list(europe_reasons) + list(infra_reasons)
    if hard_excluded:
        return ThesisFitLevel.WEAK, reasons + ["Outside Backtrace thesis (excluded domain)"]

    if europe == "yes" and infra == "infra":
        return ThesisFitLevel.STRONG, reasons
    if europe == "yes" and infra in ("mixed", "unclear"):
        return ThesisFitLevel.MODERATE, reasons
    if europe == "unclear" and infra == "infra":
        return ThesisFitLevel.MODERATE, reasons
    if europe == "no" and infra == "infra":
        return ThesisFitLevel.MODERATE, reasons + ["Strong infra but non-European nexus"]
    if europe == "no" and infra == "application":
        return ThesisFitLevel.WEAK, reasons
    if infra == "application":
        return ThesisFitLevel.WEAK, reasons
    if europe == "no":
        return ThesisFitLevel.WEAK, reasons
    return ThesisFitLevel.UNCLEAR, reasons


def assess_thesis_fit(
    researcher: Researcher,
    report: Report,
    signals: list[Signal],
    fund: FundProfile,
    *,
    papers_by_id: dict[str, Paper] | None = None,
) -> ThesisFitAssessment:
    """Rule-only thesis fit assessment for one researcher."""
    config = fund.thesis_fit
    if config is None:
        return ThesisFitAssessment(
            researcher_id=researcher.id,
            fund_id=fund.id,
            fit_level=ThesisFitLevel.UNCLEAR,
            reasons=["Fund has no thesis_fit configuration"],
        )

    papers_by_id = papers_by_id or {}
    text = _collect_text(researcher, report, signals, papers_by_id)
    europe, europe_reasons = assess_europe_nexus(researcher, config)
    infra, infra_reasons, hard_excluded = assess_infra_layer(text, config)
    fit_level, reasons = _combine_fit_level(
        europe=europe,
        infra=infra,
        hard_excluded=hard_excluded,
        europe_reasons=europe_reasons,
        infra_reasons=infra_reasons,
    )

    return ThesisFitAssessment(
        researcher_id=researcher.id,
        fund_id=fund.id,
        infra_layer=infra,
        europe_nexus=europe,
        fit_level=fit_level,
        reasons=reasons,
        source="rules",
        sonar_used=False,
    )
