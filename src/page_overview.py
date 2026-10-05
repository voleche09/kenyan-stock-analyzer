"""
🏠 Overview — the home page.

Crucial first: when a portfolio is set up, a "Today for you" strip (your net
worth and today's change, your next payment, your watchlist); then the
market — the pulse figures, how broadly it moved, today's top movers,
sectors — and every stock with its signal, price (and price check), change,
30-day trend and score. The heatmaps and charts live on Market map.
"""

import html as html_mod

from markupsafe import Markup, escape

import svg_charts as sc
import ui_kit as ui


def _join(parts):
    return Markup("".join(str(p) for p in parts if p))


def _pct(v, d=2):
    if v is None:
        return "—"
    return f"{v:+.{d}f}%"


# ------------------------------------------------------------------ today for you
def _for_you(nw, events, watch_count):
    """Three tiles linking into your own pages. Amounts are private."""
    tiles = []
    if nw:
        delta = ui.delta(nw["day_pct"]) if nw["day_pct"] is not None else None
        today = (f'today {"+" if (nw["day_value"] or 0) >= 0 else "−"}KES {abs(nw["day_value"]):,.0f}'
                 if nw["day_value"] is not None else "bonds don't move daily")
        tiles.append(ui.kpi("Your net worth", f"KES {nw['total']:,.0f}", private=True, delta_html=delta,
                            sub=today, href_to="portfolio.html#summary", term="net-worth"))
        nxt = next((e for e in events if e[2]), events[0] if events else None)
        if nxt:
            tiles.append(ui.kpi("Next for you", nxt[2] or nxt[1], private=bool(nxt[2]),
                                sub=f"{nxt[0]} · {nxt[1]}" if nxt[2] else nxt[0],
                                href_to="portfolio.html#summary"))
    if watch_count:
        tiles.append(ui.kpi("Your watchlist", f"{watch_count} stock{'s' if watch_count != 1 else ''}",
                            sub="today's alerts and your buy / sell targets", href_to="watchlist.html"))
    if not tiles:
        return ""
    return ui.section(ui.kpi_row(tiles, cls="for-you"), title="Today for you", icon="star", sec_id="for-you",
                      sub="Private: built from your own portfolio files on this computer.")


# ------------------------------------------------------------------ the market
def _pulse(total, bullish, bearish, neutral, breadth):
    kpis = [ui.kpi("Stocks", str(total), sub="traded on the NSE today"),
            ui.kpi("Bullish", str(bullish), tone="up", term="tv-rating", sub="TradingView buy ratings"),
            ui.kpi("Bearish", str(bearish), tone="down", term="tv-rating", sub="sell ratings"),
            ui.kpi("Neutral", str(neutral), sub="neither")]
    for key, label, term, sub in (("pct_above_sma50", "Above SMA50", "sma", "trading above their 50-day average"),
                                  ("pct_bullish_macd", "Bullish MACD", "macd", "momentum building up"),
                                  ("pct_rsi_above_50", "RSI > 50", "rsi", "more buying than selling pressure")):
        if breadth and key in breadth:
            kpis.append(ui.kpi(label, f"{breadth[key]}%", term=term, sub=sub))
    return ui.kpi_row(kpis, cls="kpis-pulse")


def _breadth(stocks):
    up = sum(1 for s in stocks if (s.get("change") or 0) > 0)
    down = sum(1 for s in stocks if (s.get("change") or 0) < 0)
    flat = len(stocks) - up - down
    total = len(stocks) or 1
    bar = (f'<div class="breadth" role="img" aria-label="{up} rose, {down} fell, {flat} unchanged">'
           f'<i class="b-up" style="width:{up / total * 100:.1f}%"></i>'
           f'<i class="b-flat" style="width:{flat / total * 100:.1f}%"></i>'
           f'<i class="b-down" style="width:{down / total * 100:.1f}%"></i></div>'
           f'<div class="breadth-legend"><span><i class="sw b-up"></i><b>{up}</b> rose</span>'
           f'<span><i class="sw b-flat"></i><b>{flat}</b> unchanged</span>'
           f'<span><i class="sw b-down"></i><b>{down}</b> fell</span></div>')
    lean = ("More stocks rose than fell — a broad up day." if up > down * 1.2 else
            "More stocks fell than rose — a broad down day." if down > up * 1.2 else
            "Rises and falls were roughly balanced.")
    return ui.card(Markup(bar + f'<p class="footnote">{escape(lean)}</p>'), title="How broadly the market moved",
                   term="breadth", icon="activity")


