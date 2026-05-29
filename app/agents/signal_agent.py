"""Signal agent — attaches commercialization signals to researchers/clusters (Step 5)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.profile_agent import build_profiles
from app.enrichment_audit import (
    EnrichmentAudit,
    EnrichmentMode,
    agentic_skip_reason,
    build_enrichment_audit,
)
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
    enrichment_audit: EnrichmentAudit | None = None

    @property
    def matched_signal_count(self) -> int:
        return sum(1 for signal in self.signals if signal.researcher_id)

    def signals_for_researcher(self, researcher_id: str) -> list[Signal]:
        return [signal for signal in self.signals if signal.researcher_id == researcher_id]

    def signals_for_cluster(self, cluster_id: str) -> list[Signal]:
        return [signal for signal in self.signals if signal.cluster_id == cluster_id]


def _should_load_mock_signals(
    github_config=None,
    perplexity_config=None,
    agentic_signal_config=None,
) -> bool:
    """Load mock JSON unless an enabled integration opts out of supplementing it."""
    if agentic_signal_config is not None and agentic_signal_config.enabled:
        return True
    for config in (github_config, perplexity_config):
        if config is not None and config.enabled and not config.supplement_mock_signals:
            return False
    return True


def detect_signals(
    papers_path: Path | str | None = None,
    signals_path: Path | str | None = None,
    *,
    papers: list | None = None,
    openalex_config=None,
    openreview_config=None,
    semantic_scholar_config=None,
    github_config=None,
    perplexity_config=None,
    agentic_signal_config=None,
    use_mock_signals: bool = True,
    run_id: str | None = None,
    conference: str = "Unknown",
    year: int = 2024,
    topic_scores: dict[str, int] | None = None,
    cached_researchers: list | None = None,
) -> SignalDetectionResult:
    """Load profiles and attach commercialization signals."""
    profile_result = build_profiles(
        papers_path,
        papers=papers,
        openalex_config=openalex_config,
        openreview_config=openreview_config,
        semantic_scholar_config=semantic_scholar_config,
    )

    raw_signals: list[Signal] = []
    if use_mock_signals and _should_load_mock_signals(
        github_config,
        perplexity_config,
        agentic_signal_config,
    ):
        raw_signals = load_signals(signals_path)

    researchers = profile_result.researchers
    if cached_researchers:
        from dataclasses import replace

        from app.researcher_enrichment import merge_researcher_enrichment

        researchers = merge_researcher_enrichment(researchers, cached_researchers)
        profile_result = replace(profile_result, researchers=researchers)
    pre_researchers = [researcher.model_copy(deep=True) for researcher in researchers]
    enrichment_audit: EnrichmentAudit | None = None
    priority_ids: set[str] | None = None
    link_retry_config = perplexity_config

    from app.profile_link_discovery import (
        discover_profile_links_tier0,
        retry_missing_profile_links,
    )

    researchers = discover_profile_links_tier0(
        researchers,
        openreview_config=openreview_config,
    )

    if agentic_signal_config is not None and agentic_signal_config.enabled:
        from app.agents.signal_coordinator import build_investigation_plan
        from app.agents.signal_graph import run_agentic_signal_graph
        from app.integrations.perplexity_agent import merge_agent_signals

        effective_run_id = run_id or "agentic_detect_local"
        papers_by_id = {paper.id: paper for paper in profile_result.papers}
        scores, queue, tiers = build_investigation_plan(
            researchers,
            papers_by_id=papers_by_id,
            config=agentic_signal_config,
            topic_scores=topic_scores,
            db_path=agentic_signal_config.db_path,
        )
        targeted_ids = (
            {researcher_id for researcher_id, tier in tiers.items() if tier != "skip"}
            if agentic_signal_config.max_agent_calls <= 0
            else set(queue[: agentic_signal_config.max_agent_calls])
        )
        priority_ids = targeted_ids
        if link_retry_config is None and agentic_signal_config.api_key:
            from app.integrations.perplexity import PerplexityConfig

            link_retry_config = PerplexityConfig(
                enabled=True,
                api_key=agentic_signal_config.api_key,
                fund_context=agentic_signal_config.fund_context,
                enrich_profiles=False,
            )
        skip_reason_by_id = {
            researcher.id: reason
            for researcher in researchers
            if (
                reason := agentic_skip_reason(
                    tiers.get(researcher.id, "skip"),
                    researcher,
                    scores.get(researcher.id, 0.0),
                    agentic_signal_config,
                )
            )
        }

        researchers, agent_signals, traces = run_agentic_signal_graph(
            run_id=effective_run_id,
            papers=profile_result.papers,
            researchers=researchers,
            clusters=profile_result.clusters,
            config=agentic_signal_config,
            conference=conference,
            year=year,
            topic_scores=topic_scores,
        )
        raw_signals = merge_agent_signals(raw_signals, agent_signals)
        investigated_ids = {
            trace["researcher_id"] if isinstance(trace, dict) else trace.researcher_id for trace in traces
        }
        investigation_status_by_id: dict[str, str] = {}
        for trace in traces:
            researcher_id = trace["researcher_id"] if isinstance(trace, dict) else trace.researcher_id
            status = trace["status"] if isinstance(trace, dict) else trace.status
            if status == "completed":
                investigation_status_by_id[researcher_id] = "completed"
            elif researcher_id not in investigation_status_by_id:
                investigation_status_by_id[researcher_id] = status
        investigation_failed_ids = {
            researcher_id
            for researcher_id, status in investigation_status_by_id.items()
            if status == "failed"
        }
        enrichment_audit = build_enrichment_audit(
            run_id=run_id,
            mode=EnrichmentMode.AGENTIC,
            pre_researchers=pre_researchers,
            post_researchers=researchers,
            signals=agent_signals,
            targeted_ids=targeted_ids,
            investigated_ids=investigated_ids,
            investigation_failed_ids=investigation_failed_ids,
            tier_by_id=tiers,
            skip_reason_by_id=skip_reason_by_id,
            config_summary={
                "max_agent_calls": (
                    "all" if agentic_signal_config.max_agent_calls <= 0 else agentic_signal_config.max_agent_calls
                ),
                "deep_slots": agentic_signal_config.deep_slots,
                "standard_slots": agentic_signal_config.standard_slots,
                "prefilter_min_score": agentic_signal_config.prefilter_min_score,
                "enrich_profiles": agentic_signal_config.enrich_profiles,
            },
        )
    elif perplexity_config is not None and perplexity_config.enabled:
        from app.integrations.perplexity import (
            _target_researchers_for_perplexity,
            enrich_researchers_with_perplexity,
            merge_perplexity_signals,
        )

        targeted = _target_researchers_for_perplexity(researchers, perplexity_config)
        targeted_ids = {researcher.id for researcher in targeted}
        priority_ids = targeted_ids
        skip_reason_by_id: dict[str, str] = {}
        if perplexity_config.max_researchers > 0:
            skip_reason_by_id = {
                researcher.id: f"not_in_top_{perplexity_config.max_researchers}_by_paper_count"
                for researcher in researchers
                if researcher.id not in targeted_ids
            }

        researchers, perplexity_signals = enrich_researchers_with_perplexity(
            profile_result.papers,
            researchers,
            perplexity_config,
        )
        raw_signals = merge_perplexity_signals(raw_signals, perplexity_signals)
        enrichment_audit = build_enrichment_audit(
            run_id=run_id,
            mode=EnrichmentMode.SONAR,
            pre_researchers=pre_researchers,
            post_researchers=researchers,
            signals=perplexity_signals,
            targeted_ids=targeted_ids,
            investigated_ids=targeted_ids,
            skip_reason_by_id=skip_reason_by_id,
            config_summary={
                "max_researchers": (
                    "all" if perplexity_config.max_researchers <= 0 else perplexity_config.max_researchers
                ),
                "enrich_profiles": perplexity_config.enrich_profiles,
                "model": perplexity_config.model,
            },
        )
    else:
        enrichment_audit = build_enrichment_audit(
            run_id=run_id,
            mode=EnrichmentMode.NONE,
            pre_researchers=pre_researchers,
            post_researchers=researchers,
            signals=[],
            config_summary={"note": "No Perplexity or agentic enrichment enabled"},
        )

    researchers = retry_missing_profile_links(
        researchers,
        profile_result.papers,
        perplexity_config=link_retry_config,
        priority_ids=priority_ids,
    )

    # GitHub is optional — OSS momentum supplement keyed off paper titles.
    if github_config is not None and github_config.enabled:
        from app.integrations.github import (
            apply_github_usernames,
            detect_github_signals,
            merge_github_signals,
        )

        github_signals = detect_github_signals(
            profile_result.papers,
            researchers,
            github_config,
        )
        raw_signals = merge_github_signals(raw_signals, github_signals)
        researchers = apply_github_usernames(researchers, github_signals)

    resolved_signals, unmatched = attach_signals(
        raw_signals,
        researchers,
        profile_result.clusters,
    )

    return SignalDetectionResult(
        papers=profile_result.papers,
        researchers=researchers,
        clusters=profile_result.clusters,
        signals=resolved_signals,
        unmatched_researcher_names=unmatched,
        enrichment_audit=enrichment_audit,
    )


def summarize_signal_detection(result: SignalDetectionResult) -> dict[str, object]:
    """Return quick stats for inspecting signal attachment."""
    by_type = Counter(signal.signal_type.value for signal in result.signals if signal.researcher_id)
    researchers_with_signals = len({signal.researcher_id for signal in result.signals if signal.researcher_id})
    clusters_with_signals = len({signal.cluster_id for signal in result.signals if signal.cluster_id})

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
