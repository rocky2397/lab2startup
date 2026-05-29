"""Tiered discovery of GitHub and LinkedIn profile links for researchers."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.integrations.openreview import OpenReviewClient, OpenReviewConfig
from app.integrations.perplexity import (
    PerplexityConfig,
    build_researcher_context,
    retry_profile_links_with_perplexity,
)
from app.models import IdentityConfidence, Paper, Researcher
from app.researcher_enrichment import is_unknown_affiliation
from app.researcher_links import (
    accept_github_profile_for_researcher,
    accept_linkedin_profile_for_researcher,
    github_username_from_url,
    normalize_github_profile_url,
    normalize_linkedin_profile_url,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Lab2Startup/0.1 (mailto:research@example.com)"
_HREF_URL_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)
_HTTP_URL_RE = re.compile(r"""https?://[^\s"'<>]+""", re.IGNORECASE)


def researcher_missing_profile_links(researcher: Researcher) -> bool:
    return not researcher.linkedin_url and not researcher.github_username


def researcher_needs_openreview_profile_fetch(researcher: Researcher) -> bool:
    """Return True when this researcher still needs an OpenReview profile API call."""
    if not researcher.openreview_profile_id:
        return False
    if not is_unknown_affiliation(researcher.affiliation) and not researcher_missing_profile_links(researcher):
        return False
    return True


def _openreview_field(content: dict[str, Any], key: str) -> str:
    value = content.get(key)
    if isinstance(value, dict):
        raw = value.get("value")
    else:
        raw = value
    return str(raw or "").strip()


def github_user_from_homepage_url(url: str) -> str | None:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or "").lower()
    if host.endswith(".github.io"):
        username = host.removesuffix(".github.io")
        if username and username not in {"www", "raw"}:
            return username
    return None


def extract_urls_from_page_content(content: str) -> list[str]:
    urls = _HREF_URL_RE.findall(content)
    urls.extend(_HTTP_URL_RE.findall(content))
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = url.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(url)
    return deduped


def discover_links_from_openreview_profile(
    researcher: Researcher,
    profile: dict[str, Any],
) -> Researcher:
    """Tier 0a: use self-reported OpenReview homepage and LinkedIn fields."""
    content = profile.get("content") or {}
    homepage = _openreview_field(content, "homepage")
    linkedin_raw = _openreview_field(content, "linkedin")

    updates: dict[str, Any] = {}

    if homepage and not researcher.profile_url:
        updates["profile_url"] = homepage.rstrip("/")

    if linkedin_raw and not researcher.linkedin_url:
        linkedin = accept_linkedin_profile_for_researcher(
            researcher.name,
            linkedin_raw,
            require_name_match=False,
        )
        if linkedin:
            updates["linkedin_url"] = linkedin

    if not researcher.github_username:
        github_candidates: list[str] = []
        if homepage:
            github_user = github_user_from_homepage_url(homepage)
            if github_user:
                github_candidates.append(github_user)
        for candidate in github_candidates:
            github = accept_github_profile_for_researcher(
                researcher.name,
                candidate,
                verify_exists=False,
                require_name_match=True,
            )
            if github:
                login = github_username_from_url(github)
                if login:
                    updates["github_username"] = login
                    break

    if not updates:
        return researcher
    return researcher.model_copy(update=updates)


