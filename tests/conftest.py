"""Pytest configuration — disable live integrations during tests."""

from __future__ import annotations

import os

os.environ["LAB2STARTUP_PERPLEXITY_ENABLED"] = "false"
os.environ["LAB2STARTUP_GITHUB_ENABLED"] = "false"
os.environ["LAB2STARTUP_SEMANTIC_SCHOLAR_ENABLED"] = "false"
os.environ["LAB2STARTUP_OPENREVIEW_ENABLED"] = "false"
os.environ["LAB2STARTUP_PAPER_SOURCE"] = "json"
os.environ["LAB2STARTUP_MODE"] = "development"
os.environ["LAB2STARTUP_USE_MOCK_SIGNALS"] = "true"
os.environ["LAB2STARTUP_PIPELINE_CACHE_ENABLED"] = "false"
os.environ["LAB2STARTUP_AGENTIC_SIGNALS"] = "false"

import pytest

from app.config import clear_settings_cache
from app.service import clear_cache

clear_settings_cache()
clear_cache()


@pytest.fixture(autouse=True)
def _fresh_settings_after_test():
    """Drop the settings lru_cache after each test.

    Tests that monkeypatch env vars and call clear_settings_cache() would
    otherwise leak their polluted settings into later tests when monkeypatch
    restores the environment without invalidating the cache.
    """
    yield
    clear_settings_cache()
