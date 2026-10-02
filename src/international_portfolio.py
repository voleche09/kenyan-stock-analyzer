"""
Personal INTERNATIONAL (US-listed, USD) stock portfolio tracker — pure data
layer. Sibling to portfolio.py (NSE stocks) and bonds_portfolio.py (Kenya
bonds); same private-by-default discipline as both (see portfolio/README.md
and the portfolio-privacy-rule memory: real holdings are NEVER committed).

Reads the user's PRIVATE holdings (portfolio/international_holdings.csv, or the older .json,
gitignored) and computes live performance using data this pipeline run
already fetched via international_data.py (Yahoo Finance) and already ran
through the SAME AnalysisEngine/scoring used for NSE stocks — nothing here
fetches a second, contradicting price.

Design rules — same as portfolio.py (money is involved, no room for error):
  - Only symbol/quantity/buy_price(USD)/buy_date/note are ever persisted.
    Company name, sector, price, everything else is fetched fresh every run.
  - A holding with no data THIS run is reported as missing, never dropped or
    guessed.
  - KES figures are only ever shown when a real USD/KES rate was fetched
    this run (market_context.fetch_usd_kes); when it wasn't, KES fields are
    None and the render layer says so plainly rather than reusing a stale or
    invented rate.
  - No fabricated "sentiment". The closest thing offered is Wall Street's
    own real analyst consensus (recommendationKey + price targets), clearly
    labeled as sourced, third-party opinion — not personalized advice.
"""

import os
import json
import datetime as dt

from logger import get_logger
import portfolio_csv

logger = get_logger(__name__)

HOLDINGS_FILE = "international_holdings.json"
HOLDINGS_CSV_FILE = "international_holdings.csv"
HISTORY_FILE = "international_history.json"

# Wall Street analyst consensus -> (label, css class). Reuses the SAME
# badge classes ('bullish'/'bearish'/'neutral'/'undefined') already styled
# in _dashboard_css()/base.html for the NSE "TV Signal" badge, so no new CSS
# is needed.
_RECOMMENDATION_LABELS = {
    'strong_buy': ('Strong Buy', 'bullish'),
    'buy': ('Buy', 'bullish'),
    'hold': ('Hold', 'neutral'),
    'sell': ('Sell', 'bearish'),
    'strong_sell': ('Strong Sell', 'bearish'),
}


def signal_from_recommendation(key):
    """Map yfinance's recommendationKey to the same (label, css_class)
    shape as fundamental_analysis.signal_from_tech_rating, so it renders
    with the existing badge CSS unchanged."""
    return _RECOMMENDATION_LABELS.get((key or '').lower(), ('—', 'undefined'))


# ----------------------------------------------------------------------------
# Loading + saving holdings (the private lots file) — identical shape/logic
# to portfolio.py's, just USD instead of KES and no NSE-ticker regex.
# ----------------------------------------------------------------------------
def _holdings_path(portfolio_dir):
    return os.path.join(portfolio_dir, HOLDINGS_FILE)


def _load_holdings_csv(path):
    """international_holdings.csv -> lots (same shape as the JSON path)."""
    name = os.path.basename(path)
    try:
        rows = portfolio_csv.read_rows(path, portfolio_csv.STOCK_LOT_FIELDS)
    except portfolio_csv.CsvFormatError as e:
        logger.warning(f"Intl portfolio: {e} — no holdings loaded from this file.")
        return []
    out = []
    for line_no, r in rows:
        qty, price = r["quantity"], r["buy_price"]
        if qty <= 0 or price <= 0:
            logger.warning(f"Intl portfolio: {name} line {line_no}: quantity and buy_price must "
                           f"be above zero — skipping this row")
            continue
        out.append({
            "symbol": r["symbol"].strip().upper(), "quantity": qty, "buy_price": price,
            "buy_date": r.get("buy_date"), "note": r.get("note") or "",
        })
    logger.info(f"Intl portfolio: loaded {len(out)} lot(s) from {name}")
    return out


