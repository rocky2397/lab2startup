"""OpenReview integration — fetch papers and enrich affiliations (Step 10c)."""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.integrations.openalex import TOPIC_KEYWORD_RULES, infer_topic
from app.models import IdentityConfidence, Paper, PaperAuthor, Researcher

logger = logging.getLogger(__name__)

OPENREVIEW_API_BASE = "https://api2.openreview.net"
DEFAULT_USER_AGENT = "Lab2Startup/0.1 (mailto:research@example.com)"
NOTES_PAGE_SIZE = 1000
DEFAULT_MAX_RETRIES = 6
PROFILE_PROGRESS_EVERY = 25

VENUE_PREFIXES: dict[str, str] = {
    "neurips": "NeurIPS.cc",
    "iclr": "ICLR.cc",
    "icml": "ICML.cc",
}


@dataclass
class OpenReviewConfig:
    """Parameters for OpenReview fetch or enrichment."""

    enabled: bool = False
    conference: str = "NeurIPS"
    year: int = 2024
    max_results: int = 50
    accepted_only: bool = True
    fetch_profiles: bool = True
    fetch_as_source: bool = False
    request_delay_seconds: float = 1.0
    max_retries: int = DEFAULT_MAX_RETRIES


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "unknown"


def normalize_title(title: str) -> str:
    """Normalize titles for cross-source matching."""
    normalized = unicodedata.normalize("NFKD", title)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_title.lower()).strip()


def normalize_person_name(name: str) -> str:
    """Normalize person names for matching authorship records."""
    cleaned = re.sub(r"[^a-zA-Z\s]", " ", name.lower())
    tokens = [token for token in cleaned.split() if len(token) > 1]
    return " ".join(sorted(set(tokens)))


def names_match(left: str, right: str) -> bool:
    """Return True when two author names likely refer to the same person."""
    left_norm = normalize_person_name(left)
    right_norm = normalize_person_name(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True

    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if len(left_tokens.intersection(right_tokens)) >= 2:
        return True
    return left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens)


def venue_id_for_conference(conference: str, year: int) -> str:
    """Build the OpenReview venue ID for a conference and year."""
    prefix = VENUE_PREFIXES.get(conference.strip().lower())
    if prefix is None:
        raise ValueError(f"Unsupported OpenReview conference: {conference}")
    return f"{prefix}/{year}/Conference"


def _content_value(content: dict[str, Any], key: str) -> Any:
    field_value = content.get(key)
    if isinstance(field_value, dict):
        return field_value.get("value")
    return field_value


def _current_position(profile: dict[str, Any]) -> tuple[str, str]:
    """Return current affiliation and role from an OpenReview profile."""
    history = profile.get("content", {}).get("history") or []
    for entry in history:
        if entry.get("end") is None:
            institution = entry.get("institution") or {}
            affiliation = institution.get("name") or "Unknown"
            role = entry.get("position") or "Researcher"
            return affiliation, role

    if history:
        institution = history[0].get("institution") or {}
        affiliation = institution.get("name") or "Unknown"
        role = history[0].get("position") or "Researcher"
        return affiliation, role

    return "Unknown", "Researcher"


def _openreview_url(profile_id: str | None) -> str | None:
    if not profile_id:
        return None
    return f"https://openreview.net/profile?id={profile_id}"


def _paper_url(forum_id: str | None) -> str | None:
    if not forum_id:
        return None
    return f"https://openreview.net/forum?id={forum_id}"


