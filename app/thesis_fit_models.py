"""Models for fund thesis fit assessments (Step 17)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

InfraLayer = Literal["infra", "application", "mixed", "unclear"]
EuropeNexus = Literal["yes", "no", "unclear"]
ThesisFitSource = Literal["rules", "sonar", "rules+sonar"]


class ThesisFitLevel(StrEnum):
    """Backtrace-specific thesis alignment band."""

    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    UNCLEAR = "unclear"


class ThesisFitAssessment(BaseModel):
    """Per-researcher thesis fit for a fund."""

    researcher_id: str
    fund_id: str
    infra_layer: InfraLayer = "unclear"
    europe_nexus: EuropeNexus = "unclear"
    fit_level: ThesisFitLevel = ThesisFitLevel.UNCLEAR
    reasons: list[str] = Field(default_factory=list)
    source: ThesisFitSource = "rules"
    sonar_used: bool = False
