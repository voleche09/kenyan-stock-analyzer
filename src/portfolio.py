"""
Personal portfolio tracker — pure data layer.

Reads the user's PRIVATE holdings (portfolio/holdings.json, gitignored —
never committed) and computes live performance using the SAME verified data
the rest of the pipeline already produced this run: official-close-anchored
prices, TradingView fundamentals, the transparent factor score, and the
NSE dividend calendar. Nothing here fetches a second, different price — it
reuses exactly what's already been validated upstream, so the portfolio can
never disagree with the rest of the dashboard.

Design rules (money is involved — no room for error):
  - Only `symbol`, `quantity` and `buy_price` (cost) are ever stored by the
    user. Current price, market value, gain/loss — everything that changes
    day to day — is computed fresh on every run, never persisted as a stale
    number a user could mistake for "live".
  - A holding the pipeline has no data for TODAY is reported as missing —
    never silently dropped, never shown with a guessed price.
  - History (for daily/weekly/monthly/yearly performance) is only ever real,
    previously-recorded snapshots. If there isn't enough history yet for a
    given lookback window, that window says so plainly instead of
    interpolating or guessing.
  - No fabricated "sentiment". News headlines are shown neutral, dated and
    sourced — same discipline as market_pulse.py. The closest thing to
    "sentiment" offered is the real TradingView technical signal already
    computed for every stock elsewhere in this dashboard.
"""

import os
import re
import json
import datetime as dt

from logger import get_logger

logger = get_logger(__name__)

HOLDINGS_FILE = "holdings.json"
HISTORY_FILE = "history.json"

# Best-effort SYMBOL -> company name, used only to build better news search
# queries (e.g. "Safaricom" finds far more than "SCOM" would). Falls back to
# "<SYMBOL> NSE Kenya" for anything not listed here — still functional.
SYMBOL_NAMES = {
    "SCOM": "Safaricom", "EQTY": "Equity Group", "KCB": "KCB Group",
    "EABL": "East African Breweries", "COOP": "Co-operative Bank Kenya",
    "ABSA": "Absa Bank Kenya", "NCBA": "NCBA Group", "SCBK": "Standard Chartered Kenya",
    "IMH": "I&M Group", "KPLC": "Kenya Power", "KEGN": "KenGen",
    "HFCB": "HF Group", "SBIC": "Stanbic Holdings", "NSE": "Nairobi Securities Exchange",
    "BAT": "BAT Kenya", "BKG": "Bank of Kigali", "JUB": "Jubilee Holdings",
    "CIC": "CIC Insurance", "KQ": "Kenya Airways", "UMME": "Umeme",
    "BAMB": "Bamburi Cement", "CARB": "Carbacid", "TOTL": "TotalEnergies Kenya",
    "KAPC": "Kapchorua Tea", "SASN": "Sasini", "WTK": "Williamson Tea",
    "CTUM": "Centum Investment", "BRIT": "Britam", "SLAM": "Sanlam Kenya",
    "LBTY": "Liberty Kenya", "CGEN": "Car & General",
}


# ----------------------------------------------------------------------------
# Loading + saving holdings (the private lots file)
# ----------------------------------------------------------------------------

def _holdings_path(portfolio_dir):
    return os.path.join(portfolio_dir, HOLDINGS_FILE)


def load_holdings(portfolio_dir):
    """
    Return the list of lots: [{symbol, quantity, buy_price, buy_date, note}].
    [] if the file doesn't exist yet or can't be parsed — never raises, so a
    fresh clone (no private file) just renders an empty-state page.
    """
    path = _holdings_path(portfolio_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        lots = data.get("holdings", []) if isinstance(data, dict) else data
        out = []
        for lot in lots:
            try:
                sym = str(lot["symbol"]).strip().upper()
                qty = float(lot["quantity"])
                price = float(lot["buy_price"])
                if not sym or qty <= 0 or price <= 0:
                    continue
                out.append({
                    "symbol": sym, "quantity": qty, "buy_price": price,
                    "buy_date": lot.get("buy_date") or None,
                    "note": lot.get("note") or "",
                })
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"Portfolio: skipping malformed lot {lot!r}: {e}")
        return out
    except Exception as e:
        logger.warning(f"Portfolio: could not read {path}: {e}")
        return []