def load_holdings(portfolio_dir):
    """Return [{symbol, quantity, buy_price, buy_date, note}]. Reads
    international_holdings.csv if it exists, otherwise the .json (see
    portfolio_csv.pick_source). [] if neither exists or the file can't be
    parsed — never raises."""
    kind, path = portfolio_csv.pick_source(portfolio_dir, HOLDINGS_CSV_FILE, HOLDINGS_FILE)
    if kind is None:
        return []
    if kind == "csv":
        return _load_holdings_csv(path)
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
                logger.warning(f"Intl portfolio: skipping malformed lot {lot!r}: {e}")
        return out
    except Exception as e:
        logger.warning(f"Intl portfolio: could not read {path}: {e}")
        return []


def add_lot(portfolio_dir, symbol, quantity, buy_price, buy_date=None, note=""):
    """Append one new lot and return the updated full lot list. Used by
    add_international_holding.py. Goes to international_holdings.csv if it
    exists (added in the file's own column layout), otherwise the .json
    (created if needed) — never to a JSON file a CSV is shadowing."""
    csv_path = os.path.join(portfolio_dir, HOLDINGS_CSV_FILE)
    if os.path.exists(csv_path):
        dropped = portfolio_csv.append_row(csv_path, portfolio_csv.STOCK_LOT_FIELDS, {
            "symbol": symbol.strip().upper(), "quantity": float(quantity),
            "buy_price": float(buy_price), "buy_date": buy_date, "note": note or "",
        })
        if dropped:
            logger.warning(f"{HOLDINGS_CSV_FILE} has no column for {', '.join(dropped)}, so that "
                           f"value was not saved — add the column to its header row to keep it.")
        return load_holdings(portfolio_dir)
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
# Live computation — reuses this run's already-fetched data, fetches nothing new
# ----------------------------------------------------------------------------
def compute_international_portfolio(lots, analysis_results, fundamentals_data,
                                     scores=None, usd_kes=None, as_of=None):
    """
    Build the full live picture from the user's lots plus this run's already
    -fetched international_data.py results. Returns None if there are no
    lots. Otherwise a dict: {as_of, holdings, totals, allocation, best,
    worst, missing_symbols, fx_rate, fx_as_of}.

    fx_rate (from market_context.fetch_usd_kes(), fetched once earlier in
    this same run) is used ONLY to add KES-equivalent figures alongside the
    real USD ones — never to replace them. If fx is unavailable, every KES
    field is None and totals['fx_available'] is False, so the render layer
    can say so plainly instead of guessing a rate.
    """
    if not lots:
        return None

    scores = scores or {}
    as_of = as_of or dt.datetime.now().strftime("%Y-%m-%d")
    agg = _aggregate_lots(lots)
    fx_rate = usd_kes.get('rate') if usd_kes else None

    def to_kes(usd_value):
        return round(usd_value * fx_rate, 2) if (usd_value is not None and fx_rate) else None

    holdings = []
    missing = []
    for sym in sorted(agg.keys()):
        a = agg[sym]
        result = analysis_results.get(sym) if analysis_results else None
        fund = (fundamentals_data or {}).get(sym) or {}
        latest = (result or {}).get("latest", {})

        price = latest.get("close")
        if price is None:
            price = fund.get("price")

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

        div_rate = fund.get('dividend_rate')
        est_annual_dividend = (qty * div_rate) if (data_available and div_rate) else None

        label, cls = signal_from_recommendation(fund.get('recommendation_key'))
        sc = scores.get(sym, {})

        target_mean = fund.get('target_mean_price')
        upside_pct = ((target_mean - price) / price * 100.0) if (target_mean and data_available and price) else None

        holdings.append({
            "symbol": sym,
            "name": fund.get('name') or sym,
            "website": fund.get('website'),
            "quote_type": fund.get('quote_type'),
            "quantity": qty,
            "avg_cost": round(avg_cost, 4),
            "cost_basis": round(cost_basis, 2),
            "cost_basis_kes": to_kes(cost_basis),
            "earliest_buy_date": a["earliest_buy_date"],
            "n_lots": len(a["lots"]),
            "lots": a["lots"],
            "data_available": data_available,
            "price": round(price, 4) if data_available else None,
            "market_value": round(market_value, 2) if data_available else None,
            "market_value_kes": to_kes(market_value),
            "gain": round(gain, 2) if gain is not None else None,
            "gain_kes": to_kes(gain),
            "gain_pct": round(gain_pct, 2) if gain_pct is not None else None,
            "day_change_pct": round(day_change_pct, 2) if day_change_pct is not None else None,
            "day_change_value": round(day_change_value, 2) if day_change_value is not None else None,
            "sector": fund.get("sector") or ("ETF" if fund.get('quote_type') == 'ETF' else "Other"),
            "industry": fund.get("industry"),
            "recommendation_label": label,
            "recommendation_class": cls,
            "num_analysts": fund.get('num_analysts'),
            "target_mean_price": target_mean,
            "target_upside_pct": round(upside_pct, 1) if upside_pct is not None else None,
            "score": sc.get("overall"),
            "score_detail": sc,
            "pe_ratio": fund.get("pe_ratio"),
            "roe": fund.get("roe"),
            "beta": fund.get("beta"),
            "dividend_yield": fund.get("dividend_yield"),
            "dividend_rate": div_rate,
            "est_annual_dividend": round(est_annual_dividend, 2) if est_annual_dividend is not None else None,
            "rsi": latest.get("rsi"),
            "week52_low": fund.get("week52_low"),
            "week52_high": fund.get("week52_high"),
            "market_cap": fund.get("market_cap"),
            "report_file": None,  # filled in by report_generator once the per-stock page is written
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
            "cost_basis_kes": to_kes(total_cost),
            "market_value": round(total_value, 2),
            "market_value_kes": to_kes(total_value),
            "gain": round(total_gain, 2),
            "gain_kes": to_kes(total_gain),
            "gain_pct": round(total_gain_pct, 2) if total_gain_pct is not None else None,
            "day_change_value": round(total_day_value, 2),
            "day_change_pct": round(total_day_pct, 2) if total_day_pct is not None else None,
            "est_annual_dividend": round(total_est_dividend, 2),
            "dividend_yield_on_cost": round(dividend_yield_on_cost, 2) if dividend_yield_on_cost is not None else None,
            "n_holdings": len(holdings),
            "n_available": len(avail),
            "fx_available": fx_rate is not None,
        },
        "sector_allocation": sector_alloc,
        "best": {"symbol": best["symbol"], "gain_pct": best["gain_pct"]} if best else None,
        "worst": {"symbol": worst["symbol"], "gain_pct": worst["gain_pct"]} if worst else None,
        "missing_symbols": missing,
        "fx_rate": fx_rate,
        "fx_as_of": usd_kes.get('updated') if usd_kes else None,
    }


