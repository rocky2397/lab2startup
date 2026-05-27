"""OpenAlex integration — fetch and normalize conference papers (Step 10a)."""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.models import Paper, PaperAuthor

OPENALEX_API_BASE = "https://api.openalex.org"
DEFAULT_PER_PAGE = 25
DEFAULT_USER_AGENT = "Lab2Startup/0.1 (mailto:research@example.com)"

# Known OpenAlex source IDs for major ML/systems venues.
KNOWN_CONFERENCE_SOURCES: dict[str, str] = {
    "neurips": "S4306420609",
    "neural information processing systems": "S4306420609",
    "icml": "S196734849",
    "international conference on machine learning": "S196734849",
    "iclr": "S4306419643",
    "international conference on learning representations": "S4306419643",
    "mlsys": "S4210210349",
    "osdi": "S4306420608",
    "sosp": "S4306420610",
    "usenix security": "S4306420611",
}

TOPIC_KEYWORD_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("AI agents", ("agent", "llm agent", "tool use", "software engineering", "code generation")),
    ("ML systems", ("inference", "training system", "mlsys", "serving", "efficient inference")),
    ("Security", ("security", "cyber", "vulnerability", "privacy", "encryption")),
    ("Platform engineering", ("kubernetes", "platform engineering", "devops", "infrastructure as code")),
    ("Robotics", ("robot", "manipulation", "locomotion", "reinforcement learning control")),
    ("Biotech AI", ("drug", "protein", "molecule", "genomics", "biomedical")),
]

AUTHOR_POSITION_ROLES = {
    "first": "First Author",
    "middle": "Coauthor",
    "last": "Senior Author",
}


@dataclass
class OpenAlexFetchConfig:
    """Parameters for fetching papers from OpenAlex."""

    conference: str = "NeurIPS"
    year: int = 2024
    search_query: str | None = None
    topic_keywords: list[str] = field(default_factory=list)
    openalex_work_ids: list[str] = field(default_factory=list)
    source_id: str | None = None
    max_results: int = 50
    mailto: str | None = None
    request_delay_seconds: float = 0.11


