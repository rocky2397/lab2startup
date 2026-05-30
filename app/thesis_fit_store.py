"""Persist and load thesis fit assessments per run."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.database import get_connection, init_db
from app.thesis_fit_models import ThesisFitAssessment


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def serialize_thesis_fit(assessments: dict[str, ThesisFitAssessment]) -> str:
    payload = {
        researcher_id: assessment.model_dump(mode="json")
        for researcher_id, assessment in assessments.items()
    }
    return json.dumps(payload)


def deserialize_thesis_fit(payload: str | dict[str, Any]) -> dict[str, ThesisFitAssessment]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    return {
        researcher_id: ThesisFitAssessment.model_validate(item)
        for researcher_id, item in data.items()
    }


def save_thesis_fit(
    run_id: str,
    assessments: dict[str, ThesisFitAssessment],
    *,
    db_path: str | Path | None = None,
) -> None:
    init_db(db_path)
    created_at = _utc_now_iso()
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO run_thesis_fit (run_id, thesis_fit_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                thesis_fit_json = excluded.thesis_fit_json,
                created_at = excluded.created_at
            """,
            (run_id, serialize_thesis_fit(assessments), created_at),
        )
        connection.commit()


def load_thesis_fit(
    run_id: str,
    *,
    db_path: str | Path | None = None,
) -> dict[str, ThesisFitAssessment] | None:
    init_db(db_path)
    with get_connection(db_path, readonly=True) as connection:
        row = connection.execute(
            "SELECT thesis_fit_json FROM run_thesis_fit WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return deserialize_thesis_fit(row["thesis_fit_json"])
