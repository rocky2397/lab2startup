"""Merge and reuse researcher enrichment across pipeline runs."""

from __future__ import annotations

from app.models import IdentityConfidence, Researcher


def is_unknown_affiliation(affiliation: str | None) -> bool:
    return not affiliation or affiliation.strip().lower() == "unknown"


def is_generic_role(role: str | None) -> bool:
    return not role or role.strip().lower() in {"unknown", "researcher", "coauthor"}


def _prefer_cached_string(fresh: str | None, cached: str | None) -> str | None:
    if cached and str(cached).strip():
        if not fresh or not str(fresh).strip():
            return cached
        if is_unknown_affiliation(fresh) and not is_unknown_affiliation(cached):
            return cached
    return None


def _prefer_cached_optional(fresh, cached):
    if cached is not None and fresh in (None, "", 0):
        return cached
    return None


def merge_researcher_record(fresh: Researcher, cached: Researcher) -> Researcher:
    """Overlay cached enrichment onto a freshly ingested researcher."""
    updates: dict[str, object] = {}

    for field in (
        "affiliation",
        "identity_confidence_explanation",
        "semantic_scholar_id",
        "openreview_profile_id",
        "openreview_url",
        "github_username",
        "linkedin_url",
        "profile_url",
    ):
        value = _prefer_cached_string(getattr(fresh, field), getattr(cached, field))
        if value is not None:
            updates[field] = value

    role = _prefer_cached_string(getattr(fresh, "role"), getattr(cached, "role"))
    if role is not None or (is_generic_role(fresh.role) and cached.role and not is_generic_role(cached.role)):
        updates["role"] = cached.role

    for field in ("citation_count", "h_index", "paper_count"):
        value = _prefer_cached_optional(getattr(fresh, field), getattr(cached, field))
        if value is not None:
            updates[field] = value

    confidence_rank = {
        IdentityConfidence.LOW: 0,
        IdentityConfidence.MEDIUM: 1,
        IdentityConfidence.HIGH: 2,
    }
    if confidence_rank[cached.identity_confidence] > confidence_rank[fresh.identity_confidence]:
        updates["identity_confidence"] = cached.identity_confidence

    if not updates:
        return fresh
    return fresh.model_copy(update=updates)


def merge_researcher_enrichment(
    researchers: list[Researcher],
    cached_researchers: list[Researcher],
) -> list[Researcher]:
    """Apply prior-run enrichment to researchers rebuilt from reused papers."""
    if not cached_researchers:
        return researchers
    cached_by_id = {researcher.id: researcher for researcher in cached_researchers}
    return [
        merge_researcher_record(researcher, cached_by_id[researcher.id])
        if researcher.id in cached_by_id
        else researcher
        for researcher in researchers
    ]
