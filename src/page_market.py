"""
The market pages: Technicals, Fundamentals, Dividends, Next earnings,
Sectors and Data quality.

Each page leads with a strip of the figures that matter (how many stocks
are bullish, who pays the highest yield, who reports next…), then the full
table — sortable, filterable, numbers right-aligned, good / average / weak
in one colour scheme from the theme — with the plain-English guide one
click away. A stock's hover preview is written once per page and pointed
at, instead of being repeated in every cell that shows it.

The wording and figures are the old pages' own; tools/factcheck.py compares
the two versions.
"""

import datetime as dt
import html as html_mod
import re

from markupsafe import Markup, escape

import svg_charts as sc
import ui_kit as ui


def _join(parts):
    return Markup("".join(str(p) for p in parts if p))


def _num(v):
    return float(v) if sc.ok(v) else None


_PV = {"ok": ("✓", "up", "Verified against independent source"),
       "mismatch": ("❗", "down", ""), "stale": ("🕒", "warn", ""),
       "unverified": ("", "flat", "No independent source to compare")}

# Most bullish first when a signal column is sorted.
_SIGNAL_RANK = {"golden_cross": 6, "bullish_cross": 6, "bullish": 5, "oversold": 4.5, "high_volume": 4,
                "neutral": 3, "normal": 3, "within_bands": 3, "low_volume": 2, "overbought": 1.5,
                "bearish": 1, "bearish_cross": 0, "death_cross": 0}


class _Cells:
    """The stock cells the market tables share, plus each stock's hover
    preview — written once per page as a <template> the cells point at."""

    def __init__(self, rg):
        self.rg = rg
        self._tips = {}

    def tip(self, s):
        key = f"tip-{ui.slug(s['symbol'])}"
        if key not in self._tips:
            # _stock_tip escapes for an attribute; a template holds plain HTML
            self._tips[key] = html_mod.unescape(self.rg._stock_tip(s))
        return key

    def templates(self):
        return Markup("".join(f'<template id="{k}">{v}</template>' for k, v in self._tips.items()))

    def symbol(self, s, star=False):
        link = ui.href(s.get("report_file") or "")
        name = s.get("name") or ""
        inner = Markup(f'{self.rg._ticker_logo_html(s["symbol"])}<span>{escape(s["symbol"])}'
                       + (f'<small title="{escape(name)}">{escape(name)}</small>' if name else "") + "</span>")
        sym = (Markup(f'<a href="{link}" class="stock-link sym">{inner}</a>') if link
               else Markup(f'<span class="stock-link sym">{inner}</span>'))
        if star:
            sym += Markup(f'<button type="button" class="wl-star app-only" data-symbol="{escape(s["symbol"])}" '
                          f'data-market="NSE" data-name="{escape(name or s["symbol"])}" '
                          f'title="Add {escape(s["symbol"])} to your watchlist">☆</button>')
        return ui.cell(sym, sort=s["symbol"], tip_ref=self.tip(s))

    @staticmethod
    def price(s):
        val = s.get("validation") or {}
        mark, tone, tip0 = _PV.get(val.get("status", "unverified"), ("", "flat", ""))
        mk = (Markup(f' <span class="pv-mark" data-tone="{tone}" title="{escape(val.get("note") or tip0)}">'
                     f'{mark}</span>') if mark else "")
        price = f"{s['price']:.2f}" if s.get("price") else "—"
        return ui.cell(Markup(f"{escape(price)}{mk}"), sort=s.get("price"))

    @staticmethod
    def change(s):
        c = s.get("change")
        return ui.cell(f"{c:+.2f}%" if c is not None else "—", sort=c,
                       tone=None if c is None else ("up" if c > 0 else "down" if c < 0 else None))

    @staticmethod
    def signal(value, text=None):
        v = str(value or "undefined")
        return ui.cell(Markup(f'<span class="pill {escape(v)}">{escape(text or v)}</span>'),
                       sort=_SIGNAL_RANK.get(v))

    def score(self, s):
        sc_ = s.get("score")
        if sc_ is None:
            return ui.cell(Markup('<span class="score undefined hint">—</span>'), tip_ref=self.tip(s))
        cls = "score-high" if sc_ >= 70 else "score-mid" if sc_ >= 45 else "score-low"
        return ui.cell(Markup(f'<span class="score {cls} hint">{escape(sc_)}</span>'), sort=sc_,
                       tip_ref=self.tip(s))


def _page(intro, *parts, cells=None):
    return _join([Markup(f'<p class="page-intro">{intro}</p>'), *parts, cells.templates() if cells else ""])


# ------------------------------------------------------------------ technicals
def technicals(rg, stocks):
    c = _Cells(rg)
    rows = []
    for s in stocks:
        rsi = _num(s.get("rsi"))
        rows.append([c.symbol(s), c.price(s), c.change(s),
                     ui.cell(f"{rsi:.1f}" if rsi else "—", sort=rsi),
                     c.signal(s["trend"]), c.signal(s["ma"], s["ma"].replace("_", " ")),
                     c.signal(s["macd"], s["macd"].replace("_", " ")), c.signal(s["stochastic"]),
                     c.signal(s["volume_signal"], s["volume_signal"].replace("_", " ")), c.signal(s["overall"])])
    cols = [ui.Col("Symbol", sort="text"), ui.Col("Price", sort="number", term="price-check"),
            ui.Col("Change", sort="number", term="day-change"), ui.Col("RSI", sort="number", term="rsi"),
            ui.Col("Trend", sort="number", term="sma", title="Price above (bullish) or below its 50-day average"),
            ui.Col("MA", sort="number", term="sma", title="20-day average vs 50-day average"),
            ui.Col("MACD", sort="number", term="macd"), ui.Col("Stoch", sort="number", term="stochastic"),
            ui.Col("Vol", sort="number", term="volume"), ui.Col("Overall", sort="number", term="signal")]
    n = len(stocks) or 1

    def count(key, values):
        return sum(1 for s in stocks if s.get(key) in values)
    up, down = count("overall", ("bullish",)), count("overall", ("bearish",))
    above = count("trend", ("bullish",))
    hot = sum(1 for s in stocks if (_num(s.get("rsi")) or 0) > 70)
    cold = sum(1 for s in stocks if _num(s.get("rsi")) is not None and s["rsi"] < 30)
    crosses = count("ma", ("golden_cross", "death_cross")) + count("macd", ("bullish_cross", "bearish_cross"))
    strip = ui.kpi_row([
        ui.kpi("Overall bullish", f"{up}", tone="up", sub=f"{up / n * 100:.0f}% of {len(stocks)} stocks", term="signal"),
        ui.kpi("Overall bearish", f"{down}", tone="down", sub=f"{down / n * 100:.0f}% of stocks"),
        ui.kpi("Above their 50-day average", f"{above}", sub="an upward trend", term="sma"),
        ui.kpi("RSI above 70", f"{hot}", sub="overbought — may pull back", term="rsi"),
        ui.kpi("RSI below 30", f"{cold}", sub="oversold — may bounce", term="rsi"),
        ui.kpi("Crosses today", f"{crosses}", sub="a moving-average or MACD line crossed", term="macd"),
    ], cls="kpis-stats")
    table = ui.table(cols, rows, table_id="mainTable", filter_placeholder="🔍 Filter by symbol or name…")
    return _page("Momentum &amp; trend indicators for every stock. Green = bullish, red = bearish, amber = neutral.",
                 strip,
                 ui.section(Markup('<p class="dq-note">Click a column to sort — signal columns sort most bullish '
                                   'first. Hover a symbol or score for its preview.</p>') + table,
                            title="Technical Indicators", sec_id="technical-indicators", icon="activity"),
                 cells=c)


# ------------------------------------------------------------------ fundamentals
_FUND_EXPLAIN = [
    ("P/E — Price to Earnings",
     "How many shillings you pay for each 1 shilling of yearly profit.",
     "P/E of 10 → you pay KES 10 for every KES 1 the company earns a year (about 10 years of profit to earn back the price).",
     "Under 10 = cheap (or troubled); 10–20 = fair; over 25 = expensive (fast growth expected). Always compare within the same sector."),
    ("PEG — P/E adjusted for growth",
     "A high P/E is fine if profits grow fast. PEG divides the P/E by the growth rate to check that.",
     "P/E of 20 but profits growing 20%/yr → PEG = 1.0 (fairly priced).",
     "Under 1.0 = attractively priced for its growth; around 1 = fair; over 2 = pricey."),
    ("P/B — Price to Book",
     "Price versus the company's net worth on paper (assets minus debts) per share.",
     "P/B of 1 = you pay exactly the company's book value; 2 = twice that.",
     "Under 1 can be cheap (common for NSE banks); 1–3 is typical; high = paying a premium for brand/growth."),
    ("EPS — Earnings Per Share",
     "The company's yearly profit split across each share — the profit that 'belongs' to one share.",
     "EPS of KES 5 → each share earned 5 shillings this year. It's the 'E' in P/E.",
     "Higher and rising year on year is better. Negative EPS = the company is losing money."),
    ("ROE — Return on Equity",
     "How much profit the company squeezes out of shareholders' money.",
     "ROE of 20% → for every KES 100 of shareholder money, it makes KES 20 profit a year.",
     "15–20% = good; above 20% = excellent (check it's sustainable); below 10% = weak."),
    ("Net Margin",
     "Out of every 100 shillings of sales, how many shillings become actual profit after ALL costs and taxes.",
     "Net margin of 25% → KES 25 profit from every KES 100 of sales.",
     "10%+ = solid; 20%+ = strong. It varies a lot by industry, so compare like with like."),
    ("D/E — Debt to Equity",
     "How much the company has borrowed versus what shareholders own — its debt load and risk.",
     "D/E of 1.0 → debt equals shareholder equity; 0.3 → very little debt.",
     "Under 0.5 = conservative/safe; 0.5–1.5 = moderate; over 2 = risky. Banks naturally run higher."),
    ("Rev Growth — Revenue Growth",
     "How much total sales grew compared with a year ago — is the business getting bigger?",
     "+15% → sales are 15% higher than last year; a red −5% → sales shrank.",
     "10%+ = healthy; flat is okay for a mature company; negative is a warning sign."),
    ("Yield — Dividend Yield",
     "The yearly dividend as a percentage of the share price — your income return just for holding it.",
     "Yield of 6% → KES 6 a year for every KES 100 invested.",
     "4–8% is attractive on the NSE — but confirm it's sustainable (see the Dividends page)."),
    ("Score (0–100)",
     "Our own transparent screen that blends value, quality, momentum, dividend and liquidity into one number.",
     "85 = strong across the board; 40 = weak. Higher means stronger on these factors overall.",
     "A quick way to compare stocks at a glance — it is a mechanical guide, NOT a recommendation to buy or sell."),
]


