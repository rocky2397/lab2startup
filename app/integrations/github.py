"""GitHub integration — detect open-source commercialization signals (Step 10d)."""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.models import EvidenceStrength, Paper, Researcher, Signal, SignalType

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_USER_AGENT = "Lab2Startup/0.1"
RECENT_ACTIVITY_DAYS = 365


@dataclass
class GitHubConfig:
    """Parameters for GitHub signal detection."""

    enabled: bool = False
    api_token: str | None = None
    min_stars: int = 5
    max_repos_per_paper: int = 2
    supplement_mock_signals: bool = True
    request_delay_seconds: float = 0.5


def _normalize_login(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _normalize_person_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z\s]", " ", name.lower())
    return " ".join(cleaned.split())


def extract_search_terms(paper: Paper) -> list[str]:
    """Extract GitHub search terms from a paper title."""
    terms: list[str] = []
    title = paper.title.strip()

    if ":" in title:
        prefix = title.split(":", 1)[0].strip()
        if len(prefix) >= 3:
            terms.append(prefix)

    for match in re.finditer(r"[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", title):
        token = match.group(0)
        if len(token) >= 4 and token not in terms:
            terms.append(token)

    if not terms:
        words = [word for word in re.split(r"[^A-Za-z0-9-]+", title) if len(word) >= 4]
        terms.extend(words[:2])

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(term)
    return deduped[:3]


def _author_matches_login(author_name: str, login: str) -> bool:
    login_norm = _normalize_login(login)
    if not login_norm:
        return False

    parts = [part for part in _normalize_person_name(author_name).split() if part]
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


def _is_recent(pushed_at: str | None) -> bool:
    if not pushed_at:
        return False
    pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    age_days = (datetime.now(timezone.utc) - pushed).days
    return age_days <= RECENT_ACTIVITY_DAYS


def _evidence_strength(repo: dict[str, Any]) -> EvidenceStrength:
    stars = int(repo.get("stargazers_count") or 0)
    recent = _is_recent(repo.get("pushed_at"))

    if stars >= 500 or (stars >= 100 and recent):
        return EvidenceStrength.HIGH
    if stars >= 50 or (stars >= 20 and recent):
        return EvidenceStrength.MEDIUM
    return EvidenceStrength.LOW


def pick_researcher_for_repo(
    repo: dict[str, Any],
    paper: Paper,
    researchers_by_name: dict[str, Researcher],
) -> Researcher | None:
    """Choose the most likely paper author for a repository signal."""
    owner = repo.get("owner") or {}
    login = owner.get("login") or ""

    for author in paper.authors:
        if _author_matches_login(author.name, login):
            return researchers_by_name.get(author.name)

    if owner.get("type") == "Organization":
        org_norm = _normalize_login(login)
        for term in extract_search_terms(paper):
            if _normalize_login(term) in org_norm or org_norm in _normalize_login(term):
                return researchers_by_name.get(paper.authors[0].name)

    if paper.authors:
        return researchers_by_name.get(paper.authors[0].name)
    return None


def repo_to_signal(
    repo: dict[str, Any],
    *,
    researcher: Researcher,
    paper: Paper,
) -> Signal:
    """Convert a GitHub repository payload into a Signal."""
    full_name = repo.get("full_name") or repo.get("name") or "unknown/repo"
    stars = int(repo.get("stargazers_count") or 0)
    description = repo.get("description") or "No repository description."
    strength = _evidence_strength(repo)

    signal_description = (
        f"GitHub repository '{full_name}' ({stars} stars) appears related to "
        f"'{paper.title}'. {description}"
    )

    owner = repo.get("owner") or {}
    owner_login = owner.get("login") or "unknown"
    repo_name = repo.get("name") or "repo"
    signal_id = f"github_{_normalize_login(owner_login)}_{_normalize_login(repo_name)}"

    return Signal(
        id=signal_id,
        signal_type=SignalType.COMMERCIALIZATION,
        description=signal_description[:500],
        source_url=repo.get("html_url") or f"https://github.com/{full_name}",
        evidence_strength=strength,
        date_found=date.today(),
        researcher_name=researcher.name,
    )


class GitHubClient:
    """Minimal GitHub REST client."""

    def __init__(
        self,
        *,
        api_token: str | None = None,
        timeout: float = 30.0,
        request_delay_seconds: float = 0.5,
    ) -> None:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/vnd.github+json",
        }
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        self._client = httpx.Client(
            base_url=GITHUB_API_BASE,
            headers=headers,
            timeout=timeout,
        )
        self.request_delay_seconds = request_delay_seconds

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _pause(self) -> None:
        if self.request_delay_seconds:
            time.sleep(self.request_delay_seconds)

    def search_repositories(self, query: str, *, per_page: int = 5) -> list[dict[str, Any]]:
        response = self._client.get(
            "/search/repositories",
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": per_page,
            },
        )
        response.raise_for_status()
        self._pause()
        payload = response.json()
        return payload.get("items") or []


