"""Data models for papers, researchers, signals, clusters, and reports."""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field, computed_field


class SignalType(str, Enum):
    """Public commercialization signal categories."""

    CONFIRMED_FOUNDER = "confirmed_founder"
    POSSIBLE_FOUNDER = "possible_founder"
    COMMERCIALIZATION = "commercialization"
    NO_SIGNAL = "no_signal"


class EvidenceStrength(str, Enum):
    """How strongly a signal supports a commercialization hypothesis."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IdentityConfidence(str, Enum):
    """Confidence that a public profile matches the intended researcher."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PriorityBand(str, Enum):
    """Startup likelihood classification bands."""

    HIGH_PRIORITY = "high_priority"
    MONITOR_CLOSELY = "monitor_closely"
    WEAK_SIGNAL = "weak_signal"
    LOW_PRIORITY = "low_priority"


class VCAction(str, Enum):
    """Recommended next step for a VC analyst."""

    TAKE_MEETING = "take_meeting"
    MONITOR_MONTHLY = "monitor_monthly"
    ADD_TO_WATCHLIST = "add_to_watchlist"
    IGNORE_FOR_NOW = "ignore_for_now"


class RunStatus(str, Enum):
    """Lifecycle status for a persisted pipeline run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class PipelineRun(BaseModel):
    """Metadata for a stored conference sourcing run."""

    id: str
    conference: str
    year: int
    status: RunStatus
    paper_source: str
    fund_profile: str | None = None
    created_at: str
    completed_at: str | None = None
    config_json: dict[str, object] = Field(default_factory=dict)
    error_message: str | None = None
    paper_count: int | None = None
    researcher_count: int | None = None
    signal_count: int | None = None
    report_count: int | None = None


class PaperAuthor(BaseModel):
    """Author entry embedded in a conference paper record."""

    name: str
    affiliation: str
    role: str
    semantic_scholar_id: str | None = None
    openreview_profile_id: str | None = None


class Paper(BaseModel):
    """Academic conference paper with embedded author metadata."""

    id: str
    title: str
    conference: str
    year: int
    topic: str
    abstract: str
    authors: list[PaperAuthor]
    source_url: str | None = None
    openalex_id: str | None = None
    semantic_scholar_id: str | None = None
    citation_count: int | None = None
    influential_citation_count: int | None = None
    reference_count: int | None = None
    openreview_id: str | None = None
    openreview_url: str | None = None


class Researcher(BaseModel):
    """Researcher profile built from paper authorship data."""

    id: str
    name: str
    affiliation: str
    role: str
    papers: list[str] = Field(default_factory=list)
    coauthors: list[str] = Field(default_factory=list)
    identity_confidence: IdentityConfidence = IdentityConfidence.MEDIUM
    identity_confidence_explanation: str = ""
    semantic_scholar_id: str | None = None
    citation_count: int | None = None
    h_index: int | None = None
    paper_count: int | None = None
    openreview_profile_id: str | None = None
    openreview_url: str | None = None
    github_username: str | None = None


class Signal(BaseModel):
    """Detected public signal of startup or commercialization activity."""

    id: str
    signal_type: SignalType
    description: str
    source_url: str
    evidence_strength: EvidenceStrength
    date_found: date
    researcher_id: str | None = None
    cluster_id: str | None = None
    # Populated from mock JSON before researcher IDs are resolved (Step 5).
    researcher_name: str | None = None


class Cluster(BaseModel):
    """Group of researchers who repeatedly coauthor together."""

    id: str
    name: str
    researchers: list[str] = Field(default_factory=list)
    shared_papers: list[str] = Field(default_factory=list)
    topic: str
    score: float | None = None


class ScoreBreakdown(BaseModel):
    """Component scores that sum to startup likelihood."""

    research_quality: int = Field(ge=0, le=20)
    applied_relevance: int = Field(ge=0, le=20)
    team_continuity: int = Field(ge=0, le=15)
    open_source_or_project_momentum: int = Field(ge=0, le=15)
    commercialization_signal_strength: int = Field(ge=0, le=20)
    recency: int = Field(ge=0, le=10)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def startup_likelihood_score(self) -> int:
        """Total score from 0 to 100."""
        return (
            self.research_quality
            + self.applied_relevance
            + self.team_continuity
            + self.open_source_or_project_momentum
            + self.commercialization_signal_strength
            + self.recency
        )


class Report(BaseModel):
    """Founder-monitoring report for a researcher or cluster."""

    id: str
    researcher_or_cluster: str
    summary: str
    signals: list[Signal] = Field(default_factory=list)
    score_breakdown: ScoreBreakdown
    startup_likelihood_score: int = Field(ge=0, le=100)
    priority_band: PriorityBand
    recommendation: VCAction
    open_questions: list[str] = Field(default_factory=list)


def classify_priority_band(score: int) -> PriorityBand:
    """Map a 0-100 score to a priority band."""
    if score >= 80:
        return PriorityBand.HIGH_PRIORITY
    if score >= 60:
        return PriorityBand.MONITOR_CLOSELY
    if score >= 40:
        return PriorityBand.WEAK_SIGNAL
    return PriorityBand.LOW_PRIORITY


def recommend_vc_action(priority_band: PriorityBand) -> VCAction:
    """Map a priority band to a recommended VC action."""
    mapping = {
        PriorityBand.HIGH_PRIORITY: VCAction.TAKE_MEETING,
        PriorityBand.MONITOR_CLOSELY: VCAction.MONITOR_MONTHLY,
        PriorityBand.WEAK_SIGNAL: VCAction.ADD_TO_WATCHLIST,
        PriorityBand.LOW_PRIORITY: VCAction.IGNORE_FOR_NOW,
    }
    return mapping[priority_band]
