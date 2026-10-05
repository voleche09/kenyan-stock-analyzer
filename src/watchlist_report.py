"""
⭐ Watchlist page — gathers today's data for every stock on your watchlist
(portfolio/watchlist.csv) and writes reports/watchlist.html, plus a full
per-stock page for any watched stock that doesn't have one yet.

Two ways in:

  generate_watchlist_page(...)  — called near the end of a full main.py run,
      reusing everything that run already fetched (NSE prices, fundamentals,
      price checks, scores, international holdings data, FX).

  refresh_watchlist_page(config) — `main.py --watchlist-page-only`, which is
      what the dashboard app runs right after you add, edit or remove a stock.
      Fetches only the watchlist's own stocks (mostly straight from today's
      caches) and rewrites just this page — never the other pages.

The decisions about what to show (signals, targets, attention items) live in
watchlist.py as pure functions; this module only fetches and renders.
"""

import concurrent.futures
import datetime as dt
import os
import re
from html import escape

from markupsafe import Markup

import ui_kit as ui
import watchlist as wl
from logger import get_logger

logger = get_logger(__name__)

PAGE_FILE = "watchlist.html"
NEWS_TTL_SECONDS = 3600


def _e(value):
    return escape("" if value is None else str(value), quote=True)


def _dt_today():
    return dt.date.today()


# ----------------------------------------------------------------------------
# Data collection
# ----------------------------------------------------------------------------
def _short_company_name(name):
    """'Africa Mega Agricorp PLC' -> 'Africa Mega Agricorp' (a better news query)."""
    words = (name or "").replace(",", " ").split()
    junk = {"PLC", "PLC.", "LTD", "LTD.", "LIMITED", "CO.", "CO", "COMPANY", "CORPORATION", "INC", "INC."}
    while words and words[-1].upper() in junk:
        words.pop()
    return " ".join(words) or name


def _holdings_by_symbol(config, preloaded):
    """{(market, symbol): {'quantity', 'avg_cost'}} from the private holdings files."""
    out = {}
    sources = ((wl.MARKET_NSE, "portfolio", "nse_lots"),
               (wl.MARKET_INTL, "international_portfolio", "intl_lots"))
    for market, module_name, key in sources:
        lots = preloaded.get(key)
        if lots is None:
            try:
                module = __import__(module_name)
                lots = module.load_holdings(config.portfolio_dir)
            except Exception as e:
                logger.debug(f"Watchlist: couldn't read {module_name} holdings: {e}")
                lots = []
        for lot in lots or []:
            try:
                k = (market, lot["symbol"].upper())
                agg = out.setdefault(k, {"quantity": 0.0, "cost": 0.0})
                agg["quantity"] += float(lot["quantity"])
                agg["cost"] += float(lot["quantity"]) * float(lot["buy_price"])
            except (KeyError, TypeError, ValueError):
                continue
    for agg in out.values():
        agg["avg_cost"] = agg["cost"] / agg["quantity"] if agg["quantity"] else 0.0
    return out


def _news_cache_path(cache_dir, market, symbol):
    import international_data
    key = international_data._cache_key(symbol)[len(international_data.CACHE_PREFIX):]
    return os.path.join(cache_dir, f"wl_news_{market}_{key}.json")


def _fetch_news(config, market, symbol, name):
    import international_data
    path = _news_cache_path(config.cache_dir, market, symbol)
    cached = international_data._load_json_cache(path, max_age_seconds=NEWS_TTL_SECONDS)
    if cached is not None:
        return cached
    try:
        if market == wl.MARKET_NSE:
            from portfolio import fetch_portfolio_news
            items = fetch_portfolio_news([symbol], max_items_per_symbol=3,
                                         names={symbol: _short_company_name(name)})
        else:
            items = international_data.fetch_news(symbol, name, max_items_per_symbol=3)
    except Exception as e:
        logger.debug(f"Watchlist news failed for {symbol}: {e}")
        items = []
    international_data._save_json_cache(path, items)
    return items


def _existing_detail_page(output_dir, prefix):
    """Today's newest per-stock page with this file prefix, if one exists."""
    today = dt.date.today()
    found = []
    try:
        for f in os.listdir(output_dir):
            if f.startswith(prefix) and f.endswith(".html"):
                if dt.date.fromtimestamp(os.path.getmtime(os.path.join(output_dir, f))) == today:
                    found.append(f)
    except OSError:
        return None
    return sorted(found)[-1] if found else None