class OpenReviewClient:
    """Minimal OpenReview API v2 client."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        request_delay_seconds: float = 1.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._client = httpx.Client(
            base_url=OPENREVIEW_API_BASE,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=timeout,
        )
        self.request_delay_seconds = request_delay_seconds
        self.max_retries = max_retries

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenReviewClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _pause(self) -> None:
        if self.request_delay_seconds:
            time.sleep(self.request_delay_seconds)

    @staticmethod
    def _retry_wait(response: httpx.Response, attempt: int, base_delay: float) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), base_delay)
            except ValueError:
                pass
        return min(base_delay * (2**attempt), 60.0)

    def _retry_base_delay(self) -> float:
        return max(self.request_delay_seconds, 0.01)

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        base_delay = self._retry_base_delay()
        for attempt in range(self.max_retries):
            response = self._client.get(path, params=params)

            if response.status_code == 429:
                if attempt >= self.max_retries - 1:
                    response.raise_for_status()
                wait = self._retry_wait(response, attempt, base_delay)
                logger.warning(
                    "OpenReview rate limited on %s (attempt %s/%s); waiting %.1fs",
                    path,
                    attempt + 1,
                    self.max_retries,
                    wait,
                )
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                if attempt >= self.max_retries - 1:
                    response.raise_for_status()
                wait = min(base_delay * (2**attempt), 60.0)
                logger.warning(
                    "OpenReview server error %s on %s; waiting %.1fs",
                    response.status_code,
                    path,
                    wait,
                )
                time.sleep(wait)
                continue

            response.raise_for_status()
            self._pause()
            return response.json()

        raise RuntimeError(f"OpenReview request failed after {self.max_retries} retries: {path}")

    def get_note(self, note_id: str) -> dict[str, Any] | None:
        payload = self._get("/notes", params={"id": note_id})
        notes = payload.get("notes") or []
        return notes[0] if notes else None

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        try:
            payload = self._get("/profiles", params={"id": profile_id})
        except httpx.HTTPError as exc:
            logger.warning(
                "Skipping OpenReview profile %s after retries: %s",
                profile_id,
                exc,
            )
            return None
        profiles = payload.get("profiles") or []
        return profiles[0] if profiles else None

    def get_profiles(self, profile_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch profiles one at a time (OpenReview guest API is single-id only)."""
        profiles: dict[str, dict[str, Any]] = {}
        skipped = 0
        total = len(profile_ids)

        for index, profile_id in enumerate(profile_ids, start=1):
            profile = self.get_profile(profile_id)
            if profile:
                profiles[profile_id] = profile
            else:
                skipped += 1

            if index % PROFILE_PROGRESS_EVERY == 0:
                logger.info(
                    "OpenReview profiles: %s/%s fetched (%s skipped so far)",
                    index,
                    total,
                    skipped,
                )

        if skipped:
            logger.warning(
                "OpenReview profile fetch skipped %s/%s profiles (rate limits or missing data)",
                skipped,
                total,
            )
        return profiles

    def iter_submission_notes(
        self,
        *,
        venue_id: str,
        accepted_only: bool = True,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Paginate accepted submissions for a venue."""
        invitation = f"{venue_id}/-/Submission"
        collected: list[dict[str, Any]] = []
        offset = 0

        while len(collected) < max_results:
            params: dict[str, Any] = {
                "invitation": invitation,
                "limit": min(NOTES_PAGE_SIZE, max_results - len(collected)),
                "offset": offset,
            }
            if accepted_only:
                params["content.venueid"] = venue_id

            payload = self._get("/notes", params=params)
            notes = payload.get("notes") or []
            if not notes:
                break

            collected.extend(notes)
            if len(notes) < params["limit"]:
                break
            offset += len(notes)

        return collected[:max_results]


def _build_authors(
    note: dict[str, Any],
    profiles_by_id: dict[str, dict[str, Any]],
) -> list[PaperAuthor]:
    content = note.get("content") or {}
    names = _content_value(content, "authors") or []
    author_ids = _content_value(content, "authorids") or []

    authors: list[PaperAuthor] = []
    for index, name in enumerate(names):
        profile_id = author_ids[index] if index < len(author_ids) else None
        profile = profiles_by_id.get(profile_id) if profile_id else None

        affiliation = "Unknown"
        role = "Researcher"
        if profile:
            affiliation, role = _current_position(profile)

        authors.append(
            PaperAuthor(
                name=name,
                affiliation=affiliation,
                role=role,
                openreview_profile_id=profile_id,
            )
        )
    return authors


def normalize_note(
    note: dict[str, Any],
    *,
    conference: str,
    profiles_by_id: dict[str, dict[str, Any]] | None = None,
) -> Paper:
    """Convert an OpenReview submission note into a Paper model."""
    content = note.get("content") or {}
    title = _content_value(content, "title") or "Untitled"
    abstract = _content_value(content, "abstract") or ""
    primary_area = _content_value(content, "primary_area")
    keywords = _content_value(content, "keywords") or []
    keyword_text = ", ".join(keywords) if isinstance(keywords, list) else str(keywords)

    year_raw = _content_value(content, "year")
    year = int(year_raw) if year_raw else 0
    if not year:
        venue_label = _content_value(content, "venue") or ""
        match = re.search(r"(20\d{2})", str(venue_label))
        year = int(match.group(1)) if match else 0

    forum_id = note.get("forum") or note.get("id")
    topic = infer_topic(title, abstract, primary_area, topic_keywords=None)
    if topic == "General AI" and keyword_text:
        topic = infer_topic(title, abstract, keyword_text)

    authors = _build_authors(note, profiles_by_id or {})

    return Paper(
        id=f"paper_{_slugify(forum_id or title)[:40]}",
        title=title,
        conference=conference,
        year=year,
        topic=topic,
        abstract=abstract,
        authors=authors,
        source_url=_paper_url(forum_id),
        openreview_id=forum_id,
        openreview_url=_paper_url(forum_id),
    )


def fetch_papers_from_openreview(config: OpenReviewConfig) -> list[Paper]:
    """Fetch accepted conference papers from OpenReview."""
    venue_id = venue_id_for_conference(config.conference, config.year)

    with OpenReviewClient(
        request_delay_seconds=config.request_delay_seconds,
        max_retries=config.max_retries,
    ) as client:
        notes = client.iter_submission_notes(
            venue_id=venue_id,
            accepted_only=config.accepted_only,
            max_results=config.max_results,
        )

        profiles_by_id: dict[str, dict[str, Any]] = {}
        if config.fetch_profiles:
            profile_ids = sorted(
                {
                    profile_id
                    for note in notes
                    for profile_id in (_content_value(note.get("content") or {}, "authorids") or [])
                    if profile_id
                }
            )
            profiles_by_id = client.get_profiles(profile_ids)

    papers = [
        normalize_note(
            note,
            conference=config.conference,
            profiles_by_id=profiles_by_id,
        )
        for note in notes
    ]
    papers.sort(key=lambda paper: (-paper.year, paper.title.lower()))
    return papers


def _index_notes_by_title(notes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        normalize_title(_content_value(note.get("content") or {}, "title") or ""): note
        for note in notes
        if _content_value(note.get("content") or {}, "title")
    }


def enrich_papers_with_openreview(
    papers: list[Paper],
    config: OpenReviewConfig,
) -> list[Paper]:
    """Match existing papers to OpenReview submissions and enrich author metadata."""
    if not config.enabled or not papers:
        return papers

    venue_id = venue_id_for_conference(config.conference, config.year)

    with OpenReviewClient(
        request_delay_seconds=config.request_delay_seconds,
        max_retries=config.max_retries,
    ) as client:
        notes = client.iter_submission_notes(
            venue_id=venue_id,
            accepted_only=config.accepted_only,
            max_results=max(config.max_results, len(papers) * 5, 1000),
        )
        notes_by_title = _index_notes_by_title(notes)

        profile_ids: set[str] = set()
        matched_pairs: list[tuple[Paper, dict[str, Any]]] = []
        for paper in papers:
            note = notes_by_title.get(normalize_title(paper.title))
            if note is None:
                continue
            matched_pairs.append((paper, note))
            for profile_id in _content_value(note.get("content") or {}, "authorids") or []:
                if profile_id:
                    profile_ids.add(profile_id)

        profiles_by_id = client.get_profiles(sorted(profile_ids)) if config.fetch_profiles else {}

    enriched: list[Paper] = []
    matched_ids = {paper.id for paper, _ in matched_pairs}
    updates = {
        paper.id: normalize_note(
            note,
            conference=paper.conference,
            profiles_by_id=profiles_by_id,
        )
        for paper, note in matched_pairs
    }

    for paper in papers:
        if paper.id not in matched_ids:
            enriched.append(paper)
            continue

        update = updates[paper.id]
        merged_authors = _merge_authors(paper.authors, update.authors)
        enriched.append(
            paper.model_copy(
                update={
                    "authors": merged_authors,
                    "abstract": paper.abstract or update.abstract,
                    "source_url": paper.source_url or update.source_url,
                    "openreview_id": update.openreview_id,
                    "openreview_url": update.openreview_url,
                    "year": paper.year or update.year,
                }
            )
        )

    return enriched


def _merge_authors(
    existing_authors: list[PaperAuthor],
    openreview_authors: list[PaperAuthor],
) -> list[PaperAuthor]:
    merged: list[PaperAuthor] = []
    for author in existing_authors:
        match = next(
            (item for item in openreview_authors if names_match(author.name, item.name)),
            None,
        )
        if match is None:
            merged.append(author)
            continue

        merged.append(
            author.model_copy(
                update={
                    "affiliation": match.affiliation
                    if match.affiliation != "Unknown"
                    else author.affiliation,
                    "role": match.role if match.role != "Researcher" else author.role,
                    "openreview_profile_id": match.openreview_profile_id,
                }
            )
        )
    return merged


def sync_researchers_with_openreview(
    papers: list[Paper],
    researchers: list[Researcher],
) -> list[Researcher]:
    """Copy OpenReview profile metadata from enriched papers onto researchers."""
    profile_by_name: dict[str, PaperAuthor] = {}
    for paper in papers:
        for author in paper.authors:
            profile_by_name[normalize_person_name(author.name)] = author

    updated: list[Researcher] = []
    for researcher in researchers:
        author = profile_by_name.get(normalize_person_name(researcher.name))
        if author is None or not author.openreview_profile_id:
            updated.append(researcher)
            continue

        affiliation = researcher.affiliation
        if author.affiliation != "Unknown":
            affiliation = author.affiliation

        role = researcher.role
        if author.role != "Researcher":
            role = author.role

        confidence = IdentityConfidence.HIGH
        explanation = (
            f"Linked to OpenReview profile {author.openreview_profile_id} "
            f"with affiliation '{affiliation}'."
        )

        updated.append(
            researcher.model_copy(
                update={
                    "affiliation": affiliation,
                    "role": role,
                    "openreview_profile_id": author.openreview_profile_id,
                    "openreview_url": _openreview_url(author.openreview_profile_id),
                    "identity_confidence": confidence,
                    "identity_confidence_explanation": explanation,
                }
            )
        )

    return updated


def summarize_openreview(
    papers: list[Paper],
    researchers: list[Researcher],
) -> dict[str, object]:
    """Return quick stats for OpenReview enrichment coverage."""
    return {
        "paper_count": len(papers),
        "papers_with_openreview_id": sum(1 for paper in papers if paper.openreview_id),
        "researcher_count": len(researchers),
        "researchers_with_openreview_profile": sum(
            1 for researcher in researchers if researcher.openreview_profile_id
        ),
        "sample_links": [
            {
                "name": researcher.name,
                "openreview_url": researcher.openreview_url,
                "affiliation": researcher.affiliation,
            }
            for researcher in researchers
            if researcher.openreview_profile_id
        ][:5],
    }


def write_papers_json(papers: list[Paper], output_path: Path | str) -> Path:
    """Write normalized papers to JSON compatible with load_papers()."""
    path = Path(output_path)
    payload = {"papers": [paper.model_dump() for paper in papers]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch conference papers from OpenReview.")
    parser.add_argument("--conference", default="NeurIPS")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write papers JSON for offline use",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    config = OpenReviewConfig(
        enabled=True,
        conference=args.conference,
        year=args.year,
        max_results=args.max_results,
    )
    papers = fetch_papers_from_openreview(config)
    summary = summarize_openreview(papers, [])

    print(json.dumps(summary, indent=2))
    print(f"Fetched {len(papers)} papers.")

    if args.output:
        path = write_papers_json(papers, args.output)
        print(f"Wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
