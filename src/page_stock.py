"""
The per-stock pages: one for every NSE stock, and one for each
international holding or watchlist stock.

Crucial first. The top of the page answers "what is it, where is the price
and what do the signals say": the price and today's move, the technical
signal, the recommendation, the factor score and where the price sits in
its 52-week range — with your average cost marked, and your position
underneath, when you own the stock. Below that, tabs:

  Summary        the key figures, the interactive price chart, the verdict
                 and the checks (price check, score, dividend, range)
  Fundamentals   every company figure, grouped and colour-coded, with ⓘ
  Technicals     the signals, the volume / RSI / MACD / stochastic / ATR
                 charts, support & resistance and the indicator values
  Peers or News  similar stocks and the whole sector (NSE) — or the
                 headlines (international)
  History        the last 10 trading days (and the dividend record)
  Glossary       every chart term, explained like you're 10

The wording and figures are the old pages' own; tools/factcheck.py compares
the two versions.
"""

import base64
import re
from types import SimpleNamespace

from markupsafe import Markup, escape

import svg_charts as sc
import ui_kit as ui
from page_portfolio import money as _money, qty as _qty


def _join(parts):
    return Markup("".join(str(p) for p in parts if p))


def _num(v):
    """The value as a float, or None when it's missing or not a number."""
    return float(v) if sc.ok(v) else None


_TONE = {"positive": "up", "midv": "warn", "negative": "down"}


# ------------------------------------------------------------------ company figures
# (label, field, format, when it counts as missing, colour rule, ⓘ term).
# "truthy": 0 shows as N/A too, as the old pages did; "set": only a missing value does.
_NSE_GROUPS = [
    ("Valuation", "bar", [
        ("Market Cap", "market_cap", "mcap", None, None, "market-cap"),
        ("P/E Ratio (TTM)", "pe_ratio", "f2", "truthy", "pe", "pe"),
        ("PEG Ratio", "peg_ratio", "f2", "truthy", "peg", "peg"),
        ("Price/Book", "price_to_book", "f2", "truthy", "pb", "price-to-book"),
        ("Price/Sales", "price_to_sales", "f2", "truthy", "ps", "price-to-sales"),
        ("Enterprise Value", "enterprise_value", "cur", None, None, "enterprise-value"),
        ("EV/Revenue", "ev_to_revenue", "f2", "truthy", None, "ev-revenue"),
        ("EV/EBITDA", "ev_to_ebitda", "f2", "truthy", "ev_ebitda", "ev-ebitda"),
    ]),
    ("Growth", "trend-up", [
        ("Revenue Growth (YoY)", "revenue_growth_yoy", "pct2", "truthy", "rg", "revenue-growth"),
        ("EPS Growth (YoY)", "eps_growth_yoy", "pct2", "truthy", "eps_growth", "eps-growth"),
        ("EPS (TTM)", "eps_ttm", "f2", "truthy", "eps", "eps"),
        ("Revenue (TTM)", "revenue_ttm", "cur", None, None, "ttm"),
        ("Net Income (TTM)", "net_income_ttm", "cur", None, None, "ttm"),
        ("Free Cash Flow (TTM)", "free_cash_flow_ttm", "cur", None, None, "free-cash-flow"),
    ]),
    ("Profitability", "percent", [
        ("ROE", "roe", "pct2", "truthy", "roe", "roe"),
        ("ROIC", "roic", "pct2", "truthy", "roic", "roic"),
        ("ROA", "roa", "pct2", "truthy", "roa", "roa"),
        ("Gross Margin", "gross_margin", "pct2", "truthy", "gm", "gross-margin"),
        ("Operating Margin", "operating_margin", "pct2", "truthy", "om", "operating-margin"),
        ("Net Margin", "net_margin", "pct2", "truthy", "nm", "net-margin"),
        ("FCF Margin", "fcf_margin", "pct2", "truthy", "fcfm", "fcf-margin"),
        ("Operating Income (TTM)", "operating_income_ttm", "cur", None, None, "ttm"),
    ]),
    ("Financial Health", "shield", [
        # A real zero means no debt / no dividend — shown as 0.00, not N/A.
        ("Debt/Equity", "debt_to_equity", "f2", "set", "de", "debt-to-equity"),
        ("Current Ratio", "current_ratio", "f2", "truthy", "cr", "current-ratio"),
        ("Quick Ratio", "quick_ratio", "f2", "truthy", "qr", "quick-ratio"),
        ("Total Assets", "total_assets", "cur", None, None, None),
        ("Total Debt", "total_debt", "cur", None, None, None),
        ("Net Debt", "net_debt", "cur", None, None, "net-debt"),
        ("Total Equity", "total_equity", "cur", None, None, None),
        ("Dividend Yield", "dividend_yield", "pct2", "set", "yield", "dividend-yield"),
    ]),
]

_INTL_GROUPS = [
    ("Valuation", "bar", [
        ("Market Cap", "market_cap", "mcap", None, None, "market-cap"),
        ("P/E Ratio (TTM)", "pe_ratio", "f2", "truthy", "pe", "pe"),
        ("Forward P/E", "forward_pe", "f2", "truthy", None, "forward-pe"),
        ("PEG Ratio", "peg_ratio", "f2", "truthy", "peg", "peg"),
        ("Price/Book", "price_to_book", "f2", "truthy", "pb", "price-to-book"),
        ("Price/Sales", "price_to_sales", "f2", "truthy", "ps", "price-to-sales"),
        ("Enterprise Value", "enterprise_value", "cur", None, None, "enterprise-value"),
        ("EV/EBITDA", "ev_to_ebitda", "f2", "truthy", "ev_ebitda", "ev-ebitda"),
    ]),
    ("Growth", "trend-up", [
        ("Revenue Growth (YoY)", "revenue_growth_yoy", "pct2", "set", "rg", "revenue-growth"),
        ("Earnings Growth (YoY)", "earnings_growth_yoy", "pct2", "set", "eps_growth", "eps-growth"),
        ("EPS (TTM)", "eps_ttm", "money", "truthy", "eps", "eps"),
        ("Forward EPS", "forward_eps", "money", "truthy", None, "eps"),
        ("Revenue (TTM)", "revenue_ttm", "cur", None, None, "ttm"),
        ("Net Income (TTM)", "net_income_ttm", "cur", None, None, "ttm"),
        ("Free Cash Flow (TTM)", "free_cash_flow_ttm", "cur", None, None, "free-cash-flow"),
    ]),
    ("Profitability", "percent", [
        ("ROE", "roe", "pct2", "set", "roe", "roe"),
        ("Gross Margin", "gross_margin", "pct2", "set", "gm", "gross-margin"),
        ("Operating Margin", "operating_margin", "pct2", "set", "om", "operating-margin"),
        ("Net Margin", "net_margin", "pct2", "set", "nm", "net-margin"),
    ]),
    ("Financial Health", "shield", [
        ("Debt/Equity", "debt_to_equity", "f2", "set", "de", "debt-to-equity"),
        ("Current Ratio", "current_ratio", "f2", "truthy", "cr", "current-ratio"),
        ("Quick Ratio", "quick_ratio", "f2", "truthy", "qr", "quick-ratio"),
        ("Total Debt", "total_debt", "cur", None, None, None),
        ("Total Cash", "total_cash", "cur", None, None, None),
        ("Dividend Yield", "dividend_yield", "pct2", "set", "yield", "dividend-yield"),
        ("Beta", "beta", "f2", "truthy", None, "beta"),
        ("Shares Outstanding", "shares_outstanding", "int", "truthy", None, None),
    ]),
]

_OWNERSHIP = ("Ownership", "pie", [
    ("Held by Insiders", "held_pct_insiders", "pct1", "set", None, "ownership"),
    ("Held by Institutions", "held_pct_institutions", "pct1", "set", None, "ownership"),
])