def collect(config, entries, analysis_engine, report_gen, preloaded=None, period="6mo",
            interval="1d", force_refresh=False, fetch_news=True):
    """
    Fetch + analyse everything the page needs for `entries`. Returns
    (rows, ctx) — one row per entry: {entry, result, fund, score, validation,
    holding, news, detail_file}; ctx: {sector_medians, usd_kes, data_date}.
    """
    preloaded = preloaded or {}
    nse_entries = [e for e in entries if e["market"] == wl.MARKET_NSE]
    intl_entries = [e for e in entries if e["market"] == wl.MARKET_INTL]

    # ---- NSE: fundamentals for the whole market (one cached scan) ----
    fundamentals = preloaded.get("fundamentals")
    fund_analyzer = preloaded.get("fund_analyzer")
    if nse_entries and fundamentals is None:
        try:
            from fundamental_analysis import FundamentalAnalysis
            fund_analyzer = fund_analyzer or FundamentalAnalysis(cache_dir=config.cache_dir)
            fundamentals = fund_analyzer.fetch_all_fundamentals(force_refresh=force_refresh)
            try:
                from dividend_calendar import apply_dividend_calendar
                apply_dividend_calendar(fundamentals, cache_dir=config.cache_dir, logger=logger)
            except Exception as e:
                logger.debug(f"Watchlist: dividend calendar skipped: {e}")
        except Exception as e:
            logger.warning(f"Watchlist: NSE fundamentals unavailable: {e}")
            fundamentals = {}
    fundamentals = fundamentals or {}

    sector_medians = preloaded.get("sector_medians")
    if sector_medians is None and fundamentals:
        try:
            from market_context import compute_sector_medians
            sector_medians = compute_sector_medians(fundamentals)
        except Exception:
            sector_medians = {}
    usd_kes = preloaded.get("usd_kes")
    if usd_kes is None and "usd_kes" not in preloaded and config.enable_fx:
        try:
            from market_context import fetch_usd_kes
            usd_kes = fetch_usd_kes()
        except Exception:
            usd_kes = None

    # ---- NSE: price history (reuse the full run's analysis where we have it) ----
    nse_results = dict(preloaded.get("nse_results") or {})
    validations = dict(preloaded.get("validations") or {})
    known_nse = set(fundamentals) or set(preloaded.get("nse_universe") or ())
    fresh, frames = {}, {}
    missing = [e["symbol"] for e in nse_entries if not nse_results.get(e["symbol"])]
    if missing:
        from data_acquisition import DataAcquisition
        data_acq = preloaded.get("data_acq") or DataAcquisition(
            data_sources=config.data_sources, cache_dir=config.cache_dir)
        for sym in missing:
            if known_nse and sym not in known_nse:
                logger.warning(f"Watchlist: {sym} is not an NSE ticker — no data fetched")
                continue
            try:
                df = data_acq.fetch_stock_data(sym, period=period, interval=interval,
                                               force_refresh=force_refresh)
            except Exception as e:
                logger.warning(f"Watchlist: {sym} price history failed: {e}")
                df = None
            if df is not None and not df.empty:
                frames[sym] = df
                fresh[sym] = analysis_engine.analyze_stock(df)
        if fresh and (config.enable_price_validation or config.enable_official_close):
            try:
                from price_validation import PriceValidator, apply_official_close
                pv = PriceValidator(cache_dir=config.cache_dir,
                                    disagree_threshold_pct=config.price_disagree_threshold_pct)
                # Only the official prices the last full run saved today — never a
                # live fetch here: that source can take ~25 s to time out when it's
                # down, and this page is rebuilt every time you add a stock.
                reference = pv.load_cached_reference()
                if config.enable_price_validation:
                    for sym, res in fresh.items():
                        validations[sym] = pv.validate(sym, res.get("latest", {}).get("close"), frames.get(sym))
                if config.enable_official_close:
                    apply_official_close(fresh, reference)
            except Exception as e:
                logger.warning(f"Watchlist: official-close check skipped: {e}")
        nse_results.update(fresh)

    # ---- International: history + fundamentals (+ news), a few at a time ----
    intl_results = dict(preloaded.get("intl_results") or {})
    intl_funds = dict(preloaded.get("intl_fundamentals") or {})
    import international_data

    def fetch_intl(sym):
        res, fund = intl_results.get(sym), intl_funds.get(sym)
        if not res:
            hist = international_data.fetch_history(sym, period=period, interval=interval,
                                                    cache_dir=config.cache_dir, force_refresh=force_refresh)
            res = analysis_engine.analyze_stock(hist) if hist is not None else None
        if not fund:
            fund = international_data.fetch_fundamentals(sym, cache_dir=config.cache_dir,
                                                         force_refresh=force_refresh)
        return sym, res, fund

    news = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch_intl, e["symbol"]) for e in intl_entries]
        for fut in futures:
            try:
                sym, res, fund = fut.result()
                intl_results[sym], intl_funds[sym] = res, fund
            except Exception as e:
                logger.warning(f"Watchlist: international fetch failed: {e}")
        if fetch_news:
            def name_of(e):
                f = fundamentals.get(e["symbol"]) if e["market"] == wl.MARKET_NSE else intl_funds.get(e["symbol"])
                return (f or {}).get("name") or e["symbol"]
            news_futs = {(e["market"], e["symbol"]): pool.submit(_fetch_news, config, e["market"],
                                                                 e["symbol"], name_of(e))
                         for e in entries}
            for k, fut in news_futs.items():
                try:
                    news[k] = fut.result()
                except Exception:
                    news[k] = []

    # ---- Scores ----
    scores = dict(preloaded.get("scores") or {})
    intl_scores = dict(preloaded.get("intl_scores") or {})
    if config.enable_scoring:
        try:
            from scoring import score_stock
            for e in entries:
                sym = e["symbol"]
                if e["market"] == wl.MARKET_NSE and sym not in scores and nse_results.get(sym):
                    scores[sym] = score_stock(sym, nse_results[sym], fundamentals.get(sym, {}))
                elif e["market"] == wl.MARKET_INTL and sym not in intl_scores and intl_results.get(sym):
                    intl_scores[sym] = score_stock(sym, intl_results[sym], intl_funds.get(sym) or {})
        except Exception as e:
            logger.warning(f"Watchlist: scoring skipped: {e}")

    # ---- Full per-stock pages (reuse today's, else build one) ----
    report_files = preloaded.get("report_files") or {}
    intl_report_files = preloaded.get("intl_report_files") or {}
    rows = []
    holdings = _holdings_by_symbol(config, preloaded)
    for e in entries:
        sym, market = e["symbol"], e["market"]
        if market == wl.MARKET_NSE:
            res, fund = nse_results.get(sym), fundamentals.get(sym, {})
            score, validation = scores.get(sym), validations.get(sym)
            detail = report_files.get(sym) or _existing_detail_page(report_gen.output_dir, f"{sym}_report_")
            if not detail and res:
                try:
                    from scoring import generate_alerts
                    similar = fund_analyzer.find_similar_stocks(sym, fundamentals) if fund_analyzer else None
                    peers = fund_analyzer.get_sector_peers(sym, fundamentals) if fund_analyzer else None
                    path = report_gen.generate_stock_report(
                        sym, res, report_type="html", fundamentals=fund, similar_stocks=similar,
                        sector_peers=peers, validation=validation, score=score,
                        alerts=generate_alerts(sym, res, fund, validation),
                        sector_medians=sector_medians, usd_kes=usd_kes)
                    detail = os.path.basename(path) if path else None
                except Exception as ex:
                    logger.warning(f"Watchlist: full page for {sym} failed: {ex}")
        else:
            res, fund = intl_results.get(sym), intl_funds.get(sym) or {}
            score, validation = intl_scores.get(sym), None
            detail = intl_report_files.get(sym) or _existing_detail_page(report_gen.output_dir, f"intl_{sym}_report_")
            if not detail and res:
                try:
                    path = report_gen.generate_international_stock_report(
                        sym, res, report_type="html", fundamentals=fund, score=score,
                        dividend_history=international_data.fetch_dividend_history(sym),
                        earnings_calendar=international_data.fetch_earnings_calendar(sym),
                        news=news.get((market, sym)) or [], usd_kes=usd_kes, context="watchlist")
                    detail = os.path.basename(path) if path else None
                except Exception as ex:
                    logger.warning(f"Watchlist: full page for {sym} failed: {ex}")
        rows.append({"entry": e, "result": res, "fund": fund, "score": score,
                     "validation": validation, "holding": holdings.get((market, sym)),
                     "news": news.get((market, sym)) or [], "detail_file": detail})

    data_date = next((f.get("_data_date") for f in fundamentals.values() if f and f.get("_data_date")), None)
    return rows, {"sector_medians": sector_medians or {}, "usd_kes": usd_kes,
                  "data_date": data_date or dt.date.today().isoformat()}


