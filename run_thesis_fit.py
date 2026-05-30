#!/usr/bin/env python3
"""CLI to compute or backfill thesis fit for a stored pipeline run."""

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

from app.agents.thesis_fit_agent import run_thesis_fit_agent
from app.config import clear_settings_cache, get_settings
from app.fund_profiles import load_fund_profile
from app.run_store import get_run, load_run_result
from app.thesis_fit_store import load_thesis_fit, save_thesis_fit


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute thesis fit for a stored run.")
    parser.add_argument("--run-id", required=True, help="Pipeline run ID")
    parser.add_argument("--fund", default=None, help="Fund profile ID (default from run)")
    parser.add_argument("--no-sonar", action="store_true", help="Rules-only assessment")
    parser.add_argument("--json", action="store_true", help="Print assessments JSON")
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

    result = load_run_result(args.run_id, db_path=db_path)
    if result is None:
        logging.error("No snapshot for run: %s", args.run_id)
        return 1

    fund_id = args.fund or run.fund_profile or settings.fund_id
    try:
        fund = load_fund_profile(fund_id)
    except FileNotFoundError:
        logging.error("Fund profile not found: %s", fund_id)
        return 1

    if fund.thesis_fit is None:
        logging.error("Fund %s has no thesis_fit configuration", fund_id)
        return 1

    assessments = run_thesis_fit_agent(
        result,
        fund=fund,
        settings=settings,
        perplexity_config=settings.perplexity_config,
        sonar_min_score=settings.thesis_sonar_min_score,
        sonar_max_calls=settings.thesis_sonar_max_calls,
        use_sonar=not args.no_sonar,
    )
    save_thesis_fit(run.id, assessments, db_path=db_path)

    if args.json:
        payload = {rid: a.model_dump(mode="json") for rid, a in assessments.items()}
        print(json.dumps(payload, indent=2))
    else:
        strong = sum(1 for a in assessments.values() if a.fit_level.value == "strong")
        moderate = sum(1 for a in assessments.values() if a.fit_level.value == "moderate")
        sonar_used = sum(1 for a in assessments.values() if a.sonar_used)
        print(f"Thesis fit for {run.id} ({fund.name})")
        print(f"  Researchers: {len(assessments)}")
        print(f"  Strong: {strong} · Moderate: {moderate}")
        print(f"  Sonar calls: {sonar_used}")

    existing = load_thesis_fit(run.id, db_path=db_path)
    if existing is None:
        logging.warning("Save verification failed for %s", run.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
