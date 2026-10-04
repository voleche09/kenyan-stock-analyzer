#!/usr/bin/env python3
"""
Open the stock dashboard as a clickable app.

The easy way: double-click "Open Dashboard.command" in this folder.
From a terminal:

    ./venv/bin/python3 app.py              # opens http://127.0.0.1:8765 in your browser
    ./venv/bin/python3 app.py --no-browser
    ./venv/bin/python3 app.py --port 8800

Inside the browser you can then search for any NSE or international stock and
add it to your ⭐ Watchlist, record a purchase on 💼 My Portfolio, fix a
mistake, and update the data with 🔄 Update — no files to edit, no commands.
Keep the window it runs in open while you use the dashboard; close it (or
press Ctrl+C) to stop. Everything stays on this computer (see
src/dashboard_app.py for how it's secured).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from venv_check import require_project_packages  # noqa: E402  (standard library only)

require_project_packages()

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import dashboard_app  # noqa: E402

if __name__ == "__main__":
    sys.exit(dashboard_app.main(sys.argv[1:]))