# ----------------------------------------------------------------------------
# Chart — one compact 3-panel picture per stock
# ----------------------------------------------------------------------------
def make_charts(view, data):
    """The card's charts as inline SVG: a 6-month sparkline for the compact
    card, and — for the opened card — the price chart (20/50-day averages,
    normal range, YOUR buy/sell prices and the 52-week high/low) with volume
    and RSI underneath. Returns {"spark": Markup, "chart": Markup}; a far-away
    target is listed under the chart instead of squashing the line."""
    import svg_charts as sc
    from markupsafe import Markup
    if data is None or getattr(data, "empty", True) or "close" not in data:
        return {"spark": sc.sparkline([], label=view["symbol"]),
                "chart": Markup('<p class="wl-nodata">Not enough price history for a chart yet.</p>')}
    cur = view["facts"]["currency"]
    money = sc.Fmt(_money_prefix(cur), 2, suffix=_money_suffix(cur))
    refs = []
    if view["buy_below"]:
        refs.append((view["buy_below"], "buy", f"Your buy price {wl.fmt_money(view['buy_below'], cur)}"))
    if view["sell_above"]:
        refs.append((view["sell_above"], "sell", f"Your sell price {wl.fmt_money(view['sell_above'], cur)}"))
    for value, label in ((view["facts"]["week52_high"], "52-week high"), (view["facts"]["week52_low"], "52-week low")):
        if value:
            refs.append((value, "level", f"{label} {wl.fmt_money(value, cur)}"))
    cid = _anchor(view).replace(".", "-")
    closes = list(data["close"])
    spark = sc.sparkline(closes[-126:], label=f"{view['symbol']}, last 6 months", fmt=money,
                         refs=[(view["buy_below"], "buy"), (view["sell_above"], "sell")])
    price = sc.price_chart(f"c-{cid}", data, money=money, refs=refs, title=f"{view['symbol']} price", height=260)
    small = sc.indicator_charts(f"c-{cid}", data, money=money, x_ref=f"c-{cid}")
    chart = Markup(f'{price}<div class="grid-2 wl-small-charts">{small["volume"]}{small["rsi"]}</div>')
    return {"spark": spark, "chart": chart}


def _money_prefix(cur):
    return {"KES": "KES ", "USD": "$", "GBP": "£", "EUR": "€"}.get(cur, "")


def _money_suffix(cur):
    return "" if cur in ("KES", "USD", "GBP", "EUR") else f" {cur}"


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
_LEAN_ICON = {"buy": "🟢", "sell": "🔴", "neutral": "⚪", "na": "▫️"}
_SUMMARY_CHIP = {"positive": "chip-buy", "negative": "chip-sell", "mixed": "chip-neutral", "none": "chip-none"}
_MARKET_LABEL = {wl.MARKET_NSE: "🇰🇪 NSE", wl.MARKET_INTL: "🌍 International"}


def _anchor(view):
    return "wl-" + re.sub(r"[^A-Za-z0-9_.-]", "_", f"{view['symbol']}-{view['market']}")


def _pct_class(v):
    return "" if v is None else ("positive" if v >= 0 else "negative")


def _target_chip(view):
    t, cur = view["target"], view["facts"]["currency"]
    if t["status"] == "buy_zone":
        return "chip-buy", "🎯 In your buy zone"
    if t["status"] == "sell_zone":
        return "chip-sell", "💰 At your sell price"
    if t["status"] == "between":
        bits = []
        if view["buy_below"]:
            bits.append(f"buy ≤ {wl.fmt_money(view['buy_below'], cur)}")
        if view["sell_above"]:
            bits.append(f"sell ≥ {wl.fmt_money(view['sell_above'], cur)}")
        return "chip-none", " · ".join(bits)
    return "chip-none", "No targets set"


def _market_badge(view):
    label = _MARKET_LABEL[view["market"]]
    exch = view["facts"].get("exchange")
    if view["market"] == wl.MARKET_INTL and exch:
        label = f"🌍 {exch}"
    return f'<span class="wl-mkt">{_e(label)}</span>'


def _fmt_ratio(v, suffix="", d=1):
    return "—" if v is None else f"{v:.{d}f}{suffix}"


