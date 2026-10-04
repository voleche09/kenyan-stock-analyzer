"""
CSV input for the private portfolio files — the spreadsheet-friendly way to
enter holdings (Excel, Numbers and Google Sheets all export CSV), so adding a
purchase is "add a row and save" rather than editing JSON.

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
the rest of the portfolio down.

append_row() is what the add_*.py helper scripts use when a CSV is the
portfolio: it adds one row at the end of the file in the file's OWN column
order and spelling, keeping its BOM, line endings and encoding, so a file
exported from a spreadsheet stays intact.

remove_row() / update_row() are what the dashboard app uses to fix a mistake:
they change exactly ONE record, keep every other record byte-for-byte, refuse
if that record no longer says what the caller saw (the file was edited since —
RowChangedError), take a safety copy in portfolio/backups/ first, and replace
the file atomically so a crash can never leave it half-written.
"""

import codecs
import csv
import io
import math
import os
import re
import shutil
import tempfile
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


class RowChangedError(ValueError):
    """The row an edit was aimed at no longer says what the caller saw — the
    file was changed in the meantime (e.g. in a spreadsheet app). Nothing was
    written; reload and try again."""


# Safety copies taken before a row is removed or edited (gitignored + dockerignored).
BACKUP_SUBDIR = "backups"
MAX_BACKUPS_PER_FILE = 20


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


def saved_to_message(portfolio_dir, csv_name, json_name):
    """One line for the add_*.py helper scripts: where the row just went, plus
    the one real hazard of editing a file behind a spreadsheet app's back."""
    if os.path.exists(os.path.join(portfolio_dir, csv_name)):
        return (f"Saved to portfolio/{csv_name}. (If that file is open in Excel, Numbers or "
                f"Sheets, close it WITHOUT saving or reload it — otherwise the spreadsheet "
                f"overwrites this new row the next time you save it there.)")
    return f"Saved to portfolio/{json_name}."


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
# File access + header handling (shared by reading and appending)
# ----------------------------------------------------------------------------
def _norm_header(h):
    """'Avg Price (USD)' -> 'avg_price' ; ' Buy-Date ' -> 'buy_date'."""
    h = (h or "").replace("﻿", "").lower()
    h = re.sub(r"\(.*?\)", " ", h)          # drop "(USD)", "(%)" and similar notes
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_")


def _load(path):
    """Return (raw_bytes, text, encoding). Excel's "CSV UTF-8" starts with a
    BOM (stripped here); older Excel "CSV" exports are Windows-1252."""
    name = os.path.basename(path)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise CsvFormatError(f"{name} could not be read ({e})")
    try:
        return raw, raw.decode("utf-8-sig").lstrip("﻿"), "utf-8"
    except UnicodeDecodeError:
        return raw, raw.decode("cp1252", errors="replace").lstrip("﻿"), "cp1252"


def _split_table(name, text):
    """-> (header cells or None if the file is empty, [(line_number, cells), ...])."""
    reader = csv.reader(io.StringIO(text, newline=""))
    header, body = None, []
    for cells in reader:
        if header is None:
            if any(c.strip() for c in cells):
                header = cells
            continue
        body.append((reader.line_num, cells))
    _check_delimiter(name, header)
    return header, body


def _check_delimiter(name, header):
    if header is not None and len(header) == 1 and re.search(r"[;\t|]", header[0]):
        raise CsvFormatError(
            f"{name} looks semicolon- or tab-separated, not comma-separated. Re-export it as "
            f"comma-separated (Excel: 'CSV UTF-8 (Comma delimited)'; Numbers: Export To > CSV)")


def _resolve_columns(name, header, fields):
    """Map each field to its column index (canonical name first, then aliases).
    Raises CsvFormatError if a required column is missing. -> (colmap, used_indexes)."""
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
    return colmap, used


# ----------------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------------
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
    _raw, text, _enc = _load(path)
    header, body = _split_table(name, text)
    if header is None:
        return []

    colmap, used = _resolve_columns(name, header, fields)
    ignored = [header[i].strip() for i in range(len(header)) if i not in used and header[i].strip()]
    if ignored:
        logger.info(f"{name}: ignoring unrecognized column(s): {', '.join(ignored)}")

    out = []
    for line_no, cells in body:
        if not any(c.strip() for c in cells):
            continue
        record, problem = _parse_record(name, line_no, cells, colmap, fields)
        if problem:
            logger.warning(f"{name} line {line_no}: {problem} — skipping this row")
            continue
        out.append((line_no, record))
    return out


def _parse_record(name, line_no, cells, colmap, fields, log=True):
    """One CSV record -> ({field: value}, problem-or-None). A bad OPTIONAL value
    becomes None (logged when log=True); a bad/blank REQUIRED value is a problem."""
    record = {}
    for f in fields:
        idx = colmap.get(f.name)
        raw = cells[idx].strip() if idx is not None and idx < len(cells) else ""
        try:
            value = _parse_cell(f.kind, raw)
        except ValueError as e:
            if f.required:
                return record, f"{f.name}: {e}"
            if log:
                logger.warning(f"{name} line {line_no}: {f.name} {e} — ignoring that value")
            value = None
        if f.required and value is None:
            return record, f"{f.name} is blank"
        record[f.name] = value
    return record, None


