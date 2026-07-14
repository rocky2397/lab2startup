"""Run the golden-set founder-detection eval against the live signal pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bootstrap_venv import reexec_with_project_venv

reexec_with_project_venv(ROOT)

from app.agents.ingestion_agent import extract_researchers
from app.config import get_settings
from evals.harness import (
    build_papers,
    classify_predictions,
    compute_metrics,
    load_golden_set,
    render_markdown_report,
)

RESULTS_DIR = ROOT / "evals" / "results"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Golden-set eval: does the pipeline detect known founders?")
    parser.add_argument("--golden", default=None, help="Path to golden_set.json (default: evals/golden_set.json)")
    parser.add_argument("--dry-run", action="store_true", help="Show who would be investigated; no API calls")
    parser.add_argument(
        "--agentic", action="store_true", help="Use the LangGraph + Agent API path instead of one-shot Sonar"
    )
    parser.add_argument("--yes", action="store_true", help="Skip the cost confirmation prompt")
    parser.add_argument("--output-dir", default=str(RESULTS_DIR), help="Directory for result reports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    golden = load_golden_set(args.golden)
    papers = build_papers(golden)
    researchers = extract_researchers(papers)

    unverified = [entry.name for entry in golden.researchers if not entry.verified]
    print(
        f"Golden set: {len(golden.researchers)} researchers "
        f"({len(golden.founders())} founders / {len(golden.non_founders())} non-founders), "
        f"{len(papers)} papers, {len(unverified)} entries not yet verified."
    )

    if args.dry_run:
        print("\nWould investigate (identity confidence from paper data):")
        for researcher in researchers:
            confidence = researcher.identity_confidence.value
            print(f"  {researcher.name:28s} {researcher.affiliation:32s} identity={confidence}")
        return 0

    settings = get_settings()
    if not settings.perplexity_config.api_key:
        print("ERROR: LAB2STARTUP_PERPLEXITY_API_KEY is not set — the eval queries the live web.", file=sys.stderr)
        return 1

    mode = "agentic" if args.agentic else "sonar"
    print(f"\nSignal mode: {mode} · model: {settings.perplexity_config.model}")
    print(f"This will make roughly {len(researchers)} researcher investigations via the Perplexity API.")
    if not args.yes:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1

    perplexity_config = replace(settings.perplexity_config, enabled=True)
    agentic_config = None
    if args.agentic:
        agentic_config = replace(settings.agentic_signal_config, enabled=True, db_path=Path(settings.db_path))

    from app.agents.report_agent import run_reports

    started = datetime.now(UTC)
    result = run_reports(
        papers=papers,
        perplexity_config=perplexity_config,
        agentic_signal_config=agentic_config,
        use_mock_signals=False,
        topic_scores=settings.topic_scores,
        conference="NeurIPS",
        year=max(paper.year for paper in papers),
        run_id=f"eval_{started.strftime('%Y%m%dT%H%M%S')}",
    )

    rows = classify_predictions(result, golden)
    strict = compute_metrics(rows, lenient=False)
    lenient = compute_metrics(rows, lenient=True)
    run_meta = {
        "mode": mode,
        "model": settings.perplexity_config.model,
        "started_at": started.isoformat(),
        "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
    }
    report_md = render_markdown_report(golden, rows, mode=mode, run_meta=run_meta)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%d_%H%M%S")
    md_path = output_dir / f"eval_{mode}_{stamp}.md"
    md_path.write_text(report_md, encoding="utf-8")
    json_path = output_dir / f"eval_{mode}_{stamp}.json"
    payload = {"meta": run_meta, "strict": strict, "lenient": lenient, "rows": [vars(row) for row in rows]}
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"\nStrict:  precision={strict['precision']} recall={strict['recall']} fpr={strict['false_positive_rate']}")
    print(f"Lenient: precision={lenient['precision']} recall={lenient['recall']} fpr={lenient['false_positive_rate']}")
    print(f"\nReport: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