def _explainer(cards, title, intro, *, det_id, good=True):
    items = []
    for c in cards:
        name, what, eg = c[0], c[1], c[2]
        items.append(f'<div class="explain-card"><h4>{escape(name)}</h4><p>{escape(what)}</p>'
                     f'<p class="eg">📌 <strong>Example:</strong> {escape(eg)}</p>'
                     + (f'<p class="good">✅ <strong>What\'s good:</strong> {escape(c[3])}</p>' if good else "")
                     + "</div>")
    return ui.details(title, Markup(f'<p class="page-intro">{escape(intro)}</p>'
                                    f'<div class="explain-grid">{"".join(items)}</div>'), det_id=det_id)


def _leaders(title, rows, fmt, term=None, tone=None):
    """A short ranked list (top 5) for the summary row."""
    if not rows:
        return ""
    tone_attr = f' data-tone="{tone}"' if tone else ""
    items = "".join(
        f'<li><span class="lead-rank">{i}</span><span class="lead-sym">{escape(s["symbol"])}</span>'
        f'<b class="num"{tone_attr}>{escape(fmt(v))}</b></li>'
        for i, (s, v) in enumerate(rows, 1))
    return ui.card(Markup(f'<ol class="leaders">{items}</ol>'), title=title, term=term, cls="lead-card")


def fundamentals(rg, stocks):
    c = _Cells(rg)

    def fcls(metric, raw):
        return {"good": "fgood", "mid": "fmid", "bad": "fbad"}.get(rg._fund_verdict(metric, raw), "")

    def fc(metric, raw, text):
        return ui.cell(text, sort=_num(raw), cls=fcls(metric, raw))
    rows = []
    for s in stocks:
        rg_ = s.get("revenue_growth")
        rows.append([
            c.symbol(s), c.price(s),
            ui.cell(rg._fmt_currency(s["market_cap"]) if s.get("market_cap") else "—",
                    sort=_num(s.get("market_cap")), cls="mcap-cell"),
            fc("pe", s.get("pe_ratio"), f"{s['pe_ratio']:.1f}" if s.get("pe_ratio") else "—"),
            fc("peg", s.get("peg_ratio"), f"{s['peg_ratio']:.2f}" if s.get("peg_ratio") else "—"),
            fc("pb", s.get("price_to_book"), f"{s['price_to_book']:.2f}" if s.get("price_to_book") else "—"),
            fc("eps", s.get("eps"), f"{s['eps']:.2f}" if s.get("eps") is not None else "—"),
            fc("roe", s.get("roe"), f"{s['roe']:.1f}%" if s.get("roe") is not None else "—"),
            fc("nm", s.get("net_margin"), f"{s['net_margin']:.1f}%" if s.get("net_margin") is not None else "—"),
            fc("de", s.get("debt_to_equity"),
               f"{s['debt_to_equity']:.2f}" if s.get("debt_to_equity") is not None else "—"),
            fc("rg", rg_, f"{rg_:+.1f}%" if rg_ is not None else "—"),
            fc("yield", s.get("dividend_yield"), f"{s['dividend_yield']:.1f}%" if s.get("dividend_yield") else "—"),
            c.score(s),
        ])
    cols = [ui.Col("Symbol", sort="text"), ui.Col("Price", sort="number", term="price-check"),
            ui.Col("Market Cap", sort="number", term="market-cap"),
            ui.Col("P/E", sort="number", term="pe", title="Price / Earnings"),
            ui.Col("PEG", sort="number", term="peg", title="P/E adjusted for growth"),
            ui.Col("P/B", sort="number", term="price-to-book", title="Price / Book value"),
            ui.Col("EPS", sort="number", term="eps", title="Earnings per share"),
            ui.Col("ROE", sort="number", term="roe", title="Return on Equity"),
            ui.Col("Net Margin", sort="number", term="net-margin", title="Net profit margin"),
            ui.Col("D/E", sort="number", term="debt-to-equity", title="Debt / Equity"),
            ui.Col("Rev Growth", sort="number", term="revenue-growth", title="Revenue growth vs last year"),
            ui.Col("Yield", sort="number", term="dividend-yield"),
            ui.Col("Score", sort="number", term="factor-score")]

    def top(key, *, reverse=True, keep=lambda v: True):
        vals = [(s, _num(s.get(key))) for s in stocks]
        vals = [(s, v) for s, v in vals if v is not None and keep(v)]
        return sorted(vals, key=lambda t: t[1], reverse=reverse)[:5]
    leaders = Markup('<div class="lead-grid">'
                     + str(_leaders("Cheapest by P/E", top("pe_ratio", reverse=False, keep=lambda v: v > 0),
                                    lambda v: f"{v:.1f}", "pe", "up"))
                     + str(_leaders("Highest dividend yield", top("dividend_yield"), lambda v: f"{v:.1f}%",
                                    "dividend-yield", "up"))
                     + str(_leaders("Highest ROE", top("roe"), lambda v: f"{v:.1f}%", "roe", "up"))
                     + str(_leaders("Highest factor score", top("score"), lambda v: f"{v:.0f}", "factor-score"))
                     + "</div>")
    table = ui.table(cols, rows, table_id="mainTable", filter_placeholder="🔍 Filter by symbol or name…")
    return _page(
        "Valuation, quality, growth &amp; health metrics (from TradingView). "
        '<span class="fgood">Green = good</span>, <span class="fmid">amber = average</span>, '
        '<span class="fbad">red = weak/risky</span>, uncoloured = no data. New to these? The plain-English guide '
        "with examples is right below the table.",
        ui.section(Markup('<p class="dq-note">The top five on four measures people often start from — a '
                          'starting point for your own research, not a recommendation.</p>') + leaders,
                   title="Leaders today", sec_id="leaders", icon="trend-up"),
        ui.section(table, title="Fundamentals", sec_id="fundamentals-table", icon="bar"),
        _explainer(_FUND_EXPLAIN, "What these numbers mean (plain English)",
                   "No accounting needed — here is each column explained simply, with an example and what counts "
                   "as a good value.", det_id="explained"),
        cells=c)


# ------------------------------------------------------------------ dividends
def _days_label(delta):
    return "today" if delta == 0 else (f"in {delta}d" if delta > 0 else f"{-delta}d ago")


def _bc_chip(date_str, today):
    """Book-closure / ex-date chip: passed, within a week, or later."""
    if not date_str:
        return Markup('<span class="exdate-none">—</span>'), None
    try:
        d = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return escape(date_str), None
    delta = (d - today).days
    cls = "bc-passed" if delta < 0 else "bc-soon" if delta <= 7 else "bc-future"
    return Markup(f'<span class="{cls}">{escape(date_str)}</span>'), d


