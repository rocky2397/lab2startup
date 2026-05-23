"""Launch the Streamlit dashboard from the project root."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    app_path = ROOT / "dashboard" / "streamlit_app.py"
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path)],
        cwd=str(ROOT),
        check=True,
    )


if __name__ == "__main__":
    main()
