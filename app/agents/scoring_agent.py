"""Scoring agent — computes component scores and startup likelihood (Step 6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.agents.signal_agent import SignalDetectionResult, detect_signals
from app.models import Cluster
from app.scoring import EntityScore, rank_entity_scores, score_cluster, score_researcher


@dataclass
class ScoringResult:
    """Full pipeline output with ranked researcher and cluster scores."""

    detection: SignalDetectionResult
    researcher_scores: list[EntityScore] = field(default_factory=list)
    cluster_scores: list[EntityScore] = field(default_factory=list)

    @property
    def ranked_researchers(self) -> list[EntityScore]:
        return rank_entity_scores(self.researcher_scores)

    @property
    def ranked_clusters(self) -> list[EntityScore]:
        return rank_entity_scores(self.cluster_scores)

    @property
    def top_researcher(self) -> EntityScore | None:
        ranked = self.ranked_researchers
        return ranked[0] if ranked else None


def compute_scores(detection: SignalDetectionResult) -> ScoringResult:
    """Score all researchers and clusters from a signal detection result."""
    papers_by_id = {paper.id: paper for paper in detection.papers}
    signals_by_researcher = {
        researcher.id: detection.signals_for_researcher(researcher.id)
        for researcher in detection.researchers
    }

    researcher_scores = [
        score_researcher(
            researcher,
            papers_by_id,
            signals_by_researcher[researcher.id],
        )
        for researcher in detection.researchers
    ]
    researcher_score_map = {score.entity_id: score for score in researcher_scores}

    cluster_scores = [
        score_cluster(cluster, researcher_score_map) for cluster in detection.clusters
    ]

    # Attach score to cluster objects for downstream report/API use.
    for cluster, cluster_score in zip(detection.clusters, cluster_scores, strict=True):
        cluster.score = float(cluster_score.startup_likelihood_score)

    return ScoringResult(
        detection=detection,
        researcher_scores=researcher_scores,
        cluster_scores=cluster_scores,
    )


def run_scoring(
    papers_path: Path | str | None = None,
    signals_path: Path | str | None = None,
    *,
    papers: list | None = None,
    openalex_config=None,
) -> ScoringResult:
    """Run the full pipeline through scoring."""
    detection = detect_signals(
        papers_path,
        signals_path,
        papers=papers,
        openalex_config=openalex_config,
    )
    return compute_scores(detection)


def summarize_scoring(result: ScoringResult) -> dict[str, object]:
    """Return quick stats for inspecting scoring output."""
    top_researchers = [
        {
            "name": score.entity_name,
            "score": score.startup_likelihood_score,
            "priority_band": score.priority_band.value,
            "recommendation": score.recommendation.value,
        }
        for score in result.ranked_researchers[:5]
    ]
    return {
        "researcher_count": len(result.researcher_scores),
        "cluster_count": len(result.cluster_scores),
        "top_researchers": top_researchers,
        "top_researcher_score": result.top_researcher.startup_likelihood_score
        if result.top_researcher
        else None,
    }
