#!/bin/sh
# Container entrypoint: render the crontab from SCHEDULE_CRON, optionally
# kick off an immediate first run so the dashboard isn't empty until the
# next scheduled slot, then hand off to supercronic as PID 1 (via `exec`,
# so it receives signals — SIGTERM on `docker stop` — directly, instead of
# a lingering shell process swallowing them).
set -eu

: "${SCHEDULE_CRON:=0 16 * * 1-5}"
: "${RUN_ON_STARTUP:=true}"

CRONTAB_PATH="/app/.crontab"

echo "# Generated at container start from SCHEDULE_CRON. Do not edit — edit" > "$CRONTAB_PATH"
echo "# the SCHEDULE_CRON environment variable instead (see README.md)." >> "$CRONTAB_PATH"
echo "$SCHEDULE_CRON cd /app && python3 docker_scheduled_run.py" >> "$CRONTAB_PATH"

echo "[entrypoint] Schedule: $SCHEDULE_CRON  (timezone: ${TZ:-not set})"
echo "[entrypoint] To trigger a run right now without waiting for the schedule:"
echo "[entrypoint]   docker compose exec generator python3 docker_scheduled_run.py --ignore-calendar"

if [ "$RUN_ON_STARTUP" = "true" ]; then
    echo "[entrypoint] RUN_ON_STARTUP=true — kicking off an immediate first run in the"
    echo "[entrypoint] background (--ignore-calendar, so a fresh deploy isn't stuck"
    echo "[entrypoint] showing an empty dashboard until the next scheduled slot)."
    echo "[entrypoint] Follow its progress with: docker compose logs -f generator"
    ( cd /app && python3 docker_scheduled_run.py --ignore-calendar ) &
else
    echo "[entrypoint] RUN_ON_STARTUP=false — waiting for the schedule ($SCHEDULE_CRON)."
fi

exec supercronic "$CRONTAB_PATH"
