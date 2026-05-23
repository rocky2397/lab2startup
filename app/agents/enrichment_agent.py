"""Enrichment agent — attach Semantic Scholar metadata (Step 10b)."""

from __future__ import annotations

from app.integrations.semantic_scholar import (
    SemanticScholarConfig,
    enrich_with_semantic_scholar,
    summarize_enrichment,
)
from app.models import Paper, Researcher


def enrich_dataset(
    papers: list[Paper],
    researchers: list[Researcher],
    config: SemanticScholarConfig | None = None,
) -> tuple[list[Paper], list[Researcher]]:
    """Optionally enrich papers and researchers with Semantic Scholar metadata."""
    if config is None or not config.enabled:
        return papers, researchers
    return enrich_with_semantic_scholar(papers, researchers, config)


def summarize_dataset_enrichment(
    papers: list[Paper],
    researchers: list[Researcher],
) -> dict[str, object]:
    """Summarize Semantic Scholar coverage for inspection."""
    return summarize_enrichment(papers, researchers)