def add_lot(portfolio_dir, symbol, quantity, buy_price, buy_date=None, note=""):
    """
    Append one new lot to holdings.json (creating the file if needed).
    Returns the updated full lot list. Used by add_holding.py.
    """
    path = _holdings_path(portfolio_dir)
    os.makedirs(portfolio_dir, exist_ok=True)
    data = {"holdings": []}
    if os.path.exists(path):
        try:
            with open(path) as f:
                existing = json.load(f)
            if isinstance(existing, dict) and "holdings" in existing:
                data = existing
        except Exception as e:
            raise ValueError(f"Existing {path} is not valid JSON — fix or remove it first: {e}")

    data.setdefault("holdings", []).append({
        "symbol": symbol.strip().upper(),
        "quantity": float(quantity),
        "buy_price": float(buy_price),
        "buy_date": buy_date,
        "note": note or "",
    })
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return data["holdings"]


# ----------------------------------------------------------------------------
# Aggregation — combine multiple lots of the same symbol
# ----------------------------------------------------------------------------

def _aggregate_lots(lots):
    """symbol -> {quantity, cost_basis, avg_cost, buy_dates:[...], lots:[...]}"""
    agg = {}
    for lot in lots:
        sym = lot["symbol"]
        a = agg.setdefault(sym, {"quantity": 0.0, "cost_basis": 0.0, "buy_dates": [], "lots": []})
        a["quantity"] += lot["quantity"]
        a["cost_basis"] += lot["quantity"] * lot["buy_price"]
        if lot.get("buy_date"):
            a["buy_dates"].append(lot["buy_date"])
        a["lots"].append(lot)
    for sym, a in agg.items():
        a["avg_cost"] = a["cost_basis"] / a["quantity"] if a["quantity"] else 0.0
        a["earliest_buy_date"] = min(a["buy_dates"]) if a["buy_dates"] else None
    return agg


# ----------------------------------------------------------------------------
# Live computation — reuses this run's already-verified data, fetches nothing new
# ----------------------------------------------------------------------------

