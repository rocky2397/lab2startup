"""Tests for thesis fit agent with mocked Sonar (Step 17)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from app.agents.report_agent import run_reports
from app.agents.thesis_fit_agent import run_thesis_fit_agent
from app.fund_profiles import DEFAULT_FUND_ID, load_fund_profile
from app.integrations.perplexity import PerplexityConfig
from app.models import Report, Researcher, VCAction
from app.thesis_fit_models import ThesisFitAssessment, ThesisFitLevel
from app.thesis_fit_store import deserialize_thesis_fit, serialize_thesis_fit


@pytest.fixture
def fund():
    return load_fund_profile(DEFAULT_FUND_ID)


def test_rules_only_when_sonar_disabled(fund) -> None:
    result = run_reports(include_clusters=False)
    assessments = run_thesis_fit_agent(
        result,
        fund=fund,
        use_sonar=False,
    )
    assert assessments
    assert all(not a.sonar_used for a in assessments.values())
    assert all(a.source == "rules" for a in assessments.values())


@patch("app.agents.thesis_fit_agent.query_thesis_fit_sonar")
def test_sonar_merged_for_gated_candidates(mock_sonar, fund) -> None:
    result = run_reports(include_clusters=False)
    detection = result.scoring.detection
    target_report = next(r for r in result.reports if r.id.startswith("report_researcher_"))
    researcher_id = target_report.id.removeprefix("report_")
    researcher = next(r for r in detection.researchers if r.id == researcher_id)

    mock_sonar.return_value = ThesisFitAssessment(
        researcher_id=researcher.id,
        fund_id=fund.id,
        infra_layer="infra",
        europe_nexus="yes",
        fit_level=ThesisFitLevel.STRONG,
        reasons=["Sonar confirmed EU infra founder fit"],
        source="sonar",
        sonar_used=True,
    )

    # Force gate: unclear rules + high score path
    target_report = target_report.model_copy(
        update={
            "startup_likelihood_score": 75,
            "recommendation": VCAction.TAKE_MEETING,
        }
    )
    result.reports = [
        target_report if r.id == target_report.id else r for r in result.reports
    ]

    config = PerplexityConfig(enabled=True, api_key="test-key")
    assessments = run_thesis_fit_agent(
        result,
        fund=fund,
        perplexity_config=config,
        sonar_min_score=60,
        sonar_max_calls=5,
        use_sonar=True,
    )

    assert mock_sonar.called
    merged = assessments[researcher.id]
    assert merged.sonar_used
    assert merged.source == "rules+sonar"
    assert merged.fit_level == ThesisFitLevel.STRONG


@patch("app.agents.thesis_fit_agent.query_thesis_fit_sonar")
def test_sonar_identity_rejection_keeps_rules(mock_sonar, fund) -> None:
    result = run_reports(include_clusters=False)
    mock_sonar.return_value = None

    config = PerplexityConfig(enabled=True, api_key="test-key")
    assessments = run_thesis_fit_agent(
        result,
        fund=fund,
        perplexity_config=config,
        sonar_max_calls=10,
        use_sonar=True,
    )
    assert assessments
    assert all(a.source == "rules" for a in assessments.values())


def test_thesis_fit_store_roundtrip(fund) -> None:
    result = run_reports(include_clusters=False)
    assessments = run_thesis_fit_agent(result, fund=fund, use_sonar=False)
    payload = serialize_thesis_fit(assessments)
    restored = deserialize_thesis_fit(payload)
    assert len(restored) == len(assessments)
    sample_id = next(iter(restored))
    assert restored[sample_id].fit_level == assessments[sample_id].fit_level
