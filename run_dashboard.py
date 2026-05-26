"""Launch the Streamlit dashboard from the project root."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _python_executable() -> str:
    """Prefer the project venv so conda/base Python without deps is not used."""
    for candidate in (
        ROOT / ".venv" / "bin" / "python3.12",
        ROOT / ".venv" / "bin" / "python3",
        ROOT / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def main() -> None:
    app_path = ROOT / "dashboard" / "streamlit_app.py"
    python = _python_executable()
    subprocess.run(
        [python, "-m", "streamlit", "run", str(app_path)],
        cwd=str(ROOT),
        check=True,
    )


if __name__ == "__main__":
    main()
