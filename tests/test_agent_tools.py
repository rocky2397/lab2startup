"""Tests for Perplexity Agent custom tool handlers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agent_trace_store import (
    AgentTraceRow,
    ResearcherHistoryRow,
    save_agent_trace,
    upsert_researcher_history,
)
from app.database import init_db
from app.integrations.agent_tools import AgentToolHandlers


def test_lookup_prior_run_returns_history(tmp_path: Path) -> None:
    db_path = tmp_path / "tools.db"
    init_db(db_path)
    upsert_researcher_history(
        ResearcherHistoryRow(
            researcher_id="researcher_jane",
            canonical_name="Jane Doe",
            last_run_id="run_prior",
            last_signal_count=2,
            last_tier="standard",
            notes_json=json.dumps({"last_trace_id": "trace_prior_1"}),
        ),
        db_path=db_path,
    )
    save_agent_trace(
        AgentTraceRow(
            id="trace_prior_1",
            run_id="run_prior",
            researcher_id="researcher_jane",
            researcher_name="Jane Doe",
            tier="standard",
            max_steps=3,
            status="completed",
            summary="Prior run",
            response_json='{"status":"completed"}',
        ),
        db_path=db_path,
    )

    handlers = AgentToolHandlers(db_path=db_path, run_id="run_current")
    result = handlers.lookup_prior_run(researcher_name="Jane Doe")

    assert result["found"] is True
    assert result["history"]["last_signal_count"] == 2
    assert result["latest_trace_id"] == "trace_prior_1"
    assert result["current_run_id"] == "run_current"


def test_github_repo_search_uses_github_client() -> None:
    handlers = AgentToolHandlers()
    mock_repo = {
        "full_name": "org/repo",
        "html_url": "https://github.com/org/repo",
        "stargazers_count": 100,
        "description": "test",
    }

    mock_client = MagicMock()
    mock_client.search_repositories.return_value = [mock_repo]
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None

    with patch("app.integrations.agent_tools.GitHubClient", return_value=mock_client):
        result = handlers.github_repo_search(query="SWE-agent", min_stars=5)

    assert result["query"] == "SWE-agent"
    assert len(result["repos"]) == 1
    assert result["repos"][0]["full_name"] == "org/repo"
