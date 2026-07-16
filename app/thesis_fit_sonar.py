"""Perplexity Sonar calls for thesis fit refinement (Step 17)."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.fund_profiles import FundProfile
from app.identity_validation import text_refers_to_different_person
from app.integrations.perplexity import (
    DEFAULT_MODEL,
    DEFAULT_USER_AGENT,
    PERPLEXITY_API_BASE,
    _extract_json_object,
)
from app.integrations.perplexity import PerplexityConfig
from app.models import Paper, Report, Researcher, Signal
from app.thesis_fit_models import (
    EuropeNexus,
    InfraLayer,
    ThesisFitAssessment,
    ThesisFitLevel,
)

logger = logging.getLogger(__name__)

THESIS_FIT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "infra_layer": {
            "type": "string",
            "enum": ["infra", "application", "mixed", "unclear"],
        },
        "europe_nexus": {
            "type": "string",
            "enum": ["yes", "no", "unclear"],
        },
        "fit_level": {
            "type": "string",
            "enum": ["strong", "moderate", "weak", "unclear"],
        },
        "reasons": {
            "type": "array",
            "items": {"type": "string"},
        },
        "identity_explanation": {"type": "string"},
    },
    "required": ["infra_layer", "europe_nexus", "fit_level", "reasons", "identity_explanation"],
    "additionalProperties": False,
}


def _map_fit_level(raw: str) -> ThesisFitLevel:
    normalized = raw.strip().lower()
    for level in ThesisFitLevel:
        if level.value == normalized:
            return level
    return ThesisFitLevel.UNCLEAR


def _map_infra_layer(raw: str) -> InfraLayer:
    normalized = raw.strip().lower()
    if normalized in ("infra", "application", "mixed", "unclear"):
        return normalized  # type: ignore[return-value]
    return "unclear"


def _map_europe_nexus(raw: str) -> EuropeNexus:
    normalized = raw.strip().lower()
    if normalized in ("yes", "no", "unclear"):
        return normalized  # type: ignore[return-value]
    return "unclear"


def build_thesis_fit_prompt(
    *,
    researcher: Researcher,
    report: Report,
    signals: list[Signal],
    papers_by_id: dict[str, Paper],
    fund: FundProfile,
) -> str:
    paper_lines: list[str] = []
    for paper_id in researcher.papers[:5]:
        paper = papers_by_id.get(paper_id)
        if paper:
            paper_lines.append(f"- {paper.title} ({paper.topic}, {paper.conference} {paper.year})")
    paper_block = "\n".join(paper_lines) if paper_lines else "No papers on file."

    signal_lines: list[str] = []
    for signal in signals[:5]:
        signal_lines.append(f"- [{signal.signal_type.value}] {signal.description[:200]}")
    signal_block = "\n".join(signal_lines) if signal_lines else "No commercialization signals on file."

    fund_context = fund.perplexity_context or fund.description

    return (
        "Assess whether this researcher fits a VC fund thesis. "
        "Do NOT re-investigate founder status — use the signals below as given.\n\n"
        f"Fund context:\n{fund_context}\n\n"
        f"Name: {researcher.name}\n"
        f"Affiliation: {researcher.affiliation}\n"
        f"Role: {researcher.role}\n"
        f"Startup likelihood score: {report.startup_likelihood_score}/100\n"
        f"Recommendation: {report.recommendation.value}\n\n"
        f"Papers:\n{paper_block}\n\n"
        f"Existing signals (do not duplicate founder investigation):\n{signal_block}\n\n"
        "Return JSON with:\n"
        "- infra_layer: infra | application | mixed | unclear (platform/devtools/ML infra vs apps)\n"
        "- europe_nexus: yes | no | unclear (European operational nexus for the fund)\n"
        "- fit_level: strong | moderate | weak | unclear\n"
        "- reasons: 2-4 short audit bullets\n"
        "- identity_explanation: one sentence confirming this is the correct person\n"
        "Deprioritize biotech, drug discovery, consumer apps, and pure US-only academic paths "
        "unless clearly infrastructure-layer for ML/systems."
    )


def parse_thesis_fit_response(
    payload: dict[str, Any],
    *,
    researcher: Researcher,
    fund_id: str,
) -> ThesisFitAssessment | None:
    """Parse Sonar JSON; return None when identity does not match."""
    explanation = str(payload.get("identity_explanation") or "").strip()
    if explanation and text_refers_to_different_person(researcher.name, explanation):
        logger.info("Thesis fit Sonar rejected wrong person for %s", researcher.name)
        return None

    reasons = [str(item).strip() for item in payload.get("reasons") or [] if str(item).strip()]
    if explanation:
        reasons = [explanation] + reasons

    return ThesisFitAssessment(
        researcher_id=researcher.id,
        fund_id=fund_id,
        infra_layer=_map_infra_layer(str(payload.get("infra_layer", "unclear"))),
        europe_nexus=_map_europe_nexus(str(payload.get("europe_nexus", "unclear"))),
        fit_level=_map_fit_level(str(payload.get("fit_level", "unclear"))),
        reasons=reasons[:6],
        source="sonar",
        sonar_used=True,
    )


def query_thesis_fit_sonar(
    *,
    researcher: Researcher,
    report: Report,
    signals: list[Signal],
    papers_by_id: dict[str, Paper],
    fund: FundProfile,
    config: PerplexityConfig,
) -> ThesisFitAssessment | None:
    """Run a single Sonar query for thesis fit."""
    if not config.api_key:
        return None

    prompt = build_thesis_fit_prompt(
        researcher=researcher,
        report=report,
        signals=signals,
        papers_by_id=papers_by_id,
        fund=fund,
    )
    model = config.model or DEFAULT_MODEL

    with httpx.Client(
        base_url=PERPLEXITY_API_BASE,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        timeout=120.0,
    ) as client:
        response = client.post(
            "/v1/sonar",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "thesis_fit",
                        "schema": THESIS_FIT_RESPONSE_SCHEMA,
                    },
                },
            },
        )
        response.raise_for_status()
        if config.request_delay_seconds:
            time.sleep(config.request_delay_seconds)
        body = response.json()

    choices = body.get("choices") or []
    if not choices:
        return None
    content = choices[0].get("message", {}).get("content") or ""
    payload = _extract_json_object(content)
    return parse_thesis_fit_response(payload, researcher=researcher, fund_id=fund.id)


def merge_thesis_assessments(
    rules: ThesisFitAssessment,
    sonar: ThesisFitAssessment | None,
) -> ThesisFitAssessment:
    """Prefer Sonar when identity-valid; otherwise keep rules."""
    if sonar is None:
        return rules
    return ThesisFitAssessment(
        researcher_id=rules.researcher_id,
        fund_id=rules.fund_id,
        infra_layer=sonar.infra_layer,
        europe_nexus=sonar.europe_nexus,
        fit_level=sonar.fit_level,
        reasons=sonar.reasons or rules.reasons,
        source="rules+sonar",
        sonar_used=True,
    )
