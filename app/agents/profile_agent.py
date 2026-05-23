"""Profile agent — coauthor clustering for researcher teams (Step 4)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.agents.ingestion_agent import IngestionResult, ingest_papers
from app.models import Cluster, Paper, Researcher


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "unknown"


def _find_connected_components(graph: dict[str, set[str]]) -> list[set[str]]:
    """Return connected components from an undirected adjacency graph."""
    visited: set[str] = set()
    components: list[set[str]] = []

    for node in graph:
        if node in visited:
            continue

        stack = [node]
        component: set[str] = set()

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            stack.extend(graph[current] - visited)

        components.append(component)

    return components


def build_coauthor_graph(
    papers: list[Paper],
    researcher_ids_by_name: dict[str, str],
) -> dict[str, set[str]]:
    """Connect researchers who appear on the same paper."""
    graph: dict[str, set[str]] = {}

    def ensure_node(node_id: str) -> None:
        if node_id not in graph:
            graph[node_id] = set()

    for paper in papers:
        member_ids = [researcher_ids_by_name[author.name] for author in paper.authors]
        for member_id in member_ids:
            ensure_node(member_id)

        for i, left_id in enumerate(member_ids):
            for right_id in member_ids[i + 1 :]:
                graph[left_id].add(right_id)
                graph[right_id].add(left_id)

    return graph


def _shared_papers_for_group(
    member_ids: set[str],
    researchers_by_id: dict[str, Researcher],
) -> list[str]:
    """Return papers co-authored by at least two members of the group."""
    paper_member_counts: Counter[str] = Counter()

    for member_id in member_ids:
        researcher = researchers_by_id[member_id]
        for paper_id in researcher.papers:
            paper_member_counts[paper_id] += 1

    return sorted(paper_id for paper_id, count in paper_member_counts.items() if count >= 2)


def _dominant_topic(shared_paper_ids: list[str], papers_by_id: dict[str, Paper]) -> str:
    """Pick the most common topic among a cluster's shared papers."""
    topic_counts = Counter(papers_by_id[paper_id].topic for paper_id in shared_paper_ids)
    return topic_counts.most_common(1)[0][0]


def _make_cluster_name(
    member_ids: set[str],
    researchers_by_id: dict[str, Researcher],
    topic: str,
) -> str:
    """Build a readable cluster label from topic and key member names."""
    members = sorted(
        researchers_by_id[member_id].name for member_id in member_ids
    )
    if len(members) <= 2:
        label = " & ".join(members)
    else:
        label = f"{members[0]} & {members[1]} (+{len(members) - 2})"
    return f"{topic}: {label}"


def _make_cluster_id(member_ids: set[str], topic: str) -> str:
    """Create a stable cluster ID from topic and sorted member IDs."""
    member_key = "_".join(sorted(member_ids))
    return f"cluster_{_slugify(topic)}_{_slugify(member_key)}"


def build_clusters(
    researchers: list[Researcher],
    papers: list[Paper],
    *,
    min_cluster_size: int = 2,
) -> list[Cluster]:
    """Group researchers who coauthor together into connected teams."""
    researchers_by_id = {researcher.id: researcher for researcher in researchers}
    papers_by_id = {paper.id: paper for paper in papers}
    researcher_ids_by_name = {researcher.name: researcher.id for researcher in researchers}

    graph = build_coauthor_graph(papers, researcher_ids_by_name)
    components = _find_connected_components(graph)

    clusters: list[Cluster] = []
    for component in components:
        if len(component) < min_cluster_size:
            continue

        shared_papers = _shared_papers_for_group(component, researchers_by_id)
        if not shared_papers:
            continue

        topic = _dominant_topic(shared_papers, papers_by_id)
        clusters.append(
            Cluster(
                id=_make_cluster_id(component, topic),
                name=_make_cluster_name(component, researchers_by_id, topic),
                researchers=sorted(component),
                shared_papers=shared_papers,
                topic=topic,
            )
        )

    return sorted(clusters, key=lambda cluster: (-len(cluster.researchers), cluster.name))


@dataclass
class ProfileResult:
    """Researchers plus coauthor clusters derived from the same ingestion run."""

    papers: list[Paper]
    researchers: list[Researcher]
    clusters: list[Cluster]

    @property
    def cluster_count(self) -> int:
        return len(self.clusters)


def build_profiles(
    path: Path | str | None = None,
    *,
    papers: list[Paper] | None = None,
    openalex_config=None,
) -> ProfileResult:
    """Ingest papers and build coauthor clusters."""
    ingestion = ingest_papers(path, papers=papers, openalex_config=openalex_config)
    clusters = build_clusters(ingestion.researchers, ingestion.papers)
    return ProfileResult(
        papers=ingestion.papers,
        researchers=ingestion.researchers,
        clusters=clusters,
    )


def summarize_profiles(result: ProfileResult) -> dict[str, object]:
    """Return quick stats for inspecting clustering output."""
    return {
        "paper_count": len(result.papers),
        "researcher_count": len(result.researchers),
        "cluster_count": result.cluster_count,
        "clusters": [
            {
                "id": cluster.id,
                "name": cluster.name,
                "researcher_count": len(cluster.researchers),
                "shared_paper_count": len(cluster.shared_papers),
                "topic": cluster.topic,
            }
            for cluster in result.clusters
        ],
    }
