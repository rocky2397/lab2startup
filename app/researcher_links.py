"""Resolve public profile links (GitHub, LinkedIn) for a researcher."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.models import Researcher, Signal

_GITHUB_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_GITHUB_EXISTS_CACHE: dict[str, bool] = {}


@dataclass(frozen=True)
class ResearcherLinks:
    """Clickable public profiles for dashboard display."""

    github: str | None = None
    linkedin: str | None = None
    openreview: str | None = None
    website: str | None = None


def _normalize_login(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _name_tokens(name: str) -> list[str]:
    cleaned = re.sub(r"[^a-zA-Z\s]", " ", name.lower())
    return [token for token in cleaned.split() if len(token) >= 2]


def github_login_matches_researcher(researcher_name: str, login: str) -> bool:
    """Return True when a GitHub login plausibly belongs to the researcher."""
    login_norm = _normalize_login(login)
    if not login_norm:
        return False

    parts = _name_tokens(researcher_name)
    if not parts:
        return False

    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        compact = first + last
        reversed_compact = last + first
        if compact in login_norm or reversed_compact in login_norm:
            return True
        if login_norm.startswith(last) and first[0] in login_norm:
            return True
        if login_norm.startswith(first) and last[0] in login_norm:
            return True

    return any(part in login_norm for part in parts if len(part) >= 3)


def _linkedin_slug_from_url(linkedin_url: str) -> str | None:
    parsed = urlparse(linkedin_url if "://" in linkedin_url else f"https://{linkedin_url}")
    path = parsed.path.strip("/")
    if path.startswith("in/"):
        slug = path.split("/", 1)[1].split("/", 1)[0]
        return slug or None
    if path.startswith("pub/"):
        return path.split("/", 1)[1].split("/", 1)[0] if "/" in path else path.removeprefix("pub/")
    return None


def linkedin_slug_matches_researcher(researcher_name: str, linkedin_url: str) -> bool:
    """Return True when a LinkedIn profile slug plausibly belongs to the researcher."""
    slug = _linkedin_slug_from_url(linkedin_url)
    if not slug:
        return False
    return github_login_matches_researcher(researcher_name, slug)


def github_user_exists(login: str) -> bool:
    """Return True when GitHub reports the user profile exists."""
    cached = _GITHUB_EXISTS_CACHE.get(login)
    if cached is not None:
        return cached

    exists = True
    try:
        response = httpx.get(
            f"https://api.github.com/users/{login}",
            headers={"User-Agent": "Lab2Startup/0.1", "Accept": "application/vnd.github+json"},
            timeout=10.0,
        )
        exists = response.status_code == 200
    except httpx.HTTPError:
        exists = True

    _GITHUB_EXISTS_CACHE[login] = exists
    return exists


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


def accept_github_profile_for_researcher(
    researcher_name: str,
    value: str | None,
    *,
    verify_exists: bool = True,
    require_name_match: bool = True,
) -> str | None:
    """Normalize and keep a GitHub profile only when it matches and exists."""
    url = normalize_github_profile_url(value)
    if not url:
        return None
    login = url.rsplit("/", 1)[-1]
    if require_name_match and not github_login_matches_researcher(researcher_name, login):
        return None
    if verify_exists and not github_user_exists(login):
        return None
    return url


def accept_linkedin_profile_for_researcher(
    researcher_name: str,
    value: str | None,
    *,
    require_name_match: bool = True,
) -> str | None:
    """Normalize and keep a LinkedIn profile only when the slug matches the researcher."""
    url = normalize_linkedin_profile_url(value)
    if not url:
        return None
    if require_name_match and not linkedin_slug_matches_researcher(researcher_name, url):
        return None
    return url


def github_username_from_url(value: str | None) -> str | None:
    url = normalize_github_profile_url(value)
    if not url:
        return None
    return url.rsplit("/", 1)[-1]


def _website_from_profile_url(profile_url: str | None) -> str | None:
    if not profile_url:
        return None
    lowered = profile_url.lower()
    if any(token in lowered for token in ("linkedin.com", "github.com", "openreview.net")):
        return None
    return profile_url.rstrip("/")


def _scan_signal_urls(
    signals: list[Signal],
    *,
    researcher_name: str,
) -> tuple[str | None, str | None]:
    github: str | None = None
    linkedin: str | None = None
    for signal in signals:
        url = signal.source_url
        if not linkedin:
            linkedin = accept_linkedin_profile_for_researcher(researcher_name, url)
        if not github:
            github = accept_github_profile_for_researcher(
                researcher_name,
                url,
                verify_exists=False,
            )
        if github and linkedin:
            break
    return github, linkedin


def resolve_researcher_links(
    researcher: Researcher,
    signals: list[Signal] | None = None,
) -> ResearcherLinks:
    """Collect the best available GitHub, LinkedIn, and related profile links."""
    signals = signals or []
    signal_github, signal_linkedin = _scan_signal_urls(signals, researcher_name=researcher.name)

    github = (
        normalize_github_profile_url(researcher.github_username)
        or signal_github
    )
    linkedin = normalize_linkedin_profile_url(researcher.linkedin_url) or signal_linkedin

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