def compute_portfolio(lots, analysis_results, fundamentals_data, scores=None,
                      validations=None, as_of=None):
    """
    Build the full live portfolio picture from the user's lots plus the data
    this pipeline run already computed for every NSE stock.

    Returns None if there are no lots (nothing to show). Otherwise a dict:
        {as_of, holdings: [...], totals: {...}, sector_allocation: {...},
         best, worst, missing_symbols: [...]}

    A holding whose symbol has no data in analysis_results/fundamentals_data
    THIS run is included with data_available=False and excluded from the
    totals (rather than silently dropped or shown with a stale/guessed
    price) — its absence is surfaced, not hidden.
    """
    if not lots:
        return None

    scores = scores or {}
    validations = validations or {}
    as_of = as_of or dt.datetime.now().strftime("%Y-%m-%d")
    agg = _aggregate_lots(lots)

    try:
        from fundamental_analysis import FundamentalAnalysis
        signal_fn = FundamentalAnalysis.signal_from_tech_rating
    except Exception:
        signal_fn = lambda tr: ("—", "undefined")

    holdings = []
    missing = []
    for sym in sorted(agg.keys()):
        a = agg[sym]
        result = analysis_results.get(sym) if analysis_results else None
        fund = (fundamentals_data or {}).get(sym) or {}
        latest = (result or {}).get("latest", {})

        # Price: prefer the official-close-anchored analysis result (same
        # price shown everywhere else on the dashboard); fall back to the
        # fundamentals snapshot close if that's all we have this run.
        price = latest.get("close")
        if price is None:
            price = fund.get("close")

        data_available = price is not None
        if not data_available:
            missing.append(sym)

        qty = a["quantity"]
        cost_basis = a["cost_basis"]
        avg_cost = a["avg_cost"]
        market_value = qty * price if data_available else None
        gain = (market_value - cost_basis) if data_available else None
        gain_pct = (gain / cost_basis * 100.0) if (data_available and cost_basis) else None

        day_change_pct = result.get("daily_change_pct") if result else None
        day_change_value = (market_value * day_change_pct / 100.0
                            / (1 + day_change_pct / 100.0)) if (
            data_available and day_change_pct not in (None, -100)) else None
        # ^ back out yesterday's value from today's value + % change, so the
        # KES day-change is internally consistent with the displayed % (no
        # separate "yesterday's price" source that could disagree).

        dps = fund.get("dps_fy")
        est_annual_dividend = (qty * dps) if (data_available and dps) else None

        label, cls = signal_fn(fund.get("tech_rating"))
        sc = scores.get(sym, {})
        val = validations.get(sym, {})

        holdings.append({
            "symbol": sym,
            "quantity": qty,
            "avg_cost": round(avg_cost, 4),
            "cost_basis": round(cost_basis, 2),
            "earliest_buy_date": a["earliest_buy_date"],
            "n_lots": len(a["lots"]),
            "lots": a["lots"],
            "data_available": data_available,
            "price": round(price, 4) if data_available else None,
            "price_status": val.get("status"),
            "market_value": round(market_value, 2) if data_available else None,
            "gain": round(gain, 2) if gain is not None else None,
            "gain_pct": round(gain_pct, 2) if gain_pct is not None else None,
            "day_change_pct": round(day_change_pct, 2) if day_change_pct is not None else None,
            "day_change_value": round(day_change_value, 2) if day_change_value is not None else None,
            "sector": fund.get("sector") or "Other",
            "tv_signal_label": label,
            "tv_signal_class": cls,
            "score": sc.get("overall"),
            "score_detail": sc,
            "roe": fund.get("roe"),
            "pe_ratio": fund.get("pe_ratio"),
            "dividend_yield": fund.get("dividend_yield"),
            "dps_fy": dps,
            "dividend_status": fund.get("dividend_status"),
            "dividend_ex_date": fund.get("dividend_ex_date"),
            "dividend_ex_upcoming": fund.get("dividend_ex_date_is_upcoming"),
            "est_annual_dividend": round(est_annual_dividend, 2) if est_annual_dividend is not None else None,
            "rsi": latest.get("rsi"),
            "week52_low": fund.get("price_52w_low"),
            "week52_high": fund.get("price_52w_high"),
            "value_traded": fund.get("value_traded"),
            "report_file": None,  # filled in by report_generator if a detailed report exists
        })

    avail = [h for h in holdings if h["data_available"]]
    total_cost = sum(h["cost_basis"] for h in avail)
    total_value = sum(h["market_value"] for h in avail)
    total_gain = total_value - total_cost
    total_gain_pct = (total_gain / total_cost * 100.0) if total_cost else None
    total_day_value = sum(h["day_change_value"] for h in avail if h["day_change_value"] is not None)
    prev_total_value = total_value - total_day_value
    total_day_pct = (total_day_value / prev_total_value * 100.0) if prev_total_value else None
    total_est_dividend = sum(h["est_annual_dividend"] for h in avail if h["est_annual_dividend"] is not None)
    dividend_yield_on_cost = (total_est_dividend / total_cost * 100.0) if total_cost else None
    # Indicative total return if this year's declared dividend is paid in full
    # on top of today's unrealized price gain. An approximation, not a
    # certified record of dividends actually received — labelled as such
    # wherever it's shown. Summed from the ROUNDED gain_pct / dividend_yield
    # figures (not the raw floats) so it exactly equals what a user would get
    # adding the two numbers actually displayed elsewhere on the page.
    _gain_pct_r = round(total_gain_pct, 2) if total_gain_pct is not None else None
    _div_yield_r = round(dividend_yield_on_cost, 2) if dividend_yield_on_cost is not None else None
    total_return_incl_div_pct = (
        round((_gain_pct_r or 0) + (_div_yield_r or 0), 2)
        if _gain_pct_r is not None else None
    )

    sector_alloc = {}
    for h in avail:
        sector_alloc[h["sector"]] = sector_alloc.get(h["sector"], 0.0) + h["market_value"]

    ranked = sorted(avail, key=lambda h: h["gain_pct"] if h["gain_pct"] is not None else -1e9)
    best = ranked[-1] if ranked else None
    worst = ranked[0] if ranked else None

    return {
        "as_of": as_of,
        "holdings": holdings,
        "totals": {
            "cost_basis": round(total_cost, 2),
            "market_value": round(total_value, 2),
            "gain": round(total_gain, 2),
            "gain_pct": round(total_gain_pct, 2) if total_gain_pct is not None else None,
            "day_change_value": round(total_day_value, 2),
            "day_change_pct": round(total_day_pct, 2) if total_day_pct is not None else None,
            "est_annual_dividend": round(total_est_dividend, 2),
            "dividend_yield_on_cost": round(dividend_yield_on_cost, 2) if dividend_yield_on_cost is not None else None,
            "total_return_incl_div_pct": round(total_return_incl_div_pct, 2) if total_return_incl_div_pct is not None else None,
            "n_holdings": len(holdings),
            "n_available": len(avail),
        },
        "sector_allocation": sector_alloc,
        "best": {"symbol": best["symbol"], "gain_pct": best["gain_pct"]} if best else None,
        "worst": {"symbol": worst["symbol"], "gain_pct": worst["gain_pct"]} if worst else None,
        "missing_symbols": missing,
    }


