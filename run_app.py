"""Launch Lab2Startup as a native desktop app (FastAPI backend + pywebview window)."""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bootstrap_venv import reexec_with_project_venv

reexec_with_project_venv(ROOT)

import uvicorn


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_server(port: int) -> uvicorn.Server:
    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        if not thread.is_alive():
            raise RuntimeError("Backend server failed to start.")
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("Backend server did not start within 20 seconds.")
    return server


def main() -> None:
    port = _free_port()
    server = _start_server(port)
    url = f"http://127.0.0.1:{port}/app/"

    try:
        import webview
    except ImportError:
        # Fallback: no pywebview installed — open the default browser instead.
        import webbrowser

        print(f"pywebview not installed; opening {url} in your browser (Ctrl+C to stop).")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    else:
        webview.create_window(
            "Lab2Startup",
            url,
            width=1440,
            height=920,
            min_size=(1100, 700),
        )
        webview.start()

    server.should_exit = True


if __name__ == "__main__":
    main()