# ----------------------------------------------------------------------------
# News for the symbols actually held — thin wrapper around
# international_data.fetch_news, same output shape as
# portfolio.fetch_portfolio_news so the existing news-card rendering works.
# ----------------------------------------------------------------------------
def fetch_international_news(symbols, fundamentals_data=None, max_items_per_symbol=4, max_age_days=7):
    if not symbols:
        return []
    fundamentals_data = fundamentals_data or {}
    from international_data import fetch_news
    out = []
    for sym in symbols:
        name = (fundamentals_data.get(sym) or {}).get('name')
        try:
            out.extend(fetch_news(sym, company_name=name,
                                  max_items_per_symbol=max_items_per_symbol,
                                  max_age_days=max_age_days))
        except Exception as e:
            logger.debug(f"Intl portfolio news failed for {sym}: {e}")
    # Sort newest-first where a parseable date exists; undated items sink to the end.
    def _key(n):
        try:
            return dt.datetime.strptime(n['published_utc'][:25], "%a, %d %b %Y %H:%M:%S")
        except (ValueError, TypeError, KeyError):
            return dt.datetime.min
    out.sort(key=_key, reverse=True)
    logger.info(f"Intl portfolio news: {len(out)} headline(s) across {len(symbols)} holding(s)")
    return out


# ---- Smoke test ----
if __name__ == "__main__":
    from logger import setup_logging
    setup_logging()
    lots = load_holdings("../portfolio")
    print(f"{len(lots)} lot(s) loaded")
    if lots:
        from international_data import fetch_history, fetch_fundamentals
        from analysis_engine import AnalysisEngine
        engine = AnalysisEngine()
        syms = sorted({l['symbol'] for l in lots})
        results, funds = {}, {}
        for s in syms:
            h = fetch_history(s, period='3mo')
            if h is not None:
                results[s] = engine.analyze_stock(h)
            funds[s] = fetch_fundamentals(s)
        p = compute_international_portfolio(lots, results, funds, usd_kes={'rate': 129.5, 'updated': 'test'})
        if p:
            print(json.dumps(p["totals"], indent=2))