_PERF = [("1 Day", "change_pct"), ("5 Days", "perf_1w"), ("1 Month", "perf_1m"), ("3 Months", "perf_3m"),
         ("6 Months", "perf_6m"), ("Year to Date", "perf_ytd"), ("1 Year", "perf_1y"), ("5 Years", "perf_5y"),
         ("All Time", "perf_all")]

_SIGNALS = ["trend", "ma_crossover", "macd", "rsi", "bollinger", "stochastic", "volume"]
_SIGNAL_UP = {"bullish", "golden_cross", "bullish_cross", "oversold"}
_SIGNAL_DOWN = {"bearish", "death_cross", "bearish_cross", "overbought"}

_TECH_VALUES = [("close", "Close Price", None), ("sma_20", "SMA 20", "sma"), ("sma_50", "SMA 50", "sma"),
                ("ema_12", "EMA 12", "ema"), ("ema_26", "EMA 26", "ema"), ("rsi", "RSI (14)", "rsi"),
                ("atr", "ATR (14)", "atr"), ("macd", "MACD", "macd"), ("macd_signal", "MACD Signal", "macd"),
                ("macd_hist", "MACD Histogram", "macd"), ("stoch_k", "Stochastic %K", "stochastic"),
                ("stoch_d", "Stochastic %D", "stochastic"), ("volume", "Volume", "volume")]


def _value(c, key, kind, when):
    """A company figure formatted as the old pages did ("N/A" when missing)."""
    v = c.f.get(key)
    if kind == "mcap":
        return c.fmt_mcap(v)
    if kind == "cur":
        return c.fmt_currency(v)
    x = _num(v)
    if x is None or (when == "truthy" and x == 0):
        return "N/A"
    return {"f2": f"{x:,.2f}", "f1": f"{x:,.1f}", "pct2": f"{x:,.2f}%", "pct1": f"{x:,.1f}%",
            "money": c.money(x), "int": f"{x:,.0f}"}[kind]


def _colour(c, rule, key, text):
    """positive / midv / negative — the dashboard's good / average / weak
    colours — or '' (no colour, also for every N/A)."""
    return c.fund_color(rule, c.f.get(key)) if rule and text != "N/A" else ""


def _mrow(label, text, *, cls="", term=None):
    """One labelled figure in a group card (data-kpi names it for the fact check)."""
    return Markup(f'<div class="mrow" data-kpi="{ui.attr(label)}"><span class="kpi-label">{ui.esc(label)}'
                  f'{ui.info(term) if term else ""}</span>'
                  f'<span class="kpi-value num{" " + cls if cls else ""}">{ui.esc(text)}</span></div>')


def _group_card(c, title, icon, rows):
    out = []
    for lab, key, kind, when, rule, term in rows:
        text = _value(c, key, kind, when)
        out.append(_mrow(lab, text, cls=_colour(c, rule, key, text), term=term))
    return ui.card(Markup(f'<div class="mrows">{_join(out)}</div>'), title=title, icon=icon, cls="mgroup")


def _legend(see_guide=True):
    return Markup('<p class="footnote legend-line"><span class="positive">Green</span> = good · '
                  '<span class="midv">Amber</span> = average · <span class="negative">Red</span> = weak/risky · '
                  'uncoloured = no data.' + (' See the guide below for what each means.' if see_guide else '')
                  + '</p>')


