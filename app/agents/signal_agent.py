"""Signal agent — attaches commercialization signals to researchers/clusters (Step 5)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.profile_agent import ProfileResult, build_profiles
from app.models import Cluster, Researcher, Signal
from app.schemas import load_signals


def _find_cluster_id(researcher_id: str, clusters: list[Cluster]) -> str | None:
    """Return the cluster ID for a researcher, if they belong to a cluster."""
    matches = [cluster.id for cluster in clusters if researcher_id in cluster.researchers]
    return matches[0] if matches else None


def resolve_signal(
    signal: Signal,
    researchers_by_name: dict[str, Researcher],
    clusters: list[Cluster],
) -> tuple[Signal, bool]:
    """Attach researcher and cluster IDs to a signal when the name matches."""
    if not signal.researcher_name:
        return signal, False

    researcher = researchers_by_name.get(signal.researcher_name)
    if researcher is None:
        return signal, False

    cluster_id = _find_cluster_id(researcher.id, clusters)
    resolved = signal.model_copy(
        update={
            "researcher_id": researcher.id,
            "cluster_id": cluster_id,
        }
    )
    return resolved, True


def attach_signals(
    raw_signals: list[Signal],
    researchers: list[Researcher],
    clusters: list[Cluster],
) -> tuple[list[Signal], list[str]]:
    """Resolve signal names to researcher IDs and attach cluster IDs."""
    researchers_by_name = {researcher.name: researcher for researcher in researchers}
    resolved_signals: list[Signal] = []
    unmatched_names: list[str] = []

    for signal in raw_signals:
        resolved, matched = resolve_signal(signal, researchers_by_name, clusters)
        resolved_signals.append(resolved)
        if signal.researcher_name and not matched:
            unmatched_names.append(signal.researcher_name)

    return resolved_signals, unmatched_names


def group_signals_by_researcher(signals: list[Signal]) -> dict[str, list[Signal]]:
    """Index resolved signals by researcher ID."""
    grouped: dict[str, list[Signal]] = defaultdict(list)
    for signal in signals:
        if signal.researcher_id:
            grouped[signal.researcher_id].append(signal)
    return dict(grouped)


def group_signals_by_cluster(signals: list[Signal]) -> dict[str, list[Signal]]:
    """Index resolved signals by cluster ID."""
    grouped: dict[str, list[Signal]] = defaultdict(list)
    for signal in signals:
        if signal.cluster_id:
            grouped[signal.cluster_id].append(signal)
    return dict(grouped)


@dataclass
class SignalDetectionResult:
    """Output after loading mock signals and attaching them to the pipeline."""

    papers: list
    researchers: list[Researcher]
    clusters: list[Cluster]
    signals: list[Signal] = field(default_factory=list)
    unmatched_researcher_names: list[str] = field(default_factory=list)

    @property
    def matched_signal_count(self) -> int:
        return sum(1 for signal in self.signals if signal.researcher_id)

    def signals_for_researcher(self, researcher_id: str) -> list[Signal]:
        return [signal for signal in self.signals if signal.researcher_id == researcher_id]

    def signals_for_cluster(self, cluster_id: str) -> list[Signal]:
        return [signal for signal in self.signals if signal.cluster_id == cluster_id]


def detect_signals(
    papers_path: Path | str | None = None,
    signals_path: Path | str | None = None,
    *,
    papers: list | None = None,
    openalex_config=None,
    openreview_config=None,
    semantic_scholar_config=None,
) -> SignalDetectionResult:
    """Load profiles and attach mock commercialization signals."""
    profile_result = build_profiles(
        papers_path,
        papers=papers,
        openalex_config=openalex_config,
        openreview_config=openreview_config,
        semantic_scholar_config=semantic_scholar_config,
    )
    raw_signals = load_signals(signals_path)
    resolved_signals, unmatched = attach_signals(
        raw_signals,
        profile_result.researchers,
        profile_result.clusters,
    )

    return SignalDetectionResult(
        papers=profile_result.papers,
        researchers=profile_result.researchers,
        clusters=profile_result.clusters,
        signals=resolved_signals,
        unmatched_researcher_names=unmatched,
    )


def summarize_signal_detection(result: SignalDetectionResult) -> dict[str, object]:
    """Return quick stats for inspecting signal attachment."""
    by_type = Counter(signal.signal_type.value for signal in result.signals if signal.researcher_id)
    researchers_with_signals = len(
        {signal.researcher_id for signal in result.signals if signal.researcher_id}
    )
    clusters_with_signals = len(
        {signal.cluster_id for signal in result.signals if signal.cluster_id}
    )

    return {
        "signal_count": len(result.signals),
        "matched_signal_count": result.matched_signal_count,
        "unmatched_researcher_names": result.unmatched_researcher_names,
        "researchers_with_signals": researchers_with_signals,
        "clusters_with_signals": clusters_with_signals,
        "signal_types": dict(by_type),
        "sample_matches": [
            {
                "signal_id": signal.id,
                "researcher_name": signal.researcher_name,
                "researcher_id": signal.researcher_id,
                "cluster_id": signal.cluster_id,
                "signal_type": signal.signal_type.value,
            }
            for signal in result.signals[:3]
        ],
    }
