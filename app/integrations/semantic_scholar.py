"""Semantic Scholar integration — enrich papers and authors (Step 10b)."""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.models import Paper, PaperAuthor, Researcher

SEMANTIC_SCHOLAR_API_BASE = "https://api.semanticscholar.org/graph/v1"
DEFAULT_USER_AGENT = "Lab2Startup/0.1 (mailto:research@example.com)"
PAPER_BATCH_SIZE = 50
AUTHOR_BATCH_SIZE = 100
PAPER_FIELDS = (
    "paperId,externalIds,title,citationCount,influentialCitationCount,"
    "referenceCount,authors.authorId,authors.name"
)
AUTHOR_FIELDS = "authorId,name,citationCount,hIndex,paperCount,affiliations"


@dataclass
class SemanticScholarConfig:
    """Parameters for Semantic Scholar enrichment."""

    enabled: bool = False
    api_key: str | None = None
    fetch_author_profiles: bool = True
    request_delay_seconds: float = 1.1


def normalize_name(name: str) -> str:
    """Normalize author names for fuzzy matching across data sources."""
    cleaned = re.sub(r"[^a-zA-Z\s]", " ", name.lower())
    tokens = [token for token in cleaned.split() if len(token) > 1]
    return " ".join(sorted(set(tokens)))


def names_match(left: str, right: str) -> bool:
    """Return True when two author names likely refer to the same person."""
    left_norm = normalize_name(left)
    right_norm = normalize_name(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True

    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if len(left_tokens.intersection(right_tokens)) >= 2:
        return True

    return left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens)


def extract_semantic_scholar_paper_id(paper: Paper) -> str | None:
    """Build a Semantic Scholar lookup ID from paper URLs or identifiers."""
    if paper.source_url:
        url = paper.source_url.lower()
        arxiv_match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", url)
        if arxiv_match:
            return f"ARXIV:{arxiv_match.group(1)}"

        doi_match = re.search(r"doi\.org/(.+)$", url)
        if doi_match:
            return f"DOI:{doi_match.group(1)}"

        host = urlparse(paper.source_url).netloc.lower()
        if "semanticscholar.org" in host:
            path = urlparse(paper.source_url).path.rstrip("/")
            paper_slug = path.rsplit("/", 1)[-1]
            if paper_slug:
                return paper_slug

    return None


