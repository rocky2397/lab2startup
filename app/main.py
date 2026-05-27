"""FastAPI entrypoint (Step 8)."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.models import Cluster, Paper, Researcher, Signal
from app.report_generator import render_report_markdown
from app.schemas import (
    EntityScoreResponse,
    HealthResponse,
    ReportDetailResponse,
    ReportSummaryResponse,
    ScoresResponse,
    entity_score_to_response,
    report_to_summary,
)
from app.service import get_report_result

app = FastAPI(
    title="Lab2Startup",
    description="Agentic VC sourcing API for academic founder signal monitoring.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_model=HealthResponse, tags=["meta"])
def health_check() -> HealthResponse:
    """Return basic service health and dataset counts."""
    result = get_report_result()
    detection = result.scoring.detection
    return HealthResponse(
        status="ok",
        paper_count=len(detection.papers),
        researcher_count=len(detection.researchers),
        cluster_count=len(detection.clusters),
        signal_count=len(detection.signals),
        report_count=result.report_count,
    )


@app.get("/papers", response_model=list[Paper], tags=["data"])
def list_papers(
    conference: str | None = Query(default=None),
    year: int | None = Query(default=None),
    topic: str | None = Query(default=None),
) -> list[Paper]:
    """List conference papers with optional filters."""
    papers = get_report_result().scoring.detection.papers

    if conference:
        papers = [paper for paper in papers if paper.conference.lower() == conference.lower()]
    if year is not None:
        papers = [paper for paper in papers if paper.year == year]
    if topic:
        papers = [paper for paper in papers if paper.topic.lower() == topic.lower()]

    return papers


@app.get("/researchers", response_model=list[Researcher], tags=["data"])
def list_researchers(
    affiliation: str | None = Query(default=None),
    topic: str | None = Query(default=None),
) -> list[Researcher]:
    """List extracted researcher profiles."""
    result = get_report_result()
    researchers = result.scoring.detection.researchers
    papers_by_id = {paper.id: paper for paper in result.scoring.detection.papers}

    if affiliation:
        researchers = [
            researcher for researcher in researchers if affiliation.lower() in researcher.affiliation.lower()
        ]

    if topic:
        researchers = [
            researcher
            for researcher in researchers
            if any(
                papers_by_id[paper_id].topic.lower() == topic.lower()
                for paper_id in researcher.papers
                if paper_id in papers_by_id
            )
        ]

    return researchers


@app.get("/clusters", response_model=list[Cluster], tags=["data"])
def list_clusters(
    topic: str | None = Query(default=None),
) -> list[Cluster]:
    """List coauthor clusters."""
    clusters = get_report_result().scoring.detection.clusters

    if topic:
        clusters = [cluster for cluster in clusters if cluster.topic.lower() == topic.lower()]

    return clusters


@app.get("/signals", response_model=list[Signal], tags=["data"])
def list_signals(
    signal_type: str | None = Query(default=None),
    researcher_id: str | None = Query(default=None),
) -> list[Signal]:
    """List commercialization signals attached to researchers."""
    signals = get_report_result().scoring.detection.signals

    if signal_type:
        signals = [signal for signal in signals if signal.signal_type.value == signal_type]
    if researcher_id:
        signals = [signal for signal in signals if signal.researcher_id == researcher_id]

    return signals


@app.get("/scores", response_model=ScoresResponse, tags=["analysis"])
def list_scores() -> ScoresResponse:
    """List startup likelihood scores for researchers and clusters."""
    scoring = get_report_result().scoring
    return ScoresResponse(
        researchers=[entity_score_to_response(score) for score in scoring.ranked_researchers],
        clusters=[entity_score_to_response(score) for score in scoring.ranked_clusters],
    )


@app.get("/scores/researchers/{researcher_id}", response_model=EntityScoreResponse, tags=["analysis"])
def get_researcher_score(researcher_id: str) -> EntityScoreResponse:
    """Return the score for a single researcher."""
    scoring = get_report_result().scoring
    for score in scoring.researcher_scores:
        if score.entity_id == researcher_id:
            return entity_score_to_response(score)
    raise HTTPException(status_code=404, detail=f"Researcher score not found: {researcher_id}")


@app.get("/reports", response_model=list[ReportSummaryResponse], tags=["reports"])
def list_reports(
    min_score: int = Query(default=0, ge=0, le=100),
    recommendation: str | None = Query(default=None),
) -> list[ReportSummaryResponse]:
    """List generated founder-monitoring report summaries."""
    reports = get_report_result().reports
    summaries = [report_to_summary(report) for report in reports]

    if min_score:
        summaries = [summary for summary in summaries if summary.startup_likelihood_score >= min_score]
    if recommendation:
        summaries = [summary for summary in summaries if summary.recommendation.value == recommendation]

    summaries.sort(key=lambda summary: (-summary.startup_likelihood_score, summary.researcher_or_cluster))
    return summaries


@app.get("/reports/{report_id}", response_model=ReportDetailResponse, tags=["reports"])
def get_report(report_id: str) -> ReportDetailResponse:
    """Return one founder-monitoring report with markdown content."""
    result = get_report_result()
    for report in result.reports:
        if report.id == report_id:
            return ReportDetailResponse(
                **report.model_dump(),
                markdown=render_report_markdown(report),
            )

    raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")
