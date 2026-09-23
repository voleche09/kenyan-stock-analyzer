#!/usr/bin/env python3
"""
Docker scheduled-run entrypoint.

This is the command supercronic fires on the configured schedule (default:
16:00 Africa/Nairobi, weekdays — see docker/entrypoint.sh / SCHEDULE_CRON).
It is the Docker-native equivalent of running `./run.sh` by hand: it checks
whether the NSE actually traded today (skips weekends and Kenyan public
holidays, reusing the exact same, already-tested check send_summary.py uses
— see utils.market_closed_today()), and if so, runs the full dashboard
pipeline so the pages nginx is serving get refreshed.

Not market-closed-aware guessing, not a second copy of the calendar logic —
one shared function, imported, so the bare-metal (GitHub Actions) and Docker
paths can never silently drift apart.

Usage (normally invoked by supercronic, not by hand):
    python3 docker_scheduled_run.py
    python3 docker_scheduled_run.py --ignore-calendar   # force a run now,
                                                          # e.g. for testing:
                                                          # docker compose exec generator \
                                                          #   python3 docker_scheduled_run.py --ignore-calendar
"""

import os
import sys
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

from dotenv import load_dotenv
load_dotenv()

from config import Config
from logger import get_logger, setup_logging
from utils import market_closed_today

config = Config()
setup_logging(config)
logger = get_logger(__name__)


def main():
    logger.info("=" * 60)
    logger.info(f"DOCKER SCHEDULED RUN — {datetime.now():%Y-%m-%d %H:%M %Z}".strip())
    logger.info("=" * 60)

    if '--ignore-calendar' not in sys.argv:
        closed, reason = market_closed_today()
        if closed:
            logger.info(f"NSE is closed today ({reason}) — skipping this run.")
            print(f"Skipped: NSE closed today ({reason}).")
            return 0
    else:
        logger.info("--ignore-calendar passed — running regardless of the calendar.")

    # Same invocation as run.sh (minus the interactive `open` at the end —
    # there's no desktop to open a browser on inside a container). Email
    # sends or not exactly as ENABLE_EMAIL_NOTIFICATIONS in the environment
    # already controls — no extra flag needed here.
    cmd = [
        sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.py'),
        '--report-type', 'both',
        '--export-excel',
        '--detailed',
    ]
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        logger.error(f"main.py exited with code {result.returncode}")
    else:
        logger.info("Scheduled run complete — dashboard refreshed.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