# ------------------------------------------------------------------ the top of the page
def _logo(rg, symbol, website=None, international=False):
    """The company logo at hero size — larger only when the cached image is
    big enough to stay sharp (some favicons are 16 px)."""
    html = str(rg._ticker_logo_html(symbol, website=website, international=international))
    big = "ticker-logo-fallback" in html
    m = re.search(r"base64,([A-Za-z0-9+/=]{32})", html)
    if m:
        try:
            head = base64.b64decode(m.group(1))
            big = head[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(head[16:20], "big") >= 48
        except ValueError:
            big = False
    return Markup(f'<span class="hero-logo{" big" if big else ""}">{html}</span>')


def _star(symbol, market, name):
    """☆ Watch — text only: the local app rewrites it to ★ Watching."""
    return Markup(f'<button type="button" class="wl-star app-only" data-symbol="{escape(symbol)}" '
                  f'data-market="{market}" data-name="{escape(name or symbol)}">☆ Watch</button>')


def _signal_pill(overall):
    tone = {"bullish": "up", "bearish": "down"}.get(overall, "neutral")
    return _join([ui.pill(f"Technical signal: {str(overall).title()}", tone), ui.info("signal")])


def _rec_tone(rec_class):
    return {"buy": "up", "sell": "down", "hold": "warn"}.get(rec_class, "neutral")


def _score_word(v):
    return "strong (70+)" if v >= 70 else "middling (45–69)" if v >= 45 else "weak (below 45)"


def _hero(c, *, name_line, price_text, kes_line, meta, rec_text, rec_term, range_html):
    overall = c.signals.get("overall", "neutral")
    score = c.score.get("overall") if c.score else None
    score_html = Markup(
        f'<div class="hero-score">{sc.ring(score, label="Factor score", size=52)}'
        f'<p><b>Factor score</b>{ui.info("factor-score")}<br>'
        + (f'<span class="muted">{escape(_score_word(score))} · out of 100</span>' if sc.ok(score)
           else '<span class="muted">not enough data to score</span>') + '</p></div>')
    delta = ui.delta(c.daily_change) if c.daily_change is not None else ""
    return Markup(
        '<section class="card hero" aria-label="At a glance">'
        '<div class="hero-main">'
        f'<div class="hero-id">{c.logo}<div class="hero-name"><h2>{escape(c.symbol)}'
        f'<span class="wl-mkt">{escape(c.market_label)}</span>{c.star}</h2>'
        f'<p>{name_line}</p></div></div>'
        f'<div class="hero-price" data-kpi="Price"><span class="kpi-value num">{escape(price_text)}</span>'
        f'{delta}{kes_line}</div>'
        f'<p class="hero-meta">{meta}</p></div>'
        '<div class="hero-side">'
        f'<div class="hero-pills">{_signal_pill(overall)}'
        f'{ui.pill(rec_text, _rec_tone(c.rec_class))}{ui.info(rec_term)}</div>'
        f'{score_html}{range_html}</div></section>')


def _range(c, lo, hi, label, term="range-52w"):
    """Where today's price sits in its 52-week range (or, without one, in
    the chart's range), with your average cost marked when you own it."""
    price = _num(c.latest.get("close"))
    lo, hi = _num(lo), _num(hi)
    if price is None:
        return ""
    if lo is None or hi is None or hi <= lo:
        closes = [v for v in sc.clean(c.data["close"]) if v is not None] if "close" in c.data else []
        if len(closes) < 2:
            return ""
        lo, hi = min(closes), max(closes)
        label, term = f"Range since {sc.fmt_day(c.data.index[0])}", "range-52w"
    marks, key = [], ""
    if c.avg_cost is not None:
        if lo <= c.avg_cost <= hi:
            marks.append((c.avg_cost, "Your average cost", "cost", False))
            key = '<span class="rbar-key"><i class="cost"></i>your average cost</span>'
        else:          # never pinned to an end of the bar, where it would read as that price
            key = (f'<span class="rbar-key">your average cost is {"below" if c.avg_cost < lo else "above"} '
                   'this range</span>')
    pos = max(0.0, min((price - lo) / (hi - lo) * 100, 100.0))
    return Markup(f'<div class="hero-range"><div class="hero-range-head"><span>{escape(label)}{ui.info(term)}</span>'
                  f'<span>{pos:.0f}% of the way up{key}</span></div>'
                  f'{ui.range_bar(lo, hi, price, marks=marks, fmt=c.money, label=label)}</div>')


def _private(text, after=""):
    """An amount inside a figure's small print, masked by Hide amounts too."""
    return Markup(f'<span class="private">{escape(text)}</span>{escape(after)}')


def _position(c, h):
    """Your shares in this company — private, from your holdings files.
    Every amount (values and small print) is masked by Hide amounts."""
    cur = "USD" if c.market == "INTL" else "KES"
    tiles = [ui.kpi("Shares you own", _qty(h.get("quantity")), private=True,
                    sub=(f"{h['n_lots']} purchase{'s' if h['n_lots'] != 1 else ''}"
                         + (f" since {h['earliest_buy_date']}" if h.get("earliest_buy_date") else ""))
                    if h.get("n_lots") else None),
             ui.kpi("Your average cost", _money(h.get("avg_cost"), cur, 2), private=True, term="cost-basis",
                    sub=_private(_money(h.get("cost_basis"), cur), " in total"))]
    if h.get("data_available"):
        gain, mv = h.get("gain"), h.get("market_value")
        kes = h.get("market_value_kes")
        tiles += [
            ui.kpi("Value today", _money(mv, cur), private=True,
                   sub=_private(f"≈ {_money(kes)}") if cur == "USD" and kes is not None else None),
            ui.kpi("Gain / loss", _money(gain, cur, sign=True), private=True, term="gain-loss",
                   tone=ui.tone_of(gain) if gain else None,
                   delta_html=ui.delta(h.get("gain_pct"), fmt="{:+.1f}%") if h.get("gain_pct") is not None else None,
                   sub="vs what you paid"),
            ui.kpi("Today", _money(h.get("day_change_value"), cur, sign=True), private=True, term="day-change",
                   tone=ui.tone_of(h.get("day_change_value")) if h.get("day_change_value") else None,
                   sub="since the previous close"),
        ]
        if h.get("est_annual_dividend"):
            tiles.append(ui.kpi("Est. yearly dividend", _money(h["est_annual_dividend"], cur), private=True,
                                term="est-dividend", sub="before tax"))
    else:
        tiles.append(ui.kpi("Value today", "—", sub="no price today — left out of your totals, not guessed"))
    tab = "international" if c.market == "INTL" else "kenyan"
    return ui.section(ui.kpi_row(tiles, cls="kpis-position"), title="Your position", sec_id="position",
                      icon="briefcase", sub="Private: from your own holdings files on this computer.",
                      actions=Markup(f'<a href="portfolio.html#{tab}" class="small">Open in My Portfolio ›</a>'))


# ------------------------------------------------------------------ summary tab
def _key_stats(c, items):
    """The headline company figures in one strip: (label, text, colour class, ⓘ term)."""
    tiles = [ui.kpi(lab, text, term=term, tone=_TONE.get(cls)) for lab, text, cls, term in items]
    return ui.kpi_row(tiles, cls="kpis-stats")


def _f(v, d=2, pct=False, when="truthy"):
    x = _num(v)
    if x is None or (when == "truthy" and x == 0):
        return "N/A"
    return f"{x:,.{d}f}" + ("%" if pct else "")


def _nse_key_stats(c):
    f, col = c.f, c.fund_color
    pe, peg, roe, eps = f.get("pe_ratio"), f.get("peg_ratio"), f.get("roe"), f.get("eps_ttm")
    nm, de, rsi = f.get("net_margin"), f.get("debt_to_equity"), c.latest.get("rsi")
    items = [("Market Cap", c.fmt_currency(f.get("market_cap")), "", "market-cap"),
             ("P/E Ratio", _f(pe), col("pe", pe), "pe"),
             ("PEG Ratio", _f(peg), col("peg", peg), "peg"),
             ("ROE", _f(roe, 1, True), col("roe", roe), "roe"),
             ("EPS", _f(eps, when="set"), col("eps", eps), "eps"),
             ("Net Margin", _f(nm, 1, True, when="set"), col("nm", nm), "net-margin"),
             ("Debt / Equity", _f(de, when="set"), col("de", de), "debt-to-equity"),
             ("RSI (14)", _f(rsi, 1), "", "rsi")]
    return _key_stats(c, [(lab, t, cls if t != "N/A" else "", term) for lab, t, cls, term in items])


def _intl_key_stats(c):
    f, col = c.f, c.fund_color
    pe, peg, roe, eps = f.get("pe_ratio"), f.get("peg_ratio"), f.get("roe"), f.get("eps_ttm")
    nm, de, rsi = f.get("net_margin"), f.get("debt_to_equity"), c.latest.get("rsi")
    eps_t = c.money(_num(eps)) if _num(eps) else "N/A"
    items = [("Market Cap", c.fmt_currency(f.get("market_cap")), "", "market-cap"),
             ("P/E Ratio", _f(pe), col("pe", pe), "pe"),
             ("PEG Ratio", _f(peg), col("peg", peg), "peg"),
             ("ROE", _f(roe, 1, True, when="set"), col("roe", roe), "roe"),
             ("EPS", eps_t, col("eps", eps), "eps"),
             ("Net Margin", _f(nm, 1, True, when="set"), col("nm", nm), "net-margin"),
             ("Debt / Equity", _f(de, when="set"), col("de", de), "debt-to-equity"),
             ("RSI (14)", _f(rsi, 1), "", "rsi")]
    return _key_stats(c, [(lab, t, cls if t != "N/A" else "", term) for lab, t, cls, term in items])


_EXPLAIN = [
    ("P/E Ratio (Price-to-Earnings)",
     "How many shillings you pay for every 1 shilling of profit. "
     "<em>Low P/E (&lt;15) = potentially cheap. High P/E (&gt;25) = investors expect fast growth.</em>", "pe"),
    ("PEG Ratio (Price/Earnings to Growth)",
     "P/E divided by earnings growth rate. PEG &lt; 1.0 may mean the stock is undervalued relative to its growth.",
     "peg"),
    ("ROE (Return on Equity)",
     "How much profit the company makes with shareholder money. <em>15%+ = good, 20%+ = excellent.</em>", "roe"),
    ("ROIC (Return on Invested Capital)",
     "How well the company uses ALL invested money (from shareholders AND lenders) to generate profit. "
     "<em>ROIC &gt; 15% is strong.</em>", None),
    ("RSI (Relative Strength Index)",
     "Momentum indicator from 0-100. <em>Above 70 = overbought (price ran up fast, may pull back). Below 30 = "
     "oversold (price fell too far, may bounce). 30-70 = neutral.</em>", "rsi"),
    ("MACD (Moving Average Convergence Divergence)",
     "Tracks the relationship between two moving averages. <em>MACD above signal line = bullish. MACD below "
     "signal line = bearish. A \"cross\" signals a potential trend change.</em>", None),
    ("Bollinger Bands",
     "Price 'envelope' around a moving average. <em>Price near upper band = relatively expensive. Price near "
     "lower band = relatively cheap. Bands widen when volatility increases.</em>", None),
    ("Debt-to-Equity",
     "How much debt vs. shareholder equity. <em>Below 0.5 = conservative (low risk). Above 2.0 = aggressive "
     "(high risk).</em>", "de"),
    ("Operating Margin",
     "Percentage of revenue left after paying for operations. <em>Higher = more efficient business. 20%+ is "
     "generally strong.</em>", None),
    ("Net Margin",
     "Percentage of revenue that becomes actual profit after ALL costs. <em>The 'bottom line' — higher is "
     "better.</em>", None),
    ("EPS Growth",
     "How fast earnings per share are growing year-over-year. <em>10%+ is solid. 20%+ is excellent. Negative = "
     "profits shrinking.</em>", None),
    ("Revenue Growth",
     "How fast total sales are growing year-over-year. <em>Growing revenue = expanding business. Falling = may "
     "be losing market share.</em>", None),
]


def _explanations(c, rg):
    """The plain-English guide that sat under the key figures, with this
    stock's own reading where there is a value."""
    reading = {"pe": (c.f.get("pe_ratio"), rg._interpret_pe), "peg": (c.f.get("peg_ratio"), rg._interpret_peg),
               "roe": (c.f.get("roe"), rg._interpret_roe), "rsi": (c.latest.get("rsi"), rg._interpret_rsi),
               "de": (c.f.get("debt_to_equity"), rg._interpret_de)}
    cards = []
    for title, text, key in _EXPLAIN:
        extra = ""
        if key and _num(reading[key][0]):
            v, fn = reading[key]
            extra = f'<br><span class="interpretation">{escape(fn(v))}</span>'
        cards.append(f'<div class="explain-card"><h4>{escape(title)}</h4><p>{text}{extra}</p></div>')
    return ui.details("What These Terms Mean (Plain English)",
                      Markup(f'<div class="explain-grid">{"".join(cards)}</div>'), det_id="explained")


def _price_chart(c, extra_refs=()):
    refs = []
    if c.avg_cost is not None:
        refs.append((c.avg_cost, "cost", "Your average cost"))
    refs += list(extra_refs)
    return sc.price_chart("c-price", c.data, money=c.money_fmt, refs=refs,
                          title=f"{c.symbol} — Price & Moving Averages", height=320,
                          head_extra=ui.info("sma"))


def _overall_desc(overall):
    if overall == "bullish":
        return "Most technical indicators point upward — the stock is in a positive trend."
    if overall == "bearish":
        return "Most technical indicators point downward — the stock is in a negative trend."
    return "Technical indicators are mixed — no clear direction at this time."


def _verdict(label, value, tone, body=""):
    head = (f'<span class="verdict-label">{escape(label)}</span><b class="verdict-value">{escape(value)}</b>'
            if label else "")
    return Markup(f'<div class="verdict" data-tone="{tone}">{head}{body}</div>')


def _factor_line(score, tail):
    """Value 60 · Quality 70 · … — each with a small bar, in one sentence."""
    chips = []
    for key, lab in (("value", "Value"), ("quality", "Quality"), ("momentum", "Momentum"),
                     ("dividend", "Dividend"), ("liquidity", "Liquidity")):
        v = score.get(key)
        if sc.ok(v):
            tone = "good" if v >= 70 else "fair" if v >= 45 else "weak"
            bar = f'<span class="fbar" data-tone="{tone}"><span style="width:{max(0, min(float(v), 100)):.0f}%"></span></span>'
            chips.append(f'<span class="fchip">{lab} <b>{escape(v)}</b>{bar}</span>')
        else:
            chips.append(f'<span class="fchip">{lab} <b>—</b></span>')
    return Markup(" · ".join(chips) + ". " + tail)


def _score_kpi(c, tail):
    s = c.score or {}
    overall = s.get("overall")
    sub = _factor_line(s, tail) if overall is not None else Markup(escape(tail))
    tone = None
    if sc.ok(overall):
        tone = "up" if overall >= 70 else "warn" if overall >= 45 else "down"
    return ui.kpi("Factor score (0-100)", overall if overall is not None else "N/A", term="factor-score",
                  sub=sub, tone=tone)


def _nse_dividend_kpi(c):
    f = c.f
    dps = _num(f.get("dps_fy"))
    parts = [f'<span class="div-pay">KES {dps:g}/share</span>' if dps and dps > 0
             else '<span class="div-zero">0 — no dividend</span>']
    if f.get("dividend_ex_date"):
        if f.get("dividend_ex_date_is_upcoming"):
            parts.append(f'<small>Ex-div: <span class="exdate-upcoming">{escape(f["dividend_ex_date"])} (upcoming)'
                         '</span></small>')
        else:
            parts.append(f'<small>Ex-div: <span class="exdate-past">{escape(f["dividend_ex_date"])} (passed)'
                         '</span></small>')
    if f.get("earnings_next_date"):
        parts.append(f'<small>Next earnings: {escape(f["earnings_next_date"])}</small>')
    return ui.kpi("Dividend", Markup(" ".join(parts)), term="ex-dividend",
                  sub="A green (upcoming) ex-date means you can still buy before it to receive the declared "
                      "dividend. 0 means the company currently pays no dividend.")


def _checks_nse(c):
    v, f = c.validation, c.f
    status = v.get("status")
    mark, tone = {"ok": ("✓ Verified", "up"), "mismatch": ("❗ Differs", "down"),
                  "stale": ("🕒 Stale", "warn")}.get(status, ("— Unverified", None))
    note = escape(v.get("note") or "No independent comparison available.")
    if v.get("reference_price"):
        note += Markup(f" Independent ({escape(v.get('reference_source'))}): "
                       f"{float(v['reference_price']):.2f} KES.")
    tiles = [ui.kpi("Price verification", mark, tone=tone, term="price-check",
                    sub=Markup(f"{note} TradingView data is ~15 min delayed.")),
             _score_kpi(c, "A transparent mechanical screen of public metrics — not investment advice."),
             _nse_dividend_kpi(c)]
    if f.get("price_52w_high") or f.get("value_traded"):
        val = []
        if f.get("price_52w_low") and f.get("price_52w_high"):
            val.append(f"52wk: {float(f['price_52w_low']):.2f}–{float(f['price_52w_high']):.2f}")
        if f.get("value_traded"):
            val.append(f"<small>Traded: {escape(c.fmt_currency(f['value_traded']))}</small>")
        tiles.append(ui.kpi("Range & liquidity", Markup(" ".join(val)), term="value-traded",
                            sub="Value traded shows how easily you can buy/sell without moving the price."))
    extra = []
    if c.sector_context:
        bits = []
        for k, ctx in c.sector_context.items():
            verdict = str(ctx.get("verdict") or "")
            chip = ("chip-buy" if verdict.startswith(("cheaper", "above")) else
                    "chip-sell" if verdict.startswith(("pricier", "below")) else "chip-none")
            med = ctx.get("sector_median")
            tip = f' title="Sector median: {float(med):,.2f}"' if sc.ok(med) else ""
            bits.append(f'<span class="chip {chip}"{tip}>{escape(_metric_name(k))} {float(ctx["value"]):.2f} '
                        f'({escape(verdict)})</span>')
        extra.append(f'<p class="chip-line"><strong>Vs sector median:</strong> {" · ".join(bits)}</p>')
    if c.alerts:
        chips = " · ".join(f'<span class="chip chip-neutral">{escape(a)}</span>' for a in c.alerts)
        extra.append(f'<p class="chip-line"><strong>Alerts:</strong> {chips}</p>')
    return ui.card(ui.kpi_row(tiles, cls="kpis-checks") + Markup("".join(extra)),
                   title="Price Check, Score & Alerts", icon="check", cls="checks")


def _metric_name(k):
    """pe_ratio -> 'PE Ratio', roe -> 'ROE' (reads the same as the old title-case labels)."""
    words = str(k).replace("_", " ").split()
    return " ".join(w.upper() if w in ("pe", "pb", "ps", "roe", "roa", "roic", "eps", "ev") else w.title()
                    for w in words)


# ------------------------------------------------------------------ fundamentals tab
def _perf_card(c):
    cells = []
    for lab, key in _PERF:
        x = _num(c.f.get(key))
        if x is None:
            cells.append(f'<div class="perf-cell" data-kpi="{lab}"><span>{lab}</span>'
                         '<b class="kpi-value num">N/A</b></div>')
            continue
        cls = "positive" if x > 0 else "negative" if x < 0 else ""
        tone = "up" if x > 0 else "down" if x < 0 else "flat"
        cells.append(f'<div class="perf-cell" data-tone-bg="{tone}" data-kpi="{lab}"><span>{lab}</span>'
                     f'<b class="kpi-value num {cls}">{x:+.2f}%</b></div>')
    title = Markup('Price Performance <span class="h-note">— % price change over each period (TradingView). '
                   '<span class="positive">green = up</span>, <span class="negative">red = down</span>.</span>')
    rec = _mrow("Analyst Recommendation", c.rec_text, term="recommendation")
    return ui.card(Markup(f'<div class="perf-strip">{"".join(cells)}</div><div class="mrows">{rec}</div>'),
                   title=title, icon="activity", cls="perf-card")


def _fundamentals_tab(c, groups, source):
    title = Markup(f'Fundamental Analysis <span class="h-note">Data from {escape(source)} as of '
                   f'{escape(c.data_date)}</span>')
    cards = [_group_card(c, t, icon, rows) for t, icon, rows in groups]
    if c.market == "INTL" and (c.f.get("held_pct_insiders") is not None
                               or c.f.get("held_pct_institutions") is not None):
        t, icon, rows = _OWNERSHIP
        cards.append(_group_card(c, t, icon, rows))
    body = [_legend(see_guide=False), Markup(f'<div class="mgrid">{"".join(str(x) for x in cards)}</div>')]
    if c.market == "NSE":
        body.append(_perf_card(c))
    return ui.section(_join(body), title=title, sec_id="fundamentals-top", icon="bar")


# ------------------------------------------------------------------ technicals tab
def _signals_card(c):
    chips, up, down, other = [], 0, 0, 0
    for name in _SIGNALS:
        if name not in c.signals:
            continue
        val = str(c.signals[name])
        up += val in _SIGNAL_UP
        down += val in _SIGNAL_DOWN
        other += val not in _SIGNAL_UP and val not in _SIGNAL_DOWN
        chips.append(f'<span class="pill {escape(val)}">{escape(name.replace("_", " ").title())}: '
                     f'{escape(val.replace("_", " ").title())}</span>')
    tally = (f'<p class="footnote">{up} of these point up (bullish), {down} point down (bearish) and {other} are '
             'neutral or have no reading. "Overbought" counts as a sign of a turn down, "oversold" of a turn up.'
             '</p>') if chips else ""
    return ui.card(Markup(f'<div class="signal-board">{" ".join(chips)}</div>{tally}'),
                   title="Technical Indicators", icon="pulse", term="signal")


def _levels_card(c):
    if not (c.supports or c.resistances):
        return ""
    close = _num(c.latest.get("close"))

    def chips(levels, cls):
        out = []
        for lv in levels[:5]:
            dist = f' <small>{(lv - close) / close * 100:+.1f}%</small>' if close else ""
            out.append(f'<span class="level-badge {cls}">{escape(c.level_fmt(lv))}{dist}</span>')
        return "".join(out)
    cols = []
    if c.supports:
        cols.append(f'<div class="level-box"><h4>🟢 Support Levels (floor)</h4>{chips(c.supports, "support")}</div>')
    if c.resistances:
        cols.append(f'<div class="level-box"><h4>🔴 Resistance Levels (ceiling)</h4>'
                    f'{chips(c.resistances, "resistance")}</div>')
    return ui.card(Markup(f'<div class="levels">{"".join(cols)}</div>'
                          '<p class="footnote">The small figure is the distance from today\'s close.</p>'),
                   title="Support & Resistance Levels", icon="layers", term="support-resistance")


def _technicals_tab(c):
    small = sc.indicator_charts("c-ind", c.data, money=c.money_fmt, x_ref="c-price",
                                extras={"rsi": ui.info("rsi"), "macd": ui.info("macd"),
                                        "stoch": ui.info("stochastic"), "atr": ui.info("atr"),
                                        "volume": ui.info("volume")})
    charts = ([small["volume"]] if "volume" in c.data.columns else []) + [small["rsi"], small["macd"]]
    if "stoch_k" in c.data.columns and "stoch_d" in c.data.columns:
        charts.append(small["stoch"])
    if "atr" in c.data.columns:
        charts.append(small["atr"])
    chart_cards = "".join(str(ui.card(ch, cls="chart-card")) for ch in charts)
    tiles = []
    for key, label, term in _TECH_VALUES:
        x = _num(c.latest.get(key))
        if x is None:
            continue
        text = (f"{x:,.0f}" if x.is_integer() else f"{x:,.2f}") if key == "volume" else f"{x:,.2f}"
        tiles.append(ui.kpi(label, text, term=term))
    return _join([
        _signals_card(c),
        Markup(f'<div class="grid-2 ind-grid">{chart_cards}{_levels_card(c)}</div>'),
        ui.card(ui.kpi_row(tiles, cls="kpis-tech"), title="Technical Values", icon="activity"),
    ])


# ------------------------------------------------------------------ peers, news, history, glossary
def _peer_table(c, rows, table_id, *, why=False, you=False):
    cols = [ui.Col("Symbol", sort="text"), ui.Col("Price (KES)", sort="number"),
            ui.Col("Market Cap", sort="number", term="market-cap"), ui.Col("P/E", sort="number", term="pe"),
            ui.Col("ROE", sort="number", term="roe"), ui.Col("Net Margin", sort="number", term="net-margin"),
            ui.Col("Rev Growth", sort="number", term="revenue-growth")]
    if why:
        cols.append(ui.Col("Why Similar", sort=None))
    def pc(v, rule):
        x = _num(v)
        if not x:
            return ui.cell("N/A")
        return ui.cell(f"{x:,.1f}%", sort=round(x, 4), tone=_TONE.get(c.fund_color(rule, x)))

    body, attrs = [], []
    for p in rows:
        sym = p.get("symbol") or ""
        is_you = you and sym == c.symbol
        growth = p.get("revenue_growth") if why else p.get("revenue_growth_yoy")
        pe = _num(p.get("pe_ratio"))
        mc = _num(p.get("market_cap"))
        sym_html = Markup(f'<strong>{escape(sym)}</strong>'
                          + (' <span class="pill" data-tone="accent">← You are here</span>' if is_you else ""))
        row = [ui.cell(sym_html, sort=sym),
               ui.cell(f"{_num(p.get('close')):,.2f}" if _num(p.get("close")) else "N/A",
                       sort=_num(p.get("close"))),
               ui.cell(c.fmt_mcap(p.get("market_cap")), sort=mc),
               ui.cell(f"{pe:,.2f}" if pe else "N/A", sort=pe, tone=_TONE.get(c.fund_color("pe", pe)) if pe else None),
               pc(p.get("roe"), "roe"), pc(p.get("net_margin"), "nm"), pc(growth, "rg")]
        if why:
            row.append(ui.cell(Markup(f'<span class="similarity-reason">{escape(", ".join(p.get("reasons") or []))}'
                                      '</span>')))
        body.append(row)
        attrs.append('class="is-you"' if is_you else "")
    return ui.table(cols, body, table_id=table_id, row_attrs=attrs)


def _peers_tab(c):
    sector = c.f.get("sector") or ""
    parts = []
    if c.similar:
        parts.append(ui.section(
            Markup(f'<p class="section-desc">Stocks in the same sector ({escape(sector)}) with comparable '
                   'characteristics. Use these for peer comparison when making investment decisions.</p>')
            + _peer_table(c, c.similar, "similar-table", why=True),
            title=f"Similar Stocks to {c.symbol}", sec_id="similar", icon="layers"))
    if c.peers:
        parts.append(ui.section(
            Markup(f'<p class="section-desc">Every stock in the {escape(sector)} sector on the NSE, ranked by market '
                   f'cap. Compare {escape(c.symbol)} against its direct competitors.</p>')
            + _peer_table(c, c.peers, "peers-table", you=True),
            title=f"All {sector} Sector Peers", sec_id="sector-peers", icon="bar"))
    return _join(parts)


def _news_tab(c):
    if not c.news:
        return ui.section(Markup(f"<p>No recent headlines found for {escape(c.symbol)}.</p>"),
                          title="Recent News", sec_id="recent-news", icon="news")
    rows = []
    for n in c.news[:15]:
        when = (n.get("published_utc") or "")[:16]
        when = f"{when.replace('T', ' ')} UTC" if len(when) == 16 and "T" in when else (when or "—")
        link = ui.href(n.get("url")) if str(n.get("url") or "").lower().startswith(("http://", "https://")) else ""
        title = escape(n.get("title") or "")
        head = Markup(f'<a href="{link}" target="_blank" rel="noopener">{title}</a>') if link else title
        rows.append([ui.cell(when, sort=n.get("published_utc") or ""), ui.cell(head, cls="wrap-cell"),
                     ui.cell(n.get("source") or "")])
    table = ui.table([ui.Col("Date", sort="text"), ui.Col("Headline", sort=None), ui.Col("Source", sort="text")],
                     rows, table_id="news-table", cls="news-table")
    return ui.section(
        Markup(f'<p class="section-desc">Headlines mentioning {escape(c.symbol)}, newest first. <strong>Shown '
               'neutral, on purpose</strong> — read the headline and judge for yourself; this dashboard doesn\'t '
               'fabricate sentiment scores.</p>') + table,
        title="Recent News", sec_id="recent-news", icon="news")


def _history_tab(c):
    df = c.data
    parts = []
    if len(df):
        tail = df.tail(11)                     # one extra day, for the oldest row's change
        closes = [_num(v) for v in (tail["close"] if "close" in tail.columns else [None] * len(tail))]
        rows = []
        for i in reversed(range(len(tail))[-10:]):
            idx, r = tail.index[i], tail.iloc[i]
            date = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
            prev = closes[i - 1] if i > 0 else None
            chg = (closes[i] - prev) / prev * 100 if closes[i] is not None and prev else None

            def px(col):
                x = _num(r.get(col))
                return ui.cell(c.level_fmt(x) if x is not None else "N/A", sort=x)
            vol = _num(r.get("volume"))
            rows.append([ui.cell(date, sort=date), px("open"), px("high"), px("low"), px("close"),
                         ui.cell(ui.delta(chg) if chg is not None else "—", sort=round(chg, 4) if chg is not None else None),
                         ui.cell(f"{vol:,.0f}" if vol else "N/A", sort=vol)])
        cols = [ui.Col("Date", sort="text"), ui.Col("Open", sort="number"), ui.Col("High", sort="number"),
                ui.Col("Low", sort="number"), ui.Col("Close", sort="number"),
                ui.Col("Change", sort="number", term="day-change"), ui.Col("Volume", sort="number", term="volume")]
        parts.append(ui.section(ui.table(cols, rows, table_id="recent-table"),
                                title="Recent Trading Data (Last 10 Days)", sec_id="recent-data", icon="calendar",
                                sub="Newest first. Change is against the previous trading day's close."))
    else:
        parts.append(ui.section(Markup("<p>No recent data available.</p>"), title="Recent Trading Data (Last 10 Days)",
                                sec_id="recent-data", icon="calendar"))
    if c.market == "INTL" and c.dividend_history:
        rows = [[ui.cell(str(d.get("date")), sort=str(d.get("date"))),
                 ui.cell(c.money(_num(d.get("amount")), 4) if _num(d.get("amount")) is not None else "N/A",
                         sort=_num(d.get("amount")))]
                for d in reversed(list(c.dividend_history))]
        parts.append(ui.section(
            Markup(f'<p class="section-desc">Real payments Yahoo Finance has on record for {escape(c.symbol)} — not '
                   'a projection.</p>')
            + ui.table([ui.Col("Date", sort="text"), ui.Col("Amount per Share", sort="number")], rows,
                       table_id="dividend-table"),
            title="Dividend History", sec_id="dividend-history", icon="coins"))
    return _join(parts)


_GLOSSARY_NSE = [
    ("RSI", "(Relative Strength Index)",
     "A 0–100 speed meter of how fast the price has been rising or falling. Above 70 = bought too fast (may "
     "rest/drop); below 30 = sold too fast (may bounce).",
     "Like a swing pushed very high (70+) — it usually swings back down soon."),
    ("Stochastic", "(%K and %D)",
     "Two lines (0–100) showing where today's price sits inside its recent high–low range. Above 80 = near the "
     "ceiling; below 20 = near the floor. %K is the fast line, %D is the slower, smoother line.",
     "A ball in a box: at 90 it's near the top (may come down), at 10 near the bottom (may bounce up)."),
    ("MACD", "(Moving Avg Convergence Divergence)",
     "Compares a fast and a slow average of the price. When the MACD line crosses <em>above</em> its signal line "
     "it's a bullish hint; crossing <em>below</em> is bearish. The bars show how far apart they are.",
     "Two runners: when the fast runner overtakes the slow one, momentum is turning up."),
    ("Bollinger Bands", "",
     "A price \"envelope\" — an average line with an upper and lower band. Price near the upper band = relatively "
     "expensive; near the lower = relatively cheap. Bands widen when the price is jumpy.",
     "Guard-rails on a road: touching the top rail means the price has run high for now."),
    ("SMA / EMA", "(Simple / Exponential Moving Average)",
     "The average price over the last N days, drawn as a smooth line to reveal the trend. SMA 20 = 20-day "
     "average, SMA 50 = 50-day. EMA reacts a bit faster (weights recent days more).",
     "Price above its SMA 50 line = generally trending up."),
    ("Golden / Death Cross", "",
     "When the short average crosses <em>above</em> the long average = \"golden cross\" (bullish). Crossing "
     "<em>below</em> = \"death cross\" (bearish).",
     "Fast line jumping over the slow line going up = a hopeful sign."),
    ("Volume", "",
     "How many shares were traded that day — how busy the day was. <span class=\"positive\">Green bar</span> = "
     "price rose that day, <span class=\"negative\">red bar</span> = price fell. \"Vol SMA 20\" is the 20-day "
     "average, a baseline.",
     "A tall green bar on a price jump = lots of buyers piling in."),
    ("ATR", "(Average True Range)",
     "A \"bounciness\" meter — how much the price typically moves in a day. Higher ATR = wilder swings; lower = "
     "calmer.",
     "ATR of 2 KES means the price usually moves about 2 shillings a day."),
    ("OBV", "(On-Balance Volume)",
     "A running tug-of-war score: it adds volume on up days and subtracts it on down days. Rising OBV = buyers "
     "winning; falling = sellers winning.",
     "Like a scoreboard — if it keeps climbing, buyers are stronger."),
    ("Support &amp; Resistance", "",
     "Support = a price \"floor\" where buyers tend to step in and the price bounces up. Resistance = a "
     "\"ceiling\" where sellers step in and the price stalls.",
     "If a stock keeps bouncing off 50 KES, that 50 is support (the floor)."),
    ("TTM / YoY / FY", "",
     "TTM = \"trailing twelve months\" (the last year of results). YoY = \"year over year\" (vs the same time "
     "last year). FY = \"financial year\".",
     "\"Revenue growth YoY +10%\" = sales are 10% higher than a year ago."),
]

_GLOSSARY_INTL = [
    ("Analyst Price Target", "",
     "What professional Wall Street analysts think the stock will be worth in about a year, on average "
     "(\"mean\"). It's a real, published opinion — not a guarantee.",
     "Target $420 vs today's $338 → analysts see about 24% upside, on average."),
    ("Recommendation Consensus", "",
     "The blended Buy/Hold/Sell opinion across all analysts covering the stock — the closest thing to \"market "
     "sentiment\" this page offers, and it's real and sourced, not invented.",
     "\"Strong Buy\" from 15 analysts means most of them rate it a buy."),
    ("RSI", "(Relative Strength Index)",
     "A 0–100 speed meter of how fast the price has been rising or falling. Above 70 = bought too fast (may "
     "rest/drop); below 30 = sold too fast (may bounce).",
     "Like a swing pushed very high (70+) — it usually swings back down soon."),
    ("MACD", "(Moving Avg Convergence Divergence)",
     "Compares a fast and a slow average of the price. Crossing <em>above</em> its signal line is a bullish "
     "hint; crossing <em>below</em> is bearish.",
     "Two runners: when the fast runner overtakes the slow one, momentum is turning up."),
    ("Bollinger Bands", "",
     "A price \"envelope\" — an average line with an upper and lower band. Price near the upper band = relatively "
     "expensive; near the lower = relatively cheap.",
     "Guard-rails on a road: touching the top rail means the price has run high for now."),
    ("SMA / EMA", "(Simple / Exponential Moving Average)",
     "The average price over the last N days, drawn as a smooth line to reveal the trend. EMA reacts a bit "
     "faster (weights recent days more).",
     "Price above its SMA 50 line = generally trending up."),
    ("Volume", "",
     "How many shares were traded that day. <span class=\"positive\">Green bar</span> = price rose that day, "
     "<span class=\"negative\">red bar</span> = price fell.",
     "A tall green bar on a price jump = lots of buyers piling in."),
    ("ATR", "(Average True Range)",
     "A \"bounciness\" meter — how much the price typically moves in a day. Higher ATR = wilder swings.",
     "ATR of $2 means the price usually moves about $2 a day."),
    ("Support &amp; Resistance", "",
     "Support = a price \"floor\" where buyers tend to step in. Resistance = a \"ceiling\" where sellers step in.",
     "If a stock keeps bouncing off $50, that $50 is support (the floor)."),
    ("Beta", "",
     "How much a stock tends to move compared to the overall market. Beta of 1 = moves with the market; above 1 "
     "= swings more; below 1 = swings less.",
     "Beta 1.5 → if the market moves 10%, this stock tends to move about 15%."),
    ("TTM / YoY", "",
     "TTM = \"trailing twelve months\" (the last year of results). YoY = \"year over year\" (vs the same time "
     "last year).",
     "\"Revenue growth YoY +24%\" = sales are 24% higher than a year ago."),
]


def _glossary_tab(rows, intro):
    # Our own fixed text (with a little markup), not third-party data.
    body = [[ui.cell(Markup(f"<strong>{term}</strong>" + (f"<br>{escape(sub)}" if sub else ""))),
             ui.cell(Markup(what)), ui.cell(Markup(eg))] for term, sub, what, eg in rows]
    table = ui.table([ui.Col("Term", sort=None), ui.Col("What it means (simple)", sort=None),
                      ui.Col("Example", sort=None)], body, sortable=False, cls="wrap glossary")
    return ui.section(Markup(f'<p class="section-desc">{escape(intro)}</p>') + table,
                      title="Chart & Indicator Glossary — explained like you're 10", sec_id="glossary-table",
                      icon="info")


# ------------------------------------------------------------------ assembling a page
def _context(rg, *, symbol, market, market_label, data, latest, signals, supports, resistances, daily_change,
             fundamentals, score, rec_text, rec_class, data_date, money, money_fmt, level_fmt, fmt_mcap,
             fmt_currency, holding, logo, name, **extra):
    h = holding or None
    avg = _num(h.get("avg_cost")) if h else None
    return SimpleNamespace(
        rg=rg, symbol=symbol, market=market, market_label=market_label, data=data, latest=latest or {},
        signals=signals or {}, supports=list(supports or []), resistances=list(resistances or []),
        daily_change=daily_change, f=fundamentals, score=score or {}, rec_text=rec_text, rec_class=rec_class,
        data_date=data_date, money=money, money_fmt=money_fmt, level_fmt=level_fmt, fmt_mcap=fmt_mcap,
        fmt_currency=fmt_currency, fund_color=rg._fund_color, holding=h, avg_cost=avg if avg else None,
        logo=logo, star=_star(symbol, market, name), name=name, **extra)


def _tabs(panels):
    return ui.tabs("stock", [p for p in panels if p])


def build_nse(rg, *, symbol, data, latest, signals, supports, resistances, daily_change, fundamentals,
              similar_stocks, sector_peers, recommendation_text, rec_class, validation, score, alerts,
              sector_context, data_date, generated_at, holding=None):
    """(title, subtitle, body) of an NSE stock's page."""
    f = fundamentals
    name = f.get("name") or ""
    kes = sc.Fmt("KES ", 2)
    c = _context(rg, symbol=symbol, market="NSE", market_label="NSE", data=data, latest=latest, signals=signals,
                 supports=supports, resistances=resistances, daily_change=daily_change, fundamentals=f,
                 score=score, rec_text=recommendation_text, rec_class=rec_class, data_date=data_date,
                 money=lambda v, d=2: f"KES {v:,.{d}f}", money_fmt=kes, level_fmt=lambda v: f"{v:,.2f}",
                 fmt_mcap=rg._fmt_mcap, fmt_currency=rg._fmt_currency, holding=holding,
                 logo=_logo(rg, symbol), name=name, validation=validation or {}, alerts=alerts or [],
                 sector_context=sector_context or {}, similar=similar_stocks or [], peers=sector_peers or [],
                 news=[], dividend_history=[])
    close = _num(c.latest.get("close"))
    status = c.validation.get("status")
    check = {"ok": "✓ price verified", "mismatch": "❗ price differs from the NSE", "stale": "🕒 price may be stale"}
    meta = [f"as of {escape(data_date)}"]
    if status in check:
        meta.append(f'<span class="pv-mark" data-tone="{"up" if status == "ok" else "down" if status == "mismatch" else "warn"}">'
                    f'{check[status]}</span>')
    meta.append(f"Market cap {escape(rg._fmt_mcap(f.get('market_cap')))}")
    name_bits = [escape(x) for x in (name, f.get("sector")) if x]
    rec_term = "tv-rating" if "TradingView" in recommendation_text else "recommendation"
    hero = _hero(c, name_line=Markup(" · ".join(name_bits)) or Markup("Nairobi Securities Exchange"),
                 price_text=f"KES {close:,.2f}" if close else "N/A", kes_line="",
                 meta=Markup(" · ".join(str(m) for m in meta)), rec_text=recommendation_text, rec_term=rec_term,
                 range_html=_range(c, f.get("price_52w_low"), f.get("price_52w_high"), "52-week range"))
    verdicts = ui.card(_join([
        _verdict("Recommendation", recommendation_text, _rec_tone(rec_class)),
        _verdict("Overall Technical Signal", str(c.signals.get("overall", "neutral")).upper(),
                 {"bullish": "up", "bearish": "down"}.get(c.signals.get("overall"), "neutral"),
                 Markup(f'<p>{escape(_overall_desc(c.signals.get("overall", "neutral")))}</p>')),
    ]), title="Executive Summary", icon="flag", cls="verdicts")
    summary = _join([
        _nse_key_stats(c), _legend(), _explanations(c, rg),
        ui.card(_price_chart(c), cls="chart-card price-card"),
        verdicts, _checks_nse(c),
    ])
    n_peers = len(c.similar) + len(c.peers)
    body = _join([
        hero,
        _position(c, holding) if holding else "",
        _tabs([
            ("summary", "Summary", summary, None),
            ("fundamentals", "Fundamentals", _fundamentals_tab(c, _NSE_GROUPS, "TradingView"), None),
            ("technicals", "Technicals", _technicals_tab(c), None),
            ("peers", "Peers", _peers_tab(c), n_peers) if n_peers else None,
            ("history", "History", _history_tab(c), None),
            ("glossary", "Glossary", _glossary_tab(_GLOSSARY_NSE, "Every term and short-form used on this page, "
                                                  "in plain words with a simple example."), None),
        ]),
    ])
    source = f" · {f['_data_source']}" if f.get("_data_source") else ""
    title = f"{symbol} · {name}" if name and name != symbol else symbol
    subtitle = (f"Nairobi Securities Exchange{' · ' + f['sector'] if f.get('sector') else ''} · data as of "
                f"{data_date}{source} · page generated {generated_at}")
    return title, subtitle, body


def build_intl(rg, *, symbol, data, latest, signals, supports, resistances, daily_change, fundamentals,
               recommendation_text, rec_class, score, dividend_history, earnings_calendar, news, price_kes,
               target_upside_pct, currency, money, fmt_mcap, fmt_currency, context, data_date, generated_at,
               holding=None):
    """(title, subtitle, body) of an international stock's page — prices in
    the stock's own currency (USD, GBp, …), with KES alongside for USD."""
    f = fundamentals
    name = f.get("name") or ""
    money_fmt = sc.Fmt("$", 2) if currency == "USD" else sc.Fmt("", 2, suffix=f" {currency}")
    exchange = f.get("exchange_name") or ""
    c = _context(rg, symbol=symbol, market="INTL", market_label=exchange or currency, data=data, latest=latest,
                 signals=signals, supports=supports, resistances=resistances, daily_change=daily_change,
                 fundamentals=f, score=score, rec_text=recommendation_text, rec_class=rec_class,
                 data_date=data_date, money=money, money_fmt=money_fmt, level_fmt=lambda v: money(v),
                 fmt_mcap=fmt_mcap, fmt_currency=fmt_currency, holding=holding,
                 logo=_logo(rg, symbol, website=f.get("website"), international=True), name=name,
                 validation={}, alerts=[], sector_context={}, similar=[], peers=[], news=list(news or []),
                 dividend_history=list(dividend_history or []))
    close = _num(c.latest.get("close"))
    kes_line = Markup(f'<small class="num">≈ KES {price_kes:,.2f}</small>') if price_kes else ""
    sector = f.get("sector")
    if sector:
        where = escape(sector) + (Markup(" · ") + escape(f["industry"]) if f.get("industry") else "")
    elif f.get("quote_type") == "ETF":
        where = Markup("ETF")
    else:
        where = Markup("")
    name_bits = [x for x in (escape(name) if name and name != symbol else "", where) if x]
    meta = [f"as of {escape(data_date)}", f"Market cap {escape(fmt_mcap(f.get('market_cap')))}",
            f"priced in {escape(currency)}"]
    hero = _hero(c, name_line=Markup(" · ".join(str(b) for b in name_bits)) or Markup(escape(exchange or "International")),
                 price_text=money(close) if close else "N/A", kes_line=kes_line,
                 meta=Markup(" · ".join(meta)), rec_text=recommendation_text, rec_term="recommendation",
                 range_html=_range(c, f.get("week52_low"), f.get("week52_high"), "52-week range"))
    if context == "watchlist":
        about = Markup(f'⭐ This stock is on your private <strong>watchlist</strong> — '
                       f'{escape(exchange + " · ") if exchange else ""}priced in {escape(currency)}, sourced from '
                       'Yahoo Finance, analyzed with the exact same technical-analysis engine used for your NSE stocks.')
    else:
        about = Markup('🌍 This is one of your private <strong>international (US-listed)</strong> holdings — priced in '
                       'USD, sourced from Yahoo Finance, analyzed with the exact same technical-analysis engine used '
                       'for your NSE stocks. Never committed to git.')
    tm = _num(f.get("target_mean_price"))
    refs = [(tm, "level", "Analysts' mean target")] if tm else []
    summary = _join([
        _intl_key_stats(c), _legend(see_guide=False),
        ui.card(_price_chart(c, refs), cls="chart-card price-card"),
        _intl_verdicts(c, target_upside_pct, close), _checks_intl(c, earnings_calendar),
    ])
    body = _join([
        hero,
        Markup(f'<div class="banner" data-tone="info" role="note">{about}</div>'),
        _position(c, holding) if holding else "",
        _tabs([
            ("summary", "Summary", summary, None),
            ("fundamentals", "Fundamentals", _fundamentals_tab(c, _INTL_GROUPS, "Yahoo Finance"), None),
            ("technicals", "Technicals", _technicals_tab(c), None),
            ("news", "News", _news_tab(c), len(c.news[:15]) or None),
            ("history", "History", _history_tab(c), None),
            ("glossary", "Glossary", _glossary_tab(_GLOSSARY_INTL, "Every term used on this page, in plain words "
                                                   "with a simple example."), None),
        ]),
    ])
    title = f"{symbol} · {name}" if name and name != symbol else symbol
    subtitle = (f"{exchange + ' · ' if exchange else ''}Yahoo Finance · data as of {data_date} · "
                f"page generated {generated_at}")
    return title, subtitle, body


def _intl_verdicts(c, upside, close):
    """Two columns: what analysts and the indicators say | the analysts'
    price targets and what the company does."""
    f = c.f
    n = f.get("num_analysts")
    who = (f"Based on {n} analyst{'s' if n != 1 else ''}." if n else "No analyst coverage available.")
    overall = c.signals.get("overall", "neutral")
    left = [_verdict("Wall Street Analyst Consensus", c.rec_text, _rec_tone(c.rec_class),
                     Markup(f"<p>{escape(who)} Real, sourced third-party opinion — "
                            "<strong>not personalized advice</strong>.</p>")),
            _verdict(None, None, {"bullish": "up", "bearish": "down"}.get(overall, "neutral"),
                     Markup(f'<p><strong>Overall Technical Signal:</strong> {escape(str(overall).title())} — '
                            'computed the same way as every NSE stock on this dashboard (trend, MACD, RSI, '
                            'Bollinger Bands, volume), from real price/volume history, not sentiment.</p>'))]
    right = []
    tm = _num(f.get("target_mean_price"))
    if tm:
        lo, hi = _num(f.get("target_low_price")), _num(f.get("target_high_price"))
        rng = (f'{escape(c.money(lo)) if lo else "—"} – <strong>{escape(c.money(tm))}</strong> – '
               f'{escape(c.money(hi)) if hi else "—"}')
        up_cls = "positive" if (upside or 0) > 0 else "negative" if (upside or 0) < 0 else ""
        tiles = [ui.kpi("Analyst Target (Low – Mean – High)", Markup(rng), term="analyst-target"),
                 ui.kpi("Upside/Downside to Mean Target", f"{upside:+.1f}%" if upside is not None else "N/A",
                        tone=_TONE.get(up_cls))]
        bar = ""
        if lo and hi and hi > lo and close:
            bar = Markup('<div class="target-range"><span class="small muted">Today\'s price within the analysts\' '
                         'range — the tick is the mean target</span>'
                         + str(ui.range_bar(lo, hi, close, marks=[(tm, "Mean target", "cost")], fmt=c.money,
                                            label="Analyst price targets")) + '</div>')
        right.append(Markup(f'<div class="targets">{ui.kpi_row(tiles, cls="kpis-2")}{bar}</div>'))
    if f.get("summary"):
        s = str(f["summary"])
        right.append(Markup(f'<p class="about-co">{escape(s[:400])}{"…" if len(s) > 400 else ""}</p>'))
    cols = Markup(f'<div class="vcol">{_join(left)}</div>') + (Markup(f'<div class="vcol">{_join(right)}</div>')
                                                             if right else "")
    return ui.card(cols, title="Executive Summary", icon="flag", cls="verdicts")


def _checks_intl(c, earnings_calendar):
    f = c.f
    rate = _num(f.get("dividend_rate"))
    val = [f'<span class="div-pay">{escape(c.money(rate))}/share/yr</span>' if rate and rate > 0
           else '<span class="div-zero">0 — no dividend</span>']
    if f.get("ex_dividend_date"):
        val.append(f'<small>Last ex-div: {escape(f["ex_dividend_date"])}</small>')
    dates = (earnings_calendar or {}).get("Earnings Date")
    if dates:
        dates = [dates] if isinstance(dates, str) else list(dates)
        val.append(f'<small>Next earnings: {escape(" or ".join(str(d) for d in dates))}</small>')
    tiles = [_score_kpi(c, "A transparent mechanical screen of public metrics — not investment advice. "
                           "The same engine used dashboard-wide."),
             ui.kpi("Dividend", Markup(" ".join(val)), term="ex-dividend",
                    sub="\"Ex-div\" is the most recent date the dividend was declared for; a new one is announced "
                        "closer to the next payment.")]
    lo, hi, close = _num(f.get("week52_low")), _num(f.get("week52_high")), _num(c.latest.get("close"))
    sub = None
    if lo and hi and close:
        pos = (close - lo) / (hi - lo) * 100 if hi > lo else 50
        sub = f"Today's price sits {pos:.0f}% of the way from the 52-week low to high."
    tiles.append(ui.kpi("52-Week Range", f"{c.money(lo)} – {c.money(hi)}" if lo and hi else "N/A",
                        term="range-52w", sub=sub))
    vol = _num(f.get("average_volume"))
    tiles.append(ui.kpi("Avg. Daily Volume", f"{vol:,.0f} shares" if vol else "N/A", term="volume",
                        sub="Higher volume means it's easier to buy/sell without moving the price."))
    return ui.card(ui.kpi_row(tiles, cls="kpis-checks"), title="Score, Dividend & Range", icon="check",
                   cls="checks")
