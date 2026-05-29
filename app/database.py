"""SQLite database setup and helpers (Step 11)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / ".cache" / "lab2startup.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    conference TEXT NOT NULL,
    year INTEGER NOT NULL,
    fund_profile TEXT,
    status TEXT NOT NULL,
    paper_source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    config_json TEXT NOT NULL,
    error_message TEXT,
    paper_count INTEGER,
    researcher_count INTEGER,
    signal_count INTEGER,
    report_count INTEGER
);

CREATE TABLE IF NOT EXISTS run_snapshots (
    run_id TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
);

CREATE TABLE IF NOT EXISTS agent_traces (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    researcher_id TEXT NOT NULL,
    researcher_name TEXT NOT NULL,
    tier TEXT NOT NULL,
    max_steps INTEGER NOT NULL,
    steps_used INTEGER,
    preset TEXT,
    model TEXT,
    status TEXT NOT NULL,
    tool_calls_count INTEGER DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost_usd REAL,
    summary TEXT,
    request_json TEXT,
    response_json TEXT,
    signals_emitted INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_traces_run_id ON agent_traces(run_id);
CREATE INDEX IF NOT EXISTS idx_agent_traces_researcher_id ON agent_traces(researcher_id);

CREATE TABLE IF NOT EXISTS researcher_history (
    researcher_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    last_run_id TEXT,
    last_investigated_at TEXT,
    last_conference TEXT,
    last_year INTEGER,
    last_tier TEXT,
    last_signal_count INTEGER DEFAULT 0,
    last_best_signal_type TEXT,
    last_identity_confidence TEXT,
    affiliation TEXT,
    profile_url TEXT,
    notes_json TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (last_run_id) REFERENCES pipeline_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_researcher_history_name ON researcher_history(canonical_name);

CREATE TABLE IF NOT EXISTS run_enrichment_audits (
    run_id TEXT PRIMARY KEY,
    audit_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
);
"""


def get_connection(
    db_path: Path | str | None = None,
    *,
    readonly: bool = False,
    timeout: float = 30.0,
) -> sqlite3.Connection:
    """Open a SQLite connection with row factory enabled."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if readonly:
        connection = sqlite3.connect(
            f"file:{path.resolve()}?mode=ro",
            uri=True,
            timeout=timeout,
        )
    else:
        connection = sqlite3.connect(path, timeout=timeout)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    return connection


def init_db(db_path: Path | str | None = None) -> Path:
    """Create tables when missing and return the database path."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(path) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.commit()
    return path