def discover_links_from_homepage(
    researcher: Researcher,
    homepage_url: str,
    *,
    client: httpx.Client,
) -> Researcher:
    """Tier 0b: fetch a known homepage and extract social links."""
    if not homepage_url or (researcher.linkedin_url and researcher.github_username):
        return researcher

    try:
        response = client.get(homepage_url, follow_redirects=True, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("Homepage fetch failed for %s: %s", homepage_url, exc)
        return researcher

    updates: dict[str, Any] = {}
    for url in extract_urls_from_page_content(response.text):
        if not researcher.linkedin_url and not updates.get("linkedin_url"):
            linkedin = accept_linkedin_profile_for_researcher(
                researcher.name,
                url,
                require_name_match=False,
            )
            if linkedin:
                updates["linkedin_url"] = linkedin

        if not researcher.github_username and not updates.get("github_username"):
            github = accept_github_profile_for_researcher(
                researcher.name,
                url,
                verify_exists=False,
                require_name_match=False,
            )
            if github:
                login = github_username_from_url(github)
                if login:
                    updates["github_username"] = login

        if updates.get("linkedin_url") and updates.get("github_username"):
            break

    github_user = github_user_from_homepage_url(homepage_url)
    if github_user and not researcher.github_username and not updates.get("github_username"):
        github = accept_github_profile_for_researcher(
            researcher.name,
            github_user,
            verify_exists=False,
            require_name_match=False,
        )
        if github:
            login = github_username_from_url(github)
            if login:
                updates["github_username"] = login

    if not updates:
        return researcher
    return researcher.model_copy(update=updates)


def discover_links_from_openreview_profiles(
    researchers: list[Researcher],
    profiles_by_id: dict[str, dict[str, Any]],
) -> list[Researcher]:
    updated: list[Researcher] = []
    for researcher in researchers:
        profile = profiles_by_id.get(researcher.openreview_profile_id or "")
        if profile is None:
            updated.append(researcher)
            continue
        updated.append(discover_links_from_openreview_profile(researcher, profile))
    return updated


def fetch_openreview_profiles_for_researchers(
    researchers: list[Researcher],
    *,
    config: OpenReviewConfig,
) -> dict[str, dict[str, Any]]:
    if not config.fetch_profiles:
        return {}

    candidates = [
        researcher.openreview_profile_id
        for researcher in researchers
        if researcher.openreview_profile_id and researcher_needs_openreview_profile_fetch(researcher)
    ]
    profile_ids = sorted(set(candidates))
    total_with_ids = len(
        {
            researcher.openreview_profile_id
            for researcher in researchers
            if researcher.openreview_profile_id
        }
    )
    skipped = total_with_ids - len(profile_ids)
    if skipped:
        logger.info(
            "Skipping %s/%s OpenReview profiles already enriched; fetching %s remaining",
            skipped,
            total_with_ids,
            len(profile_ids),
        )
    if not profile_ids:
        return {}

    with OpenReviewClient(request_delay_seconds=config.request_delay_seconds) as client:
        return client.get_profiles(profile_ids)


def discover_profile_links_tier0(
    researchers: list[Researcher],
    *,
    openreview_config: OpenReviewConfig | None = None,
    fetch_homepages: bool = True,
    request_delay_seconds: float = 0.5,
) -> list[Researcher]:
    """Discover GitHub/LinkedIn from OpenReview profiles and known homepages."""
    if not researchers:
        return researchers

    profiles_by_id: dict[str, dict[str, Any]] = {}
    if openreview_config is not None and openreview_config.enabled:
        profiles_by_id = fetch_openreview_profiles_for_researchers(
            researchers,
            config=openreview_config,
        )

    current = discover_links_from_openreview_profiles(researchers, profiles_by_id)
    if not fetch_homepages:
        return current

    client = httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=10.0,
        follow_redirects=True,
    )
    try:
        enriched: list[Researcher] = []
        for researcher in current:
            if not researcher_missing_profile_links(researcher):
                enriched.append(researcher)
                continue

            homepage = researcher.profile_url
            if not homepage and profiles_by_id:
                profile = profiles_by_id.get(researcher.openreview_profile_id or "")
                if profile:
                    homepage = _openreview_field(profile.get("content") or {}, "homepage")

            if not homepage:
                enriched.append(researcher)
                continue

            updated = discover_links_from_homepage(researcher, homepage, client=client)
            enriched.append(updated)
            if request_delay_seconds:
                time.sleep(request_delay_seconds)
        return enriched
    finally:
        client.close()


def _eligible_for_link_retry(
    researcher: Researcher,
    *,
    priority_ids: set[str] | None = None,
) -> bool:
    if not researcher_missing_profile_links(researcher):
        return False
    if researcher.identity_confidence == IdentityConfidence.LOW:
        return False
    if priority_ids is not None and researcher.id not in priority_ids:
        return False
    return True


def retry_missing_profile_links(
    researchers: list[Researcher],
    papers: list[Paper],
    *,
    perplexity_config: PerplexityConfig | None = None,
    priority_ids: set[str] | None = None,
) -> list[Researcher]:
    """Tier 2: run a narrow Perplexity query for researchers still missing links."""
    if perplexity_config is None or not perplexity_config.enabled or not perplexity_config.api_key:
        return researchers

    papers_by_id = {paper.id: paper for paper in papers}
    researchers_by_id = {researcher.id: researcher for researcher in researchers}
    updates_by_id: dict[str, Researcher] = {}

    for researcher in researchers:
        if not _eligible_for_link_retry(researcher, priority_ids=priority_ids):
            continue
        context = build_researcher_context(
            researcher,
            papers_by_id,
            fund_context=perplexity_config.fund_context,
            researchers_by_id=researchers_by_id,
        )
        try:
            updated = retry_profile_links_with_perplexity(
                researcher,
                context,
                perplexity_config,
            )
        except Exception as exc:
            logger.debug("Profile link retry failed for %s: %s", researcher.name, exc)
            continue
        if updated != researcher:
            updates_by_id[researcher.id] = updated

    if not updates_by_id:
        return researchers

    return [
        updates_by_id.get(researcher.id, researcher)
        for researcher in researchers
    ]


def enrich_researchers_with_profile_links(
    researchers: list[Researcher],
    papers: list[Paper],
    *,
    openreview_config: OpenReviewConfig | None = None,
    perplexity_config: PerplexityConfig | None = None,
    priority_ids: set[str] | None = None,
    run_tier0: bool = True,
    run_tier2_retry: bool = True,
) -> list[Researcher]:
    """Run tier 0 discovery and optional tier 2 Perplexity retry."""
    current = list(researchers)
    if run_tier0:
        current = discover_profile_links_tier0(
            current,
            openreview_config=openreview_config,
        )

    if run_tier2_retry:
        current = retry_missing_profile_links(
            current,
            papers,
            perplexity_config=perplexity_config,
            priority_ids=priority_ids,
        )

    return current
