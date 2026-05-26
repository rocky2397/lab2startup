"""Report agent — generates founder-monitoring reports (Step 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.agents.scoring_agent import ScoringResult, run_scoring
from app.models import Report
from app.report_generator import (
    build_cluster_report,
    build_researcher_report,
    render_report_markdown,
)


@dataclass
class ReportResult:
    """Generated reports for researchers and clusters."""

    scoring: ScoringResult
    reports: list[Report] = field(default_factory=list)

    @property
    def report_count(self) -> int:
        return len(self.reports)

    def markdown_for(self, report_id: str) -> str | None:
        for report, markdown in zip(self.reports, self.markdown_reports, strict=True):
            if report.id == report_id:
                return markdown
        return None

    @property
    def markdown_reports(self) -> list[str]:
        return [render_report_markdown(report) for report in self.reports]


def generate_reports(
    scoring: ScoringResult,
    *,
    min_score: int = 0,
    include_clusters: bool = True,
) -> list[Report]:
    """Generate reports for scored researchers and optional clusters."""
    detection = scoring.detection
    papers_by_id = {paper.id: paper for paper in detection.papers}
    researchers_by_id = {researcher.id: researcher for researcher in detection.researchers}
    clusters_by_id = {cluster.id: cluster for cluster in detection.clusters}

    reports: list[Report] = []

    for entity_score in scoring.ranked_researchers:
        if entity_score.startup_likelihood_score < min_score:
            continue
        researcher = researchers_by_id[entity_score.entity_id]
        signals = detection.signals_for_researcher(researcher.id)
        reports.append(
            build_researcher_report(
                entity_score,
                researcher,
                signals,
                papers_by_id,
            )
        )

    if include_clusters:
        for entity_score in scoring.ranked_clusters:
            if entity_score.startup_likelihood_score < min_score:
                continue
            cluster = clusters_by_id[entity_score.entity_id]
            member_names = [
                researchers_by_id[member_id].name for member_id in cluster.researchers
            ]
            cluster_signals: list = []
            seen_signal_ids: set[str] = set()
            for member_id in cluster.researchers:
                for signal in detection.signals_for_researcher(member_id):
                    if signal.id not in seen_signal_ids:
                        cluster_signals.append(signal)
                        seen_signal_ids.add(signal.id)

            reports.append(
                build_cluster_report(
                    entity_score,
                    cluster,
                    member_names,
                    cluster_signals,
                )
            )

    return reports


def run_reports(
    papers_path: Path | str | None = None,
    signals_path: Path | str | None = None,
    *,
    papers: list | None = None,
    openalex_config=None,
    openreview_config=None,
    semantic_scholar_config=None,
    github_config=None,
    perplexity_config=None,
    agentic_signal_config=None,
    use_mock_signals: bool = True,
    topic_scores: dict[str, int] | None = None,
    run_id: str | None = None,
    conference: str = "Unknown",
    year: int = 2024,
    min_score: int = 0,
    include_clusters: bool = True,
) -> ReportResult:
    """Run the full pipeline and generate founder-monitoring reports."""
    scoring = run_scoring(
        papers_path,
        signals_path,
        papers=papers,
        openalex_config=openalex_config,
        openreview_config=openreview_config,
        semantic_scholar_config=semantic_scholar_config,
        github_config=github_config,
        perplexity_config=perplexity_config,
        agentic_signal_config=agentic_signal_config,
        use_mock_signals=use_mock_signals,
        topic_scores=topic_scores,
        run_id=run_id,
        conference=conference,
        year=year,
    )
    reports = generate_reports(
        scoring,
        min_score=min_score,
        include_clusters=include_clusters,
    )
    return ReportResult(scoring=scoring, reports=reports)


def write_reports_to_directory(
    result: ReportResult,
    output_dir: Path | str,
) -> list[Path]:
    """Write markdown reports to files and return written paths."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for report, markdown in zip(result.reports, result.markdown_reports, strict=True):
        path = directory / f"{report.id}.md"
        path.write_text(markdown, encoding="utf-8")
        written.append(path)
    return written


def summarize_reports(result: ReportResult) -> dict[str, object]:
    """Return quick stats for inspecting generated reports."""
    top_reports = [
        {
            "id": report.id,
            "subject": report.researcher_or_cluster,
            "score": report.startup_likelihood_score,
            "recommendation": report.recommendation.value,
        }
        for report in sorted(
            result.reports,
            key=lambda report: (-report.startup_likelihood_score, report.researcher_or_cluster),
        )[:5]
    ]
    return {
        "report_count": result.report_count,
        "researcher_reports": sum(1 for report in result.reports if report.id.startswith("report_researcher_")),
        "cluster_reports": sum(1 for report in result.reports if report.id.startswith("report_cluster_")),
        "top_reports": top_reports,
    }
