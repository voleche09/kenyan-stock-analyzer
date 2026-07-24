"""
Foreign-investor activity data loader.

Reads the weekly NSE foreign-flow figures from `manual_input/foreign_flows.json`
(hand-entered from the NSE Weekly Market Statistics bulletin — see
manual_input/README.md for why manual and how to update).

This module is intentionally strict about what it accepts and permissive
about what it returns: every value is validated for shape and type, missing
fields are simply left out, and any failure returns an empty structure so
the dashboard renders an empty-state page instead of crashing.

We never guess or estimate — only figures a human has entered from a
verifiable NSE bulletin appear on the Foreign Flows page.
"""

import json
import os
from typing import Any

from logger import get_logger

logger = get_logger(__name__)

DEFAULT_PATH = "manual_input/foreign_flows.json"


def load(path: str = DEFAULT_PATH) -> dict:
    """
    Load and validate the foreign-flows manual input.

    Returns a dict with:
        weeks: list of validated week dicts, newest first (as authored)
        source_home: URL that appears in the file header
        source_note: the disclaimer string from the file header
    On any error/absence, returns an empty structure — the caller renders
    an empty-state page rather than break the dashboard.
    """
    if not os.path.isabs(path):
        # Resolve relative to the project root (parent of src/)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(project_root, path)

    if not os.path.exists(path):
        logger.info(f"Foreign flows: no manual input at {path} — page will be empty")
        return {"weeks": [], "source_home": "", "source_note": ""}

    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning(f"Foreign flows: could not parse {path}: {e}")
        return {"weeks": [], "source_home": "", "source_note": ""}

    weeks = []
    for w in raw.get("weeks", []):
        cleaned = _validate_week(w)
        if cleaned:
            weeks.append(cleaned)

    # Sort newest first regardless of file order
    weeks.sort(key=lambda w: w.get("week_ending", ""), reverse=True)

    logger.info(
        f"Foreign flows: loaded {len(weeks)} week(s) of manual foreign-flow data"
    )
    return {
        "weeks": weeks,
        "source_home": str(raw.get("_source_home", "") or ""),
        "source_note": str(raw.get("_source_note", "") or ""),
    }


def _validate_week(w: Any) -> dict | None:
    """Return a cleaned week dict, or None if the week is unusable."""
    if not isinstance(w, dict):
        return None
    week_ending = w.get("week_ending")
    if not isinstance(week_ending, str) or len(week_ending) != 10:
        return None

    agg = w.get("aggregate") or {}
    aggregate = {
        k: _num(agg.get(k)) for k in [
            "foreign_participation_pct",
            "foreign_buys_kes",
            "foreign_sells_kes",
            "net_foreign_flow_kes",
        ]
    }

    buys = _clean_stock_list(w.get("top_foreign_buys"))
    sells = _clean_stock_list(w.get("top_foreign_sells"))

    return {
        "week_ending": week_ending,
        "source_label": str(w.get("source_label", "NSE Weekly Market Statistics")),
        "source_url": str(w.get("source_url", "") or ""),
        "aggregate": aggregate,
        "top_foreign_buys": buys,
        "top_foreign_sells": sells,
    }


def _clean_stock_list(rows: Any) -> list[dict]:
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = r.get("symbol")
        val = _num(r.get("value_kes"))
        if not sym or val is None:
            continue
        out.append({"symbol": str(sym).upper().strip(), "value_kes": val})
    # Preserve authored order (usually already ranked by size)
    return out


def _num(x: Any):
    """Coerce to float, or None if not numeric."""
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---- Test ----
if __name__ == "__main__":
    from logger import setup_logging
    setup_logging()
    data = load()
    print(f"weeks loaded: {len(data['weeks'])}")
    if data["weeks"]:
        w = data["weeks"][0]
        print(f"latest week: {w['week_ending']}")
        print(f"  aggregate: {w['aggregate']}")
        print(f"  top buys : {w['top_foreign_buys']}")
        print(f"  top sells: {w['top_foreign_sells']}")
