#!/usr/bin/env python3
"""Export stored pipeline runs to portable JSON bundles under .cache/exports/."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bootstrap_venv import reexec_with_project_venv

reexec_with_project_venv(ROOT)

from app.config import clear_settings_cache, get_settings
from app.run_export import export_run, export_runs, list_runs_matching


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export one or more pipeline runs from SQLite to .cache/exports/ "
            "as JSON bundles with cost summaries."
        ),
    )
    parser.add_argument("--run-id", help="Export a single pipeline run id")
    parser.add_argument(
        "--run-ids",
        help="Comma-separated run ids to export as one bundle",
    )
    parser.add_argument(
        "--batch-date",
        help="Export all complete runs whose id contains this date stamp (e.g. 20260528)",
    )
    parser.add_argument(
        "--latest-per-conference",
        action="store_true",
        help="With --batch-date, keep only the newest complete run per conference",
    )
    parser.add_argument(
        "--label",
        help="Folder name under .cache/exports/ (default: run id or batch timestamp)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override export root (default: .cache/exports)",
    )
    parser.add_argument(
        "--include-trace-payloads",
        action="store_true",
        help="Also export raw agent request/response JSON (larger files)",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    clear_settings_cache()
    settings = get_settings()

    run_ids = list_runs_matching(
        run_id=args.run_id,
        run_ids=[part.strip() for part in (args.run_ids or "").split(",") if part.strip()] or None,
        batch_date=args.batch_date,
        latest_per_conference=args.latest_per_conference,
        db_path=settings.db_path,
    )
    if not run_ids:
        print("No matching complete runs found to export.", file=sys.stderr)
        return 1

    output_root = args.output_dir or (ROOT / ".cache" / "exports")

    if len(run_ids) == 1 and not args.label and not args.batch_date:
        export_run(
            run_ids[0],
            output_root,
            db_path=settings.db_path,
            include_trace_payloads=args.include_trace_payloads,
        )
        print(f"Exported 1 run to {output_root / run_ids[0]}")
        print(f"  run_id: {run_ids[0]}")
        return 0

    bundle_dir = export_runs(
        run_ids,
        output_root,
        db_path=settings.db_path,
        label=args.label,
        include_trace_payloads=args.include_trace_payloads,
    )
    manifest_path = bundle_dir / "manifest.json"
    print(f"Exported {len(run_ids)} run(s) to {bundle_dir}")
    print(f"  manifest: {manifest_path}")
    if (bundle_dir / "lab2startup_source.db").exists():
        print(f"  db copy:  {bundle_dir / 'lab2startup_source.db'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