def _repo_matches_paper(repo: dict[str, Any], paper: Paper, search_term: str) -> bool:
    """Return True when a repository likely relates to the paper."""
    haystack = " ".join(
        part
        for part in (
            repo.get("name") or "",
            repo.get("full_name") or "",
            repo.get("description") or "",
            paper.title,
            paper.abstract,
        )
        if part
    ).lower()
    term = search_term.lower()
    if term in haystack:
        return True

    compact = _normalize_login(term)
    repo_name = _normalize_login(repo.get("name") or "")
    return bool(compact and compact in repo_name)


def detect_github_signals(
    papers: list[Paper],
    researchers: list[Researcher],
    config: GitHubConfig,
) -> list[Signal]:
    """Search GitHub for repositories related to papers and emit signals."""
    if not config.enabled or not papers:
        return []

    researchers_by_name = {researcher.name: researcher for researcher in researchers}
    signals: list[Signal] = []
    seen_repo_urls: set[str] = set()

    with GitHubClient(
        api_token=config.api_token,
        request_delay_seconds=config.request_delay_seconds,
    ) as client:
        for paper in papers:
            for term in extract_search_terms(paper):
                query = f"{term} in:name,description"
                try:
                    repos = client.search_repositories(
                        query,
                        per_page=max(config.max_repos_per_paper, 3),
                    )
                except httpx.HTTPError:
                    continue

                matched = 0
                for repo in repos:
                    stars = int(repo.get("stargazers_count") or 0)
                    if stars < config.min_stars:
                        continue
                    if not _repo_matches_paper(repo, paper, term):
                        continue

                    repo_url = (repo.get("html_url") or "").rstrip("/")
                    if not repo_url or repo_url in seen_repo_urls:
                        continue

                    researcher = pick_researcher_for_repo(repo, paper, researchers_by_name)
                    if researcher is None:
                        continue

                    signals.append(
                        repo_to_signal(repo, researcher=researcher, paper=paper)
                    )
                    seen_repo_urls.add(repo_url)
                    matched += 1
                    if matched >= config.max_repos_per_paper:
                        break

    return signals


def merge_github_signals(
    existing_signals: list[Signal],
    github_signals: list[Signal],
) -> list[Signal]:
    """Append GitHub signals without duplicating source URLs."""
    seen_urls = {signal.source_url.rstrip("/") for signal in existing_signals}
    merged = list(existing_signals)
    for signal in github_signals:
        url = signal.source_url.rstrip("/")
        if url in seen_urls:
            continue
        merged.append(signal)
        seen_urls.add(url)
    return merged


def apply_github_usernames(
    researchers: list[Researcher],
    github_signals: list[Signal],
) -> list[Researcher]:
    """Attach GitHub usernames when repo owner login matches the researcher."""
    login_by_researcher: dict[str, str] = {}
    for signal in github_signals:
        if not signal.researcher_name or "github.com/" not in signal.source_url:
            continue
        path = signal.source_url.split("github.com/", 1)[-1]
        login = path.split("/", 1)[0]
        login_by_researcher[signal.researcher_name] = login

    updated: list[Researcher] = []
    for researcher in researchers:
        login = login_by_researcher.get(researcher.name)
        if login and researcher.github_username is None:
            updated.append(researcher.model_copy(update={"github_username": login}))
        else:
            updated.append(researcher)
    return updated


def summarize_github_signals(signals: list[Signal]) -> dict[str, object]:
    """Return quick stats for GitHub signal detection."""
    github_signals = [signal for signal in signals if signal.id.startswith("github_")]
    return {
        "github_signal_count": len(github_signals),
        "researchers_with_github_signals": len(
            {signal.researcher_name for signal in github_signals if signal.researcher_name}
        ),
        "sample_signals": [
            {
                "id": signal.id,
                "researcher_name": signal.researcher_name,
                "source_url": signal.source_url,
                "evidence_strength": signal.evidence_strength.value,
            }
            for signal in github_signals[:5]
        ],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect GitHub signals for a paper title.")
    parser.add_argument("--paper-title", required=True)
    parser.add_argument("--researcher", required=True)
    parser.add_argument("--min-stars", type=int, default=5)
    parser.add_argument("--api-token")
    return parser


def main(argv: list[str] | None = None) -> int:
    from app.models import PaperAuthor

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    paper = Paper(
        id="paper_cli",
        title=args.paper_title,
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="",
        authors=[PaperAuthor(name=args.researcher, affiliation="Unknown", role="Researcher")],
    )
    researcher = Researcher(
        id="researcher_cli",
        name=args.researcher,
        affiliation="Unknown",
        role="Researcher",
        papers=[paper.id],
    )
    config = GitHubConfig(
        enabled=True,
        api_token=args.api_token,
        min_stars=args.min_stars,
    )
    signals = detect_github_signals([paper], [researcher], config)
    print(json.dumps(summarize_github_signals(signals), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