# ----------------------------------------------------------------------------
# History — one real snapshot per day you run the tool (never interpolated)
# ----------------------------------------------------------------------------

class PortfolioHistoryTracker:
    """
    Appends one row per day to portfolio/history.json: the portfolio's total
    cost basis and market value on that day, plus a per-symbol breakdown.
    Idempotent per day (re-running today replaces today's row). This is the
    ONLY source for daily/weekly/monthly/yearly performance — every number
    it produces is a real recorded value, or explicitly "not enough history
    yet", never a guess.
    """

    def __init__(self, portfolio_dir):
        self.dir = portfolio_dir
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, HISTORY_FILE)

    def record_snapshot(self, portfolio_summary, date=None):
        if not portfolio_summary:
            return
        date = date or dt.datetime.now().strftime("%Y-%m-%d")
        rows = self._load_raw()
        rows = [r for r in rows if r.get("date") != date]  # replace today if present
        rows.append({
            "date": date,
            "cost_basis": portfolio_summary["totals"]["cost_basis"],
            "market_value": portfolio_summary["totals"]["market_value"],
            "gain_pct": portfolio_summary["totals"]["gain_pct"],
            "per_symbol": {
                h["symbol"]: h["market_value"] for h in portfolio_summary["holdings"]
                if h["data_available"]
            },
        })
        rows.sort(key=lambda r: r["date"])
        try:
            with open(self.path, "w") as f:
                json.dump(rows, f, indent=2)
            logger.info(f"Portfolio history: recorded {date} ({len(rows)} day(s) total)")
        except Exception as e:
            logger.warning(f"Portfolio history write failed: {e}")

    def _load_raw(self):
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Portfolio history read failed: {e}")
            return []

    def load_history(self):
        """Rows sorted oldest -> newest."""
        return sorted(self._load_raw(), key=lambda r: r["date"])

    def lookback(self, today_value, days_ago, tolerance_days=4):
        """
        Compare today_value (live, computed just now) against the closest
        recorded snapshot at or before (today - days_ago). Returns
        {from_date, from_value, pct_change} or None if no snapshot exists
        within [target - tolerance_days, target] — i.e. we genuinely don't
        have history that far back yet, so we say nothing rather than guess.
        """
        rows = self.load_history()
        if not rows:
            return None
        today = dt.datetime.now().date()
        target = today - dt.timedelta(days=days_ago)
        window_start = target - dt.timedelta(days=tolerance_days)

        best = None
        for r in rows:
            try:
                rd = dt.datetime.strptime(r["date"], "%Y-%m-%d").date()
            except (ValueError, KeyError):
                continue
            if window_start <= rd <= target:
                if best is None or rd > best[0]:
                    best = (rd, r["market_value"])
        if best is None or not best[1]:
            return None
        from_date, from_value = best
        pct = (today_value - from_value) / from_value * 100.0
        return {
            "from_date": from_date.isoformat(),
            "from_value": round(from_value, 2),
            "to_value": round(today_value, 2),
            "change_value": round(today_value - from_value, 2),
            "pct_change": round(pct, 2),
        }

    def days_recorded(self):
        return len(self.load_history())

    def first_date(self):
        rows = self.load_history()
        return rows[0]["date"] if rows else None


