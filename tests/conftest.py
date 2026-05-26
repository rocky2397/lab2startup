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

from app.config import clear_settings_cache
from app.service import clear_cache

clear_settings_cache()
clear_cache()