# ----------------------------------------------------------------------------
# Appending (used by the add_*.py helper scripts)
# ----------------------------------------------------------------------------
def _format_cell(value):
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def append_row(path, fields, values):
    """
    Add one row to the END of an existing portfolio CSV and return the list of
    field names that could not be stored because the file has no matching
    (optional) column.

    The row follows the file's own header — its column order and spelling
    ("Ticker", "Qty", "Avg Price (USD)" all work) — and the file's own line
    endings, BOM and encoding are preserved, with one binary append, so a file
    exported from Excel, Numbers or Sheets stays intact. Raises CsvFormatError
    (a ValueError) if the file has no header row or lacks a required column.

    values: {field_name: value}; None or "" leaves that cell empty.
    """
    name = os.path.basename(path)
    raw, text, encoding = _load(path)
    header, _body = _split_table(name, text)
    if header is None:
        raise CsvFormatError(
            f"{name} has no header row — copy the matching *.example.csv template, or start the "
            f"file with: {','.join(f.name for f in fields)}")
    colmap, _used = _resolve_columns(name, header, fields)

    cells, dropped = [""] * len(header), []
    for f in fields:
        value = values.get(f.name)
        if value is None or value == "":
            continue
        idx = colmap.get(f.name)
        if idx is None:
            dropped.append(f.name)
        else:
            cells[idx] = _format_cell(value)

    terminator = ("\r\n" if b"\r\n" in raw else "\n" if b"\n" in raw
                  else "\r" if b"\r" in raw else "\n")
    row = io.StringIO()
    csv.writer(row, lineterminator=terminator).writerow(cells)
    # A file saved without a final newline would otherwise glue the new row onto its last line.
    needs_break = bool(raw) and not raw.endswith((b"\n", b"\r"))
    payload = (terminator if needs_break else "") + row.getvalue()
    with open(path, "ab") as f:
        f.write(payload.encode(encoding, errors="replace"))
    return dropped


# ----------------------------------------------------------------------------
# Creating a new file, and fixing one row (used by the dashboard app)
# ----------------------------------------------------------------------------
# Public names for the cell parsers, so the app checks typed-in values with
# exactly the same rules as the files ("1,234.50" ok, "224,3" refused, ...).
parse_number = _parse_number
parse_date = _parse_date


def unstorable_fields(path, fields, values):
    """Which of `values` (optional fields with a value) an existing CSV has no
    column for — i.e. what append_row would have to drop. [] if the file
    doesn't exist yet (it will be created with every column)."""
    if not os.path.exists(path):
        return []
    name = os.path.basename(path)
    _raw, text, _enc = _load(path)
    header, _body = _split_table(name, text)
    if header is None:
        return []
    colmap, _used = _resolve_columns(name, header, fields)
    return [f.name for f in fields
            if values.get(f.name) not in (None, "") and f.name not in colmap]