def _key_numbers(view, usd_kes):
    f, cur = view["facts"], view["facts"]["currency"]
    rows = []
    if f["price"] is not None and cur == "USD" and usd_kes and usd_kes.get("rate"):
        rows.append(("In Kenya shillings", f"≈ KES {f['price'] * usd_kes['rate']:,.2f}", None))
    if f["market_cap"]:
        rows.append(("Company value (market cap)", wl.fmt_big(f["market_cap"], cur), "market-cap"))
    if f["pe"] is not None:
        rows.append(("P/E ratio", _fmt_ratio(f["pe"]), "pe"))
    if f["forward_pe"] is not None:
        rows.append(("Forward P/E", _fmt_ratio(f["forward_pe"]), None))
    if f["peg"] is not None:
        rows.append(("PEG ratio", _fmt_ratio(f["peg"], d=2), "peg"))
    if f["price_to_book"] is not None:
        rows.append(("Price / book", _fmt_ratio(f["price_to_book"], d=2), None))
    if f["eps"] is not None:
        rows.append(("Earnings per share", wl.fmt_money(f["eps"], cur), "eps"))
    if f["dividend_yield"]:
        rows.append(("Dividend yield", _fmt_ratio(f["dividend_yield"], "%"), "dividend-yield"))
    if f["roe"] is not None:
        rows.append(("Return on equity", _fmt_ratio(f["roe"], "%"), "roe"))
    if f["debt_to_equity"] is not None:
        rows.append(("Debt / equity", _fmt_ratio(f["debt_to_equity"], d=2), "debt-to-equity"))
    if f["beta"] is not None:
        rows.append(("Beta (swings vs market)", _fmt_ratio(f["beta"], d=2), None))
    if f["volume"] and f["avg_volume"]:
        rows.append(("Today's volume vs usual", f"{f['volume'] / f['avg_volume']:.1f}× average", "volume"))
    if f["week52_low"] and f["week52_high"]:
        rows.append(("52-week range", f"{wl.fmt_money(f['week52_low'], cur)} – {wl.fmt_money(f['week52_high'], cur)}",
                     "range-52w"))
    if not rows:
        return '<p class="wl-nodata">No company numbers available.</p>'
    return "".join(f'<div class="wl-kv"><span>{_e(k)}{ui.info(term) if term else ""}</span><span>{_e(v)}</span></div>'
                   for k, v, term in rows)


def _events(view, today):
    f, out = view["facts"], []
    for label, d in (("Next earnings report", f["earnings_date"]), ("Ex-dividend date", f["ex_dividend_date"])):
        if not d:
            continue
        days = (d - today).days
        when = ("today" if days == 0 else f"in {days} days" if days > 0 else f"{-days} days ago")
        out.append(f'<div class="wl-kv"><span>{_e(label)}</span><span>{_e(d.isoformat())} ({when})</span></div>')
    return "".join(out) or '<p class="wl-nodata">No upcoming earnings or dividend dates published.</p>'


def _news_html(view):
    if not view["news"]:
        return '<p class="wl-nodata">No headlines in the last week.</p>'
    out = []
    for n in view["news"]:
        url = n.get("url") or ""
        if not url.startswith(("http://", "https://")):
            continue
        meta = " · ".join(x for x in (n.get("source"), (n.get("published_utc") or "")[:10]) if x)
        out.append(f'<a href="{_e(url)}" target="_blank" rel="noopener noreferrer">{_e(n.get("title"))}'
                   f'<br><small>{_e(meta)}</small></a>')
    return "".join(out) or '<p class="wl-nodata">No headlines in the last week.</p>'


def _perf_html(view):
    perf = view["performance"]
    if not perf:
        return '<p class="wl-nodata">Not enough history yet.</p>'
    return '<div class="perf-chips">' + "".join(
        f'<span class="{_pct_class(v)}">{_e(k)} {_e(wl.fmt_pct(v))}</span>' for k, v in perf.items()) + "</div>"


def _plain_number(v):
    """200.0 -> '200', 45.1 -> '45.1' (for pre-filling form fields)."""
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else f"{v:.6f}".rstrip("0").rstrip(".")


def _actions_html(view):
    attrs = (f'data-symbol="{_e(view["symbol"])}" data-market="{_e(view["market"])}" '
             f'data-name="{_e(view["name"])}" data-currency="{_e(view["facts"]["currency"])}" '
             f'data-buy="{_e(_plain_number(view["buy_below"]))}" '
             f'data-sell="{_e(_plain_number(view["sell_above"]))}" '
             f'data-note="{_e(view["note"])}" data-price="{_e(_plain_number(view["facts"]["price"]))}"')
    return (f'<span class="wl-actions app-only">'
            f'<button type="button" class="wl-btn" data-wl-action="edit" {attrs}>✏️ Edit targets / note</button>'
            f'<button type="button" class="wl-btn wl-btn-danger" data-wl-action="remove" {attrs}>🗑 Remove</button>'
            f'</span>')


def _logo(report_gen, view):
    return report_gen._ticker_logo_html(view["symbol"], website=view["facts"].get("website"),
                                        international=view["market"] == wl.MARKET_INTL)


def _attention_priority(icon, view):
    """0 = your own targets, 1 = everything else, 2 = missing data (same as the list)."""
    return 0 if icon in ("🎯", "💰") else 2 if (icon == "⚠️" and not view["has_data"]) else 1


def _target_bar(view):
    """Where the price sits between YOUR buy and sell prices, or — without
    both targets — within the 52-week range, with any target marked."""
    f, cur = view["facts"], view["facts"]["currency"]
    price = f["price"]
    fmt = lambda v: wl.fmt_money(v, cur)  # noqa: E731
    if price is None:
        return ""
    if view["buy_below"] and view["sell_above"] and view["sell_above"] > view["buy_below"]:
        return ui.range_bar(view["buy_below"], view["sell_above"], price, fmt=fmt, label="Between your buy and sell prices")
    if f["week52_low"] and f["week52_high"]:
        marks = [(v, lab, c) for v, lab, c in ((view["buy_below"], "Your buy price", "buy"),
                                                (view["sell_above"], "Your sell price", "sell"))
                 if v and f["week52_low"] <= v <= f["week52_high"]]
        return ui.range_bar(f["week52_low"], f["week52_high"], price, marks=marks, fmt=fmt, label="52-week range")
    return ""


