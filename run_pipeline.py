#!/usr/bin/env python3
"""CLI entry point for monthly Backtrace-scoped conference sourcing runs (Step 12/15)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.config import clear_settings_cache, get_settings
from app.fund_profiles import DEFAULT_FUND_ID, list_fund_profiles, load_fund_profile
from app.run_service import execute_pipeline_run


def build_arg_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    default_conference = "NeurIPS"
    if settings.fund_profile and settings.fund_profile.conferences:
        default_conference = settings.fund_profile.conferences[0].name

    parser = argparse.ArgumentParser(
        description=(
            "Run the Lab2Startup pipeline for a Backtrace-relevant conference "
            "and store results in SQLite."
        ),
    )
    parser.add_argument(
        "--conference",
        default=default_conference,
        help="Conference name (must be in the active fund profile)",
    )
    parser.add_argument("--year", type=int, default=2024, help="Conference year")
    parser.add_argument(
        "--paper-source",
        choices=["openreview", "openalex", "json"],
        default=None,
        help="Paper ingestion source (default: auto from fund + conference)",
    )
    parser.add_argument(
        "--topics",
        default="",
        help="Comma-separated topic filters (OpenAlex; defaults to fund keywords)",
    )
    parser.add_argument(
        "--fund",
        default=None,
        help=f"Fund profile name (default: {DEFAULT_FUND_ID})",
    )
    parser.add_argument(
        "--list-conferences",
        action="store_true",
        help="List conferences in scope for the fund profile and exit",
    )
    parser.add_argument(
        "--use-mock-signals",
        action="store_true",
        help="Include sample_signals.json (development only)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print run summary as JSON",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.use_mock_signals:
        import os

        os.environ["LAB2STARTUP_USE_MOCK_SIGNALS"] = "true"
    clear_settings_cache()
    settings = get_settings()

    fund_id = args.fund or settings.fund_id or DEFAULT_FUND_ID
    fund = load_fund_profile(fund_id)

    if args.list_conferences:
        rows = [
            {
                "name": conference.name,
                "sources": list(conference.sources),
                "priority": conference.priority,
            }
            for conference in fund.conferences
        ]
        print(json.dumps({"fund": fund.name, "conferences": rows}, indent=2))
        return 0

    topics = [part.strip() for part in args.topics.split(",") if part.strip()]

    try:
        run, result = execute_pipeline_run(
            conference=args.conference,
            year=args.year,
            paper_source=args.paper_source,
            fund_profile=fund_id,
            topics=topics,
            settings=settings,
        )
    except Exception as exc:
        logging.error("Run failed: %s", exc)
        return 1

    summary = {
        "run_id": run.id,
        "status": run.status.value,
        "fund": fund.name,
        "conference": run.conference,
        "year": run.year,
        "paper_source": run.paper_source,
        "paper_count": len(result.scoring.detection.papers),
        "researcher_count": len(result.scoring.detection.researchers),
        "signal_count": len(result.scoring.detection.signals),
        "report_count": result.report_count,
        "db_path": str(settings.db_path),
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Run complete: {run.id}")
        print(f"  Fund: {fund.name}")
        print(f"  Conference: {run.conference} {run.year} ({run.paper_source})")
        print(f"  Papers: {summary['paper_count']}")
        print(f"  Researchers: {summary['researcher_count']}")
        print(f"  Signals: {summary['signal_count']}")
        print(f"  Reports: {summary['report_count']}")
        print(f"  Database: {settings.db_path}")
        print("\nOpen the dashboard to review results:")
        print("  python run_dashboard.py")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
