"""Local handlers for Perplexity Agent API custom function tools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent_trace_store import get_trace, list_traces_for_run, lookup_researcher_history
from app.integrations.github import GitHubClient, GitHubConfig


@dataclass
class AgentToolHandlers:
    """Execute custom function calls from the Agent API locally."""

    db_path: Path | None = None
    github_config: GitHubConfig | None = None
    run_id: str | None = None

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "github_repo_search":
            return self.github_repo_search(**arguments)
        if name == "lookup_prior_run":
            return self.lookup_prior_run(**arguments)
        return {"error": f"Unknown tool: {name}"}

    def github_repo_search(
        self,
        query: str,
        min_stars: int = 5,
        **_: Any,
    ) -> dict[str, Any]:
        """Search GitHub repositories via existing integration."""
        config = self.github_config or GitHubConfig(enabled=True)
        try:
            with GitHubClient(
                api_token=config.api_token,
                request_delay_seconds=0,
            ) as client:
                repos = client.search_repositories(query, per_page=5)
        except Exception as exc:
            return {"repos": [], "error": str(exc)}

        filtered = [
            {
                "full_name": repo.get("full_name"),
                "html_url": repo.get("html_url"),
                "stargazers_count": repo.get("stargazers_count"),
                "description": repo.get("description"),
            }
            for repo in repos
            if int(repo.get("stargazers_count") or 0) >= min_stars
        ]
        return {"query": query, "repos": filtered[:5]}

    def lookup_prior_run(
        self,
        researcher_name: str,
        researcher_id: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Return prior Lab2Startup investigation data from SQLite."""
        history = lookup_researcher_history(
            researcher_id=researcher_id,
            researcher_name=researcher_name,
            db_path=self.db_path,
        )
        traces: list[dict[str, Any]] = []
        if history and history.get("last_run_id"):
            traces = list_traces_for_run(history["last_run_id"], db_path=self.db_path)
            traces = [
                row
                for row in traces
                if row.get("researcher_id") == (researcher_id or history.get("researcher_id"))
            ][:3]

        latest_trace = None
        if history and history.get("notes_json"):
            try:
                notes = json.loads(history["notes_json"])
                trace_id = notes.get("last_trace_id")
                if trace_id:
                    latest_trace = get_trace(trace_id, db_path=self.db_path)
            except json.JSONDecodeError:
                pass

        return {
            "found": history is not None,
            "history": history,
            "recent_traces": traces,
            "latest_trace_id": latest_trace.get("id") if latest_trace else None,
            "current_run_id": self.run_id,
        }