def _signal_meter(view):
    t = view["tally"]
    total = t["buy"] + t["sell"] + t["neutral"]
    if not total:
        return '<span class="chip chip-none">no signals</span>'
    up, down = t["buy"] / total * 100, t["sell"] / total * 100
    return (f'<span class="meter" role="img" aria-label="{t["buy"]} signals lean positive, {t["sell"]} negative, '
            f'{t["neutral"]} neutral"><i class="m-up" style="width:{up:.0f}%"></i>'
            f'<i class="m-down" style="width:{down:.0f}%"></i></span>'
            f'<span class="meter-text"><b class="positive">▲{t["buy"]}</b> <b class="negative">▼{t["sell"]}</b> '
            f'<span class="chip {_SUMMARY_CHIP[view["summary_token"]]}">{_e(view["summary"])}</span></span>')


def render_card(report_gen, view, charts, usd_kes, today, order=0):
    """One watchlist stock: a compact card that opens in place, full width,
    showing everything — the chart, the checklist, key numbers, performance,
    key dates, news, your note, links and the edit / remove buttons. The
    whole card is one .wl-card (the dashboard app finds its buttons and
    adds the edit form inside it)."""
    f, cur = view["facts"], view["facts"]["currency"]
    anchor = _anchor(view)
    logo = _logo(report_gen, view)
    charts = charts or {}
    name = view["name"] if view["name"] != view["symbol"] else ""
    alerts = view["attention"]
    prio = min((_attention_priority(i, view) for i, _t in alerts), default=9)
    gap = None
    if view["buy_below"] and f["price"]:
        gap = (f["price"] - view["buy_below"]) / view["buy_below"] * 100
    tags = ["nse" if view["market"] == wl.MARKET_NSE else "intl"]
    if view["target"]["status"] == "buy_zone":
        tags.append("buyzone")
    # Sort keys for the page's Sort menu (ui_runtime.js reads data-k-*).
    sort_attrs = (f'data-tags="{" ".join(tags)}" data-k-attention="{prio}" data-k-order="{order}" '
                  f'data-k-move="{abs(f["change_pct"] or 0):.2f}" '
                  f'data-k-buy="{abs(gap) if gap is not None else 1e9:.2f}" '
                  f'data-k-score="{view["score"] if view["score"] is not None else -1}" '
                  f'data-k-name="{_e(view["symbol"])}"')

    # ---- the compact card
    if view["has_data"]:
        chg = f["change_pct"]
        price = (f'<div class="wl-price"><b class="num">{_e(wl.fmt_money(f["price"], cur))}</b>'
                 f'<span class="{_pct_class(chg)} num">{_e(wl.fmt_pct(chg, decimals=2))} today</span></div>')
    else:
        price = '<div class="wl-price"><span class="chip chip-none">no data today</span></div>'
    icons = "".join(f'<span class="wl-alert" title="{_e(text)}">{_e(icon)}</span>' for icon, text in alerts)
    score = view["score"]
    score_html = (f'<span class="score {"score-high" if score >= 70 else "score-mid" if score >= 45 else "score-low"}" '
                  f'title="Factor score 0–100">{score}</span>' if score is not None else "")
    face = (f'<button type="button" class="wl-face" data-expand="{anchor}" aria-expanded="false" '
            f'aria-controls="{anchor}-detail">'
            f'<span class="wl-top"><span class="sym">{logo}<span>{_e(view["symbol"])}<small>{_e(name)}</small>'
            f'{_market_badge(view)}</span></span>{price}</span>'
            f'<span class="wl-spark">{charts.get("spark", "")}</span>'
            f'<span class="wl-target">{_target_bar(view)}</span>'
            f'<span class="wl-meta">{_signal_meter(view) if view["has_data"] else ""}'
            f'<span class="wl-icons">{icons}{score_html}</span></span>'
            f'<span class="wl-open" aria-hidden="true">Details ›</span></button>')

    # ---- the opened card
    sub = []
    if f.get("sector"):
        sub.append(_e(f["sector"] + (f" · {f['industry']}" if f.get("industry") else "")))
    sa = view["since_added"]
    if sa:
        sub.append(f'Since you started watching ({_e(sa["date"] or "")}): '
                   f'<b class="{_pct_class(sa["pct"])}">{_e(wl.fmt_pct(sa["pct"]))}</b>')
    elif view.get("added") == today.isoformat():
        sub.append("⭐ Added to your watchlist today")
    elif view.get("added"):
        sub.append(f"Watching since {_e(view['added'])}")
    if view["holding"]:
        sub.append("💼 " + _e(view["holding"]["text"]))
    links = ""
    if view["detail_file"]:
        links += f'<a href="{_e(view["detail_file"])}">📄 Full analysis →</a>'
    if not view["has_data"]:
        detail = (f'<div class="wl-sub">{" · ".join(sub)}</div>'
                  f'<p class="wl-nodata">⚠️ No price data for <b>{_e(view["symbol"])}</b> today. If this is a new '
                  f'stock, check the ticker is right (on the {"NSE" if view["market"] == wl.MARKET_NSE else "exchange you meant"}) '
                  f'— or the data source may just be down; it will be tried again on the next update.</p>'
                  f'<div class="wl-links"><a href="{_e(view["external_url"])}" target="_blank" rel="noopener noreferrer">'
                  f'Look it up on {_e(view["external_label"])} ↗</a>{_actions_html(view)}</div>')
    else:
        tchip_cls, tchip_txt = _target_chip(view)
        t = view["tally"]
        chips = (f'<span class="chip {_SUMMARY_CHIP[view["summary_token"]]}">{_e(view["summary"])}</span>'
                 f'<span class="chip {tchip_cls}">{_e(tchip_txt)}</span>')
        if score is not None:
            cls = "score-high" if score >= 70 else "score-mid" if score >= 45 else "score-low"
            chips += f'<span class="score {cls}" title="Factor score 0–100">Score {score}</span>'
        checklist = "".join(
            f'<li class="{"na" if it["lean"] == "na" else ""}"><span>{_LEAN_ICON[it["lean"]]}</span>'
            f'<span><b>{_e(it["label"])}:</b> {_e(it["text"])}</span></li>' for it in view["checklist"])
        tally_txt = (f'Tally: <b>{t["buy"]}</b> lean positive · <b>{t["sell"]}</b> lean negative · '
                     f'<b>{t["neutral"]}</b> neutral (52-week position and your own targets are shown but not counted).')
        note = f'<div class="wl-note">📝 {_e(view["note"])}</div>' if view["note"] else ""
        links += (f'<a href="{_e(view["external_url"])}" target="_blank" rel="noopener noreferrer">'
                  f'{_e(view["external_label"])} ↗</a>')
        detail = (f'<div class="wl-sub">{" · ".join(sub)}</div>'
                  f'<div class="wl-chips">{chips}</div>'
                  f'<div class="wl-chart">{charts.get("chart", "")}</div>'
                  f'<div class="wl-columns"><div><h4>The checklist{ui.info("signal")}</h4>'
                  f'<ul class="wl-check">{checklist}</ul><div class="wl-tally">{tally_txt}</div></div>'
                  f'<div class="wl-boxes"><div class="wl-box"><h4>Key numbers</h4>{_key_numbers(view, usd_kes)}</div>'
                  f'<div class="wl-box"><h4>Performance</h4>{_perf_html(view)}'
                  f'<h4 style="margin-top:12px;">Key dates</h4>{_events(view, today)}</div>'
                  f'<div class="wl-box wl-news"><h4>Latest news</h4>{_news_html(view)}</div></div></div>'
                  f'{note}<div class="wl-links">{links}{_actions_html(view)}</div>')
    return (f'<article class="wl-card" id="{anchor}" {sort_attrs}>{face}'
            f'<div class="wl-detail" id="{anchor}-detail">{detail}</div></article>')


