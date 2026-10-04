"""
Find and check tickers — powers the dashboard app's search box and add forms.

  NSE (Nairobi)   Our own list of every NSE-listed company: TradingView's Kenya
                  scan gives each ticker's full company name and last price.
                  Yahoo Finance doesn't cover the NSE (searching it for
                  "Safaricom" finds nothing), so NSE search never depends on it.
  International   Yahoo Finance's own search (yf.Search), stocks and ETFs only.

The same ticker can exist on both: "EQTY" is Equity Group on the NSE and an
unrelated ETF in New York. So results always say which market each one is on,
and the caller stores that choice explicitly.

Every lookup tells "no such ticker" apart from "couldn't reach the data source"
(LookupUnavailable), so the app can say the right thing instead of a generic
error.
"""

import asyncio
import difflib
import glob
import json
import os
import re
import threading
import time
import datetime as dt

from logger import get_logger

logger = get_logger(__name__)

NSE, INTL = "NSE", "INTL"
_INTL_TYPES = {"EQUITY", "ETF"}
_SEARCH_TTL = 600           # seconds an international search result is reused
_MAX_RESULTS = 6


class LookupUnavailable(Exception):
    """The data source couldn't be reached (offline, blocked or rate-limited)."""


# ----------------------------------------------------------------------------
# NSE universe: {symbol: {"name", "price", "change_pct", "sector"}}
# ----------------------------------------------------------------------------
_universe_lock = threading.Lock()
_universe_memo = {}         # cache_dir -> (date, universe, stale, monotonic time it was built)
_STALE_RETRY = 60           # seconds before retrying TradingView after falling back to a stale list


def _today():
    return dt.date.today().strftime("%Y%m%d")


def _from_fundamentals_file(path):
    from portfolio import SYMBOL_NAMES
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {sym: {"name": (d or {}).get("name") or SYMBOL_NAMES.get(sym) or sym,
                  "price": (d or {}).get("close"), "change_pct": (d or {}).get("change_pct"),
                  "sector": (d or {}).get("sector")}
            for sym, d in data.items() if sym}


async def _scan_names():
    from tvkit import ScannerRequest, ScannerService, Market
    from tvkit.api.scanner.models.scanner import ScannerOptions, SortConfig
    async with ScannerService() as scanner:
        request = ScannerRequest(
            columns=["name", "description", "close", "change", "sector"],
            options=ScannerOptions(filter_lang="pinescript_v5"),
            range=(0, 200), sort=SortConfig(sortBy="name", sortOrder="asc"),
            preset="all_stocks",
        )
        result = await scanner.scan_market(Market.KENYA, request)
    out = {}
    for stock in result.data:
        extra = stock.model_extra or {}
        sym = getattr(stock, "name", None)
        if not sym:
            continue
        out[sym] = {"name": getattr(stock, "description", None) or extra.get("description") or sym,
                    "price": getattr(stock, "close", None),
                    "change_pct": getattr(stock, "change", None),
                    "sector": getattr(stock, "sector", None) or extra.get("sector")}
    return out


def _fresh_today(path):
    return (os.path.exists(path)
            and dt.date.fromtimestamp(os.path.getmtime(path)) == dt.date.today())


