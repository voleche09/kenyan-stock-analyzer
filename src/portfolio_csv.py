"""
CSV input for the private portfolio files — the spreadsheet-friendly way to
enter holdings (Excel, Numbers and Google Sheets all export CSV), so adding a
purchase is "add a row and save" rather than running a script.

Three files, one per asset class, mirroring the three JSON files they can
replace (each has its own currency and meaning, so they stay separate):

    portfolio/holdings.csv                 NSE stocks          (prices in KES)
    portfolio/international_holdings.csv   US-listed stocks    (prices in USD)
    portfolio/bonds.csv                    Kenya T/I-bonds     (face value in KES)

If a CSV exists it IS the portfolio for that asset class; otherwise the old
JSON file is used (see pick_source). Only what you PAID is ever read — current
price, market value and gain/loss are always recomputed fresh, so extra
columns from a broker export (Market Value, Unrealized P&L, ...) are ignored
on purpose, never trusted.

Parsing is deliberately forgiving about things that are harmless and strict
about things that would silently corrupt money figures:

  Forgiving   header case/spacing/units ("Avg Price (USD)"), common header
              aliases, UTF-8 BOM, blank/trailing empty rows, extra columns,
              "$1,234.50" / "KES 1,234.50" / "1,234.50" numbers, and the date
              styles spreadsheets actually export (2026-09-20, 20 Sep 2026,
              20/09/2026, ...).
  Strict      a bare "price" column is NOT accepted as the price you paid (in
              a broker export it is usually TODAY's price, which would make
              every gain/loss zero); "224,3" is rejected rather than read as
              2243; an ambiguous date like 05/06/2026 is dropped with a
              warning rather than guessed (day/month vs month/day); a
              semicolon-separated file is rejected with instructions.

Problems are logged with the file name and line number; a bad row is skipped
(or, for an optional field, just that value is dropped) — one typo never takes
the rest of the portfolio down with it.
"""

import csv
import io
import math
import os
import re
import datetime as dt
from collections import namedtuple

from logger import get_logger

logger = get_logger(__name__)

# name = canonical field (also the header used in the *.example.csv templates);
# aliases = other header spellings accepted; kind = text | number | date.
Field = namedtuple("Field", "name aliases kind required")

_NOTE = Field("note", ("notes", "comment", "comments", "remarks"), "text", False)

# NSE stocks (KES) and US stocks (USD) share one shape.
# NOTE: no bare "price" alias for buy_price — see module docstring.
STOCK_LOT_FIELDS = (
    Field("symbol", ("ticker", "instrument", "stock", "security", "code"), "text", True),
    Field("quantity", ("qty", "shares", "units", "position", "no_of_shares", "number_of_shares"),
          "number", True),
    Field("buy_price", ("avg_price", "average_price", "avg_cost", "average_cost", "cost_price",
                        "purchase_price", "price_paid", "cost_per_share", "avg_cost_per_share"),
          "number", True),
    Field("buy_date", ("purchase_date", "date_bought", "bought", "trade_date", "date"), "date", False),
    _NOTE,
)

BOND_FIELDS = (
    Field("issue", ("bond", "bond_code", "issue_code", "code"), "text", True),
    Field("face_value", ("face", "nominal", "nominal_value", "par", "par_value"), "number", True),
    # Clean price as a % of face value (101.33), NOT an amount in KES — hence no bare "price" alias.
    Field("purchase_price_pct", ("price_pct", "buy_price_pct", "clean_price", "clean_price_pct",
                                 "price_percent"), "number", False),
    Field("purchase_date", ("buy_date", "date_bought", "bought", "trade_date", "date"), "date", False),
    _NOTE,
)


class CsvFormatError(ValueError):
    """The file as a whole can't be used (unreadable, wrong delimiter, a required column is missing)."""


# ----------------------------------------------------------------------------
# Which file is the portfolio — CSV or the older JSON?
# ----------------------------------------------------------------------------
def pick_source(portfolio_dir, csv_name, json_name):
    """
    Return ("csv", path) | ("json", path) | (None, None).

    One simple rule: if the CSV exists it is the portfolio, otherwise the JSON
    is. Never merged — merging two files would silently double-count anything
    present in both. If BOTH exist, say so loudly: whichever one the user is
    editing, only one of them is being read.
    """
    csv_path = os.path.join(portfolio_dir, csv_name)
    json_path = os.path.join(portfolio_dir, json_name)
    has_csv, has_json = os.path.exists(csv_path), os.path.exists(json_path)
    if has_csv and has_json:
        logger.warning(
            f"Both {csv_name} and {json_name} exist in {portfolio_dir} — using {csv_name}; "
            f"{json_name} is IGNORED. Once you've checked the CSV is complete, delete or "
            f"rename {json_name} so there's no confusion about which file counts.")
    if has_csv:
        return "csv", csv_path
    if has_json:
        return "json", json_path
    return None, None


def csv_in_use_message(csv_path, fields):
    """Why the add_*.py helper scripts refuse to write when a CSV is the portfolio."""
    name = os.path.basename(csv_path)
    cols = ", ".join(f.name for f in fields)
    return (f"{name} exists, so it is your portfolio — a script that appended to the older "
            f"JSON file would write somewhere that is ignored. Add a row to {name} in your "
            f"spreadsheet app instead (columns: {cols}), save it, and re-run ./run.sh. "
            f"(To go back to the JSON file, delete or rename {name}.)")


