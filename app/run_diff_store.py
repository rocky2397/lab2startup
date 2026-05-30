"""Persist and load run-to-run diffs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.database import get_connection, init_db
from app.run_diff_models import RunDiff


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def serialize_run_diff(diff: RunDiff) -> str:
    payload = diff.model_dump(mode="json")
    return json.dumps(payload)


def deserialize_run_diff(payload: str | dict[str, Any]) -> RunDiff:
    data = json.loads(payload) if isinstance(payload, str) else payload
    return RunDiff.model_validate(data)


def save_run_diff(
    run_id: str,
    diff: RunDiff,
    *,
    db_path: str | Path | None = None,
) -> None:
    init_db(db_path)
    created_at = diff.computed_at.isoformat() if diff.computed_at else _utc_now_iso()
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO run_diffs (run_id, prior_run_id, diff_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                prior_run_id = excluded.prior_run_id,
                diff_json = excluded.diff_json,
                created_at = excluded.created_at
            """,
            (run_id, diff.prior_run_id, serialize_run_diff(diff), created_at),
        )
        connection.commit()


def load_run_diff(
    run_id: str,
    *,
    db_path: str | Path | None = None,
) -> RunDiff | None:
    init_db(db_path)
    with get_connection(db_path, readonly=True) as connection:
        row = connection.execute(
            "SELECT diff_json FROM run_diffs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return deserialize_run_diff(row["diff_json"])