def _table(report_gen, views):
    rows = []
    for v in views:
        f, cur = v["facts"], v["facts"]["currency"]
        logo = _logo(report_gen, v)
        name = v["name"] if v["name"] != v["symbol"] else ""
        sa = (v["since_added"] or {}).get("pct")
        sa_html = (wl.fmt_pct(sa) if sa is not None
                   else "new" if v.get("added") == _dt_today().isoformat() else "—")
        pos = v["range_pos"]
        rng = ui.range_bar(f["week52_low"], f["week52_high"], f["price"],
                           fmt=lambda x: wl.fmt_money(x, cur), label="52-week range") if pos is not None else "—"
        tchip_cls, tchip_txt = _target_chip(v)
        t = v["tally"]
        if v["has_data"]:
            sig = Markup(f'<span class="chip {_SUMMARY_CHIP[v["summary_token"]]}" title="{_e(v["summary"])}">'
                         f'▲{t["buy"]} ▼{t["sell"]}</span>')
        else:
            sig = Markup('<span class="chip chip-none">no data</span>')
        sc_ = v["score"]
        sc_html = (Markup(f'<span class="score {"score-high" if sc_ >= 70 else "score-mid" if sc_ >= 45 else "score-low"}">'
                          f'{sc_}</span>') if sc_ is not None else "—")
        rows.append([
            ui.cell(Markup(f'<a href="#{_anchor(v)}" class="stock-link sym">{logo}<span>{_e(v["symbol"])}'
                           f'<small>{_e(name)}</small></span></a>'), sort=v["symbol"]),
            ui.cell(Markup(_market_badge(v)), sort=v["market"]),
            ui.cell(wl.fmt_money(f["price"], cur), sort=f["price"]),
            ui.cell(wl.fmt_pct(f["change_pct"], decimals=2), sort=f["change_pct"], cls=_pct_class(f["change_pct"])),
            ui.cell(sa_html, sort=sa, cls=_pct_class(sa)),
            ui.cell(rng, sort=round(pos, 1) if pos is not None else None),
            ui.cell(Markup(f'<span class="chip {tchip_cls}">{_e(tchip_txt)}</span>')),
            ui.cell(sig, sort=t["buy"] - t["sell"] if v["has_data"] else None),
            ui.cell(sc_html, sort=sc_),
        ])
    cols = [ui.Col("Stock", sort="text"), ui.Col("Market", sort="text"), ui.Col("Price", sort="number"),
            ui.Col("Today", sort="number"),
            ui.Col("Since added", sort="number", title="Change since the day you added it to the watchlist"),
            ui.Col("52-week range", sort="number", term="range-52w",
                   title="Where today's price sits between its 52-week low (left) and high (right)"),
            ui.Col("Your targets", sort=None),
            ui.Col("Signals", sort="number", title="How many signals lean positive (▲) vs negative (▼) — see each card"),
            ui.Col("Score", sort="number", term="factor-score")]
    return ui.table(cols, rows, table_id="mainTable", cls="wl-table")


_NO_APP_HELP = (
    '<div class="banner no-app-only" data-tone="info" role="note"><b>➕ Want to add or remove stocks with a click?</b> '
    'Open the dashboard through the app: double-click <b>Open Dashboard.command</b> in the project folder '
    '(or run <code>./venv/bin/python3 app.py</code>). This page then gets a search box and Add / Edit / '
    'Remove buttons.<br><span class="small">Prefer a spreadsheet? Add a row to '
    '<code>portfolio/watchlist.csv</code> — columns <code>symbol, market</code> (NSE or INTL), '
    '<code>buy_below, sell_above, note</code> — then run <code>./run.sh</code>. On the Docker home-server '
    'install, edit that file on the server; it shows up after the next scheduled update.</span></div>')