def _movers(rg, title, rows, tone):
    peak = max((abs(r["change"]) for r in rows if r.get("change") is not None), default=0) or 1
    body = []
    for r in rows:
        link = ui.href(r.get("report_file") or "")
        logo = rg._ticker_logo_html(r["symbol"])
        name = r.get("name") or ""
        sym = Markup(f'{logo}<span>{escape(r["symbol"])}<small>{escape(name)}</small></span>')
        sym = Markup(f'<a class="sym" href="{link}">{sym}</a>') if link else Markup(f'<span class="sym">{sym}</span>')
        width = abs(r["change"]) / peak * 100 if r.get("change") is not None else 0
        body.append([
            ui.cell(sym, sort=r["symbol"], tip=None),
            ui.cell(Markup(f'<span class="mbar" data-tone="{tone}"><i style="width:{width:.0f}%"></i></span>'
                           f'<span class="num">{_pct(r.get("change"))}</span>'),
                    sort=r.get("change"), tone=tone),
        ])
    cols = [ui.Col("Symbol", sort="text"), ui.Col("Change", sort="number")]
    return ui.card(ui.table(cols, body, sortable=False, empty="No moves today"), title=title,
                   icon="trend-up" if tone == "up" else "activity")


def _sectors(sectors):
    if not sectors:
        return ""
    rows = [{"label": name, "value": d.get("avg_change_pct"),
             "sub": f'{d.get("count", 0)} stock{"s" if d.get("count", 0) != 1 else ""}'}
            for name, d in sectors.items()]
    return ui.card(sc.hbars(rows, fmt=sc.Fmt(suffix="%", decimals=2, sign=True), diverging=True),
                   title="Sectors today", sub="Average change of the stocks in each sector.",
                   icon="layers", actions=Markup('<a href="sectors.html" class="small">All sector detail ›</a>'))


