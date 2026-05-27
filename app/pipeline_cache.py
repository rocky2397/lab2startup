"""Disk cache for full pipeline results — speeds up dashboard/API restarts."""

from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import AppSettings

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"
CACHE_FILENAME = "report_result.pkl"
META_FILENAME = "report_result.meta.json"


def _config_snapshot(settings: AppSettings) -> dict[str, Any]:
    """Build a stable fingerprint payload from runtime settings."""
    snapshot: dict[str, Any] = {
        "paper_source": settings.paper_source,
        "papers_path": str(settings.papers_path) if settings.papers_path else None,
        "signals_path": str(settings.signals_path) if settings.signals_path else None,
    }

    if settings.openalex_config is not None:
        snapshot["openalex"] = asdict(settings.openalex_config)

    if settings.openreview_config is not None:
        snapshot["openreview"] = asdict(settings.openreview_config)

    snapshot["semantic_scholar"] = asdict(settings.semantic_scholar_config)
    snapshot["github"] = asdict(settings.github_config)
    snapshot["perplexity"] = asdict(settings.perplexity_config)

    for path_key in ("papers_path", "signals_path"):
        path_value = snapshot.get(path_key)
        if not path_value:
            continue
        path = Path(path_value)
        if path.is_file():
            stat = path.stat()
            snapshot[f"{path_key}_mtime"] = stat.st_mtime
            snapshot[f"{path_key}_size"] = stat.st_size

    return snapshot


def build_pipeline_fingerprint(settings: AppSettings) -> str:
    """Hash the effective pipeline configuration and input file versions."""
    payload = json.dumps(_config_snapshot(settings), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_paths(cache_dir: Path) -> tuple[Path, Path]:
    return cache_dir / CACHE_FILENAME, cache_dir / META_FILENAME


def load_cached_report_result(
    settings: AppSettings,
    *,
    cache_dir: Path | None = None,
    ttl_hours: float,
) -> Any | None:
    """Return a cached ReportResult when fingerprint and TTL match."""
    if ttl_hours <= 0:
        return None

    directory = cache_dir or DEFAULT_CACHE_DIR
    cache_file, meta_file = _cache_paths(directory)
    if not cache_file.is_file() or not meta_file.is_file():
        return None

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    fingerprint = build_pipeline_fingerprint(settings)
    if meta.get("fingerprint") != fingerprint:
        return None

    created_at = datetime.fromisoformat(meta["created_at"])
    age_hours = (datetime.now(UTC) - created_at).total_seconds() / 3600
    if age_hours > ttl_hours:
        return None

    try:
        return pickle.loads(cache_file.read_bytes())
    except (OSError, pickle.PickleError):
        return None


def save_cached_report_result(
    settings: AppSettings,
    result: Any,
    *,
    cache_dir: Path | None = None,
) -> Path:
    """Persist a ReportResult to disk with metadata."""
    directory = cache_dir or DEFAULT_CACHE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    cache_file, meta_file = _cache_paths(directory)

    cache_file.write_bytes(pickle.dumps(result))
    meta_file.write_text(
        json.dumps(
            {
                "fingerprint": build_pipeline_fingerprint(settings),
                "created_at": datetime.now(UTC).isoformat(),
                "result_type": type(result).__name__,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return cache_file


def clear_pipeline_disk_cache(*, cache_dir: Path | None = None) -> None:
    """Remove cached pipeline artifacts."""
    directory = cache_dir or DEFAULT_CACHE_DIR
    for filename in (CACHE_FILENAME, META_FILENAME):
        path = directory / filename
        if path.is_file():
            path.unlink()


def cache_status(
    settings: AppSettings,
    *,
    cache_dir: Path | None = None,
    ttl_hours: float,
) -> dict[str, Any]:
    """Return human-readable cache metadata for the dashboard."""
    directory = cache_dir or DEFAULT_CACHE_DIR
    _, meta_file = _cache_paths(directory)
    fingerprint = build_pipeline_fingerprint(settings)

    if not meta_file.is_file():
        return {
            "enabled": ttl_hours > 0,
            "hit": False,
            "fingerprint": fingerprint,
            "message": "No cache on disk yet.",
        }

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(meta["created_at"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return {
            "enabled": ttl_hours > 0,
            "hit": False,
            "fingerprint": fingerprint,
            "message": "Cache metadata unreadable.",
        }

    age_hours = (datetime.now(UTC) - created_at).total_seconds() / 3600
    valid = meta.get("fingerprint") == fingerprint and age_hours <= ttl_hours
    return {
        "enabled": ttl_hours > 0,
        "hit": valid,
        "fingerprint": fingerprint,
        "created_at": meta.get("created_at"),
        "age_hours": round(age_hours, 1),
        "ttl_hours": ttl_hours,
        "message": "Cache valid." if valid else "Cache stale or config changed.",
    }