def dividends(rg, stocks, today=None):
    today = today or dt.datetime.now().date()
    c = _Cells(rg)
    cal = []
    for s in stocks:
        ex = s.get("ex_date")
        if not ex:
            continue
        try:
            d = dt.datetime.strptime(ex, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        delta = (d - today).days
        cls = "cal-passed" if delta < 0 else "cal-near" if delta <= 30 else "cal-far"
        cal.append({"s": s, "ex": ex, "cls": cls, "delta": delta})
    upcoming = sorted([r for r in cal if r["delta"] >= 0], key=lambda r: r["delta"])
    past = sorted([r for r in cal if r["delta"] < 0], key=lambda r: -r["delta"])

    def cal_table(rows, empty):
        if not rows:
            return Markup(f'<p class="dq-note">{escape(empty)}</p>')
        body = []
        for r in rows:
            s = r["s"]
            dps, yld = s.get("dps"), s.get("dividend_yield")
            body.append([
                ui.cell(Markup(f'{rg._ticker_logo_html(s["symbol"])}<strong>{escape(s["symbol"])}</strong>'),
                        sort=s["symbol"], cls="sym-cell"),
                ui.cell(f"{round(dps, 2):g}" if dps else "0", sort=_num(dps)),
                ui.cell(f"{yld:.1f}%" if yld else "—", sort=_num(yld)),
                ui.cell(Markup(f'<span class="cal-chip {r["cls"]}">{escape(r["ex"])}</span>'), sort=r["ex"]),
                ui.cell(_days_label(r["delta"]), sort=r["delta"])])
        return ui.table([ui.Col("Symbol", sort="text"), ui.Col("Div KES", sort="number", term="dividend-dates"),
                         ui.Col("Yield", sort="number", term="dividend-yield"), ui.Col("Ex-Date", sort="text"),
                         ui.Col("When", sort="number")], body)

    def amount(s):
        dps = s.get("dps")
        if dps and dps > 0:
            if s.get("dividend_status") == "unverified":
                return ui.cell(Markup(f'<span class="div-unverified" title="TradingView figure — not cross-checked">'
                                      f'{round(dps, 2):g}*</span>'), sort=dps)
            return ui.cell(Markup(f'<span class="div-pay">{round(dps, 2):g}</span>'), sort=dps)
        return ui.cell(Markup('<span class="div-zero">0</span>'), sort=0)

    payers = sorted([s for s in stocks if s.get("dps") and s["dps"] > 0],
                    key=lambda s: s.get("dividend_yield") or 0, reverse=True)
    others = [s for s in stocks if not (s.get("dps") and s["dps"] > 0)]
    rows = []
    for s in payers + others:
        chip, d = _bc_chip(s.get("book_closure") or s.get("ex_date"), today)
        rows.append([c.symbol(s), amount(s),
                     ui.cell(f"{s['dividend_yield']:.1f}%" if s.get("dividend_yield") else "—",
                             sort=_num(s.get("dividend_yield"))),
                     ui.cell(chip, sort=d.isoformat() if d else None)])
    all_table = ui.table([ui.Col("Symbol", sort="text"), ui.Col("Div KES", sort="number", term="dividend-dates"),
                          ui.Col("Yield", sort="number", term="dividend-yield"),
                          ui.Col("Book closure / Ex-date", sort="text", term="dividend-dates")],
                         rows, table_id="mainTable", filter_placeholder="🔍 Filter by symbol or name…")
    nxt = upcoming[0] if upcoming else None
    best = payers[0] if payers else None
    soon = sum(1 for r in upcoming if r["delta"] <= 30)
    strip = ui.kpi_row([
        ui.kpi("Paying a dividend", f"{len(payers)} of {len(stocks)}", sub="stocks with a declared dividend",
               term="est-dividend"),
        ui.kpi("Highest yield", f"{best['dividend_yield']:.1f}%" if best and best.get("dividend_yield") else "—",
               sub=best["symbol"] if best else None, tone="up" if best else None, term="dividend-yield"),
        ui.kpi("Next ex-dividend date", nxt["ex"] if nxt else "—",
               sub=f"{nxt['s']['symbol']} · {_days_label(nxt['delta'])}" if nxt else "none scheduled",
               term="ex-dividend"),
        ui.kpi("Ex-dates in the next 30 days", f"{soon}", sub="buy before the date to qualify"),
    ], cls="kpis-stats")
    yields = sc.hbars([{"label": s["symbol"], "value": s.get("dividend_yield"), "href": s.get("report_file"),
                        "sub": f"KES {round(s['dps'], 2):g}/share", "tone": "accent"}
                       for s in payers[:10] if s.get("dividend_yield")],
                      fmt=sc.Fmt(suffix="%", decimals=1), empty="No dividend yields on record")
    yields_card = ui.card(yields, title="Highest dividend yields", icon="coins", term="dividend-yield",
                          sub="The ten highest yields today — confirm a dividend is sustainable before relying on it.")
    # The short "upcoming" list and the yields chart share a column beside the longer "past" list.
    calendar = ui.section(_join([
        Markup('<div class="cal-legend"><span class="cal-chip cal-near">soon (≤30d)</span>'
               '<span class="cal-chip cal-far">later (&gt;30d)</span>'
               '<span class="cal-chip cal-passed">passed</span></div>'),
        Markup('<div class="grid-2 dividend-cal"><div class="stack">'
               + str(ui.card(cal_table(upcoming, "No upcoming ex-dividend dates."),
                             title=Markup(f'🟢 Upcoming Ex-Dividend Dates <span class="cal-count">({len(upcoming)})</span>')))
               + str(yields_card) + '</div>'
               + str(ui.card(cal_table(past, "No past ex-dividend dates recorded."),
                             title=Markup(f'🔴 Past Ex-Dividend Dates <span class="cal-count">({len(past)})</span>')))
               + '</div>'),
        Markup('<p class="footnote">Own the shares before the book-closure/ex-date to qualify.</p>'),
    ]), title="Dividend Calendar", sec_id="dividend-calendar", icon="calendar", term="dividend-dates")
    return _page(
        'Declared dividends from the NSE calendar (mystocks). '
        '<span class="div-unverified">amber*</span> = TradingView figure that could not be cross-checked.',
        strip, calendar,
        ui.section(Markup('<div class="cal-legend">Book closure / ex-date: <span class="bc-future">upcoming</span>'
                          '<span class="bc-soon">within a week</span><span class="bc-passed">passed</span></div>')
                   + all_table, title="All Dividends", sec_id="all-dividends", icon="layers"),
        cells=c)


# ------------------------------------------------------------------ next earnings
def earnings(rg, stocks, today=None):
    today = today or dt.datetime.now().date()
    c = _Cells(rg)
    raw = []
    for s in stocks:
        nd = s.get("earnings_next_date")
        if not nd:
            continue
        try:
            d = dt.datetime.strptime(nd, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if d < today:
            continue                        # past — this page is about what's coming
        raw.append(((d - today).days, d, s))
    raw.sort(key=lambda t: t[0])
    rows = []
    for days, d, s in raw:
        cls = "bc-passed" if days == 0 else "bc-soon" if days <= 7 else "bc-future"
        rows.append([c.symbol(s),
                     ui.cell(Markup(f'<span class="{cls}">{d:%Y-%m-%d}</span>'), sort=d.isoformat()),
                     ui.cell(_days_label(days), sort=days),
                     ui.cell(f"{s['price']:.2f}" if s.get("price") else "—", sort=s.get("price")),
                     c.change(s),
                     ui.cell(Markup(f'<span class="pill {escape(s.get("signal_class") or "undefined")}">'
                                    f'{escape(s.get("signal_label") or "—")}</span>'), sort=s.get("signal_label")),
                     c.score(s)])
    cols = [ui.Col("Symbol", sort="text"), ui.Col("Earnings Date", sort="text", term="earnings-date"),
            ui.Col("When", sort="number"), ui.Col("Price", sort="number"), ui.Col("Change", sort="number"),
            ui.Col("TV Signal", sort="signal", term="tv-rating"), ui.Col("Score", sort="number", term="factor-score")]
    table = ui.table(cols, rows, table_id="mainTable", filter_placeholder="🔍 Filter by symbol or name…",
                     empty="No upcoming earnings dates on record.")
    week = sum(1 for days, _d, _s in raw if days <= 7)
    first = raw[0] if raw else None
    strip = ui.kpi_row([
        ui.kpi("Upcoming results", f"{len(raw)}", sub="stocks with a scheduled date", term="earnings-date"),
        ui.kpi("Next to report", first[2]["symbol"] if first else "—",
               sub=f"{first[1]:%Y-%m-%d} · {_days_label(first[0])}" if first else "none scheduled"),
        ui.kpi("Within a week", f"{week}", sub="reporting in the next 7 days", tone="warn" if week else None),
    ], cls="kpis-stats")
    return _page(
        "Every stock with an upcoming earnings-release date, <strong>nearest first</strong>. Stocks with no "
        "scheduled date, or a date already in the past, are excluded. Earnings dates come from TradingView — "
        "confirm on the NSE announcement before making decisions.",
        strip,
        ui.section(_join([
            Markup('<div class="cal-legend"><span class="bc-passed">today</span><span class="bc-soon">within a week'
                   '</span><span class="bc-future">later</span></div>'),
            table,
            Markup(f'<p class="footnote">{len(raw)} stock(s) with an upcoming earnings release. Earnings dates '
                   'are only published for a subset of NSE stocks; a stock not listed here simply has no scheduled '
                   'date in the feed.</p>'),
            Markup('<div class="banner" data-tone="info">📥 <strong>Add these to your Google Calendar:</strong> '
                   'download <a href="earnings.ics" download>earnings.ics</a> → open Google Calendar → '
                   '<em>Settings ⚙ → Import &amp; export → Import</em> → select the file. Events include 24-hour and '
                   '1-hour reminders. The daily email attaches this file automatically — in Gmail you can also click '
                   '"Add to calendar" directly on the message.</div>'),
        ]), title="Upcoming Earnings", sec_id="upcoming-earnings", icon="calendar"),
        cells=c)


# ------------------------------------------------------------------ sectors
def sectors(rg, sectors_data, stocks):
    if not sectors_data:
        return _page("How each NSE sector performed today.",
                     ui.empty_state("No sector figures this run", "Sector averages appear after a full update.",
                                    icon="layers"))
    files = {s["symbol"]: s.get("report_file") for s in stocks}
    ordered = sorted(sectors_data.items(), key=lambda kv: kv[1].get("avg_change_pct") or 0, reverse=True)
    chart = sc.hbars([{"label": name, "value": d.get("avg_change_pct"),
                       "sub": f'{d.get("count", 0)} stock{"s" if d.get("count", 0) != 1 else ""}'}
                      for name, d in ordered], fmt=sc.Fmt(suffix="%", decimals=2, sign=True), diverging=True)
    cards = []
    for name, d in sectors_data.items():
        chg = d["avg_change_pct"]
        bull = d.get("bullish_ratio")
        members = "".join(
            (f'<a class="chip chip-none" href="{ui.href(files.get(sym))}">{escape(sym)}</a>' if files.get(sym)
             else f'<span class="chip chip-none">{escape(sym)}</span>')
            for sym in sorted(d.get("symbols") or []))
        meter = (f'<span class="meter" title="{escape(bull)}% of its stocks have a bullish signal">'
                 f'<i class="m-up" style="width:{max(0, min(float(bull), 100)):.0f}%"></i></span>'
                 if sc.ok(bull) else "")
        cards.append(
            f'<div class="sector-card" data-tone="{"up" if chg > 0 else "down" if chg < 0 else "flat"}">'
            f'<h3>{escape(name)}</h3>'
            f'<div class="sector-change {"positive" if chg >= 0 else "negative"}">{chg:+.2f}%</div>'
            f'<div class="sector-detail">{d["count"]} stock{"s" if d["count"] != 1 else ""} | '
            f'RSI {escape(d.get("avg_rsi", "—"))} | '
            f'{escape(bull)}% bullish {meter}</div>'
            + (f'<div class="sector-members">{members}</div>' if members else "") + "</div>")
    return _page(
        "How each NSE sector performed today.",
        ui.card(chart, title="Sector Performance", icon="layers", term="breadth",
                sub="Average change of the stocks in each sector, best first."),
        ui.section(Markup(f'<div class="sector-grid">{"".join(cards)}</div>'), title="Every sector",
                   sec_id="sector-cards", icon="bar",
                   sub="Each sector's average move, its stocks' average RSI and how many have a bullish signal. "
                       "Click a stock to open its page."))


# ------------------------------------------------------------------ data quality
def quality(rg, stocks, alerts):
    counts = {"ok": 0, "mismatch": 0, "stale": 0, "unverified": 0}
    mismatch = []
    for s in stocks:
        st = (s.get("validation") or {}).get("status")
        key = st if st in ("ok", "mismatch", "stale") else "unverified"
        counts[key] += 1
        if st == "mismatch":
            mismatch.append(s)
    total = len(stocks) or 1
    strip = ui.kpi_row([
        ui.kpi("Verified", str(counts["ok"]), tone="up", term="price-check", sub="✓ TradingView confirms it"),
        ui.kpi("Price mismatch", str(counts["mismatch"]), tone="down", sub="❗ differs from the NSE close"),
        ui.kpi("Stale / thin", str(counts["stale"]), tone="warn", sub="🕒 last traded over a day ago"),
        ui.kpi("Unverified", str(counts["unverified"]), sub="no independent source to compare"),
    ], cls="kpis-stats")
    bar = "".join(f'<i class="{cls}" style="width:{n / total * 100:.1f}%" title="{label}: {n}"></i>'
                  for n, cls, label in ((counts["ok"], "b-up", "Verified"), (counts["mismatch"], "b-down", "Mismatch"),
                                        (counts["stale"], "b-warn", "Stale / thin"),
                                        (counts["unverified"], "b-flat", "Unverified")) if n)
    note = ('<div class="dq-note">Prices shown are the <strong>NSE official close</strong> (afx.kwayisi.org), '
            'cross-checked against TradingView. ✓ = TradingView confirms it · ❗ = differs · 🕒 = last traded '
            '&gt;1 day ago.</div>')
    mismatch_html = ""
    if mismatch:
        items = ", ".join(f"{escape(m['symbol'])} ({(m.get('validation') or {}).get('pct_diff'):+.1f}%)"
                          for m in mismatch)
        mismatch_html = (f'<div class="banner" data-tone="danger">⚠️ TradingView differs from the NSE official close '
                         f'for: {items}</div>')
    files = {s["symbol"]: s.get("report_file") for s in stocks}
    alert_rows, kinds = [], {}
    for sym in sorted((alerts or {}).keys()):
        items = [a for a in alerts[sym] if a]
        if not items:
            continue
        for a in items:                    # "📅 Ex-dividend 2026-10-30" and "… (8.3%)" count as one kind
            kind = re.sub(r"\s*\([^)]*\)|\s*\d{4}-\d{2}-\d{2}", "", a).strip()
            kinds[kind] = kinds.get(kind, 0) + 1
        link = ui.href(files.get(sym) or "")
        name = Markup(f'<a href="{link}"><strong>{escape(sym)}</strong></a>') if link else Markup(
            f"<strong>{escape(sym)}</strong>")
        alert_rows.append([ui.cell(name, sort=sym),
                           ui.cell(Markup(" ".join(f'<span class="chip chip-neutral">{escape(a)}</span>'
                                                   for a in items)), cls="wrap-cell")])
    alerts_html = ""
    if alert_rows:
        summary = "".join(f'<span class="chip chip-none">{escape(k)} <b>×{n}</b></span>'
                          for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
        alerts_html = ui.section(
            Markup(f'<p class="chip-line">{summary}</p>')
            + ui.table([ui.Col("Symbol", sort="text"), ui.Col("Alerts", sort=None)], alert_rows,
                       table_id="alerts-table", filter_placeholder="🔍 Filter alerts…", show_first=20),
            title="Alerts & Signals", sec_id="alerts", icon="bell",
            sub="Notable per-stock signals today. The chips count each kind across the market; the table lists "
                "every stock's alerts.")
    return _page(
        "How much to trust today's prices, and notable per-stock alerts.",
        ui.section(_join([strip, Markup(f'<div class="breadth dq-bar">{bar}</div>'), Markup(note),
                          Markup(mismatch_html)]), title="Data Quality", sec_id="data-quality", icon="shield"),
        alerts_html)


# ------------------------------------------------------------------ market map (visuals)
def _heat(change, cap=3.0, style=""):
    """Attributes giving an element the theme-aware heat colour of a daily
    move: the CSS mixes up / down into the card colour by these shares (a
    lighter mix in dark mode, so the text on top stays readable in both
    themes). `style` adds other declarations, e.g. a tile's position."""
    if change is None:
        return Markup('data-dir="none"' + (f' style="{style}"' if style else ""))
    t = max(0.0, min(1.0, abs(change) / cap))
    d = "up" if change > 0 else "down" if change < 0 else "flat"
    return Markup(f'data-dir="{d}" style="{style + ";" if style else ""}--pl:{12 + 60 * t:.0f}%;--pd:{10 + 32 * t:.0f}%"')


def _tip_id(s):
    return f"tip-{ui.slug(s['symbol'])}"


def _treemap(rg, stocks):
    """The whole market as a squarified treemap: tiles sized by market cap,
    grouped by sector, coloured by today's move. Falls back to a simple grid."""
    W, H = 1000.0, 620.0
    bysec = {}
    for s in stocks:
        if s.get("market_cap"):
            bysec.setdefault(s.get("sector") or "Other", []).append(s)
    if not bysec:
        raise ValueError("no market caps")
    sec_items = sorted(([sec, sum(x["market_cap"] for x in items), items] for sec, items in bysec.items()),
                       key=lambda t: -t[1])
    sec_rects = rg._squarify(rg._tm_normalize([t[1] for t in sec_items], W, H), 0, 0, W, H)
    tiles, labels = [], []
    for (sec, _total, items), R in zip(sec_items, sec_rects):
        items = sorted(items, key=lambda x: -x["market_cap"])
        label_h = 15.0 if (R["dy"] > 44 and R["dx"] > 60) else 0.0
        srects = rg._squarify(rg._tm_normalize([x["market_cap"] for x in items], R["dx"], R["dy"] - label_h),
                              R["x"], R["y"] + label_h, R["dx"], R["dy"] - label_h)
        if label_h:
            labels.append(f'<div class="tm-label" style="left:{R["x"] / W * 100:.3f}%;top:{R["y"] / H * 100:.3f}%;'
                          f'width:{R["dx"] / W * 100:.3f}%">{escape(sec)}</div>')
        for s, r in zip(items, srects):
            wf, hf = r["dx"] / W, r["dy"] / H
            chg = s.get("change")
            f_sym = max(0.0, min(4.6, 20 * min(wf, 0.62 * hf)))      # text scales with the tile (cqw)
            inner = ""
            if r["dx"] > 26 and r["dy"] > 16:
                inner += f'<span class="tm-sym" style="font-size:{f_sym:.2f}cqw">{escape(s["symbol"])}</span>'
            if r["dx"] > 40 and r["dy"] > 34 and chg is not None:
                inner += f'<span class="tm-chg" style="font-size:{f_sym * 0.62:.2f}cqw">{chg:+.2f}%</span>'
            link = ui.href(s.get("report_file") or "") or "#"
            pos = (f'left:{r["x"] / W * 100:.3f}%;top:{r["y"] / H * 100:.3f}%;width:{wf * 100:.3f}%;'
                   f'height:{hf * 100:.3f}%')
            tiles.append(f'<a href="{link}" class="tm-tile heat" {_heat(chg, style=pos)} '
                         f'data-tip-ref="{_tip_id(s)}">{inner}</a>')
    return "".join(tiles), "".join(labels)


def visuals(rg, stocks):
    c = _Cells(rg)
    for s in stocks:
        c.tip(s)                     # one preview per stock, shared by every tile and bar below
    legend_heat = ('<span class="heat-key"><i class="heat" data-dir="down" style="--pl:72%;--pd:42%"></i>down'
                   '<i class="heat" data-dir="flat"></i>flat'
                   '<i class="heat" data-dir="up" style="--pl:72%;--pd:42%"></i>up</span>')
    try:
        tiles, labels = _treemap(rg, stocks)
        treemap = ui.section(_join([
            Markup('<p class="section-desc">The entire NSE in one map, packed like a market treemap. Each tile is a '
                   'stock, <strong>sized by market cap</strong> and grouped by sector, <strong>coloured by today\'s '
                   'move vs yesterday\'s close</strong> (stronger colour = bigger move). This is the market\'s true '
                   'shape — the giants (Safaricom, Equity, KCB, EABL…) dominate. Hover any tile for its price, previous '
                   'close, signal and score; click to open the report.</p>'),
            Markup(f'<div class="treemap">{tiles}{labels}</div>'),
            Markup(f'<div class="heat-legend">Tile size = market cap · colour = today\'s move vs yesterday: {legend_heat}'
                   ' · stronger colour = bigger move · hover a tile for details.</div>'),
        ]), title="Whole-market heatmap", sec_id="treemap", icon="map")
    except Exception as e:                                   # never lose the page over the layout
        import logging
        logging.getLogger(__name__).warning(f"Treemap layout failed ({e}); showing a simple grid")
        tiles = "".join(
            f'<a href="{ui.href(s.get("report_file") or "") or "#"}" class="heat-tile heat" '
            f'{_heat(s.get("change"))} data-tip-ref="{_tip_id(s)}"><span class="ht-sym">{escape(s["symbol"])}</span>'
            f'<span class="ht-chg">{(format(s["change"], "+.1f") + "%") if s.get("change") is not None else "—"}'
            f'</span></a>'
            for s in sorted(stocks, key=lambda s: (s.get("market_cap") or 0), reverse=True))
        treemap = ui.section(Markup('<p class="section-desc">Every stock sized by market cap, coloured by today\'s '
                                    f'move.</p><div class="heat-grid">{tiles}</div>'),
                             title="Whole-market heatmap", sec_id="treemap", icon="map")

    # ---- sector heatmap
    bysec = {}
    for s in stocks:
        bysec.setdefault(s.get("sector") or "Other", []).append(s)
    groups = []
    for sec, items in sorted(bysec.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        items = sorted(items, key=lambda s: (s.get("change") is None, -(s.get("change") or 0)))
        tiles = "".join(
            f'<a href="{ui.href(s.get("report_file") or "") or "#"}" class="heat-tile heat" '
            f'{_heat(s.get("change"), cap=4.0)} data-tip-ref="{_tip_id(s)}">'
            f'<span class="ht-sym">{escape(s["symbol"])}</span>'
            f'<span class="ht-chg">{(format(s["change"], "+.1f") + "%") if s.get("change") is not None else "—"}</span></a>'
            for s in items)
        groups.append(f'<div class="heat-sector"><div class="heat-sector-label">{escape(sec)} ({len(items)})</div>'
                      f'<div class="heat-grid">{tiles}</div></div>')
    sector_heat = ui.section(_join([
        Markup('<p class="section-desc">The same stocks, grouped by sector so you can see which parts of the market '
               'moved together today. Colour = <strong>today\'s price change vs yesterday\'s close</strong> (greener = '
               'up, redder = down, bigger move = deeper colour). Hover any tile for details.</p>'),
        Markup(f'<div class="heat-wrap">{"".join(groups)}</div>'),
        Markup('<div class="heat-legend">Daily move: <span class="heat-key">'
               '<i class="heat" data-dir="down" style="--pl:72%;--pd:42%"></i>−4%+ · 0 ·'
               '<i class="heat" data-dir="up" style="--pl:72%;--pd:42%"></i>+4%+</span>'
               ' · <span class="heat-key"><i class="heat" data-dir="none"></i>no trade</span>'
               ' · hover a tile for price, signal &amp; score · click to open the report.</div>'),
    ]), title="Heatmap by sector", sec_id="sector-heatmap", icon="layers")

    # ---- breadth + spread of moves
    adv = [s for s in stocks if (s.get("change") or 0) > 0]
    dec = [s for s in stocks if (s.get("change") or 0) < 0]
    unch = [s for s in stocks if s.get("change") == 0]
    nodata = [s for s in stocks if s.get("change") is None]
    traded = len(adv) + len(dec) + len(unch)
    total = traded or 1
    bar = "".join(f'<i class="{cls}" style="width:{n / total * 100:.1f}%" title="{lab}: {n} of {traded} traded stocks">'
                  f'</i>' for n, cls, lab in ((len(adv), "b-up", "Advancers"), (len(unch), "b-flat", "Flat"),
                                              (len(dec), "b-down", "Decliners")) if n)
    breadth = ui.card(Markup(
        '<p class="section-desc">How many stocks rose vs fell today — the market\'s breadth.</p>'
        f'<div class="breadth breadth-lg">{bar}</div>'
        '<div class="breadth-legend">'
        f'<span><i class="sw b-up"></i>▲ Advancers <b>{len(adv)}</b></span>'
        f'<span><i class="sw b-flat"></i>■ Flat <b>{len(unch)}</b></span>'
        f'<span><i class="sw b-down"></i>▼ Decliners <b>{len(dec)}</b></span>'
        + (f'<span><i class="sw b-none"></i>◻ No trade <b>{len(nodata)}</b></span>' if nodata else "")
        + "</div>"), title="Advancers vs Decliners", icon="activity", term="breadth")
    bins = [(-99, -3, "≤−3%", "down", 1.0), (-3, -2, "−3..−2", "down", .75), (-2, -1, "−2..−1", "down", .5),
            (-1, 0, "−1..0", "down", .25), (0, 0.0001, "0%", "flat", 0), (0.0001, 1, "0..1", "up", .25),
            (1, 2, "1..2", "up", .5), (2, 3, "2..3", "up", .75), (3, 99, "≥3%", "up", 1.0)]
    counts = []
    for lo, hi, lab, d, t in bins:
        n = len(unch) if lab == "0%" else len([s for s in stocks if s.get("change") is not None and lo < s["change"] <= hi])
        counts.append((n, lab, d, t))
    peak = max((n for n, *_r in counts), default=1) or 1
    hist = "".join(
        f'<div class="hist-bin" title="{n} stock(s) moved {lab} today"><b class="num">{n}</b>'
        f'<div class="hist-bar heat" data-dir="{d}" style="height:{(6 + 94 * n / peak) if n else 2:.0f}%;'
        f'--pl:{20 + 52 * t:.0f}%;--pd:{16 + 26 * t:.0f}%"></div><div class="hist-x">{lab}</div></div>'
        for n, lab, d, t in counts)
    spread = ui.card(Markup('<p class="section-desc">How many stocks fell into each move bucket — is the market '
                            f'clustered up, down, or flat?</p><div class="hist-wrap">{hist}</div>'),
                     title="Spread of today's moves", icon="bar")

    # ---- most active
    act = sorted([s for s in stocks if s.get("value_traded")], key=lambda s: s["value_traded"], reverse=True)[:10]

    def vt_txt(vt):
        return f"KES {vt / 1e9:.2f}B" if vt >= 1e9 else f"KES {vt / 1e6:.1f}M" if vt >= 1e6 else f"KES {vt / 1e3:.0f}K"
    active = ""
    if act:
        bars = sc.hbars([{"label": s["symbol"], "value": s["value_traded"], "href": s.get("report_file"),
                          "tone": ui.tone_of(s.get("change")) if s.get("change") else "flat",
                          "sub": f'{s["change"]:+.2f}% today' if s.get("change") is not None else None}
                         for s in act], fmt=vt_txt, tone_from_sign=False)
        active = ui.card(Markup('<p class="section-desc">Where the money went — stocks with the highest value traded '
                                '(KES) today. Bar length = value traded, colour = the day\'s price move. Hover for '
                                'details.</p>') + bars
                         + Markup('<p class="footnote">Value traded = shares traded × price. High value = easy to '
                                  'buy/sell without moving the price much.</p>'),
                         title="Most actively traded today", icon="coins", term="value-traded")
    return _page(
        "A visual read of the market today — hover any tile or bar for details, click a stock to open its full "
        "report. All colours use today's price change vs yesterday's close.",
        treemap, sector_heat,
        Markup(f'<div class="grid-2 map-row">{breadth}{spread}</div>'),
        active, cells=c)


# ------------------------------------------------------------------ foreign flows
_FOREIGN_EXPLAIN = [
    ("Foreign investor",
     "An investor whose registered address is outside Kenya (individual, institution, or fund).",
     "A pension fund in London buying SCOM shares on the NSE is a foreign investor."),
    ("Foreign BUYS (KES)",
     "Total value of shares that foreign investors bought that week.",
     "Foreign investors bought KES 1.2B of NSE shares → foreign buys = 1.2B."),
    ("Foreign SELLS (KES)",
     "Total value of shares that foreign investors sold that week.",
     "Foreign investors sold KES 1.35B → foreign sells = 1.35B."),
    ("Net foreign flow",
     "Buys minus Sells. Positive = net inflow (foreigners were net buyers). Negative = net outflow (they sold more "
     "than they bought).",
     "Buys 1.2B − Sells 1.35B = Net −150M → foreign investors were net sellers."),
    ("Foreign participation %",
     "The share of total weekly NSE trading value contributed by foreigners.",
     "60% participation → foreigners were on one side of 60% of the trades by value."),
    ("Why it matters",
     "Foreign flows heavily influence NSE large-caps (SCOM, EQTY, KCB, EABL). A week of heavy foreign buying often "
     "supports prices; heavy selling often pressures them.",
     "If foreigners are consistently buying SCOM, that's usually price-supportive. Consistent selling of a stock is a "
     "headwind."),
]


def _kes_m(v):
    """KES 1.20B / −KES 145.0M — the foreign-flow amounts."""
    if v is None:
        return "—"
    sign = "−" if v < 0 else ""
    a = abs(v)
    return f"{sign}KES {a / 1e9:.2f}B" if a >= 1e9 else f"{sign}KES {a / 1e6:.1f}M"


def foreign(rg, data):
    weeks = data.get("weeks") or []
    home = data.get("source_home") or "https://www.nse.co.ke/market-statistics/"
    glossary = _explainer(_FOREIGN_EXPLAIN, "What these terms mean (plain English)",
                          "No finance degree needed — each term explained simply, with a worked example.",
                          det_id="explained", good=False)
    if not weeks:
        return _page(
            "Foreign-investor participation in the NSE — weekly figures from the official NSE Weekly Market "
            "Statistics bulletin.",
            ui.section(Markup(
                '<div class="banner" data-tone="warn">⚠️ No data yet.</div>'
                '<p>To populate this page, update <code>manual_input/foreign_flows.json</code> with figures from the '
                'latest NSE Weekly Market Statistics bulletin '
                f'(<a href="{ui.href(home)}" target="_blank" rel="noopener">source</a>). See '
                '<code>manual_input/README.md</code> for the 5-minute instructions.</p>'
                '<p class="footnote">This is the honest state: no free automated feed publishes per-stock daily '
                'foreign activity, so we enter weekly figures by hand from the authoritative NSE bulletin rather than '
                'guess.</p>'), title="Foreign Flows", sec_id="foreign-flows", icon="globe"),
            glossary)
    latest = weeks[0]
    agg = latest.get("aggregate") or {}
    net, buys, sells, pct = (agg.get("net_foreign_flow_kes"), agg.get("foreign_buys_kes"),
                             agg.get("foreign_sells_kes"), agg.get("foreign_participation_pct"))
    word = "NET BUY (inflow)" if (net or 0) > 0 else "NET SELL (outflow)" if (net or 0) < 0 else "FLAT"
    strip = ui.kpi_row([
        ui.kpi("Net foreign flow", _kes_m(net), tone=ui.tone_of(net) if net else None, term="foreign-flows",
               sub="buys minus sells — positive = foreigners bought more"),
        ui.kpi("Foreign BUYS", _kes_m(buys), tone="up", sub="what foreign investors bought"),
        ui.kpi("Foreign SELLS", _kes_m(sells), tone="down", sub="what foreign investors sold"),
        ui.kpi("Foreign participation", f"{pct:.1f}%" if pct is not None else "—",
               sub="their share of all trading by value"),
        ui.kpi(f"Week ending · {word}", latest["week_ending"], sub="the latest bulletin entered"),
    ], cls="kpis-stats")

    def top_table(items, tone):
        if not items:
            return Markup('<p class="dq-note">No entries reported this week.</p>')
        peak = max((r["value_kes"] for r in items), default=1) or 1
        rows = [[ui.cell(Markup(f"<strong>{escape(r['symbol'])}</strong>"), sort=r["symbol"]),
                 ui.cell(_kes_m(r["value_kes"]), sort=r["value_kes"]),
                 ui.cell(ui.weight_bar(r["value_kes"] / peak * 100, tone=tone,
                                       label=f"{r['value_kes'] / peak * 100:.0f}% of the largest"))]
                for r in items]
        return ui.table([ui.Col("Symbol", sort="text"), ui.Col("Value", sort="number"),
                         ui.Col("Relative size", sort=None)], rows, cls="flow-table")
    wk = escape(latest["week_ending"])
    tops = Markup('<div class="grid-2">'
                  + str(ui.card(top_table(latest.get("top_foreign_buys"), "up"),
                                title=Markup(f"🟢 Top Foreign BUYS — week of {wk}")))
                  + str(ui.card(top_table(latest.get("top_foreign_sells"), "down"),
                                title=Markup(f"🔴 Top Foreign SELLS — week of {wk}")))
                  + "</div>")
    chrono = list(reversed(weeks))
    dates = [w["week_ending"] for w in chrono]
    flows = [((w.get("aggregate") or {}).get("net_foreign_flow_kes") or 0) / 1e6 for w in chrono]
    flow_chart = sc.chart("c-foreign-flow", dates,
                          [sc.Series("net", "Net foreign flow", flows, kind="bars",
                                     fmt=sc.Fmt("KES ", 1, suffix="M", sign=True))],
                          title="Weekly Net Foreign Flow (KES, millions)", height=220, bars_mode=True, legend=False,
                          include_zero=True, show_title=False)
    parts = [(w["week_ending"], (w.get("aggregate") or {}).get("foreign_participation_pct")) for w in chrono]
    parts = [(d, v) for d, v in parts if v is not None]
    if len(parts) >= 2:
        part_chart = sc.chart("c-foreign-part", [d for d, _v in parts],
                              [sc.Series("part", "Foreign share of weekly turnover", [v for _d, v in parts],
                                         fmt=sc.Fmt(suffix="%", decimals=1))],
                              title="Foreign Participation (% of total NSE turnover)", height=200, show_title=False)
    elif parts:                   # one point draws no line — say it instead
        part_chart = Markup(f'<div class="chart-empty">Only one week recorded so far: {parts[0][1]:.1f}% (week ending '
                            f'{escape(parts[0][0])}). The line appears once a second week is added.</div>')
    else:
        part_chart = Markup('<div class="chart-empty">No participation figures yet</div>')
    charts = Markup('<div class="grid-2">'
                    + str(ui.card(flow_chart + Markup('<p class="footnote">Green bars = weeks foreigners were net '
                                                      'buyers · red = net sellers. Longer bar = larger flow.</p>'),
                                  title="Weekly Net Foreign Flow", icon="activity", cls="chart-card"))
                    + str(ui.card(part_chart + Markup('<p class="footnote">Share of the total NSE weekly turnover '
                                                      'attributable to foreign investors. Higher = foreigners are '
                                                      'more active in the market.</p>'),
                                  title="Foreign Participation (% of NSE turnover)", icon="bar", cls="chart-card"))
                    + "</div>")
    hist = []
    for w in weeks:
        a = w.get("aggregate") or {}
        n, p = a.get("net_foreign_flow_kes"), a.get("foreign_participation_pct")
        hist.append([ui.cell(Markup(f"<strong>{escape(w['week_ending'])}</strong>"), sort=w["week_ending"]),
                     ui.cell(_kes_m(a.get("foreign_buys_kes")), sort=a.get("foreign_buys_kes")),
                     ui.cell(_kes_m(a.get("foreign_sells_kes")), sort=a.get("foreign_sells_kes")),
                     ui.cell(_kes_m(n), sort=n, tone=ui.tone_of(n) if n else None),
                     ui.cell(f"{p:.1f}%" if p is not None else "—", sort=p)])
    history = ui.section(
        ui.table([ui.Col("Week ending", sort="text"), ui.Col("Foreign buys", sort="number"),
                  ui.Col("Foreign sells", sort="number"), ui.Col("Net flow", sort="number"),
                  ui.Col("Participation %", sort="number")], hist, table_id="foreign-history")
        + Markup('<p class="footnote">Newest first. Every row here was entered by hand from the '
                 f'<a href="{ui.href(home)}" target="_blank" rel="noopener">NSE Weekly Market Statistics bulletin</a>.'
                 '</p>'),
        title="History (all weeks)", sec_id="foreign-history-sec", icon="calendar")
    src_link = (f' — <a href="{ui.href(latest["source_url"])}" target="_blank" rel="noopener">bulletin</a>'
                if latest.get("source_url") and ui.href(latest["source_url"]) else "")
    source = ui.details("ℹ️ Source & how this page works", Markup(
        f'<p>Latest figures are for the week ending <strong>{wk}</strong> per '
        f'<em>{escape(latest.get("source_label", "NSE Weekly Market Statistics"))}</em>{src_link}.</p>'
        '<p class="footnote">No free automated feed publishes per-stock daily foreign activity for the NSE. Instead of '
        'guessing, we enter the weekly figures manually from the NSE bulletin — every number on this page is traceable '
        'to that source. To refresh, edit <code>manual_input/foreign_flows.json</code> once a week; see '
        '<code>manual_input/README.md</code> for the 5-minute steps.</p>'), open_=True, det_id="foreign-source")
    return _page(
        "Who is buying and selling on the NSE — foreign investors vs. local. Positive net flow = foreigners were net "
        "buyers that week.",
        strip, tops, charts, history, source, glossary)


# ------------------------------------------------------------------ market pulse
_PULSE_EXPLAIN = [
    ("Central Bank Rate (CBR)",
     "The interest rate at which CBK lends to commercial banks. It's the benchmark that flows through to loans, "
     "deposits and government bond yields.",
     "CBR rising from 8.75% → 10% usually hurts loan-heavy banks in the short term (borrowers strain) but boosts their "
     "bond book. Falling CBR is the reverse."),
    ("Inflation",
     "How fast prices are rising year-over-year. High inflation eats returns and often prompts CBK to raise the CBR.",
     "Inflation 6% while your dividend yield is 5% → your real return is negative unless the share price also rises."),
    ("Oil price (Brent / WTI)",
     "Kenya imports its fuel. Rising oil raises transport, electricity and manufacturing costs — feeds through to "
     "inflation and squeezes profit margins.",
     "Brent jumping from $80 → $95 typically weakens the KES and hurts stocks like Bamburi, EABL, KenGen customers."),
    ("KES exchange rates",
     "A weaker KES helps exporters (tea, coffee, tourism) and hurts importers. It also inflates foreign debt costs.",
     "KES weakening from 125 → 135 per USD makes SCOM's tower-lease costs (USD-denominated) more expensive."),
    ("African market comparison",
     "Where the NSE sits vs. its peers (JSE South Africa, NGX Nigeria, EGX Egypt) today. Regional sell-offs often "
     "affect NSE via foreign investor flows.",
     "If JSE and EGX are down heavily on a global risk-off day, the NSE often follows a day later."),
    ("Why no 'sentiment' tags?",
     "Reliable sentiment analysis on financial short text requires a paid NLP model. Free keyword-based tagging is "
     "wrong ~40% of the time — dangerous when money is involved. So we show the source and let you decide.",
     "A headline like 'Safaricom slides on profit warning' is clearly negative to a human but a keyword tool might "
     "miss the context."),
]
_NEWS_TOPICS = ["NSE / Kenyan Stocks", "Kenyan Banking", "Central Bank of Kenya", "Kenyan Economy", "Oil / Global"]


def _pct_text(v):
    return f"{v:+.2f}%" if v is not None else "—"


def pulse(rg, p):
    cbk = p.get("cbk") or {}
    cbr = cbk.get("cbr_pct")
    notes = ""
    if cbk.get("cbr_note"):
        notes += f'<p class="footnote"><strong>MPC statement:</strong> {escape(cbk["cbr_note"])}</p>'
    if cbk.get("inflation_note"):
        notes += f'<p class="footnote"><strong>Inflation:</strong> {escape(cbk["inflation_note"])}</p>'
    src = ui.href(cbk.get("source_url") or "") or "#"
    cbk_card = ui.card(
        ui.kpi_row([ui.kpi("Central Bank Rate (CBR)", f"{cbr:.2f}%" if cbr is not None else "—", term="cbr")],
                   cls="kpis-1")
        + Markup(notes + f'<p class="footnote">Source: <a href="{src}" target="_blank" rel="noopener">'
                         'centralbank.go.ke</a>. Higher CBR usually pressures bank loan books but boosts their bond '
                         'income; falling CBR is the reverse.</p>'),
        title="Monetary Policy — Central Bank of Kenya", icon="bank")
    cards = [cbk_card]
    oil = p.get("oil") or []
    if oil:
        # Kenya imports its fuel: rising oil is the bad direction (red)
        tiles = [ui.kpi(f'{o["name"]} · 1w {_pct_text(o.get("change_1w_pct"))}', f'${o["price_usd"]:.2f}',
                        tone="down" if (o.get("change_1w_pct") or 0) > 0 else "up") for o in oil]
        cards.append(ui.card(ui.kpi_row(tiles, cls="kpis-1") + Markup(
            '<p class="footnote">Kenya is a net oil importer. <strong>Rising oil (red)</strong> pressures the KES and '
            'can lift inflation; <strong>falling oil (green)</strong> is usually favourable. Source: Yahoo Finance.</p>'),
            title="Oil Prices (USD/barrel)", icon="activity"))
    fx = p.get("fx") or []
    if fx:
        tiles = [ui.kpi(f'1 {r["code"]} = KES · {r["name"]}', f'KES {r["rate_kes"]:.2f}', term="fx-rate"
                        if r["code"] == "USD" else None) for r in fx]
        cards.append(ui.card(ui.kpi_row(tiles, cls="kpis-1") + Markup(
            f'<p class="footnote">Updated: {escape(fx[0].get("updated", ""))}. A weaker KES helps exporters (tea, '
            'coffee, tourism); a stronger KES helps importers (fuel, machinery) and the many NSE-listed dual-currency '
            'names.</p>'), title="KES Exchange Rates", icon="coins"))
    macro = Markup(f'<div class="grid-3 pulse-row">{"".join(str(c) for c in cards)}</div>')

    african = ""
    ai = p.get("african_indices") or []
    if ai:
        rows = []
        for idx in ai:
            d1, w1 = idx.get("change_1d_pct"), idx.get("change_1w_pct")
            rows.append([ui.cell(Markup(f"<strong>{escape(idx['name'])}</strong>"), sort=idx["name"]),
                         ui.cell(idx.get("country") or ""),
                         ui.cell(f'{idx["price"]:,.2f}' if idx.get("price") else "—", sort=idx.get("price")),
                         ui.cell(_pct_text(d1), sort=d1, tone=ui.tone_of(d1) if d1 is not None else None),
                         ui.cell(_pct_text(w1), sort=w1, tone=ui.tone_of(w1) if w1 is not None else None)])
        valid = sorted([(i["name"], i.get("change_1d_pct")) for i in ai if i.get("change_1d_pct") is not None],
                       key=lambda x: x[1], reverse=True)
        rank = ""
        for i, (name, chg) in enumerate(valid):
            if "🇰🇪" in name:
                rank = (f'<p class="footnote">🇰🇪 <strong>NSE ranks #{i + 1} of {len(valid)}</strong> among African '
                        f'markets today ({chg:+.2f}%).</p>')
                break
        african = ui.card(
            ui.table([ui.Col("Index", sort="text"), ui.Col("Country", sort="text"), ui.Col("Price", sort="number"),
                      ui.Col("1-day", sort="number"), ui.Col("1-week", sort="number")], rows, table_id="africa-table")
            + Markup(rank + '<p class="footnote">Source: TradingView. Kenya is derived from our own NSE stock data '
                            '(equal-weight average) since Yahoo/TV do not carry a reliable NSE 20 symbol.</p>'),
            title="African Markets Today", icon="globe")
    foreign_link = ui.card(Markup('<p>Weekly figures from the NSE Weekly Market Statistics bulletin are on the '
                                  '<a href="foreign.html">Foreign Flows page →</a></p>'),
                           title="Foreign Investor Activity", icon="globe")

    news_html = ""
    news = p.get("news") or []
    if news:
        by_topic = {}
        for n in news:
            by_topic.setdefault(n["topic"], []).append(n)
        blocks = []
        for topic in _NEWS_TOPICS:
            items = by_topic.get(topic, [])
            if not items:
                continue
            lis = []
            for n in items[:6]:
                published = n.get("published_utc", "")
                m = re.match(r"(\w{3}, \d{1,2} \w{3} \d{4})", published)
                when = m.group(1) if m else published[:16]
                href = ui.href(n.get("url")) if str(n.get("url") or "").lower().startswith(("http://", "https://")) else ""
                title = escape(n.get("title") or "")
                head = (f'<a href="{href}" target="_blank" rel="noopener">{title}</a>' if href
                        else f"<strong>{title}</strong>")
                lis.append(f'<li><span>{head}<small>{escape(when)} · {escape(n.get("source") or "")}</small></span></li>')
            blocks.append(str(ui.card(Markup(f'<ul class="news-list plain">{"".join(lis)}</ul>'),
                                      title=f"📰 {topic}", cls="news-card")))
        news_html = ui.section(
            Markup(f'<div class="grid-2 news-grid">{"".join(blocks)}</div>'
                   '<p class="footnote">Source: Google News RSS. Headlines are shown neutral with date &amp; publisher — '
                   'we do NOT auto-classify positive/negative (free sentiment tools on financial text are too unreliable '
                   'to trust with money). Click a headline to read the original.</p>'),
            title="Latest Headlines", sec_id="headlines", icon="news")
    return _page(
        "Context around the NSE trading day — the macro forces that shape prices. Everything here is "
        "<strong>refreshed on every run</strong> from named sources; nothing is auto-classified into a "
        "\"sentiment\" (see the note at the bottom of the headlines section).",
        macro,
        Markup(f'<div class="grid-2 pulse-row2">{african}{foreign_link}</div>') if african else foreign_link,
        news_html,
        _explainer(_PULSE_EXPLAIN, "How to read this page (plain English)",
                   "What each signal typically means for NSE stocks. These are heuristics, not rules — every situation "
                   "has exceptions.", det_id="explained", good=False))


# ------------------------------------------------------------------ government bonds
_BONDS_EXPLAIN = [
    ("Coupon",
     "The fixed yearly interest a bond pays you, as a % of the face value, usually split into two payments a year.",
     "A 12% coupon on KES 100,000 pays you KES 12,000 a year — KES 6,000 every six months."),
    ("Yield vs coupon",
     "The coupon is fixed. The yield is what you actually earn based on the price you pay. Buy below face value and "
     "your yield is higher than the coupon; buy above and it's lower.",
     "A 12% coupon bond bought at 96 (below 100) yields a bit more than 12%."),
    ("Tenor / maturity",
     "Tenor is how long until the bond repays you (its term). The maturity date is the day you get your money back.",
     "A bond maturing 18-Aug-2042 with '16 years to maturity' repays your capital in 2042."),
    ("Infrastructure Bond (IFB) — tax-free",
     "A special bond funding roads, energy, water etc. Its interest is 100% tax-free, unlike normal bonds which are "
     "taxed 10–15%.",
     "A 12.7% tax-free IFB beats a 13.5% normal bond after tax: 13.5% − 10% tax ≈ 12.15% net."),
    ("Clean vs dirty price",
     "The clean price is the bond's value per 100 face value. The dirty price is what you actually pay — clean price "
     "plus interest that has built up since the last payment (accrued interest).",
     "Clean 99.96 + accrued 3.84 = you pay ≈ 103.80 per 100 of the bond."),
    ("Primary vs secondary market",
     "Primary = buying a brand-new bond straight from CBK at auction. Secondary = buying an existing bond from someone "
     "else (via the NSE or a bank), at the going market price.",
     "Missed the auction? You can still buy that bond on the secondary market — the price just floats."),
    ("Real return",
     "Your return after subtracting inflation — what your money truly gains in buying power.",
     "A 12% bond when inflation is 7% gives a ~5% real return. If inflation were 13%, you'd be losing ground."),
    ("Why bond prices move",
     "When new bonds offer higher interest, older lower-interest bonds become worth less (and vice-versa). It only "
     "matters if you sell before maturity — hold to maturity and you get face value back.",
     "Rates rise after you buy → if you sell early you may get less than you paid; hold to maturity and you still get "
     "100 back."),
]


def _yield_heat(v, lo, hi):
    """Heat attributes for a net yield within the table's range (greener = higher)."""
    if v is None or hi <= lo:
        return Markup('data-dir="up" style="--pl:14%;--pd:12%"')
    t = max(0.0, min(1.0, (v - lo) / (hi - lo)))
    return Markup(f'data-dir="up" style="--pl:{14 + 58 * t:.0f}%;--pd:{12 + 30 * t:.0f}%"')


def bonds(rg, d):
    bonds_ = d.get("bonds") or []
    tbills = d.get("tbills") or []
    ctx = d.get("context") or {}
    cbr = ctx.get("cbr")
    as_of = d.get("as_of") or ""
    wht_default = rg._BOND_WHT

    def pct(v, dp=3):
        return f"{v:.{dp}f}%" if v is not None else "—"

    def teq(c):
        return c / (1 - wht_default) if c else None

    def net_yield(b):
        """After-tax yield — the fair way to rank IFB vs FXD side by side: IFB
        interest is tax-free (net = coupon); other bonds lose withholding tax."""
        c = b.get("coupon")
        if not c:
            return None
        if b.get("tax_free"):
            return c
        return c * (1 - (b.get("withholding_pct") or wht_default * 100) / 100.0)

    def tax_label(b):
        return pct(b.get("withholding_pct") or wht_default * 100, 0)

    bonds_url, bills_url, dhow = (ui.href(rg._CBK_BONDS_URL), ui.href(rg._CBK_BILLS_URL), ui.href(rg._DHOWCSD_URL))
    if not bonds_ and not tbills:
        return _page(
            "Live Treasury data from the Central Bank of Kenya is temporarily unavailable (their site may be down). "
            "Nothing is shown rather than a guessed figure. Check the official source directly:",
            Markup(f'<p><a href="{bonds_url}" target="_blank" rel="noopener">CBK Treasury Bonds ↗</a> · '
                   f'<a href="{bills_url}" target="_blank" rel="noopener">CBK Treasury Bills ↗</a></p>'))

    openb = sorted([b for b in bonds_ if b.get("status") == "open" and b.get("coupon")],
                   key=lambda b: b["coupon"], reverse=True)
    upcoming = sorted([b for b in bonds_ if b.get("status") == "upcoming" and b.get("coupon")],
                      key=lambda b: b["coupon"], reverse=True)
    allb = sorted([b for b in bonds_ if b.get("coupon")], key=lambda b: b["coupon"], reverse=True)
    ranked = sorted(allb, key=lambda b: net_yield(b) or 0, reverse=True)
    ifb_open = [b for b in openb if b.get("tax_free")]
    parts = [Markup(
        '<div class="banner" data-tone="warn">⚠️ <strong>Educational information, not financial advice.</strong> '
        'These are factual figures published by the Central Bank of Kenya, plus plain-English explanations to help you '
        'understand them. I am not a licensed investment adviser. Always confirm the exact terms on the official CBK '
        'prospectus, and consider your own goals (or a licensed adviser) before investing.</div>')]
    parts.append(ui.section(Markup(
        '<p class="page-intro">A government bond is a loan <em>you</em> make to the Government of Kenya. In return it '
        'pays you interest (the <strong>coupon</strong>) every six months, then returns your money on the '
        '<strong>maturity</strong> date. Because it is backed by the government, it is the safest shilling investment '
        'there is — the trade-off is you tie your money up for the bond\'s term. Data below is pulled live from the '
        f'<a href="{bonds_url}" target="_blank" rel="noopener">Central Bank of Kenya</a>'
        + (f' · <strong>as of {escape(as_of)}</strong>' if as_of else "") + '.</p>'),
        title="Government Bonds — Kenya", sec_id="govt-bonds", icon="bank", term="coupon"))

    # ---- the headline figures (new)
    best_t = max(tbills, key=lambda t: t["rate"]) if tbills else None
    top_net = ranked[0] if ranked else None
    parts.append(ui.kpi_row([
        ui.kpi("Highest coupon", pct(allb[0]["coupon"]) if allb else "—", term="coupon",
               sub=f'{allb[0]["issue"]}{" · tax-free" if allb[0].get("tax_free") else ""}' if allb else None),
        ui.kpi("Best after tax", pct(net_yield(top_net)) if top_net else "—", tone="up" if top_net else None,
               term="withholding-tax", sub=top_net["issue"] if top_net else None),
        ui.kpi("Central Bank Rate", pct(cbr, 2) if cbr is not None else "—", term="cbr", sub="the benchmark"),
        ui.kpi("Best T-bill", pct(best_t["rate"]) if best_t else "—", term="t-bill",
               sub=f'{best_t["label"]} bill' if best_t else None),
        ui.kpi("Open for bidding", str(len(openb)), sub="bonds on offer from CBK today"),
    ], cls="kpis-stats"))

    # ---- is it a good time? the objective factors
    top_open = openb[0] if openb else None
    top_any = allb[0] if allb else None
    chips = []
    if top_open and cbr is not None:
        spread = top_open["coupon"] - cbr
        cls = "cal-near" if spread > 0 else "cal-passed"
        chips.append(f'<li><span class="cal-chip {cls}">Yields vs CBR</span> The best open bond pays '
                     f'<strong>{pct(top_open["coupon"])}</strong> — that is <strong>{spread:+.2f} percentage '
                     f'points</strong> versus the Central Bank Rate of {pct(cbr, 2)}. A wide positive gap means bonds lock '
                     'in a healthy premium over the central-bank benchmark.</li>')
    if ifb_open:
        b = ifb_open[0]
        chips.append(f'<li><span class="cal-chip cal-near">Tax-free option</span> An infrastructure bond (IFB) is '
                     f'currently open at <strong>{pct(b["coupon"])} tax-free</strong>. Because most bonds are taxed '
                     f'10%, that is worth about <strong>{pct(teq(b["coupon"]), 2)}</strong> on a taxable bond — a '
                     'genuinely high effective yield.</li>')
    infl = ctx.get("inflation_note")
    if top_any:
        chips.append(f'<li><span class="cal-chip cal-far">Real return</span> Your <em>real</em> return is the coupon '
                     f'minus inflation. The highest coupon on offer here is <strong>{pct(top_any["coupon"])}</strong>; '
                     'compare it to the latest Kenyan inflation rate'
                     + (f' ({escape(infl)})' if infl else ' (see the Market Pulse tab)')
                     + ' to judge how much you truly earn after prices rise.</li>')
    if chips:
        parts.append(ui.section(Markup(
            '<p class="section-desc">There is no single yes/no answer, and this is not advice. Here are the objective '
            'signals from today\'s CBK data so you can judge for yourself:</p>'
            f'<ul class="factor-list">{"".join(chips)}</ul>'
            '<p class="footnote">Rule of thumb people use: bond yields well above the CBR and above inflation, on a '
            'bond whose term matches how long you can lock the money away, is generally considered attractive. Longer '
            'bonds pay more but swing more in price if you sell early. Your call.</p>'),
            title="Is it a good time to buy? — the factors to weigh", sec_id="good-time", icon="flag"))

    # ---- open now
    def bond_card(b, rank):
        tf = b.get("tax_free")
        badges = [ui.pill("TAX-FREE (IFB)", "up") if tf else ui.pill(f"Taxed {tax_label(b)}", "info")]
        if rank == 0:
            badges.append(ui.pill("★ Highest open yield", "warn"))
        ad = b.get("auction_date") or b.get("sale_end")
        if ad and as_of:
            try:
                days = (dt.date.fromisoformat(ad) - dt.date.fromisoformat(as_of)).days
                badges.append(Markup('<span class="bc-passed">closes today</span>') if days <= 0 else
                              Markup(f'<span class="bc-soon">closes in {days}d</span>') if days <= 3 else
                              Markup(f'<span class="bc-future">{days}d to bid</span>'))
            except ValueError:
                pass
        teq_txt = (f'<small>≈ {pct(teq(b["coupon"]), 2)} taxable-equivalent</small>' if tf and b.get("coupon") else "")
        if b.get("dirty_price"):
            price = (f'💵 <strong>You pay ≈ KES {b["dirty_price"]:.2f}</strong> per 100 face value (dirty price = clean '
                     f'{b["clean_price"]:.2f} + accrued interest {b["accrued_interest"]:.2f}).')
        elif b.get("clean_price"):
            price = (f'💵 Clean price ≈ <strong>KES {b["clean_price"]:.2f}</strong> per 100 (you also pay accrued '
                     'interest on top).')
        else:
            price = '💵 Price is set at the auction (around KES 100 per 100 face value).'
        link = ui.href(b.get("prospectus_url") or "")
        link = f' <a href="{link}" target="_blank" rel="noopener">Official prospectus ↗</a>' if link else ""
        tenor = f'{b["tenor_years"]:.1f} yrs' if b.get("tenor_years") else "—"
        minimum = f'KES {b["min_invest"]:,.0f}' if b.get("min_invest") else "—"
        return (f'<div class="bond-offer" data-tone="{"up" if tf else "info"}">'
                f'<div class="bond-offer-head"><strong>{escape(b["issue"])}</strong>'
                f'<span class="muted">{escape(b.get("type_label", ""))}</span>{"".join(str(x) for x in badges)}</div>'
                '<div class="bond-offer-figs">'
                f'<div><b class="big num">{pct(b["coupon"])}</b><span>Coupon (yearly interest)</span>{teq_txt}</div>'
                f'<div><b class="num">{tenor}</b><span>Term to maturity</span></div>'
                f'<div><b class="num">{escape(b.get("maturity") or "—")}</b><span>Matures</span></div>'
                f'<div><b class="num">{minimum}</b><span>Minimum</span></div></div>'
                f'<p class="footnote">{price}{link}</p></div>')
    if openb:
        parts.append(ui.section(Markup(
            '<p class="section-desc">These bonds are on offer from CBK right now, <strong>ranked highest-yielding '
            'first</strong>. Tax-free infrastructure bonds are flagged in green.</p>'
            '<div class="cal-legend"><span class="bc-future">days left to bid</span><span class="bc-soon">closing soon'
            '</span><span class="bc-passed">closes today</span></div>'
            f'<div class="bond-offers">{"".join(bond_card(b, i) for i, b in enumerate(openb))}</div>'),
            title="🟢 Open now — bonds you can currently buy", sec_id="open-now", icon="bank"))
    else:
        nxt = ""
        if upcoming:
            nxt = ('<p>Coming up next: ' + ", ".join(f'<strong>{escape(b["issue"])}</strong> ({pct(b["coupon"])})'
                                                     for b in upcoming[:3]) + '.</p>')
        parts.append(ui.section(Markup(
            '<p class="section-desc">CBK auctions new bonds roughly monthly; there isn\'t one in its sale window today. '
            'You can still buy existing bonds on the secondary market through the NSE or your bank, and Treasury bills '
            f'below are auctioned weekly.</p>{nxt}'),
            title="🟡 No bond is on primary offer right now", sec_id="open-now", icon="bank"))

    # ---- top 10 by after-tax return
    top10 = ranked[:10]
    if top10:
        nys = [net_yield(b) for b in top10 if net_yield(b) is not None]
        lo, hi = (min(nys), max(nys)) if nys else (0.0, 1.0)
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        rows = []
        for i, b in enumerate(top10):
            ny = net_yield(b)
            st = b.get("status")
            how = "🟢 at auction" if st == "open" else "🟡 upcoming" if st == "upcoming" else "⚪ secondary market"
            price = (f'{b["clean_price"]:.2f} / {b["dirty_price"]:.2f}' if b.get("dirty_price")
                     else (f'{b["clean_price"]:.2f} / —' if b.get("clean_price") else "— / —"))
            rows.append([
                ui.cell(str(medals.get(i, i + 1)), cls="a-center rank-cell", sort=i + 1),
                ui.cell(Markup(f'<strong>{escape(b["issue"])}</strong><small class="block muted">'
                               f'{escape(b.get("type_label", ""))}</small>'), sort=b["issue"]),
                ui.cell(ui.pill("tax-free", "up") if b.get("tax_free") else ui.pill(f"{tax_label(b)} tax", "info")),
                ui.cell(pct(b["coupon"]), sort=b["coupon"]),
                ui.cell(Markup(f'<span class="heat-cell heat" {_yield_heat(ny, lo, hi)}>{pct(ny)}</span>'), sort=ny),
                ui.cell(f'{round(b["tenor_years"], 1)}y' if b.get("tenor_years") else "—", sort=b.get("tenor_years")),
                ui.cell(price), ui.cell(how)])
        cols = [ui.Col("#", sort="number", align="center"), ui.Col("Bond", sort="text"), ui.Col("Tax", sort=None),
                ui.Col("Coupon", sort="number", term="coupon"), ui.Col("Net yield ★", sort="number", term="withholding-tax"),
                ui.Col("Tenor", sort="number"), ui.Col("Clean/Dirty", sort=None, align="right"),
                ui.Col("Buy via", sort=None)]
        parts.append(ui.section(Markup(
            '<p class="section-desc">Ranked by <strong>net yield — what you actually keep after tax</strong> — so '
            'tax-free infrastructure bonds and normal FXD bonds compete on a level field. This is the honest comparison: '
            'a higher-coupon FXD can out-earn a tax-free IFB once tax is taken off, or the other way round. '
            '<strong>Greener = higher net return.</strong> Price shows clean / dirty per 100 face value.</p>')
            + ui.table(cols, rows, table_id="top-bonds")
            + Markup('<p class="footnote">★ <strong>Net yield</strong> = coupon for tax-free IFBs; coupon × (1 − '
                     'withholding tax) for normal bonds (WHT is 10% on 10-year+ bonds, 15% on shorter ones). '
                     '<strong>Open</strong> bonds you buy at the CBK auction; <strong>closed</strong> bonds you buy on the '
                     'secondary market (NSE / your bank) at the day\'s price. This ranking is factual data, not a '
                     'recommendation.</p>'),
            title="Top 10 bonds by return (best → lowest)", sec_id="top-10", icon="trend-up"))

    # ---- treasury bills
    if tbills:
        parts.append(ui.section(Markup(
            '<p class="section-desc">Treasury bills are the same idea as bonds but short-term (under a year) and '
            'auctioned <strong>every week</strong>. Instead of paying a coupon, they are sold at a discount and repaid '
            'at full value — the difference is your interest. These are the latest weighted-average rates from CBK:</p>')
            + ui.kpi_row([ui.kpi(f'{t["label"]} bill', pct(t["rate"]), term="t-bill",
                                 sub=f'issue {t.get("issue") or "—"}') for t in tbills], cls="kpis-stats")
            + Markup('<p class="footnote">Good if you may need the money back within a year. Full auction details: '
                     f'<a href="{bills_url}" target="_blank" rel="noopener">CBK Treasury Bills ↗</a></p>'),
            title="Treasury bills — the short-term option", sec_id="t-bills", icon="calendar"))

    # ---- every bond, compared
    if allb:
        nys_all = [net_yield(b) for b in ranked if net_yield(b) is not None]
        nlo, nhi = (min(nys_all), max(nys_all)) if nys_all else (0.0, 1.0)
        rows = []
        for i, b in enumerate(ranked):
            st = b.get("status")
            chip = ('<span class="cal-chip cal-near">open</span>' if st == "open" else
                    '<span class="cal-chip cal-far">upcoming</span>' if st == "upcoming" else
                    '<span class="cal-chip cal-passed">closed</span>')
            ny = net_yield(b)
            price = (f'{b["clean_price"]:.2f} / {b["dirty_price"]:.2f}' if b.get("dirty_price")
                     else (f'{b["clean_price"]:.2f} / —' if b.get("clean_price") else "—"))
            rows.append([
                ui.cell(Markup(f'<strong>{escape(b["issue"])}</strong>'
                               + (' <span class="pill" data-tone="warn">★</span>' if i == 0 else "")), sort=b["issue"]),
                ui.cell(b.get("type_label", "")),
                ui.cell(Markup('<span class="fgood">Tax-free</span>') if b.get("tax_free") else f"{tax_label(b)} tax"),
                ui.cell(Markup(f'<span class="positive">{pct(b["coupon"])}</span>'), sort=b["coupon"]),
                ui.cell(Markup(f'<span class="heat-cell heat" {_yield_heat(ny, nlo, nhi)}>{pct(ny)}</span>'), sort=ny),
                ui.cell(f'{round(b["tenor_years"], 1)}y' if b.get("tenor_years") else "—", sort=b.get("tenor_years")),
                ui.cell(b.get("maturity") or "—", sort=b.get("maturity")),
                ui.cell(price), ui.cell(Markup(chip), sort=st)])
        cols = [ui.Col("Issue", sort="text"), ui.Col("Type", sort="text"), ui.Col("Tax", sort="text"),
                ui.Col("Coupon", sort="number", term="coupon"), ui.Col("Net yield", sort="number"),
                ui.Col("Tenor", sort="number"), ui.Col("Matures", sort="text"),
                ui.Col("Clean/Dirty", sort=None, align="right"), ui.Col("Status", sort="text")]
        parts.append(ui.section(Markup(
            '<p class="section-desc">Every recent CBK bond — infrastructure (IFB) <em>and</em> fixed-coupon (FXD), open '
            'and closed — <strong>ranked by net yield (what you keep after tax)</strong> so they compete on a level '
            'field. <strong>Greener net yield = higher take-home return.</strong> "Clean/Dirty" is the price per 100 '
            'face value (dirty = what you actually pay).</p>')
            + ui.table(cols, rows, table_id="all-bonds", filter_placeholder="🔍 Filter bonds…", show_first=12)
            + Markup('<p class="footnote">Closed bonds can still be bought on the secondary market (NSE / your bank) at '
                     'the going market price, which moves daily — the coupon shown is fixed for the bond\'s life. Net '
                     'yield = coupon for tax-free IFBs, coupon × (1 − WHT) for taxed bonds.</p>'),
            title="Compare all recent bonds (IFB + FXD together)", sec_id="all-bonds-sec", icon="bar"))

    # ---- summary
    summ = []
    if openb:
        top = openb[0]
        line = (f'<li><strong>Highest-yielding open bond:</strong> {escape(top["issue"])} at '
                f'<strong>{pct(top["coupon"])}</strong>' + (" (tax-free)" if top.get("tax_free") else ""))
        line += f', {top["tenor_years"]:.0f}-year term.' if top.get("tenor_years") else "."
        summ.append(line + "</li>")
        if ifb_open:
            b = ifb_open[0]
            summ.append(f'<li><strong>Best tax-free option:</strong> {escape(b["issue"])} at {pct(b["coupon"])} '
                        f'tax-free (≈ {pct(teq(b["coupon"]), 2)} before-tax equivalent).</li>')
        summ.append(f'<li><strong>{len(openb)} bond(s)</strong> are open for bidding right now.</li>')
    else:
        summ.append('<li>No bonds are on primary offer today — T-bills are auctioned weekly, and existing bonds trade on '
                    'the secondary market.</li>')
    if best_t:
        summ.append(f'<li><strong>Best short-term rate:</strong> {escape(best_t["label"])} T-bill at '
                    f'{pct(best_t["rate"])}.</li>')
    parts.append(ui.section(Markup(
        f'<ul class="summary-list">{"".join(summ)}</ul>'
        '<div class="banner" data-tone="info"><strong>How to buy:</strong> open a CBK <strong>DhowCSD</strong> account '
        f'(<a href="{dhow}" target="_blank" rel="noopener">dhow-csd ↗</a>) online or via your bank, then place a bid '
        'before the auction date. Minimum is usually KES 50,000 for bonds and KES 100,000 for infrastructure bonds.</div>'
        '<p class="footnote">This is a factual summary of available securities — not a recommendation to buy any '
        'specific bond.</p>'), title="Summary — what you can buy now", sec_id="bond-summary", icon="check"))
    parts.append(_explainer(_BONDS_EXPLAIN, "Bonds explained (plain English)",
                            "Everything on this page, in simple terms with examples.", det_id="explained", good=False))
    return _join(parts)
