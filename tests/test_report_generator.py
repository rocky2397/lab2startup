"""Tests for founder-monitoring report generation (Step 7)."""

from pathlib import Path

from app.agents.report_agent import generate_reports, run_reports, write_reports_to_directory
from app.agents.scoring_agent import run_scoring
from app.models import SignalType
from app.report_generator import build_researcher_report, render_report_markdown


def test_render_report_markdown_includes_core_sections() -> None:
    result = run_reports(include_clusters=False)
    marinka_report = next(
        report for report in result.reports if report.researcher_or_cluster == "Marinka Zitnik"
    )
    markdown = render_report_markdown(marinka_report)

    assert "# Founder Monitoring Report: Marinka Zitnik" in markdown
    assert "## Score Breakdown" in markdown
    assert "## Detected Signals" in markdown
    assert "## Open Questions" in markdown
    assert "Confirmed founder" in markdown


def test_generate_reports_for_researchers_and_clusters() -> None:
    scoring = run_scoring()
    reports = generate_reports(scoring)

    assert len(reports) == 37
    assert any(report.id.startswith("report_researcher_") for report in reports)
    assert any(report.id.startswith("report_cluster_") for report in reports)


def test_researcher_report_uses_signals_and_papers() -> None:
    scoring = run_scoring()
    detection = scoring.detection
    marinka = next(r for r in detection.researchers if r.name == "Marinka Zitnik")
    entity_score = next(s for s in scoring.researcher_scores if s.entity_id == marinka.id)
    signals = detection.signals_for_researcher(marinka.id)
    papers_by_id = {paper.id: paper for paper in detection.papers}

    report = build_researcher_report(entity_score, marinka, signals, papers_by_id)
    assert report.startup_likelihood_score == entity_score.startup_likelihood_score
    assert report.signals[0].signal_type == SignalType.CONFIRMED_FOUNDER
    assert "PocketFlow" in report.summary or "protein" in report.summary.lower()


def test_write_reports_to_directory(tmp_path: Path) -> None:
    result = run_reports(include_clusters=False, min_score=70)
    paths = write_reports_to_directory(result, tmp_path)

    assert paths
    assert paths[0].exists()
    assert paths[0].read_text(encoding="utf-8").startswith("# Founder Monitoring Report")
