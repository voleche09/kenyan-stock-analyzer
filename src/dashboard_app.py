"""
The dashboard app — a small local web server that makes the dashboard
clickable: search for a stock and add it to your ⭐ watchlist, record a
purchase, fix a mistake, and update the data — all from the browser, with no
files to edit and no commands to type.

    Double-click "Open Dashboard.command"   (or: ./venv/bin/python3 app.py)

What it does:
  - Serves the generated pages in reports/ at http://127.0.0.1:8765 and adds a
    small JSON API that the pages' manage.js uses (search, add, remove, ...).
  - Writes ONLY your private files in portfolio/ (watchlist.csv, holdings.csv,
    international_holdings.csv, bonds.csv) — through the same CSV code the
    command-line helpers use, with a backup copy before anything is removed.
  - Runs the pipeline (main.py) in the background to update the pages: a
    quick watchlist-only rebuild after a watchlist change, a full update after
    you record a purchase or click 🔄 Update. A full update is built in a
    separate folder and only swapped in when it has finished, so the pages
    you're looking at never disappear halfway and a failed update changes
    nothing.

Security — this serves your private portfolio pages and can change your files:
  - Listens on 127.0.0.1 only: nothing else on your network can reach it.
  - Every request must be addressed to 127.0.0.1/localhost on our port (the
    Host header), so a malicious website can't use "DNS rebinding" to read
    your pages.
  - Every change (POST) must carry this run's random token in a custom header
    and be JSON — together these force the browser's cross-site checks, so
    another website you visit can't make changes on your behalf. The Origin
    header, when sent, must be ours too.
  - Request bodies are capped at 16 KB; the commands it runs are fixed (no
    user input ever reaches a command line); no directory listings or hidden
    files are served.
"""

import argparse
import collections
import datetime as dt
import http.server
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

import portfolio_csv
import symbol_lookup
import watchlist as wl
from logger import get_logger
from ui_theme import NAV_FILES

logger = get_logger(__name__)

