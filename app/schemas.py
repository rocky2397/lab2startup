"""Pydantic schemas and JSON loaders for local mock data."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.models import (
    Paper,
    PriorityBand,
    Report,
    ScoreBreakdown,
    Signal,
    VCAction,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_PAPERS_PATH = DATA_DIR / "sample_papers.json"
DEFAULT_SIGNALS_PATH = DATA_DIR / "sample_signals.json"


class PapersFile(BaseModel):
    """Root object in sample_papers.json."""

    papers: list[Paper]


class SignalsFile(BaseModel):
    """Root object in sample_signals.json."""

    signals: list[Signal]


class EntityScoreResponse(BaseModel):
    """API representation of a researcher or cluster score."""

    entity_id: str
    entity_type: str
    entity_name: str
    score_breakdown: ScoreBreakdown
    startup_likelihood_score: int
    priority_band: PriorityBand
    recommendation: VCAction


class ScoresResponse(BaseModel):
    """Combined researcher and cluster scores."""

    researchers: list[EntityScoreResponse]
    clusters: list[EntityScoreResponse]


class ReportSummaryResponse(BaseModel):
    """Lightweight report listing for API consumers."""

    id: str
    researcher_or_cluster: str
    startup_likelihood_score: int
    priority_band: PriorityBand
    recommendation: VCAction


class ReportDetailResponse(Report):
    """Full report payload including rendered markdown."""

    markdown: str


class HealthResponse(BaseModel):
    """Simple health check payload."""

    status: str
    paper_count: int
    researcher_count: int
    cluster_count: int
    signal_count: int
    report_count: int


def load_papers(path: Path | str | None = None) -> list[Paper]:
    """Load and validate papers from a JSON file."""
    file_path = Path(path) if path else DEFAULT_PAPERS_PATH
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    return PapersFile.model_validate(raw).papers


def resolve_papers(
    path: Path | str | None = None,
    *,
    openalex_config=None,
    openreview_config=None,
) -> list[Paper]:
    """Load papers from JSON, OpenAlex, or OpenReview depending on configuration."""
    if openreview_config is not None:
        from app.integrations.openreview import fetch_papers_from_openreview

        return fetch_papers_from_openreview(openreview_config)
    if openalex_config is not None:
        from app.integrations.openalex import fetch_papers_from_openalex

        return fetch_papers_from_openalex(openalex_config)
    return load_papers(path)


def load_signals(path: Path | str | None = None) -> list[Signal]:
    """Load and validate signals from a JSON file."""
    file_path = Path(path) if path else DEFAULT_SIGNALS_PATH
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    return SignalsFile.model_validate(raw).signals


def load_sample_data(
    papers_path: Path | str | None = None,
    signals_path: Path | str | None = None,
) -> tuple[list[Paper], list[Signal]]:
    """Load both mock datasets into typed Pydantic objects."""
    return load_papers(papers_path), load_signals(signals_path)


class DatasetSummary(BaseModel):
    """Simple counts returned when inspecting loaded mock data."""

    paper_count: int
    signal_count: int
    unique_researcher_names: int
    topics: list[str] = Field(default_factory=list)
    signal_types: list[str] = Field(default_factory=list)


def summarize_dataset(
    papers: list[Paper],
    signals: list[Signal],
) -> DatasetSummary:
    """Summarize loaded papers and signals for quick inspection."""
    researcher_names = {author.name for paper in papers for author in paper.authors}
    topics = sorted({paper.topic for paper in papers})
    signal_types = sorted({signal.signal_type.value for signal in signals})

    return DatasetSummary(
        paper_count=len(papers),
        signal_count=len(signals),
        unique_researcher_names=len(researcher_names),
        topics=topics,
        signal_types=signal_types,
    )


def entity_score_to_response(score) -> EntityScoreResponse:
    """Convert an internal EntityScore dataclass to an API schema."""
    return EntityScoreResponse(
        entity_id=score.entity_id,
        entity_type=score.entity_type,
        entity_name=score.entity_name,
        score_breakdown=score.score_breakdown,
        startup_likelihood_score=score.startup_likelihood_score,
        priority_band=score.priority_band,
        recommendation=score.recommendation,
    )


def report_to_summary(report: Report) -> ReportSummaryResponse:
    """Convert a report to a list-friendly summary."""
    return ReportSummaryResponse(
        id=report.id,
        researcher_or_cluster=report.researcher_or_cluster,
        startup_likelihood_score=report.startup_likelihood_score,
        priority_band=report.priority_band,
        recommendation=report.recommendation,
    )
