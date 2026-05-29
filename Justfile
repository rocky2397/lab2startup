# Lab2Startup developer commands
#
# Setup (once):
#   python -m venv .venv && .venv/bin/pip install -e ".[dev]"
#
# Usage:
#   just fmt        # format Python sources
#   just lint       # check format + lint (no writes)
#   just lint-fix   # auto-fix lint issues and reformat

set dotenv-load := false

python := `if [ -x .venv/bin/python3.12 ]; then echo .venv/bin/python3.12; elif [ -x .venv/bin/python3 ]; then echo .venv/bin/python3; else echo python3; fi`

default:
    @just --list

# One-time setup for format/lint tooling
install-dev:
    {{python}} -m pip install -e ".[dev]"

# Format all Python sources
fmt:
    {{python}} -m ruff format app dashboard tests *.py

# Check formatting and lint without modifying files
lint:
    {{python}} -m ruff format --check app dashboard tests *.py
    {{python}} -m ruff check app dashboard tests *.py

# Auto-fix lint issues, then format
lint-fix:
    {{python}} -m ruff check --fix --unsafe-fixes app dashboard tests *.py
    {{python}} -m ruff format app dashboard tests *.py

# Smoke-test agentic LangGraph orchestration on N researchers from the latest stored run
agentic-smoke limit="5":
    {{python}} run_agentic_smoke.py --limit {{limit}}

# Show which researchers a smoke test would probe (no API calls)
agentic-smoke-dry limit="5":
    {{python}} run_agentic_smoke.py --limit {{limit}} --dry-run

# Export latest complete run per conference for a batch date stamp (YYYYMMDD)
export-batch batch_date label="saved_batch":
    {{python}} run_export.py --batch-date {{batch_date}} --latest-per-conference --label {{label}}