def _normalize_openalex_id(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("https://openalex.org/"):
        return value.rsplit("/", 1)[-1]
    return value


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "unknown"


def decode_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Reconstruct abstract text from OpenAlex inverted index."""
    if not inverted_index:
        return ""

    positions: list[tuple[int, str]] = []
    for token, indexes in inverted_index.items():
        for index in indexes:
            positions.append((index, token))

    if not positions:
        return ""

    positions.sort(key=lambda item: item[0])
    return " ".join(token for _, token in positions)


def infer_topic(
    title: str,
    abstract: str,
    primary_topic_name: str | None = None,
    topic_keywords: list[str] | None = None,
) -> str:
    """Map OpenAlex metadata to Lab2Startup topic labels."""
    haystack = " ".join(part for part in (title, abstract, primary_topic_name or "") if part).lower()

    if topic_keywords:
        for keyword in topic_keywords:
            if keyword.lower() in haystack:
                return keyword

    for label, keywords in TOPIC_KEYWORD_RULES:
        if any(keyword in haystack for keyword in keywords):
            return label

    if primary_topic_name:
        return primary_topic_name

    return "General AI"


def _author_affiliation(authorship: dict[str, Any]) -> str:
    institutions = authorship.get("institutions") or []
    if institutions:
        names = [inst.get("display_name", "") for inst in institutions if inst.get("display_name")]
        if names:
            return names[0]

    raw_affiliations = authorship.get("raw_affiliation_strings") or []
    if raw_affiliations:
        return raw_affiliations[0]

    affiliations = authorship.get("affiliations") or []
    for affiliation in affiliations:
        institution = affiliation.get("institution") or {}
        name = institution.get("display_name")
        if name:
            return name

    return "Unknown"


def _author_role(authorship: dict[str, Any]) -> str:
    position = authorship.get("author_position") or "middle"
    return AUTHOR_POSITION_ROLES.get(position, "Researcher")


def normalize_authorships(authorships: list[dict[str, Any]]) -> list[PaperAuthor]:
    """Convert OpenAlex authorship objects to PaperAuthor records."""
    authors: list[PaperAuthor] = []
    for authorship in authorships:
        author = authorship.get("author") or {}
        name = author.get("display_name") or authorship.get("raw_author_name")
        if not name:
            continue
        authors.append(
            PaperAuthor(
                name=name,
                affiliation=_author_affiliation(authorship),
                role=_author_role(authorship),
            )
        )
    return authors


def _pick_source_url(work: dict[str, Any]) -> str | None:
    primary_location = work.get("primary_location") or {}
    for key in ("landing_page_url", "pdf_url"):
        url = primary_location.get(key)
        if url:
            return url

    open_access = work.get("open_access") or {}
    oa_url = open_access.get("oa_url")
    if oa_url:
        return oa_url

    doi = work.get("doi")
    if doi:
        return doi

    return None


def normalize_work(
    work: dict[str, Any],
    *,
    conference: str,
    topic_keywords: list[str] | None = None,
    topic_override: str | None = None,
) -> Paper:
    """Convert one OpenAlex work payload into a Paper model."""
    openalex_id = _normalize_openalex_id(work.get("id"))
    title = work.get("title") or work.get("display_name") or "Untitled"
    abstract = decode_abstract(work.get("abstract_inverted_index"))

    primary_topic = work.get("primary_topic") or {}
    topic = topic_override or infer_topic(
        title,
        abstract,
        primary_topic.get("display_name"),
        topic_keywords=topic_keywords,
    )

    paper_id = f"paper_{_slugify(openalex_id or title)[:40]}"

    return Paper(
        id=paper_id,
        title=title,
        conference=conference,
        year=int(work.get("publication_year") or 0),
        topic=topic,
        abstract=abstract,
        authors=normalize_authorships(work.get("authorships") or []),
        source_url=_pick_source_url(work),
        openalex_id=openalex_id,
    )


class OpenAlexClient:
    """Minimal OpenAlex HTTP client with polite-pool support."""

    def __init__(
        self,
        *,
        mailto: str | None = None,
        timeout: float = 30.0,
        request_delay_seconds: float = 0.11,
    ) -> None:
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        if mailto:
            headers["User-Agent"] = f"Lab2Startup/0.1 (mailto:{mailto})"

        self._client = httpx.Client(
            base_url=OPENALEX_API_BASE,
            headers=headers,
            timeout=timeout,
        )
        self.request_delay_seconds = request_delay_seconds

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenAlexClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        if self.request_delay_seconds:
            time.sleep(self.request_delay_seconds)
        return response.json()

    def resolve_source_id(self, conference: str) -> str | None:
        """Resolve a conference name to an OpenAlex source ID."""
        normalized = conference.strip().lower()
        known = KNOWN_CONFERENCE_SOURCES.get(normalized)
        if known and known.startswith("S"):
            return known

        payload = self._get(
            "/sources",
            params={
                "filter": f"display_name.search:{conference}",
                "per-page": 5,
                "sort": "works_count:desc",
            },
        )
        results = payload.get("results") or []
        if not results:
            return None

        for source in results:
            if (source.get("type") or "").lower() == "conference":
                return _normalize_openalex_id(source.get("id"))

        return _normalize_openalex_id(results[0].get("id"))

    def fetch_works_page(
        self,
        *,
        filters: list[str],
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
        sort: str = "cited_by_count:desc",
    ) -> dict[str, Any]:
        """Fetch one page of works for the given filters."""
        params = {
            "filter": ",".join(filters),
            "page": page,
            "per-page": per_page,
            "sort": sort,
        }
        return self._get("/works", params=params)

    def fetch_works_by_ids(self, work_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch works in batch using OpenAlex pipe-separated ID filter."""
        if not work_ids:
            return []

        normalized_ids = [_normalize_openalex_id(work_id) for work_id in work_ids]
        normalized_ids = [work_id for work_id in normalized_ids if work_id]
        if not normalized_ids:
            return []

        payload = self._get(
            "/works",
            params={
                "filter": f"ids.openalex:{'|'.join(normalized_ids)}",
                "per-page": min(len(normalized_ids), 200),
            },
        )
        return payload.get("results") or []

    def iter_works(
        self,
        *,
        filters: list[str],
        max_results: int,
        sort: str = "cited_by_count:desc",
    ) -> list[dict[str, Any]]:
        """Paginate through OpenAlex works until max_results is reached."""
        collected: list[dict[str, Any]] = []
        page = 1

        while len(collected) < max_results:
            per_page = min(DEFAULT_PER_PAGE, max_results - len(collected))
            payload = self.fetch_works_page(
                filters=filters,
                page=page,
                per_page=per_page,
                sort=sort,
            )
            results = payload.get("results") or []
            if not results:
                break

            collected.extend(results)
            if len(results) < per_page:
                break
            page += 1

        return collected[:max_results]


def _matches_topic_keywords(paper_text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = paper_text.lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def fetch_papers_from_openalex(config: OpenAlexFetchConfig) -> list[Paper]:
    """Fetch papers from OpenAlex and normalize them into Paper models."""
    with OpenAlexClient(
        mailto=config.mailto,
        request_delay_seconds=config.request_delay_seconds,
    ) as client:
        works: list[dict[str, Any]] = []

        if config.openalex_work_ids:
            works.extend(client.fetch_works_by_ids(config.openalex_work_ids))

        use_query = bool(config.search_query) or not config.openalex_work_ids
        if use_query:
            source_id = config.source_id
            if source_id is None and config.conference:
                source_id = client.resolve_source_id(config.conference)

            filters: list[str] = [f"publication_year:{config.year}"]
            if source_id:
                filters.append(f"locations.source.id:{source_id}")
            if config.search_query:
                filters.append(f"title.search:{config.search_query}")

            works.extend(
                client.iter_works(
                    filters=filters,
                    max_results=config.max_results,
                )
            )

    deduped: dict[str, dict[str, Any]] = {}
    for work in works:
        work_id = _normalize_openalex_id(work.get("id"))
        if work_id:
            deduped[work_id] = work

    papers: list[Paper] = []
    for work in deduped.values():
        paper = normalize_work(
            work,
            conference=config.conference,
            topic_keywords=config.topic_keywords or None,
        )
        paper_text = f"{paper.title} {paper.abstract} {paper.topic}"
        if _matches_topic_keywords(paper_text, config.topic_keywords):
            papers.append(paper)

    papers.sort(key=lambda paper: (-paper.year, paper.title.lower()))
    return papers[: config.max_results]


def write_papers_json(papers: list[Paper], output_path: Path | str) -> Path:
    """Write normalized papers to a JSON file compatible with load_papers()."""
    path = Path(output_path)
    payload = {"papers": [paper.model_dump() for paper in papers]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch conference papers from OpenAlex.")
    parser.add_argument("--conference", default="NeurIPS", help="Conference name")
    parser.add_argument("--year", type=int, default=2024, help="Publication year")
    parser.add_argument("--search", dest="search_query", help="Title search query")
    parser.add_argument(
        "--topic",
        dest="topic_keywords",
        action="append",
        default=[],
        help="Topic keyword filter (repeatable)",
    )
    parser.add_argument(
        "--work-id",
        dest="work_ids",
        action="append",
        default=[],
        help="Explicit OpenAlex work ID (repeatable)",
    )
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--mailto", help="Email for OpenAlex polite pool")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write papers JSON for offline use",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    config = OpenAlexFetchConfig(
        conference=args.conference,
        year=args.year,
        search_query=args.search_query,
        topic_keywords=args.topic_keywords,
        openalex_work_ids=args.work_ids,
        max_results=args.max_results,
        mailto=args.mailto,
    )
    papers = fetch_papers_from_openalex(config)

    print(f"Fetched {len(papers)} papers for {config.conference} {config.year}.")
    for paper in papers[:10]:
        print(f"- [{paper.topic}] {paper.title}")

    if args.output:
        path = write_papers_json(papers, args.output)
        print(f"Wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
