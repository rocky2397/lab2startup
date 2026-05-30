#!/usr/bin/env python3
"""Batch monitor CLI — run conferences by fund priority and optional diff digest."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bootstrap_venv import reexec_with_project_venv

reexec_with_project_venv(ROOT)

from app.config import clear_settings_cache, get_settings
from app.fund_profiles import DEFAULT_FUND_ID, load_fund_profile, resolve_conference_list
from app.run_diff_store import load_run_diff
from app.run_service import execute_batch_pipeline_runs
from app.run_store import list_runs


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch pipeline runs for fund conferences and aggregate diff digest.",
    )
    parser.add_argument("--fund", default=DEFAULT_FUND_ID, help="Fund profile ID")
    parser.add_argument(
        "--priority",
        choices=["high", "medium", "low"],
        default="high",
        help="Conference priority filter",
    )
    parser.add_argument("--year", type=int, default=2024, help="Conference year")
    parser.add_argument(
        "--conferences",
        default="",
        help="Comma-separated conferences (overrides priority)",
    )
    parser.add_argument(
        "--digest-only",
        action="store_true",
        help="Skip pipeline; aggregate diffs from stored runs",
    )
    parser.add_argument(
        "--since",
        default="",
        help="ISO date — only include runs created on or after this date (digest-only)",
    )
    parser.add_argument("--json", action="store_true", help="Print digest as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _build_digest(
    *,
    fund_id: str,
    since: str | None,
    db_path: Path,
) -> dict[str, object]:
    runs = list_runs(db_path=db_path, limit=200)
    entries: list[dict[str, object]] = []
    for run in runs:
        if run.fund_profile and run.fund_profile != fund_id:
            continue
        if since and run.created_at < since:
            continue
        diff = load_run_diff(run.id, db_path=db_path)
        if diff is None:
            continue
        entries.append(
            {
                "run_id": run.id,
                "conference": run.conference,
                "year": run.year,
                "prior_run_id": diff.prior_run_id,
                "total_deltas": diff.summary.total_deltas,
                "new_take_meeting": diff.summary.new_take_meeting,
                "new_researchers": diff.summary.new_researchers,
                "score_increases": diff.summary.score_increases,
            }
        )
    return {
        "fund_id": fund_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "since": since,
        "runs_with_diff": len(entries),
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    clear_settings_cache()
    settings = get_settings()
    fund = load_fund_profile(args.fund)

    since = args.since.strip() or None
    if args.digest_only:
        digest = _build_digest(fund_id=fund.id, since=since, db_path=settings.db_path)
        if args.json:
            print(json.dumps(digest, indent=2))
        else:
            print(f"Diff digest for {fund.name}")
            print(f"  Runs with diff: {digest['runs_with_diff']}")
            for entry in digest["entries"][:20]:
                print(
                    f"  - {entry['conference']} {entry['year']}: "
                    f"{entry['total_deltas']} deltas, "
                    f"{entry['new_take_meeting']} new take-meeting"
                )
        return 0

    conference_list = [
        part.strip() for part in args.conferences.split(",") if part.strip()
    ]
    targets = resolve_conference_list(
        fund,
        conferences=conference_list or None,
        priority=args.priority if not conference_list else None,
    )

    logging.info("Monitor batch: %s conferences for %s", len(targets), fund.name)
    results = execute_batch_pipeline_runs(
        conferences=targets,
        year=args.year,
        fund_profile=fund.id,
        settings=settings,
    )

    digest = _build_digest(fund_id=fund.id, since=since, db_path=settings.db_path)
    summary = {
        "fund": fund.name,
        "conferences_run": len(results),
        "run_ids": [run.id for run, _ in results],
        "digest": digest,
    }
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"Monitor complete: {len(results)} run(s) for {fund.name}")
        for run, result in results:
            print(
                f"  - {run.conference} {run.year}: {result.report_count} reports ({run.id})"
            )
        print(f"Digest: {digest['runs_with_diff']} runs with stored diffs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
