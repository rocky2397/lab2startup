"""Capture and verify researcher enrichment for a pipeline run."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.config import AgenticSignalConfig, AppSettings, get_settings
from app.integrations.perplexity import PerplexityConfig, enrich_researchers_with_perplexity
from app.models import IdentityConfidence, Paper, Researcher, Signal


class EnrichmentMode(StrEnum):
    """Which enrichment path ran during signal detection."""

    NONE = "none"
    SONAR = "sonar"
    AGENTIC = "agentic"


UNKNOWN_AFFILIATIONS = {"", "unknown", "n/a", "na"}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_unknown(value: str | None) -> bool:
    return (value or "").strip().lower() in UNKNOWN_AFFILIATIONS


def _is_pseudo_unknown(value: str | None) -> bool:
    """Treat failed Perplexity lookups like 'Unknown (no affiliation...)' as unresolved."""
    text = (value or "").strip().lower()
    return _is_unknown(value) or text.startswith("unknown (")


def snapshot_researchers(researchers: list[Researcher]) -> list[dict[str, Any]]:
    """Serialize researchers for before/after comparison and re-run."""
    return [researcher.model_dump(mode="json") for researcher in researchers]


def researchers_from_snapshot(payload: list[dict[str, Any]]) -> list[Researcher]:
    """Restore researchers from a stored snapshot."""
    return [Researcher.model_validate(item) for item in payload]


@dataclass
class ResearcherEnrichmentRecord:
    """Per-researcher enrichment outcome."""

    researcher_id: str
    name: str
    status: str
    skip_reason: str | None = None
    pre_affiliation: str = "Unknown"
    post_affiliation: str = "Unknown"
    pre_role: str = "Researcher"
    post_role: str = "Researcher"
    pre_identity_confidence: str = "medium"
    post_identity_confidence: str = "medium"
    affiliation_resolved: bool = False
    role_changed: bool = False
    links_added: bool = False
    signal_count: int = 0
    investigation_tier: str | None = None


@dataclass
class EnrichmentAudit:
    """Full enrichment audit for one pipeline run."""

    run_id: str | None
    mode: EnrichmentMode
    created_at: str = field(default_factory=_utc_now_iso)
    total_researchers: int = 0
    targeted_count: int = 0
    investigated_count: int = 0
    affiliation_resolved_count: int = 0
    still_unknown_count: int = 0
    with_signals_count: int = 0
    skipped_count: int = 0
    config_summary: dict[str, Any] = field(default_factory=dict)
    pre_researchers: list[dict[str, Any]] = field(default_factory=list)
    records: list[ResearcherEnrichmentRecord] = field(default_factory=list)

    @property
    def enrichment_worked(self) -> bool:
        """True when at least one researcher gained affiliation or signals."""
        return self.affiliation_resolved_count > 0 or self.with_signals_count > 0

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode.value,
            "created_at": self.created_at,
            "total_researchers": self.total_researchers,
            "targeted_count": self.targeted_count,
            "investigated_count": self.investigated_count,
            "affiliation_resolved_count": self.affiliation_resolved_count,
            "still_unknown_count": self.still_unknown_count,
            "with_signals_count": self.with_signals_count,
            "skipped_count": self.skipped_count,
            "enrichment_worked": self.enrichment_worked,
            "config_summary": self.config_summary,
        }


def _links_added(pre: Researcher, post: Researcher) -> bool:
    pre_links = {pre.linkedin_url, pre.profile_url, pre.github_username}
    post_links = {post.linkedin_url, post.profile_url, post.github_username}
    return any(link and link not in pre_links for link in post_links if link)


def _record_status(
    *,
    targeted: bool,
    investigated: bool,
    investigation_failed: bool,
    affiliation_resolved: bool,
    signal_count: int,
    post: Researcher,
) -> str:
    if investigation_failed:
        return "investigation_failed"
    if signal_count > 0:
        return "investigated_with_signals"
    if affiliation_resolved:
        return "enriched"
    if investigated:
        return "investigated_no_signal"
    if targeted:
        return "targeted_not_reached"
    if _is_unknown(post.affiliation):
        return "not_targeted"
    return "unchanged"


def agentic_skip_reason(
    tier: str,
    researcher: Researcher,
    prefilter_score: float,
    config: AgenticSignalConfig,
) -> str | None:
    if tier != "skip":
        return None
    if prefilter_score < config.prefilter_min_score:
        return f"prefilter_below_{config.prefilter_min_score}"
    if researcher.identity_confidence == IdentityConfidence.LOW:
        return "low_identity_confidence"
    return "queue_cap"


def build_enrichment_audit(
    *,
    run_id: str | None,
    mode: EnrichmentMode,
    pre_researchers: list[Researcher],
    post_researchers: list[Researcher],
    signals: list[Signal],
    targeted_ids: set[str] | None = None,
    investigated_ids: set[str] | None = None,
    investigation_failed_ids: set[str] | None = None,
    tier_by_id: dict[str, str] | None = None,
    skip_reason_by_id: dict[str, str] | None = None,
    config_summary: dict[str, Any] | None = None,
) -> EnrichmentAudit:
    """Compare pre/post researcher state and build a verification audit."""
    targeted_ids = targeted_ids or set()
    investigated_ids = investigated_ids or set()
    investigation_failed_ids = investigation_failed_ids or set()
    tier_by_id = tier_by_id or {}
    skip_reason_by_id = skip_reason_by_id or {}

    pre_by_id = {researcher.id: researcher for researcher in pre_researchers}
    post_by_id = {researcher.id: researcher for researcher in post_researchers}
    signals_by_researcher: dict[str, int] = {}
    for signal in signals:
        if signal.researcher_id:
            signals_by_researcher[signal.researcher_id] = signals_by_researcher.get(signal.researcher_id, 0) + 1

    records: list[ResearcherEnrichmentRecord] = []
    affiliation_resolved_count = 0
    still_unknown_count = 0
    with_signals_count = 0
    skipped_count = 0

    for researcher_id, post in post_by_id.items():
        pre = pre_by_id.get(researcher_id, post)
        targeted = researcher_id in targeted_ids
        investigated = researcher_id in investigated_ids
        tier = tier_by_id.get(researcher_id)
        skip_reason = skip_reason_by_id.get(researcher_id)
        if skip_reason:
            skipped_count += 1

        affiliation_resolved = _is_unknown(pre.affiliation) and not _is_pseudo_unknown(post.affiliation)
        if affiliation_resolved:
            affiliation_resolved_count += 1
        if _is_pseudo_unknown(post.affiliation):
            still_unknown_count += 1

        signal_count = signals_by_researcher.get(researcher_id, 0)
        if signal_count > 0:
            with_signals_count += 1

        records.append(
            ResearcherEnrichmentRecord(
                researcher_id=researcher_id,
                name=post.name,
                status=_record_status(
                    targeted=targeted,
                    investigated=investigated,
                    investigation_failed=researcher_id in investigation_failed_ids,
                    affiliation_resolved=affiliation_resolved,
                    signal_count=signal_count,
                    post=post,
                ),
                skip_reason=skip_reason,
                pre_affiliation=pre.affiliation,
                post_affiliation=post.affiliation,
                pre_role=pre.role,
                post_role=post.role,
                pre_identity_confidence=pre.identity_confidence.value,
                post_identity_confidence=post.identity_confidence.value,
                affiliation_resolved=affiliation_resolved,
                role_changed=pre.role != post.role,
                links_added=_links_added(pre, post),
                signal_count=signal_count,
                investigation_tier=tier,
            )
        )

    records.sort(key=lambda record: (-record.signal_count, record.name.lower()))

    return EnrichmentAudit(
        run_id=run_id,
        mode=mode,
        total_researchers=len(post_researchers),
        targeted_count=len(targeted_ids),
        investigated_count=len(investigated_ids),
        affiliation_resolved_count=affiliation_resolved_count,
        still_unknown_count=still_unknown_count,
        with_signals_count=with_signals_count,
        skipped_count=skipped_count,
        config_summary=config_summary or {},
        pre_researchers=snapshot_researchers(pre_researchers),
        records=records,
    )


def serialize_enrichment_audit(audit: EnrichmentAudit) -> str:
    payload = {
        **audit.summary(),
        "pre_researchers": audit.pre_researchers,
        "records": [
            {
                "researcher_id": record.researcher_id,
                "name": record.name,
                "status": record.status,
                "skip_reason": record.skip_reason,
                "pre_affiliation": record.pre_affiliation,
                "post_affiliation": record.post_affiliation,
                "pre_role": record.pre_role,
                "post_role": record.post_role,
                "pre_identity_confidence": record.pre_identity_confidence,
                "post_identity_confidence": record.post_identity_confidence,
                "affiliation_resolved": record.affiliation_resolved,
                "role_changed": record.role_changed,
                "links_added": record.links_added,
                "signal_count": record.signal_count,
                "investigation_tier": record.investigation_tier,
            }
            for record in audit.records
        ],
    }
    return json.dumps(payload)


def deserialize_enrichment_audit(payload: str | dict[str, Any]) -> EnrichmentAudit:
    data = json.loads(payload) if isinstance(payload, str) else payload
    records = [
        ResearcherEnrichmentRecord(
            researcher_id=item["researcher_id"],
            name=item["name"],
            status=item["status"],
            skip_reason=item.get("skip_reason"),
            pre_affiliation=item.get("pre_affiliation", "Unknown"),
            post_affiliation=item.get("post_affiliation", "Unknown"),
            pre_role=item.get("pre_role", "Researcher"),
            post_role=item.get("post_role", "Researcher"),
            pre_identity_confidence=item.get("pre_identity_confidence", "medium"),
            post_identity_confidence=item.get("post_identity_confidence", "medium"),
            affiliation_resolved=bool(item.get("affiliation_resolved")),
            role_changed=bool(item.get("role_changed")),
            links_added=bool(item.get("links_added")),
            signal_count=int(item.get("signal_count") or 0),
            investigation_tier=item.get("investigation_tier"),
        )
        for item in data.get("records") or []
    ]
    return EnrichmentAudit(
        run_id=data.get("run_id"),
        mode=EnrichmentMode(data.get("mode") or EnrichmentMode.NONE.value),
        created_at=data.get("created_at") or _utc_now_iso(),
        total_researchers=int(data.get("total_researchers") or 0),
        targeted_count=int(data.get("targeted_count") or 0),
        investigated_count=int(data.get("investigated_count") or 0),
        affiliation_resolved_count=int(data.get("affiliation_resolved_count") or 0),
        still_unknown_count=int(data.get("still_unknown_count") or 0),
        with_signals_count=int(data.get("with_signals_count") or 0),
        skipped_count=int(data.get("skipped_count") or 0),
        config_summary=dict(data.get("config_summary") or {}),
        pre_researchers=list(data.get("pre_researchers") or []),
        records=records,
    )


def _profile_summary(record: ResearcherEnrichmentRecord) -> dict[str, Any]:
    return {
        "name": record.name,
        "researcher_id": record.researcher_id,
        "status": record.status,
        "pre_affiliation": record.pre_affiliation,
        "post_affiliation": record.post_affiliation,
        "pre_role": record.pre_role,
        "post_role": record.post_role,
        "signal_count": record.signal_count,
        "skip_reason": record.skip_reason,
        "investigation_tier": record.investigation_tier,
        "links_added": record.links_added,
    }


def enrichment_profile_lists(audit: EnrichmentAudit) -> dict[str, list[dict[str, Any]]]:
    """Group audit records into named profile lists for UI and logging."""
    enriched_profiles: list[dict[str, Any]] = []
    investigated_profiles: list[dict[str, Any]] = []
    with_signals_profiles: list[dict[str, Any]] = []
    investigated_no_signal_profiles: list[dict[str, Any]] = []
    investigation_failed_profiles: list[dict[str, Any]] = []
    skipped_profiles: list[dict[str, Any]] = []

    for record in audit.records:
        profile = _profile_summary(record)
        if record.skip_reason:
            skipped_profiles.append(profile)
        if record.status in {
            "enriched",
            "investigated_no_signal",
            "investigated_with_signals",
            "investigation_failed",
            "targeted_not_reached",
        }:
            investigated_profiles.append(profile)
        if record.status == "investigated_no_signal":
            investigated_no_signal_profiles.append(profile)
        if record.status == "investigation_failed":
            investigation_failed_profiles.append(profile)
        if (
            record.affiliation_resolved
            or record.signal_count > 0
            or record.links_added
            or (record.role_changed and record.post_role.lower() != "researcher")
        ):
            enriched_profiles.append(profile)
        if record.signal_count > 0:
            with_signals_profiles.append(profile)

    return {
        "enriched_profiles": enriched_profiles,
        "investigated_profiles": investigated_profiles,
        "with_signals_profiles": with_signals_profiles,
        "investigated_no_signal_profiles": investigated_no_signal_profiles,
        "investigated_no_change_profiles": investigated_no_signal_profiles,
        "investigation_failed_profiles": investigation_failed_profiles,
        "skipped_profiles": skipped_profiles,
    }


def format_enriched_profile_line(profile: dict[str, Any]) -> str:
    """One-line human summary for logs and compact UI."""
    parts = [profile["name"]]
    pre = profile.get("pre_affiliation") or "Unknown"
    post = profile.get("post_affiliation") or "Unknown"
    if pre != post:
        parts.append(f"{pre} → {post}")
    elif post and not _is_pseudo_unknown(post):
        parts.append(post)
    if profile.get("pre_role") != profile.get("post_role"):
        parts.append(f"role: {profile.get('pre_role')} → {profile.get('post_role')}")
    if profile.get("signal_count"):
        parts.append(f"{profile['signal_count']} signal(s)")
    if profile.get("links_added"):
        parts.append("links added")
    return " — ".join(parts)


def summarize_enrichment_audit(audit: EnrichmentAudit | None) -> dict[str, Any]:
    if audit is None:
        return {"available": False}
    status_counts: dict[str, int] = {}
    skip_reason_counts: dict[str, int] = {}
    for record in audit.records:
        status_counts[record.status] = status_counts.get(record.status, 0) + 1
        if record.skip_reason:
            skip_reason_counts[record.skip_reason] = skip_reason_counts.get(record.skip_reason, 0) + 1
    profile_lists = enrichment_profile_lists(audit)
    enriched_lines = [format_enriched_profile_line(profile) for profile in profile_lists["enriched_profiles"]]
    investigated_lines = [profile["name"] for profile in profile_lists["investigated_profiles"]]
    return {
        "available": True,
        **audit.summary(),
        "status_counts": status_counts,
        "skip_reason_counts": skip_reason_counts,
        **profile_lists,
        "enriched_profile_lines": enriched_lines,
        "investigated_profile_names": investigated_lines,
        "sample_resolved": profile_lists["enriched_profiles"][:5],
        "sample_still_unknown": [
            {
                "name": record.name,
                "status": record.status,
                "skip_reason": record.skip_reason,
            }
            for record in audit.records
            if _is_pseudo_unknown(record.post_affiliation)
        ][:5],
    }


def rerun_enrichment_verification(
    *,
    run_id: str,
    papers: list[Paper],
    audit: EnrichmentAudit,
    perplexity_config: PerplexityConfig | None = None,
    settings: AppSettings | None = None,
) -> EnrichmentAudit:
    """Re-run Sonar enrichment on the stored pre-enrichment snapshot."""
    settings = settings or get_settings()
    config = perplexity_config or settings.perplexity_config
    pre_researchers = researchers_from_snapshot(audit.pre_researchers)
    if not config.enabled or not config.api_key:
        return build_enrichment_audit(
            run_id=run_id,
            mode=EnrichmentMode.NONE,
            pre_researchers=pre_researchers,
            post_researchers=pre_researchers,
            signals=[],
            config_summary={
                "rerun": True,
                "error": "Perplexity is disabled or missing API key",
            },
        )

    from app.integrations.perplexity import _target_researchers_for_perplexity

    targeted = _target_researchers_for_perplexity(pre_researchers, config)
    targeted_ids = {researcher.id for researcher in targeted}
    skip_reason_by_id: dict[str, str] = {}
    if config.max_researchers > 0:
        skip_reason_by_id = {
            researcher.id: f"not_in_top_{config.max_researchers}_by_paper_count"
            for researcher in pre_researchers
            if researcher.id not in targeted_ids
        }

    post_researchers, signals = enrich_researchers_with_perplexity(
        papers,
        pre_researchers,
        config,
    )
    return build_enrichment_audit(
        run_id=run_id,
        mode=EnrichmentMode.SONAR,
        pre_researchers=pre_researchers,
        post_researchers=post_researchers,
        signals=signals,
        targeted_ids=targeted_ids,
        investigated_ids=targeted_ids,
        skip_reason_by_id=skip_reason_by_id,
        config_summary={
            "rerun": True,
            "max_researchers": "all" if config.max_researchers <= 0 else config.max_researchers,
            "enrich_profiles": config.enrich_profiles,
            "model": config.model,
        },
    )
