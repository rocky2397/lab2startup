"""Manual live smoke test for agentic signals (skipped in CI)."""

from __future__ import annotations

import os

import pytest


@pytest.mark.skip(reason="Manual live Agent API smoke test — requires API key and spend.")
def test_live_agent_probe() -> None:
    """Run manually:

    export LAB2STARTUP_PERPLEXITY_API_KEY=...
    pytest tests/test_agentic_live.py -k live_agent_probe -s
    """
    if not os.getenv("LAB2STARTUP_PERPLEXITY_API_KEY"):
        pytest.skip("LAB2STARTUP_PERPLEXITY_API_KEY not set")

    from app.integrations.perplexity_agent import main

    exit_code = main(
        [
            "--name",
            "John Yang",
            "--affiliation",
            "Stanford University",
            "--paper-title",
            "SWE-agent",
            "--tier",
            "light",
        ]
    )
    assert exit_code in {0, 1}
