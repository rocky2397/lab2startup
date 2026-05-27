#!/usr/bin/env python3
"""Inspect and re-run researcher enrichment for a stored pipeline run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bootstrap_venv import reexec_with_project_venv

reexec_with_project_venv(ROOT)

from app.config import clear_settings_cache, get_settings
from app.enrichment_audit import (
    rerun_enrichment_verification,
    summarize_enrichment_audit,
)
from app.run_store import get_run, load_enrichment_audit, load_run_result, save_enrichment_audit


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect enrichment verification data for a stored run, or re-run Sonar "
            "enrichment against the saved pre-enrichment snapshot."
        ),
    )
    parser.add_argument("--run-id", required=True, help="Pipeline run id from SQLite")
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Re-run Perplexity Sonar enrichment on the saved pre-enrichment snapshot",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="When used with --rerun, overwrite the stored enrichment audit",
    )
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="Write the audit JSON to a file",
    )
    parser.add_argument(
        "--show-unknown",
        action="store_true",
        help="Print researchers still unknown after enrichment",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    clear_settings_cache()
    settings = get_settings()

    run = get_run(args.run_id, db_path=settings.db_path)
    if run is None:
        print(f"Run not found: {args.run_id}", file=sys.stderr)
        return 1

    audit = load_enrichment_audit(args.run_id, db_path=settings.db_path)
    if audit is None:
        print(
            f"No enrichment audit saved for {args.run_id}. Re-run the pipeline first.",
            file=sys.stderr,
        )
        return 1

    if args.rerun:
        result = load_run_result(args.run_id, db_path=settings.db_path)
        if result is None:
            print(f"No snapshot found for run: {args.run_id}", file=sys.stderr)
            return 1
        audit = rerun_enrichment_verification(
            run_id=args.run_id,
            papers=result.scoring.detection.papers,
            audit=audit,
            settings=settings,
        )
        if args.save:
            save_enrichment_audit(args.run_id, audit, db_path=settings.db_path)
            print(f"Saved updated enrichment audit for {args.run_id}")

    summary = summarize_enrichment_audit(audit)
    print(json.dumps(summary, indent=2))

    enriched_lines = summary.get("enriched_profile_lines") or []
    if enriched_lines:
        print("\nProfiles enriched:")
        for line in enriched_lines:
            print(f"- {line}")

    investigated = summary.get("investigated_profile_names") or []
    if investigated:
        print("\nProfiles investigated:")
        print(", ".join(investigated))

    no_change = summary.get("investigated_no_change_profiles") or []
    if no_change:
        print("\nInvestigated — no change:")
        for profile in no_change:
            print(f"- {profile['name']}")

    if args.show_unknown:
        print("\nStill unknown:")
        for record in audit.records:
            if record.post_affiliation.strip().lower() in {"", "unknown", "n/a", "na"}:
                print(f"- {record.name}: status={record.status}, skip={record.skip_reason or 'n/a'}")

    if args.export:
        from app.enrichment_audit import serialize_enrichment_audit

        export_path = Path(args.export)
        export_path.write_text(serialize_enrichment_audit(audit), encoding="utf-8")
        print(f"\nWrote audit to {export_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