# ----------------------------------------------------------------------------
# News for the symbols actually held — same honest, no-sentiment-tagging
# discipline as market_pulse.py (reuses its exact RSS mechanism).
# ----------------------------------------------------------------------------

def fetch_portfolio_news(symbols, max_items_per_symbol=4):
    """
    Return [{symbol, title, url, source, published_utc}], newest first.
    Deliberately NOT sentiment-tagged — see module docstring. Fails safe to
    [] if the news source is unreachable.
    """
    if not symbols:
        return []
    try:
        import requests
        from html import unescape
        from utils import http_get
    except Exception as e:
        logger.warning(f"Portfolio news: setup failed: {e}")
        return []

    _UA = {"User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )}
    out = []
    for sym in symbols:
        name = SYMBOL_NAMES.get(sym, f"{sym} NSE Kenya")
        query = f'"{name}" Kenya'
        try:
            url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-KE&gl=KE&ceid=KE:en"
            r = http_get(url, headers=_UA)
            if r is None or r.status_code != 200:
                continue
            items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
            for raw in items[:max_items_per_symbol]:
                title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", raw, re.S)
                link_m = re.search(r"<link>(.*?)</link>", raw, re.S)
                date_m = re.search(r"<pubDate>(.*?)</pubDate>", raw, re.S)
                src_m = re.search(r"<source[^>]*>(.*?)</source>", raw, re.S)
                if not (title_m and link_m and date_m):
                    continue
                title = unescape((title_m.group(1) or title_m.group(2) or "").strip())
                title = re.sub(r"\s*-\s*[^-]+$", "", title).strip()
                out.append({
                    "symbol": sym,
                    "title": title,
                    "url": link_m.group(1).strip(),
                    "source": unescape(src_m.group(1).strip()) if src_m else "",
                    "published_utc": date_m.group(1).strip(),
                })
        except Exception as e:
            logger.debug(f"Portfolio news failed for {sym}: {e}")

    def _parse(x):
        try:
            return dt.datetime.strptime(x["published_utc"], "%a, %d %b %Y %H:%M:%S %Z")
        except Exception:
            try:
                return dt.datetime.strptime(x["published_utc"][:25], "%a, %d %b %Y %H:%M:%S")
            except Exception:
                return dt.datetime.min
    out.sort(key=_parse, reverse=True)
    logger.info(f"Portfolio news: {len(out)} headlines across {len(symbols)} holding(s)")
    return out


# ---- Smoke test ----
if __name__ == "__main__":
    from logger import setup_logging
    setup_logging()
    lots = load_holdings("../portfolio")
    print(f"{len(lots)} lot(s) loaded")
    fake_results = {l["symbol"]: {"latest": {"close": l["buy_price"] * 1.1, "rsi": 55},
                                  "daily_change_pct": 1.0} for l in lots}
    fake_fund = {l["symbol"]: {"sector": "Finance", "roe": 18.0, "dividend_yield": 5.0,
                               "dps_fy": 2.0, "tech_rating": 0.3} for l in lots}
    p = compute_portfolio(lots, fake_results, fake_fund)
    if p:
        print(json.dumps(p["totals"], indent=2))
