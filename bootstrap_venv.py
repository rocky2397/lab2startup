"""Re-exec the current script with the project .venv Python when available."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def reexec_with_project_venv(project_root: Path) -> None:
    """Use .venv/bin/python so conda/base interpreters are not picked up."""
    for name in ("python3.12", "python3", "python"):
        candidate = project_root / ".venv" / "bin" / name
        if not candidate.is_file():
            continue
        if candidate.resolve() == Path(sys.executable).resolve():
            return
        os.execv(str(candidate), [str(candidate), *sys.argv])
