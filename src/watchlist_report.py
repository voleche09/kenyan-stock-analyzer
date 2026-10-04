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
def make_chart(report_gen, data, view):
    """Price + 20/50-day averages + normal range + YOUR buy/sell prices, with
    volume and RSI underneath. Returns base64 PNG, or None if there's no data."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import matplotlib.ticker as mticker
        from matplotlib.lines import Line2D
        from report_generator import COLORS
    except Exception:
        return None
    if data is None or getattr(data, "empty", True) or "close" not in data:
        return None
    cur = view["facts"]["currency"]
    money = lambda v: wl.fmt_money(v, cur)  # noqa: E731
    try:
        fig = plt.figure(figsize=(10, 5.8))
        gs = fig.add_gridspec(3, 1, height_ratios=[3.3, 1, 1.1], hspace=0.07)
        ax = fig.add_subplot(gs[0])
        axv = fig.add_subplot(gs[1], sharex=ax)
        axr = fig.add_subplot(gs[2], sharex=ax)
        idx = data.index
        close = data["close"]

        if "bb_upper" in data and "bb_lower" in data:
            ax.fill_between(idx, data["bb_lower"], data["bb_upper"], color=COLORS["bb"], alpha=0.08,
                            label="Normal range (Bollinger)")
        ax.plot(idx, close, color=COLORS["price"], linewidth=1.7, label="Price")
        if "sma_20" in data:
            ax.plot(idx, data["sma_20"], color=COLORS["sma20"], linestyle="--", linewidth=1.1, label="20-day average")
        if "sma_50" in data:
            ax.plot(idx, data["sma_50"], color=COLORS["sma50"], linestyle="--", linewidth=1.1, label="50-day average")

        lo_p, hi_p = float(close.min()), float(close.max())
        span = max(hi_p - lo_p, hi_p * 0.02)
        lo_ok, hi_ok = lo_p - span * 0.6, hi_p + span * 0.6
        extra = []
        for value, colour, label in ((view["buy_below"], "#16a34a", "Your buy price"),
                                     (view["sell_above"], "#dc2626", "Your sell price")):
            if not value:
                continue
            if lo_ok <= value <= hi_ok:
                ax.axhline(value, color=colour, linewidth=1.4, linestyle=(0, (6, 3)),
                           label=f"{label} {money(value)}")
            else:
                where = "below" if value < lo_ok else "above"
                extra.append(Line2D([], [], color=colour, linewidth=1.4, linestyle=(0, (6, 3)),
                                    label=f"{label} {money(value)} ({where} this chart)"))
        for value, label in ((view["facts"]["week52_high"], "52-week high"),
                             (view["facts"]["week52_low"], "52-week low")):
            if value and lo_ok <= value <= hi_ok:
                ax.axhline(value, color="#64748b", linewidth=0.9, linestyle=":", label=f"{label} {money(value)}")

        handles, labels = ax.get_legend_handles_labels()
        handles += extra
        labels += [h.get_label() for h in extra]
        ax.legend(handles, labels, loc="upper left", fontsize=7.5, ncol=2, frameon=True, framealpha=0.85)
        ax.grid(True, alpha=0.3)
        ax.set_ylabel(cur, fontsize=8)
        ax.tick_params(labelbottom=False, labelsize=8)

        if "volume" in data:
            ups = close.diff().fillna(0) >= 0
            axv.bar(idx, data["volume"], color=[COLORS["bullish"] if u else COLORS["sma50"] for u in ups],
                    alpha=0.65, width=0.8)
            axv.yaxis.set_major_formatter(mticker.FuncFormatter(
                lambda x, _p: f"{x / 1e6:.1f}M" if x >= 1e6 else f"{x / 1e3:.0f}K" if x >= 1e3 else f"{x:.0f}"))
        axv.set_ylabel("Volume", fontsize=8)
        axv.grid(True, alpha=0.3)
        axv.tick_params(labelbottom=False, labelsize=7)

        if "rsi" in data:
            axr.plot(idx, data["rsi"], color=COLORS["rsi"], linewidth=1.1)
            axr.axhline(70, color=COLORS["sma50"], linestyle="--", linewidth=0.8, alpha=0.7)
            axr.axhline(30, color=COLORS["bullish"], linestyle="--", linewidth=0.8, alpha=0.7)
            axr.fill_between(idx, 70, 100, color=COLORS["sma50"], alpha=0.06)
            axr.fill_between(idx, 0, 30, color=COLORS["bullish"], alpha=0.06)
        axr.set_ylim(0, 100)
        axr.set_yticks([30, 70])
        axr.set_ylabel("RSI", fontsize=8)
        axr.grid(True, alpha=0.3)
        axr.tick_params(labelsize=8)
        locator = mdates.AutoDateLocator()
        axr.xaxis.set_major_locator(locator)
        axr.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        return report_gen._fig_to_b64(dpi=100)
    except Exception as e:
        logger.warning(f"Watchlist chart failed for {view['symbol']}: {e}")
        try:
            plt.close("all")
        except Exception:
            pass
        return None


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
        rows.append(("In Kenya shillings", f"≈ KES {f['price'] * usd_kes['rate']:,.2f}"))
    if f["market_cap"]:
        rows.append(("Company value (market cap)", wl.fmt_big(f["market_cap"], cur)))
    if f["pe"] is not None:
        rows.append(("P/E ratio", _fmt_ratio(f["pe"])))
    if f["forward_pe"] is not None:
        rows.append(("Forward P/E", _fmt_ratio(f["forward_pe"])))
    if f["peg"] is not None:
        rows.append(("PEG ratio", _fmt_ratio(f["peg"], d=2)))
    if f["price_to_book"] is not None:
        rows.append(("Price / book", _fmt_ratio(f["price_to_book"], d=2)))
    if f["eps"] is not None:
        rows.append(("Earnings per share", wl.fmt_money(f["eps"], cur)))
    if f["dividend_yield"]:
        rows.append(("Dividend yield", _fmt_ratio(f["dividend_yield"], "%")))
    if f["roe"] is not None:
        rows.append(("Return on equity", _fmt_ratio(f["roe"], "%")))
    if f["debt_to_equity"] is not None:
        rows.append(("Debt / equity", _fmt_ratio(f["debt_to_equity"], d=2)))
    if f["beta"] is not None:
        rows.append(("Beta (swings vs market)", _fmt_ratio(f["beta"], d=2)))
    if f["volume"] and f["avg_volume"]:
        rows.append(("Today's volume vs usual", f"{f['volume'] / f['avg_volume']:.1f}× average"))
    if f["week52_low"] and f["week52_high"]:
        rows.append(("52-week range", f"{wl.fmt_money(f['week52_low'], cur)} – {wl.fmt_money(f['week52_high'], cur)}"))
    if not rows:
        return '<p class="wl-nodata">No company numbers available.</p>'
    return "".join(f'<div class="wl-kv"><span>{_e(k)}</span><span>{_e(v)}</span></div>' for k, v in rows)


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


def render_card(report_gen, view, chart_b64, usd_kes, today):
    f, cur = view["facts"], view["facts"]["currency"]
    logo = _logo(report_gen, view)
    head = (f'<div class="wl-head"><div class="wl-title">{logo}<span>{_e(view["symbol"])}</span>'
            f'<span class="wl-fullname">{_e(view["name"] if view["name"] != view["symbol"] else "")}</span>'
            f'{_market_badge(view)}</div>')
    if view["has_data"]:
        chg = f["change_pct"]
        head += (f'<div class="wl-price"><div class="wl-p">{_e(wl.fmt_money(f["price"], cur))}</div>'
                 f'<div class="{_pct_class(chg)}">{_e(wl.fmt_pct(chg, decimals=2))} today</div></div>')
    head += "</div>"

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

    if not view["has_data"]:
        return (f'<div class="section wl-card" id="{_anchor(view)}">{head}'
                f'<div class="wl-sub">{" · ".join(sub)}</div>'
                f'<p class="wl-nodata">⚠️ No price data for <b>{_e(view["symbol"])}</b> today. If this is a new '
                f'stock, check the ticker is right (on the {"NSE" if view["market"] == wl.MARKET_NSE else "exchange you meant"}) '
                f'— or the data source may just be down; it will be tried again on the next update.</p>'
                f'<div class="wl-links"><a href="{_e(view["external_url"])}" target="_blank" rel="noopener noreferrer">'
                f'Look it up on {_e(view["external_label"])} ↗</a>{_actions_html(view)}</div></div>')

    tchip_cls, tchip_txt = _target_chip(view)
    t = view["tally"]
    chips = (f'<span class="chip {_SUMMARY_CHIP[view["summary_token"]]}">{_e(view["summary"])}</span>'
             f'<span class="chip {tchip_cls}">{_e(tchip_txt)}</span>')
    if view["score"] is not None:
        sc = view["score"]
        cls = "score-high" if sc >= 70 else "score-mid" if sc >= 45 else "score-low"
        chips += f'<span class="score {cls}" title="Factor score 0–100">Score {sc}</span>'

    checklist = "".join(
        f'<li class="{"na" if it["lean"] == "na" else ""}"><span>{_LEAN_ICON[it["lean"]]}</span>'
        f'<span><b>{_e(it["label"])}:</b> {_e(it["text"])}</span></li>' for it in view["checklist"])
    tally_txt = (f'Tally: <b>{t["buy"]}</b> lean positive · <b>{t["sell"]}</b> lean negative · '
                 f'<b>{t["neutral"]}</b> neutral (52-week position and your own targets are shown but not counted).')
    chart = (f'<img class="chart-img" src="data:image/png;base64,{chart_b64}" alt="{_e(view["symbol"])} price chart" loading="lazy">'
             if chart_b64 else '<p class="wl-nodata">Not enough price history for a chart yet.</p>')

    boxes = (f'<div class="wl-box"><h4>Key numbers</h4>{_key_numbers(view, usd_kes)}</div>'
             f'<div class="wl-box"><h4>Performance</h4>{_perf_html(view)}'
             f'<h4 style="margin-top:12px;">Key dates</h4>{_events(view, today)}</div>'
             f'<div class="wl-box wl-news"><h4>Latest news</h4>{_news_html(view)}</div>')
    note = f'<div class="wl-note">📝 {_e(view["note"])}</div>' if view["note"] else ""
    links = ""
    if view["detail_file"]:
        links += f'<a href="{_e(view["detail_file"])}">📄 Full analysis →</a>'
    links += (f'<a href="{_e(view["external_url"])}" target="_blank" rel="noopener noreferrer">'
              f'{_e(view["external_label"])} ↗</a>')
    return (f'<div class="section wl-card" id="{_anchor(view)}">{head}'
            f'<div class="wl-sub">{" · ".join(sub)}</div>'
            f'<div class="wl-chips">{chips}</div>'
            f'<div class="wl-body"><div>{chart}</div><div><ul class="wl-check">{checklist}</ul>'
            f'<div class="wl-tally">{tally_txt}</div></div></div>'
            f'<div class="wl-grid">{boxes}</div>{note}'
            f'<div class="wl-links">{links}{_actions_html(view)}</div></div>')


def _table(report_gen, views):
    rows = []
    for v in views:
        f, cur = v["facts"], v["facts"]["currency"]
        logo = _logo(report_gen, v)
        name = v["name"] if v["name"] != v["symbol"] else ""
        sa = (v["since_added"] or {}).get("pct")
        sa_html = (_e(wl.fmt_pct(sa)) if sa is not None
                   else "new" if v.get("added") == _dt_today().isoformat() else "—")
        pos = v["range_pos"]
        rng = (f'<span style="display:none">{pos:.0f}</span><span class="range-bar" '
               f'title="{_e(wl.fmt_money(f["week52_low"], cur))} – {_e(wl.fmt_money(f["week52_high"], cur))} '
               f'(52 weeks)"><i style="left:{pos:.0f}%"></i></span>') if pos is not None else "—"
        tchip_cls, tchip_txt = _target_chip(v)
        t = v["tally"]
        if v["has_data"]:
            sig = (f'<span class="chip {_SUMMARY_CHIP[v["summary_token"]]}" title="{_e(v["summary"])}">'
                   f'▲{t["buy"]} ▼{t["sell"]}</span>')
        else:
            sig = '<span class="chip chip-none">no data</span>'
        sc = v["score"]
        sc_html = (f'<span class="score {"score-high" if sc >= 70 else "score-mid" if sc >= 45 else "score-low"}">{sc}</span>'
                   if sc is not None else "—")
        rows.append(
            f'<tr><td><a href="#{_anchor(v)}" class="stock-link">{logo}<strong>{_e(v["symbol"])}</strong></a>'
            f'<span class="wl-name-sm">{_e(name)}</span></td>'
            f'<td>{_market_badge(v)}</td>'
            f'<td>{_e(wl.fmt_money(f["price"], cur))}</td>'
            f'<td class="{_pct_class(f["change_pct"])}">{_e(wl.fmt_pct(f["change_pct"], decimals=2))}</td>'
            f'<td class="{_pct_class(sa)}">{sa_html}</td>'
            f'<td>{rng}</td>'
            f'<td><span class="chip {tchip_cls}">{_e(tchip_txt)}</span></td>'
            f'<td>{sig}</td><td>{sc_html}</td></tr>')
    return ('<div class="table-wrap"><table id="mainTable" class="wl-table"><thead><tr>'
            '<th class="sortable" data-sort-type="text" onclick="sortTable(this)">Stock</th>'
            '<th class="sortable" data-sort-type="text" onclick="sortTable(this)">Market</th>'
            '<th class="sortable" data-sort-type="number" onclick="sortTable(this)">Price</th>'
            '<th class="sortable" data-sort-type="number" onclick="sortTable(this)">Today</th>'
            '<th class="sortable" data-sort-type="number" onclick="sortTable(this)" '
            'title="Change since the day you added it to the watchlist">Since added</th>'
            '<th class="sortable" data-sort-type="number" onclick="sortTable(this)" '
            'title="Where today\'s price sits between its 52-week low (left) and high (right)">52-week range</th>'
            '<th>Your targets</th>'
            '<th title="How many signals lean positive (▲) vs negative (▼) — see each card">Signals</th>'
            '<th class="sortable" data-sort-type="number" onclick="sortTable(this)">Score</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


_NO_APP_HELP = (
    '<div class="banner banner-info no-app-only"><b>➕ Want to add or remove stocks with a click?</b> '
    'Open the dashboard through the app: double-click <b>Open Dashboard.command</b> in the project folder '
    '(or run <code>./venv/bin/python3 app.py</code>). This page then gets a search box and Add / Edit / '
    'Remove buttons.<br><span style="font-size:0.85rem;">Prefer a spreadsheet? Add a row to '
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
    return ('<details class="section-details"><summary><h2>📖 How to read this page</h2>'
            '<span class="toggle-hint"></span></summary><div class="details-body">'
            f'<div class="explain-grid">{body}</div>'
            '<div class="banner banner-warn" style="margin-top:16px;">⚠️ This page is information, not financial '
            'advice. Signals describe what prices and company figures have done — they can be wrong, and nobody can '
            'predict the market. Think about your goals, how long you can stay invested, spreading your money '
            'across different investments, and trading fees before you buy or sell.</div>'
            '</div></details>')


def render_body(report_gen, views, charts, ctx, load_error=None, today=None):
    today = today or dt.date.today()
    usd_kes = ctx.get("usd_kes")
    out = ['<p class="page-intro">Stocks you\'re keeping an eye on — Kenyan (NSE) and international. Each card '
           'brings together the price chart, trend, momentum, valuation, what analysts say and the latest news, '
           'so you can decide whether it\'s time to buy, wait or sell. This is information, not financial '
           'advice.</p>', _NO_APP_HELP,
           '<div id="wl-add-panel" class="section app-only"></div>']
    if load_error:
        out.append(f'<div class="banner banner-danger"><b>Your watchlist file couldn\'t be read:</b> '
                   f'{_e(load_error)}<br>Fix <code>portfolio/watchlist.csv</code> (or delete it to start '
                   f'again) — see <code>portfolio/README.md</code>.</div>')
    if not views:
        out.append('<div class="section wl-empty"><div class="big">⭐</div><h2>Your watchlist is empty</h2>'
                   '<p class="app-only">Use the search box above: type a company name or ticker — '
                   '<i>Equity</i>, <i>EABL</i>, <i>Apple</i>, <i>AAPL</i> — pick it from the list and click '
                   '<b>Add</b>. Or tap the ☆ next to any stock on the Overview page.</p>'
                   '<p class="no-app-only">Start the dashboard app (see the blue box above) to add stocks with a '
                   'search box, or add a row to <code>portfolio/watchlist.csv</code>.</p></div>')
        out.append(_explainer())
        return "".join(out)

    # 🔔 Attention list — your own targets first, then everything else, missing data last
    attn = []
    for i, v in enumerate(views):
        for icon, text in v["attention"]:
            prio = 0 if icon in ("🎯", "💰") else 2 if (icon == "⚠️" and not v["has_data"]) else 1
            attn.append((prio, i, icon, text, _anchor(v)))
    attn.sort(key=lambda a: (a[0], a[1]))
    if attn:
        items = "".join(f'<li>{icon} <a href="#{anchor}">{_e(text)}</a></li>' for _p, _i, icon, text, anchor in attn)
        out.append(f'<div class="section"><h2>🔔 Needs your attention today</h2><ul class="wl-attn">{items}</ul></div>')
    else:
        out.append('<div class="section"><h2>🔔 Needs your attention today</h2>'
                   '<p class="wl-nodata">Nothing unusual today — no targets reached, no big moves, no '
                   'earnings or dividend dates this week.</p></div>')

    in_buy = sum(1 for v in views if v["target"]["status"] == "buy_zone")
    positive = sum(1 for v in views if v["summary_token"] == "positive")
    stats = (f'{len(views)} stock{"s" if len(views) != 1 else ""} · {positive} with mostly positive signals'
             + (f" · {in_buy} in your buy zone" if in_buy else ""))
    out.append(f'<div class="section"><h2>⭐ Your watchlist</h2><p class="dq-note" style="margin:-6px 0 12px;">'
               f'{_e(stats)}. Click a stock to jump to its card; click a column header to sort.</p>'
               f'{_table(report_gen, views)}</div>')
    for v in views:
        out.append(render_card(report_gen, v, charts.get(_anchor(v)), usd_kes, today))
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
    body = (f'<div class="banner banner-danger"><b>The watchlist page couldn\'t be built this time.</b> '
            f'{_e(message)}<br>Your watchlist file is untouched. Try updating again; if it keeps happening, '
            f'the details are in logs/analyzer.log.</div>' + _NO_APP_HELP +
            '<div id="wl-add-panel" class="section app-only"></div>')
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
            charts[_anchor(view)] = make_chart(report_gen, row["result"].get("data"), view)

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
