"""Ingestion agent — loads papers and extracts researchers (Step 3)."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.models import IdentityConfidence, Paper, Researcher
from app.schemas import resolve_papers


def make_researcher_id(name: str) -> str:
    """Create a stable researcher ID from a display name."""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")
    return f"researcher_{slug or 'unknown'}"


def _pick_primary_affiliation(
    affiliation_counts: Counter[str],
    role_by_affiliation: dict[str, str],
) -> tuple[str, str]:
    """Choose the most common affiliation and an associated role."""
    affiliation, _ = affiliation_counts.most_common(1)[0]
    role = role_by_affiliation.get(affiliation, "Researcher")
    return affiliation, role


def _assess_identity_confidence(
    name: str,
    affiliation_counts: Counter[str],
) -> tuple[IdentityConfidence, str]:
    """Estimate whether a name maps cleanly to a single person."""
    distinct_affiliations = list(affiliation_counts.keys())

    if len(distinct_affiliations) == 1:
        return (
            IdentityConfidence.HIGH,
            f"Single affiliation ({distinct_affiliations[0]}) across all papers for '{name}'.",
        )

    if len(distinct_affiliations) > 1:
        affiliation_list = ", ".join(distinct_affiliations)
        return (
            IdentityConfidence.MEDIUM,
            f"Multiple affiliations observed for '{name}': {affiliation_list}. "
            "Using the most frequent affiliation; manual verification recommended.",
        )

    return (
        IdentityConfidence.LOW,
        f"No affiliation data found for '{name}'.",
    )


def extract_researchers(papers: list[Paper]) -> list[Researcher]:
    """Build researcher profiles with papers and coauthor links from paper authorship."""
    papers_by_id = {paper.id: paper for paper in papers}

    # Map each author name to the papers and metadata seen in the dataset.
    papers_for_name: dict[str, list[str]] = defaultdict(list)
    affiliation_counts: dict[str, Counter[str]] = defaultdict(Counter)
    role_by_affiliation: dict[str, dict[str, str]] = defaultdict(dict)

    for paper in papers:
        for author in paper.authors:
            papers_for_name[author.name].append(paper.id)
            affiliation_counts[author.name][author.affiliation] += 1
            role_by_affiliation[author.name][author.affiliation] = author.role

    # Resolve names to researcher IDs once so coauthor links stay consistent.
    name_to_id = {name: make_researcher_id(name) for name in papers_for_name}

    researchers: list[Researcher] = []
    for name in sorted(papers_for_name):
        researcher_id = name_to_id[name]
        affiliation, role = _pick_primary_affiliation(
            affiliation_counts[name],
            role_by_affiliation[name],
        )
        confidence, explanation = _assess_identity_confidence(name, affiliation_counts[name])

        coauthor_ids: set[str] = set()
        for paper_id in papers_for_name[name]:
            paper = papers_by_id[paper_id]
            for author in paper.authors:
                if author.name == name:
                    continue
                coauthor_ids.add(name_to_id[author.name])

        researchers.append(
            Researcher(
                id=researcher_id,
                name=name,
                affiliation=affiliation,
                role=role,
                papers=sorted(set(papers_for_name[name])),
                coauthors=sorted(coauthor_ids),
                identity_confidence=confidence,
                identity_confidence_explanation=explanation,
            )
        )

    return researchers


@dataclass
class IngestionResult:
    """Output of the ingestion agent."""

    papers: list[Paper]
    researchers: list[Researcher]

    @property
    def paper_count(self) -> int:
        return len(self.papers)

    @property
    def researcher_count(self) -> int:
        return len(self.researchers)


def ingest_papers(
    path: Path | str | None = None,
    *,
    papers: list[Paper] | None = None,
    openalex_config=None,
) -> IngestionResult:
    """Load papers from JSON or OpenAlex and extract researcher profiles."""
    if papers is None:
        papers = resolve_papers(path, openalex_config=openalex_config)
    researchers = extract_researchers(papers)
    return IngestionResult(papers=papers, researchers=researchers)


def summarize_ingestion(result: IngestionResult) -> dict[str, object]:
    """Return quick stats for inspecting ingestion output."""
    confidence_counts = Counter(
        researcher.identity_confidence.value for researcher in result.researchers
    )
    return {
        "paper_count": result.paper_count,
        "researcher_count": result.researcher_count,
        "identity_confidence": dict(confidence_counts),
        "sample_researchers": [
            {
                "id": researcher.id,
                "name": researcher.name,
                "affiliation": researcher.affiliation,
                "paper_count": len(researcher.papers),
                "coauthor_count": len(researcher.coauthors),
            }
            for researcher in result.researchers[:3]
        ],
    }