def _explainer():
    cards = [
        ("How to use the checklist",
         "Each stock gets a checklist of signals. 🟢 means that signal leans positive (a reason someone might "
         "buy or keep holding), 🔴 leans negative (a reason to wait, avoid or sell), ⚪ is neutral, ▫️ means no "
         "data. The tally just counts them — it's a starting point for your own judgement, never an instruction."),
        ("Trend & moving averages",
         "A moving average is the average closing price over the last 20 or 50 trading days. Price above its "
         "50-day average = uptrend. The 20-day crossing above the 50-day (a 'golden cross') often marks a new "
         "uptrend; crossing below (a 'death cross') a downtrend."),
        ("Momentum (MACD) & RSI",
         "MACD compares a fast and a slow average to show whether momentum is building up or fading. RSI (0–100) "
         "shows how stretched the recent move is: above 70 'overbought' (risen fast, pull-backs common), below 30 "
         "'oversold' (fallen fast, bounces common)."),
        ("Valuation: P/E and PEG",
         "P/E = price ÷ yearly profit per share — how many years of today's profit you're paying for. Lower is "
         "cheaper, but compare like with like (NSE stocks are compared with their sector). PEG divides P/E by "
         "expected growth: under 1 looks cheap for the growth, over 2 looks expensive."),
        ("Analysts & ratings",
         "For international stocks: the average view of professional analysts and their average 12-month price "
         "target. For NSE stocks: analysts' rating where one exists, otherwise TradingView's technical rating."),
        ("Your targets",
         "Set a 'buy below' price (you'd consider buying at or under it) and a 'sell above' price (you'd consider "
         "selling or taking profit at or over it). The page flags when either is reached — it's your own plan, "
         "written down, so emotions don't make the decision."),
    ]
    body = "".join(f'<div class="explain-card"><h4>{_e(t)}</h4><p>{_e(p)}</p></div>' for t, p in cards)
    return str(ui.details("📖 How to read this page", Markup(
        f'<div class="explain-grid">{body}</div>'
        '<div class="banner" data-tone="warn" role="note">⚠️ This page is information, not financial advice. Signals '
        'describe what prices and company figures have done — they can be wrong, and nobody can predict the market. '
        'Think about your goals, how long you can stay invested, spreading your money across different investments, '
        'and trading fees before you buy or sell.</div>'), det_id="wl-howto"))


def render_body(report_gen, views, charts, ctx, load_error=None, today=None):
    today = today or dt.date.today()
    usd_kes = ctx.get("usd_kes")
    out = ['<p class="page-intro">Stocks you\'re keeping an eye on — Kenyan (NSE) and international. Each card '
           'brings together the price chart, trend, momentum, valuation, what analysts say and the latest news, '
           'so you can decide whether it\'s time to buy, wait or sell. This is information, not financial '
           'advice.</p>', _NO_APP_HELP,
           '<div id="wl-add-panel" class="section app-only card card-pad"></div>']
    if load_error:
        out.append(f'<div class="banner" data-tone="danger" role="note"><b>Your watchlist file couldn\'t be read:</b> '
                   f'{_e(load_error)}<br>Fix <code>portfolio/watchlist.csv</code> (or delete it to start '
                   f'again) — see <code>portfolio/README.md</code>.</div>')
    if not views:
        out.append('<div class="section wl-empty card card-pad"><div class="big">⭐</div><h2>Your watchlist is empty</h2>'
                   '<p class="app-only">Use the search box above: type a company name or ticker — '
                   '<i>Equity</i>, <i>EABL</i>, <i>Apple</i>, <i>AAPL</i> — pick it from the list and click '
                   '<b>Add</b>. Or tap the ☆ next to any stock on the Overview page.</p>'
                   '<p class="no-app-only">Start the dashboard app (see the blue box above) to add stocks with a '
                   'search box, or add a row to <code>portfolio/watchlist.csv</code>.</p></div>')
        out.append(_explainer())
        return "".join(out)

    # ---- headline figures
    attn = []
    for i, v in enumerate(views):
        for icon, text in v["attention"]:
            attn.append((_attention_priority(icon, v), i, icon, text, _anchor(v)))
    attn.sort(key=lambda a: (a[0], a[1]))
    in_buy = sum(1 for v in views if v["target"]["status"] == "buy_zone")
    at_sell = sum(1 for v in views if v["target"]["status"] == "sell_zone")
    positive = sum(1 for v in views if v["summary_token"] == "positive")
    out.append(str(ui.kpi_row([
        ui.kpi("Watching", str(len(views)), sub="stocks on your list"),
        ui.kpi("In your buy zone", str(in_buy), tone="up" if in_buy else None, sub="at or under your buy price"),
        ui.kpi("At your sell price", str(at_sell), tone="accent" if at_sell else None, sub="at or over your sell price"),
        ui.kpi("Mostly positive signals", str(positive), sub="more signals lean positive than negative", term="signal"),
        ui.kpi("Needs your attention", str(len(attn)), tone="warn" if attn else None, sub="items today, below"),
    ], cls="kpis-5")))

    # ---- needs attention (chips that open the stock)
    if attn:
        items = "".join(f'<li><a href="#{anchor}" class="attn-chip" data-prio="{p}">{icon} {_e(text)}</a></li>'
                        for p, _i, icon, text, anchor in attn)
        out.append(f'<div class="section"><h2>🔔 Needs your attention today</h2><ul class="wl-attn">{items}</ul></div>')
    else:
        out.append('<div class="section"><h2>🔔 Needs your attention today</h2>'
                   '<p class="wl-nodata">Nothing unusual today — no targets reached, no big moves, no '
                   'earnings or dividend dates this week.</p></div>')

    # ---- the list: cards (default) or the table
    stats = (f'{len(views)} stock{"s" if len(views) != 1 else ""} · {positive} with mostly positive signals'
             + (f" · {in_buy} in your buy zone" if in_buy else ""))
    controls = (
        '<div class="wl-controls" role="group" aria-label="Arrange your watchlist">'
        '<label class="ctl">Sort <select data-sort-cards="wl-grid" aria-label="Sort the cards">'
        '<option value="attention">Needs attention</option><option value="move">Biggest move today</option>'
        '<option value="buy">Closest to your buy price</option><option value="score">Score</option>'
        '<option value="name">Name</option></select></label>'
        '<div class="seg" role="group" aria-label="Show">'
        '<button type="button" data-filter-cards="wl-grid" data-filter="all" aria-pressed="true">All</button>'
        '<button type="button" data-filter-cards="wl-grid" data-filter="nse" aria-pressed="false">NSE</button>'
        '<button type="button" data-filter-cards="wl-grid" data-filter="intl" aria-pressed="false">International</button>'
        '<button type="button" data-filter-cards="wl-grid" data-filter="buyzone" aria-pressed="false">In buy zone</button></div>'
        '<div class="seg" role="group" aria-label="View">'
        '<button type="button" data-view-switch="wl" data-view="cards" aria-pressed="true">Cards</button>'
        '<button type="button" data-view-switch="wl" data-view="table" aria-pressed="false">Table</button></div>'
        '</div>')
    order = sorted(range(len(views)), key=lambda i: (min((_attention_priority(ic, views[i]) for ic, _t in views[i]["attention"]),
                                                         default=9), i))
    cards = "".join(render_card(report_gen, views[i], charts.get(_anchor(views[i])), usd_kes, today, order=n)
                    for n, i in enumerate(order))
    out.append(f'<div class="section" id="wl-list"><h2>⭐ Your watchlist</h2><p class="dq-note">{_e(stats)}. Click a '
               f'stock to jump to its card; click a column header to sort.</p>{controls}'
               f'<div class="wl-grid" id="wl-grid" data-view-of="wl" data-view-name="cards">{cards}</div>'
               f'<div class="wl-table-view" data-view-of="wl" data-view-name="table">{_table(report_gen, views)}</div>'
               '</div>')
    out.append(_explainer())
    return "".join(out)