def nse_universe(cache_dir, allow_network=True):
    """
    Every NSE-listed company -> (universe, stale). Tries, in order: today's
    fundamentals cache (written by the daily run), today's own name cache, a
    light live TradingView scan (cached for the day), then the most recent
    older cache (stale=True — fine for names, not for prices), and finally the
    built-in name list. Raises LookupUnavailable only if all of those fail.
    """
    today = _today()
    with _universe_lock:
        memo = _universe_memo.get(cache_dir)
        if memo and memo[0] == today and (not memo[2] or time.monotonic() - memo[3] < _STALE_RETRY):
            return memo[1], memo[2]

        universe, stale = None, False
        fund_path = os.path.join(cache_dir, f"fundamentals_{today}.json")
        own_path = os.path.join(cache_dir, f"nse_universe_{today}.json")
        for path in (fund_path, own_path):
            if universe is None and _fresh_today(path):
                try:
                    universe = (_from_fundamentals_file(path) if path == fund_path
                                else _read_json(path))
                except Exception as e:
                    logger.debug(f"NSE universe cache unreadable ({path}): {e}")
                    universe = None
        if universe is None and allow_network:
            try:
                universe = asyncio.run(asyncio.wait_for(_scan_names(), 30))
                if universe:
                    tmp = f"{own_path}.{os.getpid()}.tmp"
                    os.makedirs(cache_dir, exist_ok=True)
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(universe, f)
                    os.replace(tmp, own_path)
                else:
                    universe = None
            except Exception as e:
                logger.warning(f"Couldn't fetch the NSE company list from TradingView: {e}")
                universe = None
        if universe is None:
            older = sorted(glob.glob(os.path.join(cache_dir, "fundamentals_*.json"))
                           + glob.glob(os.path.join(cache_dir, "nse_universe_*.json")),
                           key=os.path.getmtime, reverse=True)
            for path in older:
                try:
                    universe = (_from_fundamentals_file(path) if "fundamentals_" in os.path.basename(path)
                                else _read_json(path))
                    stale = True
                    break
                except Exception:
                    continue
        if universe is None:
            from portfolio import SYMBOL_NAMES
            if not SYMBOL_NAMES:
                raise LookupUnavailable("Couldn't load the list of NSE companies.")
            universe = {s: {"name": n, "price": None, "change_pct": None, "sector": None}
                        for s, n in SYMBOL_NAMES.items()}
            stale = True
        _universe_memo[cache_dir] = (today, universe, stale, time.monotonic())
        return universe, stale


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def nse_symbols(cache_dir, allow_network=True):
    """Just the set of NSE tickers (empty set if even the fallbacks fail)."""
    try:
        return set(nse_universe(cache_dir, allow_network=allow_network)[0])
    except LookupUnavailable:
        return set()


# ----------------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------------
def _norm(s):
    return re.sub(r"[^a-z0-9& ]+", " ", (s or "").lower()).strip()


def _search_nse(q, universe):
    qu, ql = q.upper().strip(), _norm(q)
    scored = []
    for sym, info in universe.items():
        name = _norm(info.get("name"))
        words = name.split()
        if sym == qu:
            score = 100
        elif sym.startswith(qu):
            score = 80
        elif ql and any(w.startswith(ql) for w in words):
            score = 60
        elif ql and len(ql) >= 3 and ql in name:
            score = 40
        elif len(qu) >= 3 and difflib.SequenceMatcher(None, sym, qu).ratio() >= 0.75:
            score = 20
        else:
            continue
        scored.append((-score, sym))
    scored.sort()
    return [{"symbol": sym, "market": NSE, "name": universe[sym].get("name") or sym,
             "exchange": "NSE Kenya", "quote_type": "EQUITY", "currency": "KES",
             "price": universe[sym].get("price"), "change_pct": universe[sym].get("change_pct")}
            for _, sym in scored[:_MAX_RESULTS]]


_intl_cache = {}            # query -> (time, results)
_intl_lock = threading.Lock()


def _yahoo_search(q):
    """Raw Yahoo quotes for a query. Raises LookupUnavailable if Yahoo can't be reached."""
    try:
        import yfinance as yf
        return yf.Search(q, max_results=10, news_count=0, lists_count=0,
                         enable_fuzzy_query=True, timeout=8, raise_errors=True).quotes or []
    except Exception as e:
        raise LookupUnavailable(f"Couldn't reach Yahoo Finance ({type(e).__name__}).")


def _search_intl(q):
    key = q.lower().strip()
    with _intl_lock:
        hit = _intl_cache.get(key)
        if hit and time.time() - hit[0] < _SEARCH_TTL:
            return hit[1]
    out, seen = [], set()
    for quote in _yahoo_search(q):
        sym = (quote.get("symbol") or "").upper()
        qt = (quote.get("quoteType") or "").upper()
        if not sym or sym in seen or qt not in _INTL_TYPES or "^" in sym or "=" in sym:
            continue
        seen.add(sym)
        exchange = quote.get("exchDisp") or quote.get("exchange") or ""
        if exchange.upper() == "NSE":
            exchange = "NSE India"     # India's National Stock Exchange — not Nairobi's NSE
        out.append({"symbol": sym, "market": INTL,
                    "name": quote.get("longname") or quote.get("shortname") or sym,
                    "exchange": exchange,
                    "quote_type": qt, "currency": None, "price": None, "change_pct": None})
        if len(out) >= _MAX_RESULTS:
            break
    with _intl_lock:
        if len(_intl_cache) > 300:
            _intl_cache.clear()
        _intl_cache[key] = (time.time(), out)
    return out


