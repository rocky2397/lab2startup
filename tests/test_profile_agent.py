"""Tests for coauthor clustering (Step 4)."""

from app.agents.ingestion_agent import ingest_papers
from app.agents.profile_agent import (
    build_clusters,
    build_coauthor_graph,
    build_profiles,
    summarize_profiles,
)


def test_build_coauthor_graph_links_paper_teams() -> None:
    ingestion = ingest_papers()
    name_to_id = {researcher.name: researcher.id for researcher in ingestion.researchers}
    graph = build_coauthor_graph(ingestion.papers, name_to_id)

    john = "researcher_john_yang"
    carlos = "researcher_carlos_e_jimenez"
    assert carlos in graph[john]
    assert john in graph[carlos]


def test_build_clusters_creates_one_team_per_paper() -> None:
    ingestion = ingest_papers()
    clusters = build_clusters(ingestion.researchers, ingestion.papers)

    assert len(clusters) == 7
    swe_cluster = next(
        cluster
        for cluster in clusters
        if "researcher_john_yang" in cluster.researchers
    )
    assert len(swe_cluster.researchers) == 7
    assert swe_cluster.shared_papers == ["paper_001"]
    assert swe_cluster.topic == "AI agents"
    assert swe_cluster.name.startswith("AI agents:")


def test_build_profiles_end_to_end() -> None:
    result = build_profiles()
    summary = summarize_profiles(result)

    assert summary["paper_count"] == 7
    assert summary["researcher_count"] == 30
    assert summary["cluster_count"] == 7
    assert all(cluster["researcher_count"] >= 2 for cluster in summary["clusters"])