# ----------------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------------
def _subtitle(report_gen, ctx):
    return report_gen.page_subtitle(data_date=ctx.get("data_date"), usd_kes=ctx.get("usd_kes")) + \
        " · International: Yahoo Finance"


def write_error_page(report_gen, message):
    """Last resort so the ⭐ Watchlist link never leads nowhere."""
    body = (f'<div class="banner" data-tone="danger" role="note"><b>The watchlist page couldn\'t be built this '
            f'time.</b> {_e(message)}<br>Your watchlist file is untouched. Try updating again; if it keeps happening, '
            f'the details are in logs/analyzer.log.</div>' + _NO_APP_HELP +
            '<div id="wl-add-panel" class="section app-only card card-pad"></div>')
    html = report_gen._page_shell("NSE — Watchlist", PAGE_FILE, report_gen.page_subtitle(), body)
    return report_gen.write_page(PAGE_FILE, html)


def generate_watchlist_page(config, report_gen, analysis_engine, preloaded=None, period="6mo",
                            interval="1d", force_refresh=False, fetch_news=True, today=None):
    """Build and write reports/watchlist.html. Returns a small summary dict."""
    preloaded = preloaded or {}
    today = today or dt.date.today()
    nse_universe = set(preloaded.get("fundamentals") or ())
    if not nse_universe:
        import symbol_lookup
        nse_universe = symbol_lookup.nse_symbols(config.cache_dir)
    load_error = None
    try:
        entries = wl.load_watchlist(config.portfolio_dir, nse_symbols=nse_universe)
    except ValueError as e:          # CsvFormatError — the file as a whole is unusable
        logger.warning(f"Watchlist: {e}")
        entries, load_error = [], str(e)

    logger.info(f"Watchlist: {len(entries)} stock(s) — fetching data…")
    rows, ctx = collect(config, entries, analysis_engine, report_gen,
                        preloaded=dict(preloaded, nse_universe=nse_universe), period=period,
                        interval=interval, force_refresh=force_refresh, fetch_news=fetch_news)

    logger.info("Watchlist: building charts…")
    views, charts = [], {}
    for row in rows:
        view = wl.build_entry_view(row["entry"], row["result"], row["fund"],
                                   sector_medians=ctx["sector_medians"], score=row["score"],
                                   holding=row["holding"], validation=row["validation"],
                                   news=row["news"], detail_file=row["detail_file"], today=today)
        views.append(view)
        if view["has_data"] and row["result"]:
            charts[_anchor(view)] = make_charts(view, row["result"].get("data"))

    body = render_body(report_gen, views, charts, ctx, load_error=load_error, today=today)
    html = report_gen._page_shell("NSE — Watchlist", PAGE_FILE, _subtitle(report_gen, ctx), body)
    path = report_gen.write_page(PAGE_FILE, html)
    missing = [v["symbol"] for v in views if not v["has_data"]]
    logger.info(f"Watchlist page saved: {len(views)} stock(s)"
                + (f"; no data today for {', '.join(missing)}" if missing else ""))
    return {"path": path, "count": len(views), "missing": missing, "load_error": load_error}


def refresh_watchlist_page(config, period="6mo", interval="1d", force_refresh=False):
    """Standalone rebuild of just the watchlist page (main.py --watchlist-page-only)."""
    from analysis_engine import AnalysisEngine
    from report_generator import ReportGenerator
    report_gen = ReportGenerator(template_dir=config.template_dir, output_dir=config.report_directory,
                                 clean_old=False, cache_dir=config.cache_dir)
    try:
        return generate_watchlist_page(config, report_gen, AnalysisEngine(config=config), period=period,
                                       interval=interval, force_refresh=force_refresh)
    except Exception as e:
        logger.error(f"Watchlist page failed: {e}", exc_info=True)
        write_error_page(report_gen, str(e))
        raise