APP_NAME = "kenyan-stock-analyzer"
DEFAULT_PORT = 8765
PORT_TRIES = 10
MAX_BODY = 16 * 1024
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(ROOT, "app_assets")
ASSET_TYPES = {".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
STAGING_SUBDIR = "_report_staging"
FULL_UPDATE_DELAY = 5          # seconds to wait after a holdings change (batches quick edits)
TIMEOUTS = {"watchlist": 5 * 60, "full": 20 * 60}


class ApiError(Exception):
    """An API failure with an HTTP status and a plain-English message for the page."""

    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


# ----------------------------------------------------------------------------
# Background updates
# ----------------------------------------------------------------------------
def default_command(kind):
    main_py = os.path.join(ROOT, "main.py")
    if kind == "watchlist":
        return [sys.executable, main_py, "--watchlist-page-only", "--no-email"]
    return [sys.executable, main_py, "--report-type", "html", "--detailed", "--no-email"]


# (log text to look for, progress %, what to tell the user)
_FULL_STEPS = [
    ("KENYAN STOCK ANALYZER", 2, "Starting…"),
    ("Fetching stock data from NSE", 5, "Downloading today's Nairobi prices…"),
    ("Analyzing...", 30, "Analysing trends…"),
    ("Fetching fundamental data", 38, "Downloading company figures…"),
    ("Scoring stocks", 45, "Scoring stocks…"),
    ("Portfolio:", 48, "Updating your portfolio…"),
    ("International portfolio:", 52, "Updating your international stocks…"),
    ("Generating individual stock reports", 55, "Building a page for every stock…"),
    ("Generating individual international stock reports", 82, "Building international stock pages…"),
    ("Generating market summary", 86, "Building the market summary…"),
    ("Generating index dashboard", 90, "Building the dashboard pages…"),
    ("Watchlist:", 94, "Updating your watchlist…"),
    ("Done!", 99, "Finishing…"),
]
_WATCHLIST_STEPS = [
    ("Rebuilding the watchlist page", 5, "Starting…"),
    ("Watchlist:", 15, "Fetching prices and news for your watchlist…"),
    ("Watchlist: building charts", 80, "Drawing charts…"),
    ("Watchlist page saved", 98, "Saving…"),
]


def publish_staging(staging, reports_dir):
    """Move a finished update's pages from the staging folder into reports/
    (one atomic rename per file), then remove pages left over from earlier
    runs — e.g. yesterday's per-stock pages, whose names carry a timestamp.
    Only .html files are ever removed; anything else in reports/ is kept."""
    os.makedirs(reports_dir, exist_ok=True)
    new = set()
    for name in sorted(os.listdir(staging)):
        src = os.path.join(staging, name)
        if not os.path.isfile(src) or name.endswith(".tmp"):
            continue
        dst = os.path.join(reports_dir, name)
        try:
            os.replace(src, dst)
        except OSError:                       # different disk: copy, then rename into place
            tmp = dst + ".publish.tmp"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        new.add(name)
    for name in os.listdir(reports_dir):
        path = os.path.join(reports_dir, name)
        if name.endswith(".html") and name not in new and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
    shutil.rmtree(staging, ignore_errors=True)
    return len(new)


class UpdateJobs:
    """Runs updates in the background, one at a time.

    Requests are coalesced: asking for the same kind of update while one is
    already queued does nothing extra, and a queued full update also covers a
    queued watchlist one (the full run rebuilds the watchlist page too).
    """

    def __init__(self, config, command_factory=default_command, timeouts=None):
        self.config = config
        self.command_factory = command_factory
        self.timeouts = timeouts or TIMEOUTS
        self.staging = os.path.join(config.cache_dir, STAGING_SUBDIR)
        self.cond = threading.Condition()
        self.pending = set()
        self.not_before = 0.0
        self.current = None
        self.last = None
        self.generation = 0
        self.proc = None
        self.stopping = False
        self.thread = threading.Thread(target=self._worker, name="update-jobs", daemon=True)

    def start(self):
        shutil.rmtree(self.staging, ignore_errors=True)   # leftovers from an interrupted run
        self.thread.start()

    def request(self, kind, delay=0):
        if kind not in ("watchlist", "full"):
            raise ValueError(f"unknown update kind {kind!r}")
        with self.cond:
            self.pending.add(kind)
            if delay:
                self.not_before = max(self.not_before, time.time() + delay)
            self.cond.notify_all()

    def status(self):
        with self.cond:
            cur = dict(self.current) if self.current else None
            if cur:
                cur["elapsed"] = round(time.time() - cur.pop("_t0"), 1)
                cur.pop("_log", None)
            last = dict(self.last) if self.last else None
            return {"running": cur is not None, "job": cur, "queued": sorted(self.pending),
                    "last": last, "generation": self.generation}

    def wait_idle(self, timeout=30):
        """For tests: block until nothing is running or queued."""
        end = time.time() + timeout
        with self.cond:
            while (self.current or self.pending) and time.time() < end:
                self.cond.wait(0.05)
            return not (self.current or self.pending)

    def stop(self):
        with self.cond:
            self.stopping = True
            self.pending.clear()
            proc = self.proc
            self.cond.notify_all()
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(self.staging, ignore_errors=True)

    # ---- worker ----
    def _worker(self):
        while True:
            with self.cond:
                while not self.stopping and (not self.pending or time.time() < self.not_before):
                    self.cond.wait(0.5 if self.pending else None)
                if self.stopping:
                    return
                kind = "full" if "full" in self.pending else "watchlist"
                self.pending.discard(kind)
                if kind == "full":
                    self.pending.discard("watchlist")
                self.current = {"kind": kind, "step": "Starting…", "progress": 1,
                                "started": dt.datetime.now().strftime("%H:%M:%S"), "_t0": time.time()}
            ok, message, tail = self._run(kind)
            with self.cond:
                started = self.current["_t0"]
                self.current = None
                if ok:
                    self.generation += 1
                self.last = {"kind": kind, "ok": ok, "message": message,
                             "finished": dt.datetime.now().strftime("%H:%M:%S"),
                             "duration": round(time.time() - started, 1),
                             "log_tail": [] if ok else tail[-25:]}
                self.cond.notify_all()

    def _set_progress(self, kind, line, counters):
        steps = _FULL_STEPS if kind == "full" else _WATCHLIST_STEPS
        with self.cond:
            cur = self.current
            if not cur:
                return
            for needle, pct, text in steps:
                if needle in line and pct >= cur["progress"]:
                    cur["progress"], cur["step"] = pct, text
            m = re.search(r"Fetched (\d+) stocks", line)
            if m:
                counters["total"] = int(m.group(1))
            if kind == "full" and "Saved HTML:" in line and 55 <= cur["progress"] < 82:
                counters["pages"] = counters.get("pages", 0) + 1
                total = counters.get("total") or 60
                done = min(counters["pages"], total)
                cur["progress"] = 55 + int(26 * done / total)
                cur["step"] = f"Building a page for every stock ({done}/{total})…"

    def _run(self, kind):
        env = dict(os.environ)
        env.update({"COLUMNS": "250", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        if kind == "full":
            shutil.rmtree(self.staging, ignore_errors=True)
            os.makedirs(self.staging, exist_ok=True)
            env["REPORT_DIRECTORY"] = self.staging
        tail = collections.deque(maxlen=60)
        log_path = os.path.join(os.path.dirname(os.path.abspath(self.config.log_file)), "last_update.log")
        counters = {}
        try:
            cmd = self.command_factory(kind)
            proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                    bufsize=1)
        except Exception as e:
            logger.error(f"Couldn't start the update: {e}")
            return False, f"The update couldn't start: {e}", list(tail)
        with self.cond:
            self.proc = proc
        timed_out = []

        def on_timeout():
            timed_out.append(True)
            proc.kill()
        timer = threading.Timer(self.timeouts.get(kind, 1200), on_timeout)
        timer.daemon = True
        timer.start()
        try:
            with open(log_path, "w", encoding="utf-8") as log:
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                    tail.append(line.rstrip())
                    self._set_progress(kind, line, counters)
            proc.wait()
        finally:
            timer.cancel()
            with self.cond:
                self.proc = None
        if self.stopping:
            return False, "Stopped.", list(tail)
        if proc.returncode != 0:
            reason = ("it took too long and was stopped" if timed_out
                      else f"it stopped with an error (exit code {proc.returncode})")
            logger.warning(f"{kind} update failed: {reason}")
            if kind == "full":
                shutil.rmtree(self.staging, ignore_errors=True)
            return False, (f"The update didn't finish — {reason}. Your dashboard wasn't changed. "
                           f"Details: logs/last_update.log"), list(tail)
        if kind == "full":
            try:
                if not os.path.exists(os.path.join(self.staging, "index.html")):
                    raise RuntimeError("the new dashboard is incomplete (no index.html)")
                n = publish_staging(self.staging, self.config.report_directory)
                logger.info(f"Update published: {n} file(s)")
            except Exception as e:
                logger.error(f"Publishing the update failed: {e}")
                return False, f"The update finished but couldn't be put in place: {e}", list(tail)
        return True, ("Your dashboard is up to date." if kind == "full" else "Your watchlist is up to date."), list(tail)


# ----------------------------------------------------------------------------
# Purchases — checking what someone typed before it touches their portfolio
# ----------------------------------------------------------------------------
_KINDS = ("nse", "intl", "bonds")


def _modules(kind):
    if kind == "nse":
        import portfolio as m
        return m, m.HOLDINGS_CSV_FILE, m.HOLDINGS_FILE, portfolio_csv.STOCK_LOT_FIELDS
    if kind == "intl":
        import international_portfolio as m
        return m, m.HOLDINGS_CSV_FILE, m.HOLDINGS_FILE, portfolio_csv.STOCK_LOT_FIELDS
    if kind == "bonds":
        import bonds_portfolio as m
        return m, m.BONDS_CSV_FILE, m.BONDS_FILE, portfolio_csv.BOND_FIELDS
    raise ApiError(400, "Choose what you bought: a Kenyan stock, an international stock or a bond.")


def _number(raw, label, required=True):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if required:
            raise ApiError(400, f"Please enter the {label}.")
        return None
    try:
        value = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) \
            else portfolio_csv.parse_number(str(raw))
    except ValueError:
        raise ApiError(400, f"The {label} '{raw}' isn't a number — use digits with a dot for decimals, e.g. 34.50.")
    if not math.isfinite(value) or value <= 0:
        raise ApiError(400, f"The {label} must be more than zero.")
    return value


def _date(raw, label, today):
    if raw is None or not str(raw).strip():
        return None
    try:
        d = dt.date.fromisoformat(portfolio_csv.parse_date(str(raw)))
    except ValueError as e:
        raise ApiError(400, f"The {label}: {e}")
    if d > today:
        raise ApiError(400, f"The {label} ({d.isoformat()}) is in the future — please check it.")
    if d.year < 1990:
        raise ApiError(400, f"The {label} ({d.isoformat()}) looks wrong — please check the year.")
    return d.isoformat()


def _fmt_qty(q):
    return f"{q:,.0f}" if float(q).is_integer() else f"{q:,.6g}"


def check_purchase(kind, body, cache_dir, today=None, lookup=symbol_lookup):
    """
    Validate one purchase. -> (values for add_lot/add_bond, summary line, [warnings]).
    Raises ApiError (400 bad input, 503 data source unreachable) with a
    plain-English message. Warnings don't block — they ask "are you sure?".
    """
    today = today or dt.date.today()
    note = wl.clean_note(body.get("note"))
    warnings = []
    if kind == "bonds":
        import bonds_portfolio as B
        issue = B._canonical_issue(body.get("issue") or "")
        ref = B.BOND_REFERENCE.get(issue)
        if not isinstance(ref, dict):
            raise ApiError(400, f"We don't have the terms for bond '{issue or '?'}' yet, so it can't be tracked "
                                f"accurately. Pick it from the list — if yours isn't there, see portfolio/README.md.")
        face = _number(body.get("face_value"), "face value (KES)")
        pct = _number(body.get("purchase_price_pct"), "price (% of face value)", required=False) or 100.0
        when = _date(body.get("purchase_date"), "purchase date", today)
        if face < 50_000:
            warnings.append(f"KES {face:,.0f} is below the usual KES 50,000 minimum for a Treasury bond — "
                            f"is the face value right?")
        if not 80 <= pct <= 120:
            warnings.append(f"A price of {pct:g}% of face value is unusual (most trade between 80% and 120%) — "
                            f"this is the clean price as a percentage, e.g. 101.33, not an amount in KES.")
        summary = (f"KES {face:,.0f} face value of {issue} at {pct:g}% of face "
                   f"(≈ KES {face * pct / 100:,.2f})" + (f", bought {when}" if when else ""))
        return ({"issue": issue, "face_value": face, "purchase_price_pct": pct,
                 "purchase_date": when, "note": note}, summary, warnings)

    if kind not in ("nse", "intl"):
        _modules(kind)
    market = wl.MARKET_NSE if kind == "nse" else wl.MARKET_INTL
    symbol = wl.clean_symbol(body.get("symbol"))
    try:
        wl.validate_symbol(symbol, market)
    except ValueError as e:
        raise ApiError(400, str(e))
    qty = _number(body.get("quantity"), "number of shares")
    price = _number(body.get("buy_price"), "price you paid per share")
    when = _date(body.get("buy_date"), "purchase date", today)

    if body.get("undo"):              # re-adding a row we just removed: already checked once
        name, cur, today_price = symbol, ("KES" if kind == "nse" else "USD"), None
    else:
        try:
            q = lookup.quote(symbol, market, cache_dir)
        except lookup.LookupUnavailable as e:
            raise ApiError(503, f"{e} Your purchase wasn't saved — check your internet connection and "
                                f"try again in a moment.", offline=True)
        if not q.get("found"):
            raise ApiError(400, q.get("message") or f"Couldn't find {symbol}.",
                           suggestions=q.get("suggestions", []))
        name, cur, today_price = q.get("name") or symbol, q.get("currency") or "USD", q.get("price")
        if kind == "intl" and cur != "USD":
            raise ApiError(400, f"{symbol} is priced in {cur}, and your international portfolio is tracked in "
                                f"US dollars — so it can't be added here yet. You can still add it to your "
                                f"⭐ watchlist.")
        if today_price and (price > today_price * 1.5 or price < today_price / 1.5):
            warnings.append(f"You entered {wl.fmt_money(price, cur)} but {symbol} trades at "
                            f"{wl.fmt_money(today_price, cur)} today. That's fine if you bought it a while "
                            f"ago — otherwise check the price (per share, not the total).")
    if kind == "nse" and not float(qty).is_integer():
        warnings.append("NSE shares are normally bought in whole numbers — check the number of shares.")
    total = qty * price
    summary = (f"{_fmt_qty(qty)} share{'s' if qty != 1 else ''} of {symbol}"
               + (f" ({name})" if name and name != symbol else "")
               + f" at {wl.fmt_money(price, cur)} each = {wl.fmt_money(total, cur)}"
               + (f", bought {when}" if when else ""))
    return ({"symbol": symbol, "quantity": qty, "buy_price": price, "buy_date": when, "note": note},
            summary, warnings)


# ----------------------------------------------------------------------------
# The app: API methods (plain functions, so tests needn't speak HTTP)
# ----------------------------------------------------------------------------
class DashboardApp:
    def __init__(self, config, jobs=None, lookup=symbol_lookup):
        self.config = config
        self.reports_dir = config.report_directory
        self.portfolio_dir = config.portfolio_dir
        self.cache_dir = config.cache_dir
        self.token = secrets.token_urlsafe(24)
        self.lookup = lookup
        self.jobs = jobs or UpdateJobs(config)
        self.lock = threading.RLock()      # one change to the portfolio files at a time
        self.port = None
        self.allowed_hosts = set()

    def bind_port(self, port):
        self.port = port
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    # ---- read-only ----
    def dashboard_info(self):
        idx = os.path.join(self.reports_dir, "index.html")
        if not os.path.exists(idx):
            return {"exists": False}
        m = dt.datetime.fromtimestamp(os.path.getmtime(idx))
        return {"exists": True, "updated": m.strftime("%Y-%m-%d %H:%M"),
                "updated_time": m.strftime("%H:%M"), "is_today": m.date() == dt.date.today()}

    def status(self):
        s = self.jobs.status()
        s["dashboard"] = self.dashboard_info()
        return s

    def _nse_symbols(self):
        return symbol_lookup.nse_symbols(self.cache_dir)

    def watchlist_entries(self):
        try:
            entries = wl.load_watchlist(self.portfolio_dir, nse_symbols=self._nse_symbols())
        except ValueError as e:
            raise ApiError(500, f"Your watchlist file couldn't be read: {e}")
        return {"entries": [{k: e[k] for k in ("symbol", "market", "buy_below", "sell_above", "note",
                                               "added", "added_price")} for e in entries]}

    def search(self, q):
        result = self.lookup.search(q, self.cache_dir)
        try:
            watching = {(e["market"], e["symbol"]) for e in self.watchlist_entries()["entries"]}
        except ApiError:
            watching = set()
        for r in result["results"]:
            r["watching"] = (r["market"], r["symbol"]) in watching
        return result

    def quote(self, symbol, market):
        market = wl.normalize_market(market)
        if market is None:
            raise ApiError(400, "Say which market: NSE or INTL.")
        try:
            return self.lookup.quote(symbol, market, self.cache_dir)
        except self.lookup.LookupUnavailable as e:
            raise ApiError(503, str(e), offline=True)

    def holdings(self, kind):
        mod, csv_name, json_name, fields = _modules(kind)
        csv_path = os.path.join(self.portfolio_dir, csv_name)
        json_path = os.path.join(self.portfolio_dir, json_name)
        if os.path.exists(csv_path):
            try:
                rows = portfolio_csv.read_rows(csv_path, fields)
            except ValueError as e:
                raise ApiError(500, f"{csv_name} couldn't be read: {e}")
            return {"kind": kind, "source": "csv", "file": csv_name, "editable": True,
                    "entries": [dict(rec, line=ln) for ln, rec in rows]}
        if os.path.exists(json_path):
            lots = mod.load_bonds(self.portfolio_dir) if kind == "bonds" else mod.load_holdings(self.portfolio_dir)
            keys = [f.name for f in fields]
            return {"kind": kind, "source": "json", "file": json_name, "editable": False,
                    "entries": [{k: lot.get(k) for k in keys} for lot in lots]}
        return {"kind": kind, "source": None, "file": csv_name, "editable": True, "entries": []}

    def bond_issues(self):
        import bonds_portfolio as B
        out = []
        for issue, ref in B.BOND_REFERENCE.items():
            if not isinstance(ref, dict):
                continue              # an alias of another entry
            bits = [b for b in (ref.get("tenor_label"), ref.get("type_label")) if b]
            if ref.get("coupon_pct"):
                bits.append(f"coupon {ref['coupon_pct']:.2f}%")
            if ref.get("maturity_date"):
                bits.append(f"matures {ref['maturity_date']}")
            out.append({"issue": issue, "label": issue + (f" — {' · '.join(bits)}" if bits else "")})
        return {"issues": out}

    # ---- changes ----
    def watchlist_add(self, body):
        symbol = wl.clean_symbol(body.get("symbol"))
        market = wl.normalize_market(body.get("market"))
        if market is None:
            raise ApiError(400, "Choose whether this is an NSE stock or an international one.")
        try:
            wl.validate_symbol(symbol, market)
            buy, sell = wl.validate_targets(body.get("buy_below"), body.get("sell_above"))
            note = wl.clean_note(body.get("note"))
            added = portfolio_csv.parse_date(str(body["added"])) if body.get("added") else None
            added_price = wl.parse_target(body.get("added_price"), "Price when added")
        except ValueError as e:
            raise ApiError(400, str(e))
        name, offline = symbol, False
        if not body.get("undo"):
            try:
                q = self.lookup.quote(symbol, market, self.cache_dir)
                if not q.get("found"):
                    raise ApiError(400, q.get("message") or f"Couldn't find {symbol}.",
                                   suggestions=q.get("suggestions", []))
                name = q.get("name") or symbol
                if added_price is None and q.get("price"):
                    added_price = float(q["price"])
            except self.lookup.LookupUnavailable as e:
                if not body.get("force"):
                    raise ApiError(503, f"{e} You can try again in a moment — or add {symbol} anyway, and "
                                        f"its data will appear once the connection is back.", offline=True)
                offline = True
        with self.lock:
            try:
                entry, dropped = wl.add_to_watchlist(self.portfolio_dir, symbol, market, buy, sell, note,
                                                     added=added, added_price=added_price,
                                                     nse_symbols=self._nse_symbols())
            except wl.AlreadyWatching as e:
                raise ApiError(409, str(e))
            except ValueError as e:
                raise ApiError(400, str(e))
        self.jobs.request("watchlist")
        msg = f"{symbol}{f' ({name})' if name != symbol else ''} is on your watchlist."
        if offline:
            msg += " Its data will appear once the connection is back."
        return {"ok": True, "entry": entry, "name": name, "message": msg, "dropped": dropped}

    def watchlist_remove(self, body):
        with self.lock:
            try:
                removed = wl.remove_from_watchlist(self.portfolio_dir, body.get("symbol"), body.get("market"),
                                                   nse_symbols=self._nse_symbols())
            except wl.NotOnWatchlist as e:
                raise ApiError(404, str(e))
            except portfolio_csv.RowChangedError as e:
                raise ApiError(409, str(e))
            except ValueError as e:
                raise ApiError(400, str(e))
        self.jobs.request("watchlist")
        return {"ok": True, "removed": removed, "message": f"{removed['symbol']} was removed from your watchlist."}

    def watchlist_update(self, body):
        changes = {k: body.get(k) for k in ("buy_below", "sell_above", "note") if k in body}
        with self.lock:
            try:
                entry, dropped = wl.update_watchlist_entry(self.portfolio_dir, body.get("symbol"),
                                                           body.get("market"), nse_symbols=self._nse_symbols(),
                                                           **changes)
            except wl.NotOnWatchlist as e:
                raise ApiError(404, str(e))
            except portfolio_csv.RowChangedError as e:
                raise ApiError(409, str(e))
            except ValueError as e:
                raise ApiError(400, str(e))
        self.jobs.request("watchlist")
        msg = f"Saved your changes to {entry['symbol']}."
        if dropped:
            msg += (f" (Your watchlist.csv has no {', '.join(dropped)} column, so that wasn't saved — add the "
                    f"column to its header row to keep it.)")
        return {"ok": True, "entry": entry, "message": msg, "dropped": dropped}

    def holdings_add(self, body):
        kind = body.get("kind")
        mod, csv_name, json_name, fields = _modules(kind)
        try:
            values, summary, warnings = check_purchase(kind, body, self.cache_dir, lookup=self.lookup)
        except ValueError as e:                  # e.g. a note that's too long
            raise ApiError(400, str(e))
        csv_path = os.path.join(self.portfolio_dir, csv_name)
        missing = portfolio_csv.unstorable_fields(csv_path, fields, values)
        if missing:
            warnings.append(f"Your {csv_name} has no {', '.join(missing)} column, so that won't be saved — "
                            f"add the column to its header row if you want to keep it.")
        if body.get("dry_run"):
            return {"ok": True, "dry_run": True, "summary": summary, "warnings": warnings}
        with self.lock:
            try:
                if kind == "bonds":
                    mod.add_bond(self.portfolio_dir, values["issue"], values["face_value"],
                                 values["purchase_price_pct"], values["purchase_date"], values["note"])
                else:
                    mod.add_lot(self.portfolio_dir, values["symbol"], values["quantity"], values["buy_price"],
                                values["buy_date"], values["note"])
            except ValueError as e:
                raise ApiError(400, str(e))
        self.jobs.request("full", delay=FULL_UPDATE_DELAY)
        return {"ok": True, "summary": summary, "warnings": warnings,
                "message": "Saved: " + summary + ". " + portfolio_csv.saved_to_message(
                    self.portfolio_dir, csv_name, json_name)}

    def holdings_remove(self, body):
        kind = body.get("kind")
        mod, csv_name, json_name, fields = _modules(kind)
        csv_path = os.path.join(self.portfolio_dir, csv_name)
        if not os.path.exists(csv_path):
            raise ApiError(400, f"Entries can only be removed here from {csv_name}. Your purchases are in "
                                f"{json_name} (the older format) — edit that file, or switch to {csv_name} "
                                f"(see portfolio/README.md).")
        try:
            line = int(body.get("line"))
        except (TypeError, ValueError):
            raise ApiError(400, "Which entry? (missing line number) — reload the page and try again.")
        expect = body.get("expect") or {}
        key = "issue" if kind == "bonds" else "symbol"
        if not isinstance(expect, dict) or not expect.get(key):
            raise ApiError(400, "Missing details of the entry to remove — reload the page and try again.")
        expect = {k: v for k, v in expect.items() if k in {f.name for f in fields}}
        with self.lock:
            try:
                removed = portfolio_csv.remove_row(csv_path, fields, line, expect=expect)
            except portfolio_csv.RowChangedError as e:
                raise ApiError(409, str(e))
            except ValueError as e:
                raise ApiError(400, str(e))
        self.jobs.request("full", delay=FULL_UPDATE_DELAY)
        what = removed.get("symbol") or removed.get("issue")
        return {"ok": True, "removed": removed, "message": f"Removed that {what} entry from {csv_name}. "
                                                          f"(A backup copy was saved in portfolio/backups/.)"}

    def refresh(self, body):
        kind = body.get("kind") or "full"
        if kind not in ("full", "watchlist"):
            raise ApiError(400, "Unknown update type.")
        self.jobs.request(kind)
        return self.status()


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
# The dashboard's own pages — the same list the sidebar shows.
_NAV_PAGES = set(NAV_FILES)

_PREPARING_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Preparing your dashboard</title>
<script>(function(){var h=document.documentElement;try{h.setAttribute('data-theme',localStorage.getItem('nse-theme')||
(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'));}catch(e){}})();</script>
<style>
:root{--bg:#f5f7fb;--card:#fff;--text:#0f172a;--muted:#5a6a7e;--bar:#e7ecf3;--accent:#4f46e5;--down:#be123c;--border:#e2e8f0}
:root[data-theme="dark"]{--bg:#0b0f17;--card:#121a27;--text:#e6edf7;--muted:#94a3b8;--bar:#1e2a3d;--accent:#a5b4fc;--down:#fb7185;--border:#233044}
body{font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI Variable Text","Segoe UI",Roboto,sans-serif;
background:var(--bg);color:var(--text);margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;
padding:20px;box-sizing:border-box;-webkit-font-smoothing:antialiased}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:30px 28px;max-width:520px;width:100%;
box-shadow:0 10px 30px rgba(15,23,42,.10)}
h1{font-size:1.3rem;margin:0 0 6px;letter-spacing:-.01em}p{color:var(--muted);line-height:1.5;margin:8px 0}
.bar{height:8px;border-radius:999px;background:var(--bar);overflow:hidden;margin:18px 0 8px}
.bar>div{height:100%;width:2%;background:var(--accent);border-radius:inherit;transition:width .6s ease}
#step{font-weight:600;color:var(--text)}.err{color:var(--down)!important}a{color:var(--accent)}
</style></head><body><div class="card"><h1>🇰🇪 Preparing your dashboard…</h1>
<p>__MESSAGE__</p><div class="bar"><div id="fill"></div></div><p id="step">Checking…</p>
<p style="font-size:.85rem">This page refreshes by itself — leave it open. The first update of the day takes a few minutes.</p>
</div><script>
(function(){var fill=document.getElementById('fill'),step=document.getElementById('step'),gen0=null;
function tick(){fetch('/api/status',{cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
 if(gen0===null){gen0=s.generation;}
 if(s.job){fill.style.width=Math.max(2,s.job.progress)+'%';step.textContent=s.job.step+' ('+Math.round(s.job.elapsed)+'s)';}
 else if(s.queued&&s.queued.length){step.textContent='Starting…';}
 else if(s.generation>gen0){location.reload();return;}
 else if(s.last&&!s.last.ok){step.className='err';step.textContent=s.last.message;return;}
 else{step.textContent='Waiting for an update — click 🔄 Update on any dashboard page.';}
 setTimeout(tick,1500);}).catch(function(){step.textContent='The dashboard app has stopped — start it again (Open Dashboard.command).';});}
tick();})();</script></body></html>"""


def make_handler(app):
    class Handler(http.server.SimpleHTTPRequestHandler):
        server_version = "KenyanStockAnalyzer/1"
        sys_version = ""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=app.reports_dir, **kwargs)

        def log_message(self, fmt, *args):
            logger.debug("app: " + (fmt % args))

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        # ---- helpers ----
        def _send(self, status, body, ctype):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload, default=str), "application/json; charset=utf-8")

        def _host_ok(self):
            return (self.headers.get("Host") or "").strip().lower() in app.allowed_hosts

        # ---- GET / HEAD ----
        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "Forbidden", "text/plain; charset=utf-8")
            parts = urllib.parse.urlsplit(self.path)
            path = parts.path
            if path.startswith("/api/"):
                return self._api(path, urllib.parse.parse_qs(parts.query), None)
            if path.startswith("/app-assets/"):
                return self._asset(path)
            return self._static(path)

        do_HEAD = do_GET

        def _asset(self, path):
            name = urllib.parse.unquote(path[len("/app-assets/"):])
            ext = os.path.splitext(name)[1]
            if "/" in name or name.startswith(".") or ext not in ASSET_TYPES:
                return self._send(404, "Not found", "text/plain; charset=utf-8")
            full = os.path.join(ASSETS_DIR, name)
            if not os.path.isfile(full):
                return self._send(404, "Not found", "text/plain; charset=utf-8")
            with open(full, "rb") as f:
                return self._send(200, f.read(), ASSET_TYPES[ext])

        def _static(self, path):
            clean = urllib.parse.unquote(path)
            if clean in ("", "/"):
                self.send_response(302)
                self.send_header("Location", "/index.html")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            if any(p.startswith(".") for p in clean.split("/") if p) or "\\" in clean:
                return self._send(404, "Not found", "text/plain; charset=utf-8")
            local = self.translate_path(self.path)
            real_root = os.path.realpath(app.reports_dir)
            if not os.path.realpath(local).startswith(real_root + os.sep) or os.path.isdir(local):
                return self._send(404, "Not found", "text/plain; charset=utf-8")
            if not os.path.isfile(local):
                name = os.path.basename(clean)
                if name in _NAV_PAGES:
                    s = app.status()
                    if not s["dashboard"]["exists"] and not (s["running"] or s["queued"]):
                        app.jobs.request("full")          # first ever start: build everything
                        s = app.status()
                    if s["running"] or s["queued"]:
                        msg = ("Your dashboard is being updated with today's prices — this page will "
                               "appear when it's ready.")
                    else:
                        msg = ("This page hasn't been built yet. Click 🔄 Update on any dashboard page "
                               "(or run ./run.sh) to build it.")
                    return self._send(200, _PREPARING_PAGE.replace("__MESSAGE__", msg), "text/html; charset=utf-8")
                return self._send(404, "Not found", "text/plain; charset=utf-8")
            return super().do_HEAD() if self.command == "HEAD" else super().do_GET()

        # ---- POST ----
        def do_POST(self):
            if not self._host_ok():
                return self._send(403, "Forbidden", "text/plain; charset=utf-8")
            path = urllib.parse.urlsplit(self.path).path
            if not path.startswith("/api/"):
                return self._json(405, {"error": "Not allowed."})
            origin = self.headers.get("Origin")
            if origin and origin.lower() not in {f"http://{h}" for h in app.allowed_hosts}:
                return self._json(403, {"error": "Requests from other websites aren't allowed."})
            if not secrets.compare_digest(self.headers.get("X-Dashboard-Token") or "", app.token):
                return self._json(403, {"error": "This page is out of date — reload it and try again."})
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return self._json(415, {"error": "Expected JSON."})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                return self._json(413, {"error": "That request is too large."})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"error": "That request couldn't be read (not valid JSON)."})
            if not isinstance(body, dict):
                return self._json(400, {"error": "That request couldn't be read."})
            return self._api(path, {}, body)

        # ---- API routing ----
        def _api(self, path, query, body):
            q = lambda k: (query.get(k) or [""])[0]  # noqa: E731
            get_routes = {
                "/api/ping": lambda: {"app": APP_NAME, "version": 1},
                "/api/session": lambda: {"token": app.token},
                "/api/status": app.status,
                "/api/search": lambda: app.search(q("q")),
                "/api/quote": lambda: app.quote(q("symbol"), q("market")),
                "/api/watchlist": app.watchlist_entries,
                "/api/holdings": lambda: app.holdings(q("kind")),
                "/api/bonds/issues": app.bond_issues,
            }
            post_routes = {
                "/api/watchlist/add": app.watchlist_add,
                "/api/watchlist/remove": app.watchlist_remove,
                "/api/watchlist/update": app.watchlist_update,
                "/api/holdings/add": app.holdings_add,
                "/api/holdings/remove": app.holdings_remove,
                "/api/refresh": app.refresh,
            }
            try:
                if body is None:
                    fn = get_routes.get(path)
                    if fn is None:
                        return self._json(404 if path not in post_routes else 405, {"error": "Not found."})
                    return self._json(200, fn())
                fn = post_routes.get(path)
                if fn is None:
                    return self._json(404 if path not in get_routes else 405, {"error": "Not found."})
                return self._json(200, fn(body))
            except ApiError as e:
                return self._json(e.status, dict(e.extra, error=e.message))
            except Exception as e:
                logger.error(f"app: {path} failed: {e}", exc_info=True)
                return self._json(500, {"error": f"Something went wrong ({type(e).__name__}) — the details "
                                                 f"are in logs/analyzer.log."})

    return Handler


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # Lets a restarted app take its port straight back (instead of waiting out
    # the OS's TIME_WAIT); a port another program is LISTENING on is still
    # refused, which is how "port in use" is detected.
    allow_reuse_address = True


def make_server(app, port):
    """Bind 127.0.0.1:port (0 = any free port). Raises OSError if it's taken."""
    server = _Server(("127.0.0.1", port), make_handler(app))
    app.bind_port(server.server_address[1])
    return server


def _ours_at(port):
    """Is our app already answering on this port?"""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ping", headers={"Host": f"127.0.0.1:{port}"})
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == APP_NAME
    except Exception:
        return False


def _warm_up(cache_dir):
    """Load the NSE company list and open a first connection to Yahoo in the
    background, so the first search someone types is answered quickly."""
    try:
        symbol_lookup.nse_symbols(cache_dir)
        symbol_lookup._yahoo_search("apple")
    except Exception as e:
        logger.debug(f"Search warm-up skipped: {e}")


def _prepare_environment():
    """What run.sh does, so double-click launching behaves the same."""
    if not os.environ.get("SSL_CERT_FILE"):
        try:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
        except ImportError:
            pass
    if sys.platform == "darwin" and os.path.isdir("/opt/homebrew/lib"):
        existing = os.environ.get("DYLD_LIBRARY_PATH", "")
        if "/opt/homebrew/lib" not in existing:
            os.environ["DYLD_LIBRARY_PATH"] = f"/opt/homebrew/lib:{existing}" if existing else "/opt/homebrew/lib"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open the stock dashboard with click-to-add (local app).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT") or DEFAULT_PORT))
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser window")
    parser.add_argument("--no-auto-update", action="store_true",
                        help="Don't update the data automatically when it isn't from today")
    args = parser.parse_args(argv)

    _prepare_environment()
    from config import Config
    from logger import setup_logging
    config = Config()
    setup_logging(config)

    server = None
    for port in range(args.port, args.port + PORT_TRIES):
        if _ours_at(port):
            url = f"http://127.0.0.1:{port}/index.html"
            print(f"\nThe dashboard app is already running — opening {url}\n")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        try:
            app = DashboardApp(config)
            server = make_server(app, port)
            break
        except OSError:
            continue
    if server is None:
        print(f"\nCouldn't start: ports {args.port}–{args.port + PORT_TRIES - 1} are all in use. "
              f"Close other programs or set DASHBOARD_PORT to a free port.\n")
        return 1

    app.jobs.start()
    info = app.dashboard_info()
    if not args.no_auto_update and not (info["exists"] and info["is_today"]):
        app.jobs.request("full")
    threading.Thread(target=_warm_up, args=(config.cache_dir,), daemon=True).start()

    url = f"http://127.0.0.1:{app.port}/index.html"
    print("\n" + "=" * 64)
    print("  🇰🇪  Your stock dashboard is running")
    print(f"      {url}")
    if not (info["exists"] and info["is_today"]) and not args.no_auto_update:
        print("      Updating to today's prices in the background (a few minutes)…")
    print("\n  Keep this window open while you use the dashboard.")
    print("  To stop: close this window, or press Ctrl+C.")
    print("=" * 64 + "\n")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    def _stop(signum, frame):
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, _stop)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping the dashboard app…")
    finally:
        app.jobs.stop()
        server.server_close()
    return 0