# ----------------------------------------------------------------------------
# Cell parsers
# ----------------------------------------------------------------------------
_CURRENCY_PREFIX = re.compile(r"^(?:KSHS|KSH|KES|USD|US\$|\$)\s*", re.I)
_THOUSANDS = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def _parse_number(raw):
    s = _CURRENCY_PREFIX.sub("", raw.strip())
    s = s.replace(" ", "").replace(" ", "").rstrip("%")
    # A comma only counts as a thousands separator in the exact 1,234,567.89
    # shape. "224,3" (a decimal comma) must NOT be silently read as 2243.
    if _THOUSANDS.match(s):
        s = s.replace(",", "")
    try:
        value = float(s)
    except ValueError:
        value = None
    if value is None or not math.isfinite(value):
        raise ValueError(f"'{raw}' is not a number (use a dot as the decimal point, e.g. 190.25)")
    return value


_ISO_WITH_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]")
_NUMERIC_DATE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})$")
_TEXT_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y",
                      "%B %d, %Y", "%d-%b-%Y", "%d-%B-%Y", "%d-%b-%y", "%d %b %y")
_DATE_HELP = "write dates as YYYY-MM-DD, e.g. 2026-09-20"


def _parse_date(raw):
    """Return an ISO YYYY-MM-DD string. Numeric day/month dates are only
    accepted when unambiguous — a wrong guess would silently shift a date."""
    s = raw.strip()
    m = _ISO_WITH_TIME.match(s)
    if m:                       # "2026-09-20 00:00:00" (spreadsheet date-time cell)
        s = m.group(1)
    for fmt in _TEXT_DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    m = _NUMERIC_DATE.match(s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
        if a > 12 and b <= 12:
            day, month = a, b           # 20/09/2026 can only be day-first
        elif b > 12 and a <= 12:
            month, day = a, b           # 09/20/2026 can only be month-first
        elif a == b:
            day = month = a             # 05/05/2026 reads the same either way
        elif a <= 12 and b <= 12:
            raise ValueError(f"'{raw}' is ambiguous (day/month or month/day?) — {_DATE_HELP}")
        else:
            raise ValueError(f"'{raw}' is not a valid date — {_DATE_HELP}")
        try:
            return dt.date(year, month, day).isoformat()
        except ValueError:
            raise ValueError(f"'{raw}' is not a valid date — {_DATE_HELP}")
    raise ValueError(f"'{raw}' is not a recognised date — {_DATE_HELP}")


def _parse_cell(kind, raw):
    if raw == "":
        return None
    if kind == "number":
        return _parse_number(raw)
    if kind == "date":
        return _parse_date(raw)
    return raw


# ----------------------------------------------------------------------------
# The reader
# ----------------------------------------------------------------------------
def _norm_header(h):
    """'Avg Price (USD)' -> 'avg_price' ; ' Buy-Date ' -> 'buy_date'."""
    h = (h or "").replace("﻿", "").lower()
    h = re.sub(r"\(.*?\)", " ", h)          # drop "(USD)", "(%)" and similar notes
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_")


def _read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig")      # Excel's "CSV UTF-8" starts with a BOM
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")   # older Excel "CSV" on Windows/Mac
    return text.lstrip("﻿")


def read_rows(path, fields):
    """
    Parse a portfolio CSV into [(line_number, {field_name: value}), ...] with
    values already converted (numbers -> float, dates -> 'YYYY-MM-DD', text ->
    str, blank -> None). Rows that are entirely blank are skipped silently;
    rows with a bad/missing REQUIRED value are skipped with a warning; a bad
    OPTIONAL value is dropped (set to None) with a warning.

    An empty file, or one with only a header, is an empty portfolio ([]).
    Raises CsvFormatError if the file can't be used at all.
    """
    name = os.path.basename(path)
    try:
        text = _read_text(path)
    except OSError as e:
        raise CsvFormatError(f"{name} could not be read ({e})")

    reader = csv.reader(io.StringIO(text, newline=""))
    header, body = None, []
    for cells in reader:
        if header is None:
            if any(c.strip() for c in cells):
                header = cells
            continue
        body.append((reader.line_num, cells))
    if header is None:
        return []

    if len(header) == 1 and re.search(r"[;\t|]", header[0]):
        raise CsvFormatError(
            f"{name} looks semicolon- or tab-separated, not comma-separated. Re-export it as "
            f"comma-separated (Excel: 'CSV UTF-8 (Comma delimited)'; Numbers: Export To > CSV)")

    norm = [_norm_header(h) for h in header]
    colmap, used = {}, set()
    for f in fields:
        for candidate in (f.name,) + tuple(f.aliases):
            if candidate in norm:
                colmap[f.name] = norm.index(candidate)
                used.add(colmap[f.name])
                break
    missing = [f for f in fields if f.required and f.name not in colmap]
    if missing:
        detail = "; ".join(f"'{f.name}' (accepted header names: {', '.join((f.name,) + f.aliases)})"
                           for f in missing)
        found = ", ".join(h.strip() for h in header if h.strip()) or "(none)"
        raise CsvFormatError(f"{name} is missing required column(s): {detail}. Columns found: {found}")
    ignored = [header[i].strip() for i in range(len(header)) if i not in used and header[i].strip()]
    if ignored:
        logger.info(f"{name}: ignoring unrecognized column(s): {', '.join(ignored)}")

    out = []
    for line_no, cells in body:
        if not any(c.strip() for c in cells):
            continue
        record, problem = {}, None
        for f in fields:
            idx = colmap.get(f.name)
            raw = cells[idx].strip() if idx is not None and idx < len(cells) else ""
            try:
                value = _parse_cell(f.kind, raw)
            except ValueError as e:
                if f.required:
                    problem = f"{f.name}: {e}"
                    break
                logger.warning(f"{name} line {line_no}: {f.name} {e} — ignoring that value")
                value = None
            if f.required and value is None:
                problem = f"{f.name} is blank"
                break
            record[f.name] = value
        if problem:
            logger.warning(f"{name} line {line_no}: {problem} — skipping this row")
            continue
        out.append((line_no, record))
    return out
