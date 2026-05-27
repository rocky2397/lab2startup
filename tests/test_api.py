"""Tests for FastAPI backend (Step 8)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.service import clear_cache

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def warm_pipeline_cache() -> None:
    """Build the cached pipeline once for API tests."""
    clear_cache()
    client.get("/")
    yield


def test_health_check() -> None:
    response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["paper_count"] == 7
    assert payload["researcher_count"] == 30


def test_list_papers_with_filters() -> None:
    response = client.get("/papers", params={"year": 2024, "topic": "AI agents"})
    assert response.status_code == 200
    papers = response.json()
    assert papers
    assert all(paper["year"] == 2024 for paper in papers)
    assert all(paper["topic"] == "AI agents" for paper in papers)


def test_list_researchers_clusters_signals() -> None:
    assert client.get("/researchers").status_code == 200
    assert len(client.get("/researchers").json()) == 30

    clusters = client.get("/clusters").json()
    assert len(clusters) == 7

    signals = client.get("/signals", params={"signal_type": "confirmed_founder"}).json()
    assert len(signals) == 1
    assert signals[0]["researcher_name"] == "Marinka Zitnik"


def test_scores_endpoint() -> None:
    response = client.get("/scores")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["researchers"]) == 30
    assert len(payload["clusters"]) == 7
    top_score = payload["researchers"][0]["startup_likelihood_score"]
    bottom_score = payload["researchers"][-1]["startup_likelihood_score"]
    assert top_score >= bottom_score


def test_get_researcher_score() -> None:
    response = client.get("/scores/researchers/researcher_marinka_zitnik")
    assert response.status_code == 200
    assert response.json()["entity_name"] == "Marinka Zitnik"


def test_reports_endpoints() -> None:
    summaries = client.get("/reports", params={"min_score": 70}).json()
    assert summaries
    assert all(summary["startup_likelihood_score"] >= 70 for summary in summaries)

    report_id = summaries[0]["id"]
    detail = client.get(f"/reports/{report_id}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["id"] == report_id
    assert payload["markdown"].startswith("# Founder Monitoring Report")


def test_report_not_found() -> None:
    response = client.get("/reports/report_does_not_exist")
    assert response.status_code == 404
