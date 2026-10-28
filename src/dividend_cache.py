"""Persistent public dividend data, independent of daily quote-cache resets.

Only public declarations go here. Successful checks are reused for seven days;
failed checks back off for a day and retain the last usable result. Writes are
atomic, so a failed refresh cannot destroy the last known declaration.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile


def read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def write(path, value):
    temporary = None
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False,
                                         encoding="utf-8") as stream:
            temporary = stream.name
            json.dump(value, stream, allow_nan=False)
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        pass  # caching must never stop the dashboard
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def cached_public_data(cache_dir, key, loader, *, refresh_days=7,
                       force_refresh=False, today=None, seed=None, seed_date=None):
    """loader returns JSON-compatible data, or raises on failure.

    Return (data, metadata). A valid empty result differs from a failed fetch.
    Store the checked date separately from the last date the data changed.
    """
    today = today or dt.datetime.now(dt.timezone.utc).date()
    path = Path(cache_dir) / "dividends" / (hashlib.sha256(key.encode()).hexdigest() + ".json")
    old = read(path)
    if not old and seed is not None and seed_date:
        try:
            verified = dt.date.fromisoformat(seed_date)
            old = {"data": seed, "checked": seed_date, "changed": seed_date,
                   "next_check": (verified + dt.timedelta(days=max(1, refresh_days))).isoformat(),
                   "stale": False}
            write(path, old)
        except (ValueError, TypeError):
            pass
    try:
        due = dt.date.fromisoformat(old["next_check"])
    except (KeyError, TypeError, ValueError):
        due = dt.date.min
    if not force_refresh and today < due:
        return old.get("data"), old
    try:
        data = loader()
        if data is None:
            raise ValueError("No dividend response")
        json.dumps(data, allow_nan=False)
        changed = data != old.get("data")
        state = {"data": data, "checked": today.isoformat(),
                 "changed": today.isoformat() if changed else old.get("changed"),
                 "next_check": (today + dt.timedelta(days=max(1, refresh_days))).isoformat(),
                 "stale": False}
    except Exception:
        state = {**old, "stale": True,
                 "next_check": (today + dt.timedelta(days=1)).isoformat()}
    write(path, state)
    return state.get("data"), state
