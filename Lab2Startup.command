#!/bin/zsh
# Double-click launcher for the Lab2Startup desktop app.
cd "$(dirname "$0")"
exec .venv/bin/python run_app.py
