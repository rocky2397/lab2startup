#!/usr/bin/env python3
"""CLI to compute or recompute run diff for a stored pipeline run."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bootstrap_venv import reexec_with_project_venv

reexec_with_project_venv(ROOT)

from app.agents.diff_agent import compute_run_diff
from app.config import clear_settings_cache, get_settings
from app.run_diff_store import load_run_diff, save_run_diff
from app.run_store import find_prior_complete_run, get_run, load_run_result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute diff vs prior complete run.")
    parser.add_argument("--run-id", required=True, help="Pipeline run ID")
    parser.add_argument("--score-threshold", type=int, default=5, help="Min score delta to report")
    parser.add_argument("--json", action="store_true", help="Print diff JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    clear_settings_cache()
    settings = get_settings()
    db_path = settings.db_path

    run = get_run(args.run_id, db_path=db_path)
    if run is None:
        logging.error("Run not found: %s", args.run_id)
        return 1

    current = load_run_result(args.run_id, db_path=db_path)
    if current is None:
        logging.error("No snapshot for run: %s", args.run_id)
        return 1

    prior_run = find_prior_complete_run(
        conference=run.conference,
        year=run.year,
        paper_source=run.paper_source,
        fund_profile=run.fund_profile,
        exclude_run_id=run.id,
        before_created_at=run.created_at,
        db_path=db_path,
    )
    prior_result = load_run_result(prior_run.id, db_path=db_path) if prior_run else None

    diff = compute_run_diff(
        current,
        prior_result,
        run_id=run.id,
        prior_run_id=prior_run.id if prior_run else None,
        conference=run.conference,
        year=run.year,
        fund_profile=run.fund_profile,
        score_threshold=args.score_threshold,
    )
    save_run_diff(run.id, diff, db_path=db_path)

    if args.json:
        print(json.dumps(diff.model_dump(mode="json"), indent=2))
    else:
        print(f"Diff for {run.id}")
        print(f"  Prior run: {diff.prior_run_id or 'none'}")
        print(f"  Deltas: {diff.summary.total_deltas}")
        print(f"  New take meeting: {diff.summary.new_take_meeting}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
