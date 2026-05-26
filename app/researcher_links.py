"""Resolve public profile links (GitHub, LinkedIn) for a researcher."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.models import Researcher, Signal

_GITHUB_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class ResearcherLinks:
    """Clickable public profiles for dashboard display."""

    github: str | None = None
    linkedin: str | None = None
    openreview: str | None = None
    website: str | None = None


def normalize_github_profile_url(value: str | None) -> str | None:
    """Return a GitHub profile URL from a username or any github.com URL."""
    if not value or not str(value).strip():
        return None

    raw = str(value).strip().rstrip("/")
    if "github.com" in raw.lower():
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        parts = [segment for segment in parsed.path.strip("/").split("/") if segment]
        if not parts:
            return None
        login = parts[0]
        if login.lower() in {"orgs", "organizations", "sponsors", "marketplace", "topics"}:
            return None
        return f"https://github.com/{login}"

    username = raw.lstrip("@")
    if _GITHUB_USER_RE.fullmatch(username):
        return f"https://github.com/{username}"
    return None


def normalize_linkedin_profile_url(value: str | None) -> str | None:
    """Return a LinkedIn profile URL when the input looks like one."""
    if not value or not str(value).strip():
        return None

    raw = str(value).strip().rstrip("/")
    if "linkedin.com" not in raw.lower():
        return None

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.netloc or "").lower()
    if "linkedin.com" not in host:
        return None

    path = parsed.path.strip("/")
    if path.startswith("in/"):
        slug = path.split("/", 1)[1].split("/", 1)[0]
        if slug:
            return f"https://www.linkedin.com/in/{slug}"
    if path.startswith("pub/"):
        return f"https://www.linkedin.com/{path.split('?', 1)[0]}"
    return raw.split("?", 1)[0]


def _website_from_profile_url(profile_url: str | None) -> str | None:
    if not profile_url:
        return None
    lowered = profile_url.lower()
    if any(token in lowered for token in ("linkedin.com", "github.com", "openreview.net")):
        return None
    return profile_url.rstrip("/")


def _scan_signal_urls(signals: list[Signal]) -> tuple[str | None, str | None]:
    github: str | None = None
    linkedin: str | None = None
    for signal in signals:
        url = signal.source_url
        if not linkedin:
            linkedin = normalize_linkedin_profile_url(url)
        if not github:
            github = normalize_github_profile_url(url)
        if github and linkedin:
            break
    return github, linkedin


def resolve_researcher_links(
    researcher: Researcher,
    signals: list[Signal] | None = None,
) -> ResearcherLinks:
    """Collect the best available GitHub, LinkedIn, and related profile links."""
    signals = signals or []
    signal_github, signal_linkedin = _scan_signal_urls(signals)

    github = (
        normalize_github_profile_url(researcher.github_username)
        or signal_github
    )
    linkedin = (
        normalize_linkedin_profile_url(researcher.linkedin_url)
        or signal_linkedin
    )

    website = _website_from_profile_url(getattr(researcher, "profile_url", None))
    if website is None and researcher.openreview_url:
        website = None
    elif website is None:
        for signal in signals:
            candidate = _website_from_profile_url(signal.source_url)
            if candidate:
                website = candidate
                break

    return ResearcherLinks(
        github=github,
        linkedin=linkedin,
        openreview=researcher.openreview_url,
        website=website,
    )