class SemanticScholarClient:
    """Minimal Semantic Scholar HTTP client."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
        request_delay_seconds: float = 1.1,
    ) -> None:
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        if api_key:
            headers["x-api-key"] = api_key

        self._client = httpx.Client(
            base_url=SEMANTIC_SCHOLAR_API_BASE,
            headers=headers,
            timeout=timeout,
        )
        self.request_delay_seconds = request_delay_seconds

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SemanticScholarClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _pause(self) -> None:
        if self.request_delay_seconds:
            time.sleep(self.request_delay_seconds)

    def _post(self, path: str, *, params: dict[str, Any], payload: dict[str, Any]) -> Any:
        response = self._client.post(path, params=params, json=payload)
        response.raise_for_status()
        self._pause()
        return response.json()

    def fetch_papers_batch(self, paper_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch paper metadata in batches."""
        results: list[dict[str, Any]] = []
        for start in range(0, len(paper_ids), PAPER_BATCH_SIZE):
            batch = paper_ids[start : start + PAPER_BATCH_SIZE]
            payload = self._post(
                "/paper/batch",
                params={"fields": PAPER_FIELDS},
                payload={"ids": batch},
            )
            if isinstance(payload, list):
                results.extend(item for item in payload if item is not None)
        return results

    def fetch_authors_batch(self, author_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch author metadata in batches."""
        results: list[dict[str, Any]] = []
        for start in range(0, len(author_ids), AUTHOR_BATCH_SIZE):
            batch = author_ids[start : start + AUTHOR_BATCH_SIZE]
            payload = self._post(
                "/author/batch",
                params={"fields": AUTHOR_FIELDS},
                payload={"ids": batch},
            )
            if isinstance(payload, list):
                results.extend(item for item in payload if item is not None)
        return results


def _merge_paper_authors(
    existing_authors: list[PaperAuthor],
    s2_authors: list[dict[str, Any]],
) -> list[PaperAuthor]:
    merged: list[PaperAuthor] = []
    for author in existing_authors:
        s2_match = next(
            (item for item in s2_authors if names_match(author.name, item.get("name", ""))),
            None,
        )
        if s2_match is None:
            merged.append(author)
            continue

        merged.append(
            author.model_copy(
                update={"semantic_scholar_id": s2_match.get("authorId")}
            )
        )
    return merged


def apply_paper_metadata(paper: Paper, metadata: dict[str, Any]) -> Paper:
    """Merge Semantic Scholar paper metadata into a Paper model."""
    authors = _merge_paper_authors(paper.authors, metadata.get("authors") or [])
    return paper.model_copy(
        update={
            "semantic_scholar_id": metadata.get("paperId"),
            "citation_count": metadata.get("citationCount"),
            "influential_citation_count": metadata.get("influentialCitationCount"),
            "reference_count": metadata.get("referenceCount"),
            "authors": authors,
        }
    )


def apply_author_metadata(researcher: Researcher, metadata: dict[str, Any]) -> Researcher:
    """Merge Semantic Scholar author metadata into a Researcher model."""
    affiliations = metadata.get("affiliations") or []
    affiliation = researcher.affiliation
    if researcher.affiliation == "Unknown" and affiliations:
        affiliation = affiliations[0]

    return researcher.model_copy(
        update={
            "semantic_scholar_id": metadata.get("authorId"),
            "citation_count": metadata.get("citationCount"),
            "h_index": metadata.get("hIndex"),
            "paper_count": metadata.get("paperCount"),
            "affiliation": affiliation,
        }
    )


def _build_paper_lookup(
    papers: list[Paper],
    metadata_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    title_index = {
        row.get("title", "").lower(): row
        for row in metadata_rows
        if row.get("title")
    }

    for paper in papers:
        lookup_id = extract_semantic_scholar_paper_id(paper)
        if lookup_id:
            for row in metadata_rows:
                external_ids = row.get("externalIds") or {}
                arxiv_id = external_ids.get("ArXiv")
                if lookup_id.startswith("ARXIV:") and arxiv_id == lookup_id.split(":", 1)[1]:
                    lookup[paper.id] = row
                    break
                doi_id = external_ids.get("DOI")
                if lookup_id.startswith("DOI:") and doi_id == lookup_id.split(":", 1)[1]:
                    lookup[paper.id] = row
                    break
                if row.get("paperId") == lookup_id:
                    lookup[paper.id] = row
                    break
            if paper.id in lookup:
                continue

        title_match = title_index.get(paper.title.lower())
        if title_match:
            lookup[paper.id] = title_match

    return lookup


def _collect_author_ids(papers: list[Paper]) -> dict[str, str]:
    """Map normalized researcher names to Semantic Scholar author IDs."""
    author_ids: dict[str, str] = {}
    for paper in papers:
        for author in paper.authors:
            if author.semantic_scholar_id:
                author_ids[normalize_name(author.name)] = author.semantic_scholar_id
    return author_ids


def enrich_with_semantic_scholar(
    papers: list[Paper],
    researchers: list[Researcher],
    config: SemanticScholarConfig,
) -> tuple[list[Paper], list[Researcher]]:
    """Enrich papers and researchers with Semantic Scholar metadata."""
    if not config.enabled or not papers:
        return papers, researchers

    lookup_ids = []
    for paper in papers:
        lookup_id = extract_semantic_scholar_paper_id(paper)
        if lookup_id:
            lookup_ids.append(lookup_id)

    if not lookup_ids:
        return papers, researchers

    with SemanticScholarClient(
        api_key=config.api_key,
        request_delay_seconds=config.request_delay_seconds,
    ) as client:
        metadata_rows = client.fetch_papers_batch(sorted(set(lookup_ids)))
        paper_lookup = _build_paper_lookup(papers, metadata_rows)
        enriched_papers = [
            apply_paper_metadata(paper, paper_lookup[paper.id])
            if paper.id in paper_lookup
            else paper
            for paper in papers
        ]

        enriched_researchers = list(researchers)
        if config.fetch_author_profiles:
            author_ids_by_name = _collect_author_ids(enriched_papers)
            author_metadata = {
                row.get("authorId"): row
                for row in client.fetch_authors_batch(sorted(set(author_ids_by_name.values())))
                if row.get("authorId")
            }
            updated_researchers: list[Researcher] = []
            for researcher in enriched_researchers:
                author_id = author_ids_by_name.get(normalize_name(researcher.name))
                metadata = author_metadata.get(author_id) if author_id else None
                if metadata:
                    updated_researchers.append(apply_author_metadata(researcher, metadata))
                else:
                    updated_researchers.append(researcher)
            enriched_researchers = updated_researchers

    return enriched_papers, enriched_researchers


def summarize_enrichment(
    papers: list[Paper],
    researchers: list[Researcher],
) -> dict[str, object]:
    """Return quick stats for Semantic Scholar enrichment coverage."""
    enriched_papers = sum(1 for paper in papers if paper.semantic_scholar_id)
    enriched_researchers = sum(1 for researcher in researchers if researcher.semantic_scholar_id)
    top_cited = sorted(
        (
            {
                "name": researcher.name,
                "citation_count": researcher.citation_count,
                "h_index": researcher.h_index,
            }
            for researcher in researchers
            if researcher.citation_count is not None
        ),
        key=lambda item: item["citation_count"] or 0,
        reverse=True,
    )[:5]

    return {
        "paper_count": len(papers),
        "papers_with_semantic_scholar_id": enriched_papers,
        "researcher_count": len(researchers),
        "researchers_with_semantic_scholar_id": enriched_researchers,
        "top_cited_researchers": top_cited,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enrich local papers JSON with Semantic Scholar metadata."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("app/data/sample_papers.json"),
        help="Input papers JSON file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output path for enriched papers JSON",
    )
    parser.add_argument("--api-key", help="Semantic Scholar API key")
    parser.add_argument(
        "--skip-authors",
        action="store_true",
        help="Skip author profile enrichment",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from app.agents.ingestion_agent import extract_researchers
    from app.schemas import load_papers

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    config = SemanticScholarConfig(
        enabled=True,
        api_key=args.api_key,
        fetch_author_profiles=not args.skip_authors,
    )
    papers = load_papers(args.input)
    researchers = extract_researchers(papers)
    enriched_papers, enriched_researchers = enrich_with_semantic_scholar(
        papers,
        researchers,
        config,
    )
    summary = summarize_enrichment(enriched_papers, enriched_researchers)

    print(json.dumps(summary, indent=2))

    if args.output:
        payload = {"papers": [paper.model_dump() for paper in enriched_papers]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote enriched papers to {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
