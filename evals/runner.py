"""Execute a golden-set eval run — shared by run_eval.py and the desktop app API."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from evals.harness import (
    build_papers,
    classify_predictions,
    compute_metrics,
    load_golden_set,
    render_markdown_report,
)

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def execute_eval(
    mode: str,
    *,
    golden_path: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Run the golden set through the live pipeline and persist md + json artifacts.

    mode: "sonar" (one query per researcher) or "agentic" (LangGraph + Agent API).
    The eval disables the prefilter and early exit so every researcher is measured.
    """
    if mode not in ("sonar", "agentic"):
        raise ValueError(f"Unknown eval mode: {mode}")

    settings = get_settings()
    if not settings.perplexity_config.api_key:
        raise RuntimeError("LAB2STARTUP_PERPLEXITY_API_KEY is not set — the eval queries the live web.")

    golden = load_golden_set(golden_path)
    papers = build_papers(golden)

    perplexity_config = replace(settings.perplexity_config, enabled=True)
    agentic_config = None
    if mode == "agentic":
        # prefilter_min_score=0: the eval measures investigation quality, so every
        # golden-set researcher is investigated. (With fund-tuned defaults the
        # generic-topic golden papers all score below the prefilter threshold.)
        # early_exit=False: production stops the queue on the first high-confidence
        # founder hit; the eval needs every researcher investigated and measured.
        agentic_config = replace(
            settings.agentic_signal_config,
            enabled=True,
            db_path=Path(settings.db_path),
            prefilter_min_score=0.0,
            early_exit=False,
        )

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

    out_dir = Path(output_dir) if output_dir else RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"eval_{mode}_{stamp}.md"
    md_path.write_text(report_md, encoding="utf-8")
    json_path = out_dir / f"eval_{mode}_{stamp}.json"
    payload = {
        "meta": run_meta,
        "strict": strict,
        "lenient": lenient,
        "rows": [vars(row) for row in rows],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "meta": run_meta,
        "strict": strict,
        "lenient": lenient,
        "md_path": str(md_path),
        "json_path": str(json_path),
        "markdown": report_md,
    }


def list_eval_results(output_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """Summaries of stored eval results, newest first."""
    out_dir = Path(output_dir) if output_dir else RESULTS_DIR
    if not out_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for json_path in sorted(out_dir.glob("eval_*.json"), reverse=True):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        results.append(
            {
                "name": json_path.stem,
                "meta": payload.get("meta", {}),
                "strict": payload.get("strict", {}),
                "lenient": payload.get("lenient", {}),
            }
        )
    return results


def load_eval_result(name: str, output_dir: Path | str | None = None) -> dict[str, Any] | None:
    """Full stored eval result (metrics, rows, markdown) by artifact name."""
    out_dir = Path(output_dir) if output_dir else RESULTS_DIR
    if "/" in name or ".." in name:
        return None
    json_path = out_dir / f"{name}.json"
    if not json_path.is_file():
        return None
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    md_path = out_dir / f"{name}.md"
    payload["markdown"] = md_path.read_text(encoding="utf-8") if md_path.is_file() else None
    payload["name"] = name
    return payload