def _all_stocks(rg, stocks, nse_results):
    pv = {"ok": ("✓", "up", "Verified against independent source"),
          "mismatch": ("❗", "down", ""), "stale": ("🕒", "warn", ""),
          "unverified": ("", "flat", "No independent source to compare")}
    rows, templates = [], []
    kes = sc.Fmt("KES ", 2)
    for s in stocks:
        tip_id = f"tip-{ui.slug(s['symbol'])}"
        templates.append(f'<template id="{tip_id}">{html_mod.unescape(rg._stock_tip(s))}</template>')
        link = ui.href(s.get("report_file") or "")
        logo = rg._ticker_logo_html(s["symbol"])
        name = s.get("name") or ""
        inner = Markup(f'{logo}<span>{escape(s["symbol"])}<small>{escape(name)}</small></span>')
        sym = Markup(f'<a href="{link}" class="stock-link sym">{inner}</a>') if link else \
            Markup(f'<span class="stock-link sym">{inner}</span>')
        star = Markup(f'<button type="button" class="wl-star app-only" data-symbol="{escape(s["symbol"])}" '
                      f'data-market="NSE" data-name="{escape(name or s["symbol"])}" '
                      f'title="Add {escape(s["symbol"])} to your watchlist">☆</button>')
        val = s.get("validation") or {}
        mark, tone, tip0 = pv.get(val.get("status", "unverified"), ("", "flat", ""))
        mk = (f' <span class="pv-mark" data-tone="{tone}" title="{escape(val.get("note") or tip0)}">{mark}</span>'
              if mark else "")
        price = f"{s['price']:.2f}" if s.get("price") else "—"
        res = (nse_results or {}).get(s["symbol"]) or {}
        df = res.get("data")
        closes = [float(v) for v in df["close"].tail(30)] if df is not None and "close" in getattr(df, "columns", ()) else []
        sc_ = s.get("score")
        score = (Markup(f'<span class="score {"score-high" if sc_ >= 70 else "score-mid" if sc_ >= 45 else "score-low"} hint">'
                        f'{sc_}</span>') if sc_ is not None else Markup('<span class="score undefined hint">—</span>'))
        rows.append([
            {"html": Markup(f'<span class="sym-row">{sym}{star}</span>'), "sort": s["symbol"]},
            {"html": Markup(f'<span class="pill {escape(s.get("signal_class") or "undefined")}">'
                            f'{escape(s.get("signal_label") or "—")}</span>'), "sort": s.get("signal_label")},
            {"html": Markup(f'{escape(price)}{mk}'), "sort": s.get("price")},
            {"html": _pct(s.get("change")), "sort": s.get("change"),
             "tone": None if s.get("change") is None else ("up" if s["change"] > 0 else "down" if s["change"] < 0 else None)},
            {"html": sc.sparkline(closes, label=f'{s["symbol"]}, last 30 trading days', fmt=kes, area=False)},
            {"html": score, "sort": sc_},
        ])
    cols = [ui.Col("Symbol", sort="text"),
            ui.Col("TV Signal", sort="signal", term="tv-rating", title="TradingView Buy/Sell rating — sorts Strong Buy → Strong Sell"),
            ui.Col("Price", sort="number", term="price-check"), ui.Col("Change", sort="number", term="day-change"),
            ui.Col("30 days", sort=None),
            ui.Col("Score", sort="number", term="factor-score", title="0-100 factor screen — hover a score for the breakdown")]
    attrs = [f'data-tip-ref="tip-{ui.slug(s["symbol"])}"' for s in stocks]
    table = ui.table(cols, rows, table_id="mainTable", filter_placeholder="🔍 Filter by symbol or name…",
                     row_attrs=attrs)
    return ui.section(Markup(
        '<p class="dq-note">💡 Hover a stock symbol or its score for a preview — price, today-vs-yesterday, signal '
        'and the full factor breakdown behind the score. <strong>Click any column header to sort</strong> (click '
        'again to reverse).</p>') + table + Markup("".join(templates)),
        title="All Stocks — Signal & Score", sec_id="all-stocks", icon="bar")


def build(rg, *, stocks, gainers, losers, sectors, breadth, bullish, bearish, neutral, total,
          nse_results=None, networth=None, events=None, watch_count=None):
    """The whole Overview page body (Markup)."""
    by_symbol = {s["symbol"]: s for s in stocks}
    # The movers lists carry only symbol and change; add each company's name and page.
    gainers = [dict(by_symbol.get(g["symbol"], {}), **g) for g in gainers]
    losers = [dict(by_symbol.get(g["symbol"], {}), **g) for g in losers]
    return _join([
        Markup('<p class="page-intro">Your at-a-glance view: the market pulse, top movers and the Buy/Sell signal, '
               'price &amp; score for every stock. Hover a symbol or score for a preview. For the market heatmaps and '
               'charts, see the <a href="visuals.html">🗺️ Visuals</a> tab.</p>'),
        _for_you(networth, events or [], watch_count),
        ui.section(_join([
            _pulse(total, bullish, bearish, neutral, breadth),
            Markup('<p class="dq-note">📊 Looking for the heatmap and charts? They moved to the '
                   '<a href="visuals.html">🗺️ Visuals</a> tab to keep this page uncluttered.</p>'),
            Markup('<div class="grid-2 market-row">' + str(_breadth(stocks)) + str(_sectors(sectors)) + '</div>'),
        ]), title="Market pulse", icon="pulse", sec_id="pulse"),
        Markup('<div class="grid-2 movers">' + str(_movers(rg, "Top Gainers", gainers[:10], "up"))
               + str(_movers(rg, "Top Losers", losers[:10], "down")) + '</div>'),
        _all_stocks(rg, stocks, nse_results),
    ])