def create_csv(path, fields):
    """Start a new portfolio CSV that holds just the header row (the canonical
    column names, same as the *.example.csv templates). Refuses to overwrite."""
    name = os.path.basename(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8", newline="") as f:
            csv.writer(f, lineterminator="\n").writerow([fl.name for fl in fields])
    except FileExistsError:
        raise CsvFormatError(f"{name} already exists — not overwriting it")


def _read_records(path):
    """
    Split a CSV file into records while keeping each record's EXACT original
    bytes, so an edit can rewrite one record and leave every other one alone.

    Returns (bom, encoding, records); each record is a dict with
        line  — its LAST physical line number (the number read_rows reports)
        raw   — its exact bytes, line terminator(s) included
        cells — its parsed cells
        term  — its own line terminator ('' if the file ends without one)
    The file is split on a byte-preserving decoding (UTF-8, else Latin-1, which
    maps every byte 1:1) while cells are decoded the way _load reads the file
    (UTF-8, else Windows-1252).
    """
    name = os.path.basename(path)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise CsvFormatError(f"{name} could not be read ({e})")
    bom = codecs.BOM_UTF8 if raw.startswith(codecs.BOM_UTF8) else b""
    body = raw[len(bom):]
    try:
        text, split_codec, encoding = body.decode("utf-8"), "utf-8", "utf-8"
    except UnicodeDecodeError:
        text, split_codec, encoding = body.decode("latin-1"), "latin-1", "cp1252"

    lines = list(io.StringIO(text, newline=""))
    reader = csv.reader(iter(lines))
    records, start = [], 0
    for cells in reader:
        end = reader.line_num
        chunk = "".join(lines[start:end])
        start = end
        if split_codec == "latin-1":
            proper = chunk.encode("latin-1").decode("cp1252", errors="replace")
            cells = next(csv.reader(io.StringIO(proper, newline="")), [])
        body_only = chunk.rstrip("\r\n")
        records.append({"line": end, "raw": chunk.encode(split_codec), "cells": cells,
                        "term": chunk[len(body_only):]})
    return bom, encoding, records


def _locate(path, fields, line_no):
    """-> (bom, encoding, records, header_cells, colmap, target_record). Raises
    CsvFormatError if the file can't be used, RowChangedError if `line_no` is
    not (or no longer) a data row."""
    name = os.path.basename(path)
    bom, encoding, records = _read_records(path)
    header_at = next((i for i, r in enumerate(records) if any(c.strip() for c in r["cells"])), None)
    if header_at is None:
        raise RowChangedError(f"{name} is empty — there is no row {line_no} to change. "
                              f"Reload the page and try again.")
    header = records[header_at]["cells"]
    _check_delimiter(name, header)
    colmap, _used = _resolve_columns(name, header, fields)
    target = next((r for r in records[header_at + 1:]
                   if r["line"] == line_no and any(c.strip() for c in r["cells"])), None)
    if target is None:
        raise RowChangedError(f"{name} no longer has that row (line {line_no}) — it was changed "
                              f"since this page loaded. Reload the page and try again.")
    return bom, encoding, records, header, colmap, target


def _same(a, b):
    if a in (None, "") and b in (None, ""):
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    return str(a).strip().upper() == str(b).strip().upper()


def _check_expect(name, line_no, values, expect):
    """Refuse to touch a row that no longer says what the caller saw."""
    for key, want in (expect or {}).items():
        have = values.get(key)
        if not _same(have, want):
            raise RowChangedError(
                f"{name} line {line_no} has changed since this page loaded ({key} is now "
                f"{have if have not in (None, '') else 'blank'}, expected {want}). Nothing was "
                f"changed — reload the page and try again.")


def _atomic_write(path, payload):
    """Replace `path` with `payload` in one step (temp file + os.replace), keeping
    the original file's permissions. The temp file ends in .csv on purpose, so
    if the process ever dies mid-write the leftover is still covered by the
    portfolio/*.csv ignore rules (it holds the same private data)."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=f".tmp-{os.path.basename(path)}-", suffix=".csv", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        try:
            shutil.copymode(path, tmp)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def backup_file(path):
    """Copy a portfolio file to <its folder>/backups/<name>.<timestamp>.bak and
    keep only the newest MAX_BACKUPS_PER_FILE copies of it. Returns the copy's path."""
    folder = os.path.join(os.path.dirname(os.path.abspath(path)), BACKUP_SUBDIR)
    os.makedirs(folder, exist_ok=True)
    base = os.path.basename(path)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = os.path.join(folder, f"{base}.{stamp}.bak")
    shutil.copy2(path, dest)
    copies = sorted(f for f in os.listdir(folder) if f.startswith(base + ".") and f.endswith(".bak"))
    for old in copies[:-MAX_BACKUPS_PER_FILE]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return dest


def remove_row(path, fields, line_no, expect=None, backup=True):
    """
    Delete the data row that read_rows() reported as `line_no`, leaving every
    other byte of the file as it was. `expect` ({field: value}) must still match
    that row, otherwise RowChangedError is raised and nothing is written.
    Returns the removed row's values ({field: value}).
    """
    name = os.path.basename(path)
    bom, _enc, records, _header, colmap, target = _locate(path, fields, line_no)
    values, _problem = _parse_record(name, line_no, target["cells"], colmap, fields, log=False)
    _check_expect(name, line_no, values, expect)
    if backup:
        backup_file(path)
    _atomic_write(path, bom + b"".join(r["raw"] for r in records if r is not target))
    return values


def update_row(path, fields, line_no, expect, changes, backup=True):
    """
    Change some fields of the row read_rows() reported as `line_no`, in place,
    leaving every other row (and that row's other columns) as they were.
    `expect` must still match the row (else RowChangedError, nothing written).
    A change for a field the file has no column for can't be stored; those
    field names are returned (same contract as append_row) and the rest is saved.
    Returns (new_values, dropped).
    """
    name = os.path.basename(path)
    bom, encoding, records, header, colmap, target = _locate(path, fields, line_no)
    values, _problem = _parse_record(name, line_no, target["cells"], colmap, fields, log=False)
    _check_expect(name, line_no, values, expect)

    cells = list(target["cells"]) + [""] * max(0, len(header) - len(target["cells"]))
    dropped = []
    for field_name, value in changes.items():
        idx = colmap.get(field_name)
        if idx is None:
            if value not in (None, ""):
                dropped.append(field_name)
            continue
        cells[idx] = "" if value is None else _format_cell(value)

    term = target["term"]
    out = io.StringIO()
    csv.writer(out, lineterminator=term or "\n").writerow(cells)
    row_text = out.getvalue() if term else out.getvalue()[:-1]
    new_raw = row_text.encode(encoding, errors="replace")

    if backup:
        backup_file(path)
    _atomic_write(path, bom + b"".join(new_raw if r is target else r["raw"] for r in records))
    new_values, _ = _parse_record(name, line_no, cells, colmap, fields, log=False)
    return new_values, dropped
