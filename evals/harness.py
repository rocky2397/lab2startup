"""Golden-set evaluation harness — measures founder-signal detection quality.

The eval feeds the pipeline exactly what it would see from a conference
(name, affiliation, paper title) for researchers whose founder status is
independently verified, then compares detected signals against the labels.
It measures *detection* of public founding evidence, not prophecy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.ingestion_agent import make_researcher_id
from app.models import Paper, PaperAuthor, SignalType

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.json"


class GoldenPaper(BaseModel):
    """One conference paper the researcher is known to have co-authored."""

    title: str
    conference: str = "NeurIPS"
    year: int
    topic: str = "machine learning"


class GoldenResearcher(BaseModel):
    """A researcher with independently verified founder / non-founder status."""

    name: str
    label: Literal["founder", "non_founder"]
    company: str | None = None
    founded_year: int | None = None
    evidence_url: str | None = None
    affiliation_at_pub: str
    role: str = "Researcher"
    papers: list[GoldenPaper] = Field(min_length=1)
    verified: bool = False
    notes: str = ""

    @property
    def researcher_id(self) -> str:
        return make_researcher_id(self.name)


class GoldenSet(BaseModel):
    """Root of golden_set.json."""

    description: str = ""
    as_of: str = ""
    researchers: list[GoldenResearcher]

    def founders(self) -> list[GoldenResearcher]:
        return [r for r in self.researchers if r.label == "founder"]

    def non_founders(self) -> list[GoldenResearcher]:
        return [r for r in self.researchers if r.label == "non_founder"]


def load_golden_set(path: Path | str | None = None) -> GoldenSet:
    """Load and validate the golden set JSON."""
    file_path = Path(path) if path else GOLDEN_SET_PATH
    return GoldenSet.model_validate(json.loads(file_path.read_text(encoding="utf-8")))


def _paper_slug(title: str, year: int) -> str:
    slug = make_researcher_id(title).removeprefix("researcher_")
    return f"paper_eval_{year}_{slug[:60]}"


def build_papers(golden: GoldenSet) -> list[Paper]:
    """Build Paper objects from the golden set — the pipeline's only input.

    Researchers sharing a paper title/year are merged onto one paper so
    coauthor clustering behaves as it would on real conference data.
    """
    papers: dict[str, Paper] = {}
    for researcher in golden.researchers:
        for golden_paper in researcher.papers:
            paper_id = _paper_slug(golden_paper.title, golden_paper.year)
            author = PaperAuthor(
                name=researcher.name,
                affiliation=researcher.affiliation_at_pub,
                role=researcher.role,
            )
            if paper_id in papers:
                papers[paper_id].authors.append(author)
            else:
                papers[paper_id] = Paper(
                    id=paper_id,
                    title=golden_paper.title,
                    conference=golden_paper.conference,
                    year=golden_paper.year,
                    topic=golden_paper.topic,
                    abstract="",
                    authors=[author],
                )
    return list(papers.values())


FOUNDER_SIGNAL_TYPES_STRICT = {SignalType.CONFIRMED_FOUNDER}
FOUNDER_SIGNAL_TYPES_LENIENT = {SignalType.CONFIRMED_FOUNDER, SignalType.POSSIBLE_FOUNDER}


@dataclass
class PredictionRow:
    """Prediction outcome for one golden-set researcher."""

    name: str
    label: str
    researcher_id: str
    profile_found: bool
    signal_count: int
    best_signal_type: str | None
    best_signal_strength: str | None
    best_signal_url: str | None
    predicted_strict: bool
    predicted_lenient: bool
    startup_score: int | None
    recommendation: str | None
    company: str | None = None
    verified: bool = False


def classify_predictions(result, golden: GoldenSet) -> list[PredictionRow]:
    """Compare pipeline output against golden labels, one row per researcher."""
    detection = result.scoring.detection
    researchers_by_id = {researcher.id: researcher for researcher in detection.researchers}
    reports_by_id = {report.id: report for report in result.reports}

    type_rank = {
        SignalType.CONFIRMED_FOUNDER: 0,
        SignalType.POSSIBLE_FOUNDER: 1,
        SignalType.COMMERCIALIZATION: 2,
        SignalType.NO_SIGNAL: 3,
    }
    strength_rank = {"high": 0, "medium": 1, "low": 2}

    rows: list[PredictionRow] = []
    for entry in golden.researchers:
        rid = entry.researcher_id
        signals = [signal for signal in detection.signals if signal.researcher_id == rid]
        signals.sort(
            key=lambda signal: (
                type_rank.get(signal.signal_type, 9),
                strength_rank.get(signal.evidence_strength.value, 9),
            )
        )
        best = signals[0] if signals else None
        signal_types = {signal.signal_type for signal in signals}
        report = reports_by_id.get(f"report_{rid}")
        rows.append(
            PredictionRow(
                name=entry.name,
                label=entry.label,
                researcher_id=rid,
                profile_found=rid in researchers_by_id,
                signal_count=len(signals),
                best_signal_type=best.signal_type.value if best else None,
                best_signal_strength=best.evidence_strength.value if best else None,
                best_signal_url=best.source_url if best else None,
                predicted_strict=bool(signal_types & FOUNDER_SIGNAL_TYPES_STRICT),
                predicted_lenient=bool(signal_types & FOUNDER_SIGNAL_TYPES_LENIENT),
                startup_score=report.startup_likelihood_score if report else None,
                recommendation=report.recommendation.value if report else None,
                company=entry.company,
                verified=entry.verified,
            )
        )
    return rows


def compute_metrics(rows: list[PredictionRow], *, lenient: bool) -> dict[str, float | int]:
    """Precision / recall / FPR for founder detection at one threshold."""
    tp = fp = fn = tn = 0
    for row in rows:
        predicted = row.predicted_lenient if lenient else row.predicted_strict
        actual = row.label == "founder"
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and actual:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    accuracy = (tp + tn) / len(rows) if rows else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "false_positive_rate": round(fpr, 3),
        "accuracy": round(accuracy, 3),
    }


def _metrics_table(strict: dict, lenient: dict) -> str:
    header = "| Threshold | TP | FP | FN | TN | Precision | Recall | FPR | Accuracy |"
    rule = "|---|---|---|---|---|---|---|---|---|"
    line = (
        "| {name} | {tp} | {fp} | {fn} | {tn} | {precision} | {recall} "
        "| {false_positive_rate} | {accuracy} |"
    )
    return "\n".join(
        [
            header,
            rule,
            line.format(name="Strict (confirmed_founder only)", **strict),
            line.format(name="Lenient (confirmed + possible)", **lenient),
        ]
    )


def render_markdown_report(
    golden: GoldenSet,
    rows: list[PredictionRow],
    *,
    mode: str,
    run_meta: dict | None = None,
) -> str:
    """Human-readable eval report, ready to link from the README."""
    strict = compute_metrics(rows, lenient=False)
    lenient = compute_metrics(rows, lenient=True)
    unverified = sum(1 for entry in golden.researchers if not entry.verified)

    lines = [
        "# Founder-signal detection eval",
        "",
        f"Golden set: **{len(golden.researchers)} researchers** "
        f"({len(golden.founders())} verified founders, {len(golden.non_founders())} non-founders) · "
        f"signal mode: **{mode}** · ground truth as of {golden.as_of or 'n/a'}",
        "",
    ]
    if unverified:
        lines += [
            f"> ⚠️ {unverified} golden-set entries are not yet marked `verified` — treat results as preliminary.",
            "",
        ]
    if run_meta:
        lines += ["```json", json.dumps(run_meta, indent=2), "```", ""]

    lines += ["## Metrics", "", _metrics_table(strict, lenient), ""]

    lines += [
        "## Per-researcher results",
        "",
        "| Researcher | Truth | Company | Detected | Best signal | Strength | Score | Evidence found |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda r: (r.label != "founder", -(r.startup_score or 0))):
        truth = "founder" if row.label == "founder" else "—"
        detected = "✅" if row.predicted_strict else ("🟡" if row.predicted_lenient else "—")
        url = f"[link]({row.best_signal_url})" if row.best_signal_url else "—"
        lines.append(
            f"| {row.name} | {truth} | {row.company or '—'} | {detected} "
            f"| {row.best_signal_type or '—'} | {row.best_signal_strength or '—'} "
            f"| {row.startup_score if row.startup_score is not None else '—'} | {url} |"
        )

    misses = [row for row in rows if row.label == "founder" and not row.predicted_lenient]
    false_positives = [row for row in rows if row.label != "founder" and row.predicted_lenient]
    lines += ["", "## Failure modes", ""]
    if misses:
        lines.append("**Missed founders:** " + ", ".join(row.name for row in misses))
    if false_positives:
        lines.append("**False positives:** " + ", ".join(row.name for row in false_positives))
    if not misses and not false_positives:
        lines.append("No misses or false positives at the lenient threshold.")
    lines.append("")
    return "\n".join(lines)