def search(query, cache_dir, include_intl=True):
    """
    Search both markets. -> {"query", "results": [...], "intl_error", "nse_error", "nse_stale"}.
    NSE results come first and carry today's price; international results get
    their price when one is picked (see quote()).
    """
    q = (query or "").strip()[:40]
    out = {"query": q, "results": [], "intl_error": None, "nse_error": None, "nse_stale": False}
    if not q:
        return out
    try:
        universe, stale = nse_universe(cache_dir)
        out["results"].extend(_search_nse(q, universe))
        out["nse_stale"] = stale
    except LookupUnavailable as e:
        out["nse_error"] = str(e)
    if include_intl:
        try:
            out["results"].extend(_search_intl(q))
        except LookupUnavailable as e:
            out["intl_error"] = str(e)
    return out


# ----------------------------------------------------------------------------
# Quote — "does this ticker exist, and what's it called / priced at?"
# ----------------------------------------------------------------------------
def quote(symbol, market, cache_dir):
    """
    -> {"found": True, symbol, market, name, price, currency, exchange, quote_type, change_pct}
     | {"found": False, "reason": "not_found" | "not_a_stock", "message", "suggestions": [...]}
    Raises LookupUnavailable if the data source couldn't be reached.
    """
    symbol = (symbol or "").strip().upper()
    if market == NSE:
        universe, _stale = nse_universe(cache_dir)
        info = universe.get(symbol)
        if info:
            return {"found": True, "symbol": symbol, "market": NSE, "name": info.get("name") or symbol,
                    "price": info.get("price"), "currency": "KES", "exchange": "NSE Kenya",
                    "quote_type": "EQUITY", "change_pct": info.get("change_pct")}
        close = difflib.get_close_matches(symbol, list(universe), n=3, cutoff=0.6)
        by_name = _search_nse(symbol, universe) if not close else []
        suggestions = [{"symbol": s, "market": NSE, "name": universe[s].get("name") or s}
                       for s in close] or [{"symbol": r["symbol"], "market": NSE, "name": r["name"]}
                                           for r in by_name[:3]]
        hint = ("Did you mean " + " or ".join(f"{s['symbol']} ({s['name']})" for s in suggestions) + "?"
                if suggestions else "Search by the company's name instead.")
        return {"found": False, "reason": "not_found", "suggestions": suggestions,
                "message": f"'{symbol}' isn't a Nairobi Securities Exchange ticker. {hint}"}

    import international_data
    try:
        info = international_data.fetch_info(symbol)
    except Exception as e:
        raise LookupUnavailable(f"Couldn't reach Yahoo Finance ({type(e).__name__}).")
    price = (info or {}).get("regularMarketPrice") or (info or {}).get("currentPrice")
    if not info or len(info) < 3 or not price:
        # Yahoo answers an unknown ticker with a near-empty record. Make sure it
        # really answered (the search below raises if Yahoo is unreachable).
        suggestions = [{"symbol": r["symbol"], "market": INTL, "name": r["name"]}
                       for r in _search_intl(symbol)[:3]]
        hint = ("Did you mean " + " or ".join(f"{s['symbol']} ({s['name']})" for s in suggestions) + "?"
                if suggestions else "Search by the company's name instead.")
        return {"found": False, "reason": "not_found", "suggestions": suggestions,
                "message": f"Yahoo Finance doesn't have a price for '{symbol}'. {hint}"}
    qt = (info.get("quoteType") or "").upper()
    if qt not in _INTL_TYPES:
        return {"found": False, "reason": "not_a_stock", "suggestions": [],
                "message": f"{symbol} is a {qt.lower() or 'non-stock'} quote, not a stock or ETF."}
    try:
        # Save it as today's fundamentals so the watchlist refresh needn't re-download it.
        fund = international_data.fundamentals_from_info(symbol, info)
        if fund:
            international_data._save_json_cache(international_data._fund_cache_path(symbol, cache_dir), fund)
    except Exception:
        pass
    return {"found": True, "symbol": symbol, "market": INTL,
            "name": info.get("longName") or info.get("shortName") or symbol,
            "price": price, "currency": info.get("currency") or "USD",
            "exchange": info.get("fullExchangeName") or info.get("exchange") or "",
            "quote_type": qt, "change_pct": info.get("regularMarketChangePercent")}
