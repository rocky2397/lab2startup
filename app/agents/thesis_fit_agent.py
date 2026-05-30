"""Thesis fit agent — rules plus optional Sonar gate (Step 17)."""

from __future__ import annotations

import logging

from app.agents.report_agent import ReportResult
from app.config import AppSettings
from app.fund_profiles import FundProfile
from app.integrations.perplexity import PerplexityConfig
from app.models import Report, VCAction
from app.thesis_fit_models import ThesisFitAssessment, ThesisFitLevel
from app.thesis_fit_rules import assess_thesis_fit
from app.thesis_fit_sonar import merge_thesis_assessments, query_thesis_fit_sonar

logger = logging.getLogger(__name__)


def _should_call_sonar(
    *,
    assessment: ThesisFitAssessment,
    report: Report,
    sonar_min_score: int,
) -> bool:
    if assessment.fit_level == ThesisFitLevel.UNCLEAR:
        return True
    if report.startup_likelihood_score >= sonar_min_score:
        return True
    if report.recommendation == VCAction.TAKE_MEETING:
        return True
    return False


def run_thesis_fit_agent(
    result: ReportResult,
    *,
    fund: FundProfile,
    settings: AppSettings | None = None,
    perplexity_config: PerplexityConfig | None = None,
    sonar_min_score: int | None = None,
    sonar_max_calls: int | None = None,
    use_sonar: bool = True,
) -> dict[str, ThesisFitAssessment]:
    """Assess thesis fit for every scored researcher report."""
    if fund.thesis_fit is None:
        return {}

    settings = settings  # reserved for future audit hooks
    _ = settings

    tf_config = fund.thesis_fit
    min_score = sonar_min_score if sonar_min_score is not None else tf_config.sonar_min_score
    max_calls = sonar_max_calls if sonar_max_calls is not None else tf_config.sonar_max_calls

    detection = result.scoring.detection
    papers_by_id = {paper.id: paper for paper in detection.papers}
    researchers_by_id = {researcher.id: researcher for researcher in detection.researchers}
    reports_by_researcher: dict[str, Report] = {}
    for report in result.reports:
        if report.id.startswith("report_researcher_"):
            reports_by_researcher[report.id.removeprefix("report_")] = report

    assessments: dict[str, ThesisFitAssessment] = {}
    sonar_calls = 0

    for researcher_id, report in reports_by_researcher.items():
        researcher = researchers_by_id.get(researcher_id)
        if researcher is None:
            continue
        signals = detection.signals_for_researcher(researcher_id)
        rules_assessment = assess_thesis_fit(
            researcher,
            report,
            signals,
            fund,
            papers_by_id=papers_by_id,
        )

        sonar_assessment: ThesisFitAssessment | None = None
        if (
            use_sonar
            and perplexity_config
            and perplexity_config.enabled
            and perplexity_config.api_key
            and sonar_calls < max_calls
            and _should_call_sonar(
                assessment=rules_assessment,
                report=report,
                sonar_min_score=min_score,
            )
        ):
            try:
                sonar_assessment = query_thesis_fit_sonar(
                    researcher=researcher,
                    report=report,
                    signals=signals,
                    papers_by_id=papers_by_id,
                    fund=fund,
                    config=perplexity_config,
                )
                if sonar_assessment is not None:
                    sonar_calls += 1
            except Exception as exc:
                logger.warning("Thesis fit Sonar failed for %s: %s", researcher.name, exc)

        assessments[researcher_id] = merge_thesis_assessments(rules_assessment, sonar_assessment)

    if sonar_calls:
        logger.info("Thesis fit Sonar calls for %s: %s", fund.id, sonar_calls)

    return assessments
