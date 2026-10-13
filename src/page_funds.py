"""
🏦 Your mutual funds — the Funds tab of My Portfolio, and one private page
per fund (fund_<ISIN>.html).

Crucial first. The tab answers, in order: what are my funds worth and what
have I made (overall, today, this week / month / year); then each fund as a
card you can open; then how they compare, the profit day by day, week by
week and month by month, the top-up plan and every transaction.

Each fund's page: the price and your position at the top, then tabs —
Summary (what the fund is, in plain English, and the facts), Performance
(price, growth with payouts reinvested, every period and calendar year,
how bumpy), Holdings (what it owns), Income (its payouts), Competitors,
Your money (your top-ups, payouts and profit) and News.

Every figure comes from funds_portfolio (the arithmetic) and fund_data (the
published prices and facts); every term has an ⓘ. Private: these pages show
your units and money, so they're only ever written to your reports folder.
"""

import datetime as dt

from markupsafe import Markup, escape

import funds_portfolio as fp
import svg_charts as sc
import ui_kit as ui
from page_portfolio import _join, money, pct, tone

COLORS = ["c1", "c3", "c5", "c6", "c2", "c7", "c4"]
USD0, USD2 = sc.Fmt("$", 0), sc.Fmt("$", 2)
USD_PL = sc.Fmt("$", 2, sign=True)
PERIOD_LABELS = [("1M", "1 month"), ("3M", "3 months"), ("6M", "6 months"), ("YTD", "This year"), ("1Y", "1 year"),
                 ("3Y", "3 years, a year"), ("5Y", "5 years, a year"), ("10Y", "10 years, a year")]
TYPE_WORDS = {"topup": "Top-up", "buy": "Purchase", "sell": "Sale", "reinvest": "Payout reinvested",
              "payout": "Payout paid to you"}
PAYOUT_WORDS = {"reinvest": "Payouts reinvested", "cash": "Payouts paid to you", "none": "Accumulating — no payouts"}


# ------------------------------------------------------------------ small pieces
def usd(v, d=0, sign=False):
    return money(v, "USD", d, sign)


def nav_text(v):
    """A price per unit as published: 9.9120, 12.50, 152.4407."""
    if v is None:
        return "—"
    s = f"{v:,.4f}".rstrip("0")
    return s + "0" * max(0, 2 - len(s.split(".")[1])) if "." in s else f"{v:,.2f}"


def chip(v, *, cap=10.0, d=1, suffix="%", sign=True):
    """A return as a small coloured chip: green / red, stronger the bigger."""
    if v is None:
        return Markup('<span class="heat heat-chip num" data-dir="none">—</span>')
    t = max(0.0, min(1.0, abs(v) / cap))
    dirn = "up" if v > 0 else "down" if v < 0 else "flat"
    s = "−" if v < 0 and round(abs(v), d) else ("+" if sign and v > 0 else "")
    return Markup(f'<span class="heat heat-chip num" data-dir="{dirn}" style="--pl:{12 + 50 * t:.0f}%;'
                  f'--pd:{10 + 30 * t:.0f}%">{s}{abs(v):,.{d}f}{suffix}</span>')


def _when(d):
    try:
        return dt.date.fromisoformat(str(d)[:10]).strftime("%d %b %Y")
    except ValueError:
        return str(d)


def page_file(isin):
    return f"fund_{isin}.html"


def weekly(points, daily_days=0):
    """Keep every point from the last `daily_days` days and one a week (the
    week's last) before that — the shape stays exact, the page stays light."""
    if not points:
        return []
    cut = points[-1][0] - dt.timedelta(days=daily_days)
    out, last_week = [], None
    for i, (d, v) in enumerate(points):
        if d > cut:
            out.append((d, v))
            continue
        wk = d.isocalendar()[:2]
        nxt = points[i + 1][0].isocalendar()[:2] if i + 1 < len(points) else None
        if nxt != wk or points[i + 1][0] > cut:
            out.append((d, v))
        last_week = wk
    return out


def _bump_word(vol):
    if vol is None:
        return None
    return ("very steady" if vol < 3 else "steady" if vol < 7 else "moderately bumpy" if vol < 12
            else "bumpy" if vol < 18 else "very bumpy")


def risk_dots(n):
    if not n:
        return Markup('<span class="muted">—</span>')
    dots = "".join(f'<i class="{"on" if i < n else ""}"></i>' for i in range(7))
    return Markup(f'<span class="risk-dots" role="img" aria-label="Risk class {n} of 7">{dots}</span> '
                  f'<b class="num">{n}</b><span class="muted"> / 7</span>')


def _fund_color(i):
    return COLORS[i % len(COLORS)]


def _name_cell(h, i=0):
    link = ui.href(h.get("report_file") or "")
    inner = (f'<i class="sw {_fund_color(i)}"></i><span>{escape(h["name"])}'
             f'<small>{escape((h.get("facts") or {}).get("category") or h["isin"])}</small></span>')
    if link:
        return Markup(f'<a class="sym" href="{link}">{inner}</a>')
    return Markup(f'<span class="sym">{inner}</span>')


# ------------------------------------------------------------------ charts
def _pl_bars(chart_id, rows, *, title, when):
    """Profit / loss columns (green up, red down) — rows [(date or (y, m), $)]."""
    rows = [(dt.date(k[0], k[1], 1) if isinstance(k, tuple) else k, v) for k, v in rows]
    if not rows:
        return Markup('<div class="chart-empty">Not enough prices yet</div>')
    total = sum(v for _d, v in rows)
    note = f"Total {usd(total, 2, sign=True)} over these {len(rows)} {when}."
    return sc.chart(chart_id, [d for d, _v in rows], [sc.Series("pl", "Profit / loss", [v for _d, v in rows],
                                                                 kind="bars", fmt=USD_PL)],
                    title=title, height=170, bars_mode=True, legend=False, note=note, show_title=False)


def _growth_lines(chart_id, series, *, title, years=3, start_value=10000.0):
    """Growth of $10,000 for several funds over the same window.
    series: [(key, label, [(date, index)])] — the first one sets the dates."""
    base = series[0][2]
    if len(base) < 2:
        return None
    end = base[-1][0]
    start = dt.date(end.year - years, end.month, min(end.day, 28))
    first = max([s[2][0][0] for s in series if s[2]] + [start])
    dates = [d for d, _v in weekly([(d, v) for d, v in base if d >= first])]
    if len(dates) < 2:
        return None
    out = []
    for key, label, pts in series:
        if not pts:
            continue
        vals, j, last, b = [], 0, None, None
        for d in dates:
            while j < len(pts) and pts[j][0] <= d:
                last = pts[j][1]
                j += 1
            if last is not None and b is None:
                b = last
            vals.append(start_value * last / b if (last is not None and b) else None)
        out.append(sc.Series(key, label, vals, fmt=USD0))
    return sc.chart(chart_id, dates, out, title=title, height=260, y_fmt=sc._short_num, show_title=False,
                    note=f"From {first:%d %b %Y}: what $10,000 would have become, with every payout reinvested.")


def _value_chart(chart_id, rows, *, title):
    rows = [r for r in rows or [] if r[1] is not None]
    if len(rows) < 2:
        return None
    return sc.chart(chart_id, [r[0] for r in rows],
                    [sc.Series("you", "Value", [r[1] for r in rows], fmt=USD2),
                     sc.Series("putin", "Money you put in", [r[2] for r in rows], fmt=USD2)],
                    title=title, height=220, y_fmt=sc._short_num, show_title=False, time_axis=True)


# ------------------------------------------------------------------ the Funds tab
def _empty_tab():
    return Markup(
        '<a id="funds-top"></a>'
        + str(ui.empty_state("You haven't added any mutual funds yet",
                             "Funds you hold through your bank go in portfolio/funds.csv on your own machine — never "
                             "committed to git.", icon="layers"))
        + str(_how_to_add(open_=True)))


def _how_to_add(open_=False):
    return ui.details("➕ Add a fund, record a purchase or confirm units", Markup(
        '<p><b>A new fund:</b> add one row to <code>portfolio/funds.csv</code> (copy '
        '<code>portfolio/funds.example.csv</code> to start) from your bank statement: <code>isin</code> (12 characters, '
        'on the statement or the fund\'s factsheet), <code>name</code>, <code>units</code>, <code>avg_cost</code> '
        '(average unit cost), <code>as_of</code> (the NAV date on the statement) and <code>statement_nav</code> (the '
        'price per unit it shows — used to check the dashboard is pricing exactly your share class). Then how it\'s run: '
        '<code>payouts</code> = reinvest, cash or none; <code>monthly_amount</code> + <code>monthly_day</code> for a '
        'standing monthly top-up; <code>fee_pct</code> = the bank\'s sales charge; optionally <code>peers</code> (ISINs '
        'of competitor funds, separated by ;) and <code>factsheet_url</code> (the fund\'s page on its manager\'s site, '
        'for the manager\'s own prices and payouts). Or from the project folder:</p>'
        '<pre>./venv/bin/python3 fund.py add ISIN UNITS AVG_COST --as-of YYYY-MM-DD --nav PRICE --name "Fund name" '
        '[--payouts reinvest|cash|none] [--monthly 50 --day 15] [--fee 2]\n'
        './venv/bin/python3 fund.py buy ISIN AMOUNT [--units UNITS] [--date YYYY-MM-DD]\n'
        './venv/bin/python3 fund.py confirm ISIN REF UNITS      # your statement\'s units for an automatic row\n'
        './venv/bin/python3 fund.py list</pre>'
        '<p><b>A one-off purchase or sale:</b> add a row to <code>portfolio/fund_transactions.csv</code> with '
        '<code>date, isin, type</code> (buy or sell), <code>amount</code> (cash) and, if you know them, the '
        '<code>units</code> from your statement — leave units blank and the dashboard works them out from that '
        'day\'s price after the sales charge.</p>'
        '<p><b>Monthly top-ups and payouts are added for you</b> — once each, never twice (every automatic row has its '
        'own <code>ref</code>). If the computer was off on the day, the next run catches up. Their units are '
        '<i>estimated</i>: when your statement arrives, type its units into the row and change <code>status</code> to '
        '<code>confirmed</code> — the dashboard never changes a confirmed row.</p>'), open_=open_, det_id="funds-add")


def funds_tab(rg, fs, *, today=None):
    """The Funds tab body (Markup)."""
    if not fs:
        return _empty_tab()
    t, funds = fs["totals"], fs["funds"]
    avail = [h for h in funds if h.get("data_available")]
    parts = [Markup('<a id="funds-top"></a>')]
    srcs = sorted({h.get("source") for h in avail if h.get("source")})
    xcheck = [h for h in avail if h.get("cross_check")]
    agree = sum(1 for h in xcheck if not h["cross_check"]["mismatches"])
    parts.append(ui.banner(Markup(
        '🏦 <strong>Your mutual funds — private, information not advice.</strong> Prices are each fund manager\'s own '
        f'published prices per unit ({escape(", ".join(srcs) or "FT")}), checked day by day against the Financial '
        f'Times: {agree} of {len(xcheck)} fund(s) agree on every date. Returns include payouts (total return), worked out '
        'here in US dollars the way fund managers report them. Your files stay on this computer.'), tone="info"))
    if fs.get("missing"):
        parts.append(ui.banner(Markup(f'⚠️ <strong>No price today for: {escape(", ".join(fs["missing"]))}.</strong> Left out '
                                      'of the totals below rather than guessed.'), tone="danger"))
    for w in fs.get("warnings") or []:
        parts.append(ui.banner(f"⚠️ {w}", tone="warn"))
    pending = [(h, p) for h in avail for p in h.get("pending") or []]
    if pending:
        parts.append(ui.banner(Markup(
            f'⏳ {len(pending)} top-up(s) waiting for their dealing day\'s price — counted at their cash value after the '
            'sales charge until the price is published.'), tone="info"))

    latest = max((h["nav_date"] for h in avail), default=None)
    kes = f"≈ KES {t['market_value_kes']:,.0f} · " if t.get("market_value_kes") is not None else ""
    nxt = sorted(((h["next_topup"], h) for h in avail if h.get("next_topup")), key=lambda x: x[0])
    plan = (f"next: {_when(nxt[0][0])} · " + " + ".join(usd(h["monthly_amount"]) for d, h in nxt if d == nxt[0][0])
            if nxt else "no monthly top-ups set")
    pp = t.get("pl_periods") or {}
    kpis = [
        ui.kpi("Funds value", usd(t["market_value"]), big=True, private=True, term="nav",
               sub=f"{kes}{t['n_available']} of {t['n_funds']} fund(s) · prices of {_when(latest)}" if latest else None),
        ui.kpi("Money you put in", usd(t["invested"]), private=True, term="cost-basis",
               sub="statement + top-ups, sales charges included"
               + (f" · top-ups {usd(t['monthly_topups'])}/month, {plan}" if t.get("monthly_topups") else "")),
        ui.kpi("Gain / loss", usd(t["gain"], sign=True), tone=tone(t["gain"]), private=True, term="gain-loss",
               delta_html=ui.delta(t["gain_pct"], fmt="{:+.2f}%") if t.get("gain_pct") is not None else None,
               sub="what your bank calls unrealised gain"),
        ui.kpi("Today", usd(t.get("day_change_value"), 2, sign=True), tone=tone(t.get("day_change_value")),
               private=True, term="day-change",
               delta_html=ui.delta(t["day_change_pct"]) if t.get("day_change_pct") is not None else None,
               sub="latest price vs the one before"),
        ui.kpi("Payouts a year", usd(t["est_annual_income"]) if t.get("est_annual_income") else "—", private=True,
               term="fund-payout", sub=f"today's units × the last 12 months' payouts · paid to you since your "
                                       f"statement: {usd(t['payouts_received'], 2)}"),
    ]
    parts.append(ui.kpi_row(kpis, cls="kpis-summary"))
    strip = []
    for key, label in (("1W", "1 week"), ("1M", "1 month"), ("3M", "3 months"), ("YTD", "This year"), ("1Y", "1 year")):
        v = pp.get(key)
        strip.append(ui.kpi(label, usd(v, 0, sign=True) if v is not None else "—", tone=tone(v), private=True))
    parts.append(ui.section(_join([
        ui.kpi_row(strip, cls="kpis-5"),
        Markup('<div class="grid-3">'
               + str(ui.card(_pl_bars("c-funds-daily", t.get("daily_pl") or [], title="Daily profit", when="days"),
                             title="Day by day", sub="last 30 price days", icon="activity"))
               + str(ui.card(_pl_bars("c-funds-weekly", t.get("weekly_pl") or [], title="Weekly profit", when="weeks"),
                             title="Week by week", sub="last 13 weeks", icon="bar"))
               + str(ui.card(_pl_bars("c-funds-monthly", t.get("monthly_pl") or [], title="Monthly profit",
                                      when="months"), title="Month by month", sub="last 12 months", icon="calendar"))
               + "</div>"),
        Markup('<p class="footnote">Profit = units held × the change in price per unit, plus payouts. Top-ups aren\'t '
               'profit, so they\'re left out; the sales charge counts as a small loss on its day. Before your '
               'statement date the dashboard assumes you held the statement\'s units (the history before it isn\'t '
               'known), so read older bars as "what your units would have made".</p>'),
    ]), title="What your funds made", sec_id="funds-profit", icon="trend-up", term="profit-periods"))

    # ---- the funds as cards
    cards = []
    for i, h in enumerate(funds):
        cards.append(_fund_card(h, i))
    parts.append(ui.section(Markup(f'<div class="fund-grid">{"".join(str(c) for c in cards)}</div>'),
                            title="Your funds", sec_id="funds-cards", icon="layers",
                            sub="Click a fund for everything about it: what it holds, its returns every year, its "
                                "payouts, its competitors and your own money in it."))

    # ---- compare: returns, risk, cost
    parts.append(_compare_section(funds))

    # ---- split + value over time + growth comparison
    segs = [(h["name"], h["market_value"], _fund_color(i)) for i, h in enumerate(funds) if h.get("data_available")]
    donut = sc.donut("c-funds-split", segs, title="Your funds", fmt=USD0, center_value=sc._short_num(t["market_value"]),
                     center_label="in funds")
    value = _value_chart("c-funds-value", t.get("value_series"), title="Funds value since your statement")
    growth = _growth_lines("c-funds-growth", [(f"f{i + 1}", h["name"], h["total_return_index"])
                                              for i, h in enumerate(funds) if h.get("data_available")],
                           title="Growth of $10,000 — your funds side by side", years=3)
    parts.append(ui.section(_join([
        Markup('<div class="grid-2">'),
        ui.card(donut, title="How your fund money is split", icon="pie", term="weight"),
        ui.card(value or Markup('<p class="muted small">📈 This line starts on your statement date and grows with every '
                                'run — your top-ups show as steps in the dashed "money you put in" line.</p>'),
                title="Value since your statement", icon="trend-up", term="cost-basis"),
        Markup('</div>'),
        ui.card(growth or Markup('<p class="muted small">Not enough shared history to compare.</p>'),
                title="Your funds side by side — growth of $10,000 over 3 years", icon="activity",
                term="fund-total-return"),
    ]), title="Charts", sec_id="funds-charts", icon="bar"))

    # ---- plan, coming up and every transaction
    parts.append(_plan_section(funds, today or dt.date.today()))
    parts.append(_explainer())
    parts.append(_how_to_add())
    return _join(parts)


def _fund_card(h, i):
    f = h.get("facts") or {}
    link = ui.href(h.get("report_file") or "")
    tag = PAYOUT_WORDS.get(h.get("payouts") or ("none" if f.get("income_treatment") == "Accumulation" else ""), "")
    if h.get("sold_out"):
        tag = "Sold — you hold none"
    head = (f'<div class="fc-head"><div class="fc-name"><b>{escape(h["name"])}</b>'
            f'<small>{escape(f.get("category") or "")}{" · " if f.get("category") else ""}{escape(h["isin"])}</small></div>'
            + (f'{ui.pill(tag, "warn" if h.get("sold_out") else "info")}' if tag else "") + "</div>")
    if not h.get("data_available"):
        body = '<p class="muted small">No price today — left out of the totals, not guessed.</p>'
        return Markup(f'<a class="fund-card" data-c="{_fund_color(i)}" href="{link}">{head}{body}</a>')
    navs = h["navs"]
    year = [v for d, v in weekly([(d, v) for d, v in navs if d > navs[-1][0] - dt.timedelta(days=365)])]
    spark = sc.sparkline(year, label=f"{h['name']}, price per unit over the last year", fmt=USD2)
    p = h["returns"]["periods"]
    stats = [("Value", usd(h["market_value"]), None, True), ("Gain / loss", f'{usd(h["gain"], sign=True)} '
                                                                           f'({pct(h["gain_pct"])})', tone(h["gain"]), True),
             ("Units", f'{h["units"]:,.3f}', None, True),
             ("Today", usd(h.get("day_change_value"), 2, sign=True), tone(h.get("day_change_value")), True)]
    stat_html = "".join(f'<div><span>{escape(k)}</span><b class="private"' + (f' data-tone="{tn}"' if tn else "")
                        + f'>{escape(v)}</b></div>' for k, v, tn, _p in stats)
    rets = " ".join(f'<span>{escape(lab)} {chip(p.get(k), cap=15 if k != "1M" else 5)}</span>'
                    for k, lab in (("1Y", "1Y"), ("3Y", "3Y/yr"), ("5Y", "5Y/yr")) if k in p)
    foot = []
    if h.get("sales"):
        r = h.get("realized") or 0.0
        foot.append(f'Sold {len(h["sales"])}×: <b class="private" data-tone="{tone(r) or "flat"}">'
                    f'{escape(usd(r, 2, sign=True))}</b> {"profit" if r >= 0 else "loss"} locked in')
    if h.get("next_topup"):
        foot.append(f'Next top-up {escape(_when(h["next_topup"]))}: <b class="private">{escape(usd(h["monthly_amount"]))}</b>'
                    + (f' ≈ {h["next_topup_units_est"]:,.3f} units' if h.get("next_topup_units_est") else ""))
    if h.get("distribution_yield"):
        foot.append(f'Payout yield {h["distribution_yield"]:.1f}%')
    foot.append('<span class="fc-open">Open ›</span>')
    return Markup(
        f'<a class="fund-card" data-c="{_fund_color(i)}" href="{link}">{head}'
        f'<div class="fc-price"><span class="num">${escape(nav_text(h["nav"]))}</span>'
        f'{ui.delta(h.get("nav_change_pct")) if h.get("nav_change_pct") is not None else ""}'
        f'<small>per unit · {escape(_when(h["nav_date"]))}</small></div>{spark}'
        f'<div class="fc-stats">{stat_html}</div><div class="fc-returns">{rets}'
        f'<span>Risk {risk_dots(f.get("risk_class"))}</span></div>'
        f'<div class="fc-foot">{"".join(f"<span>{x}</span>" for x in foot)}</div></a>')


def _compare_section(funds):
    rows = []
    for i, h in enumerate(funds):
        if not h.get("data_available"):
            continue
        p, rk, f = h["returns"]["periods"], h["returns"]["risk"], h.get("facts") or {}
        rows.append([
            ui.cell(_name_cell(h, i), sort=h["name"]),
            *[ui.cell(chip(p.get(k), cap=15), sort=p.get(k)) for k in ("YTD", "1Y", "3Y", "5Y")],
            ui.cell(f'{rk["volatility_1y"]:.1f}%' if rk.get("volatility_1y") is not None else "—",
                    sort=rk.get("volatility_1y")),
            ui.cell(chip(rk.get("max_drawdown_3y"), cap=25) if rk.get("max_drawdown_3y") is not None else "—",
                    sort=rk.get("max_drawdown_3y")),
            ui.cell(f'{f["ongoing_charge_pct"]:.2f}%' if f.get("ongoing_charge_pct") else "—",
                    sort=f.get("ongoing_charge_pct")),
            ui.cell(risk_dots(f.get("risk_class")), sort=f.get("risk_class")),
        ])
    cols = [ui.Col("Fund", sort="text"),
            ui.Col("This year", sort="number"), ui.Col("1 year", sort="number", term="fund-total-return"),
            ui.Col("3 years, a year", sort="number", term="annualised-return"),
            ui.Col("5 years, a year", sort="number", term="annualised-return"),
            ui.Col("Volatility", sort="number", term="volatility"),
            ui.Col("Biggest fall, 3 years", sort="number", term="max-drawdown"),
            ui.Col("Yearly cost", sort="number", term="ongoing-charge"),
            ui.Col("Risk class", sort="number", term="risk-class")]
    years = sorted({y for h in funds if h.get("data_available") for y in h["returns"]["calendar"]})[-7:]
    cal_rows = []
    for i, h in enumerate(funds):
        if not h.get("data_available"):
            continue
        c = h["returns"]["calendar"]
        cal_rows.append([ui.cell(_name_cell(h, i), sort=h["name"])]
                        + [ui.cell(chip(c.get(y), cap=20) if y in c else "—", sort=c.get(y)) for y in years])
    cal_cols = [ui.Col("Fund", sort="text")] + [ui.Col(str(y) + (" so far" if y == dt.date.today().year else ""),
                                                       sort="number") for y in years]
    return ui.section(_join([
        ui.table(cols, rows, table_id="t-funds-compare"),
        Markup('<h3 class="sub-h">Every calendar year</h3>'),
        ui.table(cal_cols, cal_rows, table_id="t-funds-years"),
        Markup('<p class="footnote">Total returns in US dollars, payouts reinvested, after the fund\'s yearly costs '
               '(not your bank\'s sales charge). Past returns don\'t promise future ones. "A year" = the average yearly '
               'growth over that period.</p>'),
    ]), title="How your funds compare", sec_id="funds-compare", icon="percent", term="fund-total-return")


def _plan_section(funds, today):
    plan_rows = []
    for i, h in enumerate(funds):
        if not h.get("monthly_amount"):
            continue
        d = dt.date.fromisoformat(h["next_topup"]) if h.get("next_topup") else None
        for _k in range(3):
            if not d:
                break
            plan_rows.append([ui.cell(_when(d), sort=d.isoformat()), ui.cell(_name_cell(h, i), sort=h["name"]),
                              ui.cell(usd(h["monthly_amount"]), cls="private", sort=h["monthly_amount"]),
                              ui.cell(f'{h["fee_pct"]:.2f}% = {usd(h["monthly_amount"] * h["fee_pct"] / 100, 2)}',
                                      sort=h["fee_pct"]),
                              ui.cell(f'≈ {h["next_topup_units_est"]:,.3f}' if h.get("next_topup_units_est") else "—",
                                      cls="private")])
            d = fp.next_topup(h["monthly_day"], d)
    events = []
    for i, h in enumerate(funds):
        f = h.get("facts") or {}
        ex = f.get("next_ex_date") or h.get("next_payout_est")
        if ex and h.get("payouts") in ("reinvest", "cash") and h.get("last_payout") and not h.get("sold_out"):
            est = "" if f.get("next_ex_date") else " (estimated date)"
            amt = h["last_payout"]["amount"] * (h.get("units") or 0)
            events.append([ui.cell(_when(ex), sort=ex), ui.cell(_name_cell(h, i), sort=h["name"]),
                           ui.cell(("Reinvested" if h["payouts"] == "reinvest" else "Paid to you") + est),
                           ui.cell(f'≈ {usd(amt, 2)}', cls="private", sort=amt)])
    txn_rows = []
    names = {h["isin"]: (h, i) for i, h in enumerate(funds)}
    for h in funds:
        for x in h.get("transactions") or []:
            hh, i = names[h["isin"]]
            st = x.get("status") or ""
            txn_rows.append([ui.cell(_when(x["date"]), sort=x["date"]), ui.cell(_name_cell(hh, i), sort=hh["name"]),
                             ui.cell(TYPE_WORDS.get(x["type"], x["type"])),
                             ui.cell(usd(x.get("amount"), 2) if x.get("amount") is not None else "—", cls="private",
                                     sort=x.get("amount")),
                             ui.cell(f'{x["units"]:,.3f}' if x.get("units") is not None else "—", cls="private",
                                     sort=x.get("units")),
                             ui.cell(nav_text(x.get("price")) if x.get("price") else "—", sort=x.get("price")),
                             ui.cell(ui.pill(st, {"confirmed": "up", "estimated": "info", "pending": "warn"}.get(st,
                                                                                                         "neutral"))),
                             ui.cell(x.get("note") or "", cls="small muted")])
    body = []
    if plan_rows:
        body.append(Markup('<h3 class="sub-h">Next top-ups</h3>'))
        body.append(ui.table([ui.Col("Date", sort="text"), ui.Col("Fund", sort="text"), ui.Col("Amount", sort="number"),
                              ui.Col("Sales charge", sort="number", term="sales-charge"),
                              ui.Col("Units it buys at today's price", sort="number", term="estimated-units")],
                             sorted(plan_rows, key=lambda r: r[0]["sort"]), table_id="t-funds-plan"))
    if events:
        body.append(Markup('<h3 class="sub-h">Next payouts</h3>'))
        body.append(ui.table([ui.Col("Ex-date", sort="text"), ui.Col("Fund", sort="text"), ui.Col("What happens"),
                              ui.Col("About", sort="number")], events, table_id="t-funds-payouts-next"))
    body.append(Markup('<h3 class="sub-h">Every transaction since your statements</h3>'))
    body.append(ui.table([ui.Col("Date", sort="text"), ui.Col("Fund", sort="text"), ui.Col("Type", sort="text"),
                          ui.Col("Cash", sort="number"), ui.Col("Units", sort="number"),
                          ui.Col("Price used", sort="number", term="nav"), ui.Col("Status", term="estimated-units"),
                          ui.Col("Note")], sorted(txn_rows, key=lambda r: r[0]["sort"], reverse=True),
                         table_id="t-funds-txns", show_first=12,
                         empty="Nothing yet — top-ups and payouts after your statement date appear here automatically"))
    return ui.section(_join(body), title="Top-ups, payouts and transactions", sec_id="funds-plan", icon="calendar",
                      term="top-up",
                      sub="Added automatically, once each. Estimated units → type your statement's units in and mark "
                          "the row confirmed.")


def _explainer():
    items = [
        ("What a fund is", "Your money is pooled with other investors' and spread over hundreds of investments by "
                           "professional managers. You own units; a unit's price (the NAV) is published once each "
                           "working day."),
        ("How your value is worked out", "Units × the latest price per unit. Top-ups add units; reinvested payouts add "
                                         "units too; cash payouts land in your bank account instead."),
        ("Why a paying fund's price can drift down", "On each payout date the price drops by the payout — the money "
                                                     "leaves the fund and goes to you. That's why returns here always "
                                                     "add the payouts back (total return)."),
        ("What 'gain / loss' means", "Today's value minus everything you paid in, sales charges included. It's on "
                                     "paper until you sell."),
        ("Reading the colours", "Green = up, red = down; the stronger the colour, the bigger the move."),
    ]
    lis = "".join(f"<li><b>{escape(k)}.</b> {escape(v)}</li>" for k, v in items)
    return ui.details("📘 How to read your funds", Markup(f'<ul class="plain-list">{lis}</ul>'), det_id="funds-explain")


# ------------------------------------------------------------------ one fund's page
def _plain_summary(h, peers_rank):
    """What the fund is and how it's doing, in plain words, from its figures."""
    f, p, rk = h.get("facts") or {}, h["returns"]["periods"], h["returns"]["risk"]
    out = []
    what = f.get("category")
    out.append(Markup(f"<b>What it is.</b> A {escape(what) + ' ' if what else ''}fund"
                      f"{' from ' + escape(h.get('source')) if h.get('source') and h.get('source') != 'FT' else ''}. "
                      + (f"In its own words: <i>{escape(f['objective'])}</i>" if f.get("objective") else "")))
    mix = f.get("asset_mix_official") or f.get("asset_mix") or []
    if mix:
        txt = ", ".join(f"{v:.0f}% {k.lower()}" for k, v in mix[:4])
        out.append(Markup(f"<b>What it holds.</b> Roughly {escape(txt)}."
                          + (" It uses futures, so parts can add up to more than 100%." if any(v < 0 for _k, v in mix)
                             else "")))
    if h.get("payouts") == "reinvest" or (h.get("payouts") == "cash"):
        lp = h.get("last_payout") or {}
        way = "your bank reinvests it into more units" if h["payouts"] == "reinvest" else "it's paid into your account"
        out.append(Markup(f"<b>How it pays you.</b> About ${lp.get('amount', 0):.4f} per unit "
                          f"{'every month' if (h.get('payouts_per_year') or 0) >= 10 else 'per payout'}"
                          + (f" — a payout yield of {h['distribution_yield']:.1f}% a year" if h.get("distribution_yield")
                             else "") + f"; {way}."))
    elif h.get("payouts") == "none" or f.get("income_treatment") == "Accumulation":
        out.append(Markup("<b>How it pays you.</b> It doesn't pay out: its income stays inside and grows the price per "
                          "unit (an accumulating fund), so its price change is its whole return."))
    perf = [f"{v:+.1f}% over the last year" if (v := p.get("1Y")) is not None else None,
            f"{v:+.1f}% a year over 3 years" if (v := p.get("3Y")) is not None else None,
            f"{v:+.1f}% a year over 5 years" if (v := p.get("5Y")) is not None else None]
    perf = [x for x in perf if x]
    cal = h["returns"]["calendar"]
    worst = min(cal.items(), key=lambda x: x[1]) if cal else None
    if perf:
        out.append(Markup(f"<b>How it has done.</b> {escape(', '.join(perf))}, payouts included."
                          + (f" Its worst calendar year was {worst[0]} ({worst[1]:+.1f}%)." if worst and worst[1] < 0
                             else "")))
    vol = rk.get("volatility_1y")
    if vol is not None:
        out.append(Markup(f"<b>How bumpy.</b> Its price swings about {vol:.0f}% a year — {_bump_word(vol)}."
                          + (f" The biggest fall in the last 3 years was {rk['max_drawdown_3y']:.1f}%."
                             if rk.get("max_drawdown_3y") is not None else "")))
    costs = []
    if f.get("ongoing_charge_pct"):
        costs.append(f"{f['ongoing_charge_pct']:.2f}% a year to run (already taken out of the price)")
    if h.get("fee_pct"):
        costs.append(f"your bank's {h['fee_pct']:.1f}% sales charge on each purchase")
    if costs:
        out.append(Markup(f"<b>What it costs.</b> {escape(' + '.join(costs))}."))
    if peers_rank:
        r, n, per = peers_rank
        out.append(Markup(f"<b>Against its competitors.</b> {ordinal(r)} best of {n} over {escape(per)} "
                          "(see Competitors)."))
    return Markup('<ul class="plain-list">' + "".join(f"<li>{x}</li>" for x in out) + "</ul>")


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _facts_list(h):
    f = h.get("facts") or {}
    xc = h.get("cross_check") or {}
    check = h.get("check") or {}
    rows = [
        ("ISIN", h["isin"]), ("Category", f.get("category")), ("Launched", f.get("launch_date")),
        ("Domicile", f.get("domicile")), ("Fund type", f.get("fund_type")),
        ("Income", PAYOUT_WORDS.get(h.get("payouts")) or f.get("income_treatment")),
        ("Priced", f.get("pricing_frequency")),
        ("Fund size", f"${f['fund_size'] / 1e6:,.0f} million" if f.get("fund_size") else None),
        ("Yearly running cost (OCF)", f"{f['ongoing_charge_pct']:.2f}% (FT)" if f.get("ongoing_charge_pct") else None),
        ("Management fee", f"{f['management_fee_pct']:.2f}%" if f.get("management_fee_pct") else
         (f"{f['max_annual_charge_pct']:.2f}% max" if f.get("max_annual_charge_pct") else None)),
        ("Max entry charge (the fund's)", f"{f.get('max_initial_charge_pct') or f.get('initial_charge_pct')}%"
         if (f.get("max_initial_charge_pct") or f.get("initial_charge_pct")) else None),
        ("Your bank's sales charge", f"{h['fee_pct']:.2f}%" if h.get("fee_pct") else None),
        ("Official risk class", f"{f['risk_class']} of 7" if f.get("risk_class") else None),
        ("Managers", f.get("managers")),
        ("Prices from", (h.get("source") or "") + (f" · checked against {xc.get('other')}: "
                                                   f"{xc['compared']:,} days, {xc['mismatches']} different"
                                                   if xc else "")),
        ("Your statement's price", (f"{nav_text(check.get('statement_nav'))} on {_when(check.get('date'))} — "
                                    + ("matches ✓" if check.get("ok") else "not the same ✗" if check.get("ok") is False
                                       else "no price that day to compare")) if check else None),
    ]
    items = "".join(f'<div><dt>{escape(k)}</dt><dd>{escape(v)}</dd></div>' for k, v in rows if v)
    links = []
    if h.get("source_url"):
        links.append(f'<a href="{ui.href(h["source_url"])}" target="_blank" rel="noopener">Fund manager\'s page ↗</a>')
    if f.get("ft_url"):
        links.append(f'<a href="{ui.href(f["ft_url"])}" target="_blank" rel="noopener">FT fund page ↗</a>')
    return Markup(f'<dl class="fund-facts">{items}</dl>'
                  + (f'<p class="small">{" · ".join(links)}</p>' if links else ""))


def _insights(h, peers):
    """Plain facts worth knowing — not advice."""
    f, p, rk = h.get("facts") or {}, h["returns"]["periods"], h["returns"]["risk"]
    out = []
    y, r1 = h.get("distribution_yield"), p.get("1Y")
    if y and r1 is not None and y > r1:
        out.append(f"The payout yield ({y:.1f}%) is higher than the last year's total return ({r1:+.1f}%) — part of "
                   "the payouts came from the fund's own value, so the price per unit fell.")
    oc = [x["ocf"] for x in peers if x.get("ocf")]
    if f.get("ongoing_charge_pct") and oc:
        avg = sum(oc) / len(oc)
        word = "more than" if f["ongoing_charge_pct"] > avg + 0.05 else "less than" \
            if f["ongoing_charge_pct"] < avg - 0.05 else "about the same as"
        out.append(f"Its yearly cost ({f['ongoing_charge_pct']:.2f}%) is {word} its competitors' average "
                   f"({avg:.2f}%).")
    top = f.get("top_holdings") or []
    if top:
        share = sum(x["weight"] or 0 for x in top[:10])
        out.append(f"Its 10 biggest holdings are {share:.0f}% of the fund.")
    regions = f.get("regions") or []
    if regions:
        big = max(regions, key=lambda x: x["weight"] or 0)
        if (big["weight"] or 0) >= 50:
            out.append(f"Most of it ({big['weight']:.0f}%) is invested in one region: {big['name']}.")
    navs = h.get("navs") or []
    year = [v for d, v in navs if d > navs[-1][0] - dt.timedelta(days=365)] if navs else []
    if year:
        hi, lo = max(year), min(year)
        out.append(f"Price per unit is {(h['nav'] / hi - 1) * 100:+.1f}% from its 1-year high "
                   f"(${nav_text(hi)}) and {(h['nav'] / lo - 1) * 100:+.1f}% from its low (${nav_text(lo)}).")
    if rk.get("best_month") and rk.get("worst_month"):
        (by, bm), bv = rk["best_month"]
        (wy, wm), wv = rk["worst_month"]
        out.append(f"Last 3 years: best month {dt.date(by, bm, 1):%b %Y} ({bv:+.1f}%), worst {dt.date(wy, wm, 1):%b %Y} "
                   f"({wv:+.1f}%); up in {rk.get('months_up_pct', 0):.0f}% of months.")
    if not out:
        return None
    return Markup('<ul class="plain-list">' + "".join(f"<li>{escape(x)}</li>" for x in out) + "</ul>")


def _peer_rows(h, market):
    """Your fund + its competitors with the same figures, best 3-year first."""
    rows = []
    f = h.get("facts") or {}
    rows.append({"name": h["name"], "you": True, "category": f.get("category"), "returns": h["returns"],
                 "ocf": f.get("ongoing_charge_pct"), "index": h["total_return_index"], "href": None,
                 "income": PAYOUT_WORDS.get(h.get("payouts"), "")})
    for isin in h.get("peers") or []:
        m = market.get(isin) or {}
        navs = m.get("navs") or []
        if len(navs) < 30:
            continue
        idx = fp.total_return_index(navs, m.get("distributions") or [])
        pf = m.get("facts") or {}
        rows.append({"name": m.get("name") or pf.get("name") or isin, "you": False, "category": pf.get("category"),
                     "returns": fp.returns_from_index(idx), "ocf": pf.get("ongoing_charge_pct"), "index": idx,
                     "href": pf.get("ft_url"), "income": pf.get("income_treatment") or "", "isin": isin})
    return rows


def _rank(rows, key):
    vals = [(r["returns"]["periods"].get(key), r["you"]) for r in rows]
    have = sorted([v for v in vals if v[0] is not None], key=lambda x: -x[0])
    for i, (_v, you) in enumerate(have):
        if you:
            return i + 1, len(have)
    return None


def _competitors_tab(h, rows):
    if len(rows) < 2:
        return ui.empty_state("No competitors listed", "Add competitor ISINs to this fund's `peers` column in "
                              "portfolio/funds.csv (separated by ;) and they're compared here.", icon="layers")
    body = []
    trs, attrs = [], []
    for i, r in enumerate(sorted(rows, key=lambda x: -(x["returns"]["periods"].get("3Y") or -99))):
        p, rk = r["returns"]["periods"], r["returns"]["risk"]
        name = escape(r["name"])
        if r.get("href"):
            name = Markup(f'<a href="{ui.href(r["href"])}" target="_blank" rel="noopener">{name}</a>')
        label = Markup(f'<span class="sym"><span>{name}{" (yours)" if r["you"] else ""}'
                       f'<small>{escape(r.get("category") or "")}</small></span></span>')
        trs.append([ui.cell(label, sort=r["name"]),
                    *[ui.cell(chip(p.get(k), cap=15), sort=p.get(k)) for k in ("YTD", "1Y", "3Y", "5Y")],
                    ui.cell(f'{rk["volatility_1y"]:.1f}%' if rk.get("volatility_1y") is not None else "—",
                            sort=rk.get("volatility_1y")),
                    ui.cell(chip(rk.get("max_drawdown_3y"), cap=25) if rk.get("max_drawdown_3y") is not None else "—",
                            sort=rk.get("max_drawdown_3y")),
                    ui.cell(f'{r["ocf"]:.2f}%' if r.get("ocf") else "—", sort=r.get("ocf"))])
        attrs.append('class="row-you"' if r["you"] else "")
    cols = [ui.Col("Fund", sort="text"), ui.Col("This year", sort="number"),
            ui.Col("1 year", sort="number", term="fund-total-return"),
            ui.Col("3 years, a year", sort="number", term="annualised-return"),
            ui.Col("5 years, a year", sort="number", term="annualised-return"),
            ui.Col("Volatility", sort="number", term="volatility"),
            ui.Col("Biggest fall, 3 years", sort="number", term="max-drawdown"),
            ui.Col("Yearly cost", sort="number", term="ongoing-charge")]
    body.append(ui.table(cols, trs, table_id="t-fund-peers", row_attrs=attrs))
    ranks = [(k, _rank(rows, k)) for k in ("1Y", "3Y", "5Y")]
    words = [f"{ordinal(r[0])} of {r[1]} over {lab}" for (k, r), lab in zip(ranks, ("1 year", "3 years", "5 years")) if r]
    if words:
        body.append(Markup(f'<p class="section-sub"><b>Where yours ranks:</b> {escape(" · ".join(words))}.</p>'))
    growth = _growth_lines("c-fund-peers", [("you" if r["you"] else f"f{i}", r["name"], r["index"])
                                           for i, r in enumerate(rows)], title="Growth of $10,000", years=3)
    if growth:
        body.append(ui.card(growth, title="Growth of $10,000 over 3 years — yours vs competitors", icon="activity"))
    body.append(Markup('<p class="footnote">Competitors are other funds in a similar category, compared on their '
                       'accumulating share classes so that payouts count for everyone (yours: total return with payouts '
                       'reinvested). Their figures come from FT\'s published prices; returns are worked out here in US '
                       'dollars. The list is yours to change in funds.csv.</p>'))
    return _join(body)


def _performance_tab(h):
    navs, idx = h["navs"], h["total_return_index"]
    r = h["returns"]
    p, rk = r["periods"], r["risk"]
    shown = weekly(navs, daily_days=400)          # daily for the last year+, weekly before
    dates = [d for d, _v in shown]
    end = dates[-1]
    tf = []
    for k, days in (("1M", 31), ("3M", 92), ("6M", 183), ("1Y", 366), ("3Y", 1096), ("5Y", 1827)):
        c = sum(1 for d in dates if d > end - dt.timedelta(days=days))
        if c < len(dates) - 5:
            tf.append((k, c))
    tf.append(("All", None))
    price = sc.chart("c-fund-price", dates, [sc.Series("price", "Price per unit", [v for _d, v in shown], fmt=USD2)]
                     + ([sc.Series("cost", "Your average cost", [h["avg_cost"]] * len(shown), fmt=USD2, tooltip=False)]
                        if h.get("avg_cost") else []),
                     title="Price per unit", height=280, timeframes=tf, show_title=False, time_axis=True)
    tiles = [ui.kpi(lab, f"{p[k]:+.2f}%" if p.get(k) is not None else "—", tone=tone(p.get(k)),
                    term="annualised-return" if k in ("3Y", "5Y", "10Y") else "fund-total-return")
             for k, lab in [("1D", "Latest day"), ("1W", "1 week")] + PERIOD_LABELS if k in p]
    if p.get("since_start") is not None:
        tiles.append(ui.kpi("Since launch, a year" if (r["last_date"] - r["first_date"]).days > 365 else "Since launch",
                            f"{p['since_start']:+.2f}%", tone=tone(p["since_start"]),
                            sub=f"from {_when(r['first_date'])}"))
    cal = r["calendar"]
    bars = sc.hbars([{"label": str(y) + (" so far" if y == r["last_date"].year else ""), "value": v}
                     for y, v in sorted(cal.items(), reverse=True)], fmt=sc.Fmt(suffix="%", decimals=1, sign=True),
                    diverging=True)
    risk = [ui.kpi("Volatility, 1 year", f'{rk["volatility_1y"]:.1f}%' if rk.get("volatility_1y") is not None else "—",
                   term="volatility", sub=_bump_word(rk.get("volatility_1y"))),
            ui.kpi("Biggest fall, 1 year", f'{rk["max_drawdown_1y"]:.1f}%' if rk.get("max_drawdown_1y") is not None
                   else "—", term="max-drawdown"),
            ui.kpi("Biggest fall, 3 years", f'{rk["max_drawdown_3y"]:.1f}%' if rk.get("max_drawdown_3y") is not None
                   else "—", term="max-drawdown"),
            ui.kpi("Months up", f'{rk["months_up_pct"]:.0f}%' if rk.get("months_up_pct") is not None else "—",
                   sub="of the last 36")]
    parts = [ui.kpi_row(tiles, cls="kpis-5"),
             Markup('<p class="footnote">Total returns in US dollars with payouts reinvested on their ex-dates — '
                    'checked against the fund managers\' own published figures. Periods over a year are a year\'s '
                    'worth on average.</p>'),
             ui.card(price, title="Price per unit", icon="trend-up", term="nav", cls="chart-card")]
    if h.get("payouts") in ("reinvest", "cash") or h.get("distributions"):
        growth = _growth_lines("c-fund-tr", [("you", "With payouts reinvested", idx),
                                             ("f2", "Price only", navs)], title="Growth of $10,000", years=5)
        if growth:
            parts.append(ui.card(growth, title="Growth of $10,000 over 5 years — with and without payouts",
                                 icon="activity", term="fund-total-return",
                                 sub="A paying fund's price drifts down as it pays out; the payouts make up the difference."))
    parts.append(Markup('<div class="grid-2">' + str(ui.card(bars, title="Every calendar year", term="calendar-return",
                                                             icon="calendar"))
                        + str(ui.card(ui.kpi_row(risk, cls="kpis-2"), title="How bumpy", icon="activity",
                                      term="volatility")) + "</div>"))
    return _join(parts)


def _holdings_tab(h):
    f = h.get("facts") or {}
    parts = []
    mix = f.get("asset_mix_official") or f.get("asset_mix") or []
    pos = [(k, v) for k, v in mix if v > 0]
    if pos:
        donut = sc.donut("c-fund-mix", [(k, v, COLORS[i % len(COLORS)]) for i, (k, v) in enumerate(pos)],
                         title="Asset mix", fmt=sc.Fmt(suffix="%", decimals=1), center_value=f"{len(pos)}",
                         center_label="kinds")
        neg = [f"{k} {v:.1f}%" for k, v in mix if v < 0]
        note = (Markup(f'<p class="footnote">Also: {escape(", ".join(neg))} — the fund uses futures, so the parts add up '
                       'to more than 100% and cash shows below zero.</p>') if neg else "")
        src = "fund manager" if f.get("asset_mix_official") else f"FT, as of {_when(f.get('holdings_as_of'))}"
        parts.append(ui.card(_join([donut, note]), title="What it holds", sub=f"Source: {src}.", icon="pie",
                             term="asset-mix"))
    top = f.get("top_holdings") or []
    if top:
        parts.append(ui.card(sc.hbars([{"label": x["name"], "value": x["weight"],
                                        "sub": (f'{x["change_1y"]:+.1f}% over a year' if x.get("change_1y") is not None
                                                else None)} for x in top[:10]],
                                      fmt=sc.Fmt(suffix="%", decimals=2), tone_from_sign=False),
                             title="Top 10 holdings", sub=f"% of the fund · FT, as of {_when(f.get('holdings_as_of'))}",
                             icon="bar"))
    secs = f.get("sectors") or []
    if secs:
        parts.append(ui.card(sc.hbars([{"label": n, "value": v, "sub": f"category average {c:.1f}%" if c is not None else None}
                                       for n, v, c in secs[:10]], fmt=sc.Fmt(suffix="%", decimals=1),
                                      tone_from_sign=False),
                             title="Sectors (shares part)", sub="vs the category average · long positions", icon="layers"))
    regs = f.get("regions") or []
    if regs:
        parts.append(ui.card(sc.hbars([{"label": r["name"], "value": r["weight"],
                                        "sub": ", ".join(f'{x["name"]} {x["weight"]:.1f}%' for x in r["parts"][:4])
                                        or None} for r in regs], fmt=sc.Fmt(suffix="%", decimals=1),
                                      tone_from_sign=False),
                             title="Where in the world", sub="FT · long positions", icon="globe"))
    if not parts:
        return ui.empty_state("No holdings data", "The holdings source didn't answer this run.", icon="layers")
    return Markup('<div class="grid-2">' + "".join(str(x) for x in parts) + "</div>")


def _income_tab(h):
    dists = sorted(h.get("distributions") or [], key=lambda d: d["ex_date"])
    if not dists:
        return ui.empty_state("This fund doesn't pay out", "It's an accumulating fund: income stays inside and grows the "
                              "price per unit.", icon="coins")
    recent = dists[-36:]
    chart = sc.chart("c-fund-payouts", [d["ex_date"] for d in recent],
                     [sc.Series("payout", "Payout per unit", [d["amount"] for d in recent], kind="bars",
                                fmt=sc.Fmt("$", 4))], title="Payout per unit", height=170, bars_mode=True,
                     legend=False, show_title=False)
    rows = []
    for d in reversed(dists[-24:]):
        nav = (fp.nav_on_or_before(h["navs"], d["ex_date"]) or (None, None))[1]
        rows.append([ui.cell(_when(d["ex_date"]), sort=d["ex_date"].isoformat()),
                     ui.cell(_when(d["pay_date"]) if d.get("pay_date") else "—"),
                     ui.cell(f'${d["amount"]:.4f}' + (" (provisional)" if d.get("provisional") else ""),
                             sort=d["amount"]),
                     ui.cell(f'{d["amount"] * (h.get("payouts_per_year") or 12) / nav * 100:.2f}%' if nav else "—")])
    y = h.get("distribution_yield")
    kpis = [ui.kpi("Payout yield", f"{y:.2f}%" if y else "—", term="distribution-yield", sub="last 12 months ÷ price"),
            ui.kpi("Per unit, last 12 months", f'${h["dist_per_unit_12m"]:.4f}' if h.get("dist_per_unit_12m") else "—",
                   sub=f'{h.get("payouts_per_year") or 0} payouts'),
            ui.kpi("Your yearly income", f'{usd(h.get("est_annual_income"))}' if h.get("est_annual_income") else "—",
                   private=True, sub="today's units × last 12 months' payouts"),
            ui.kpi("Received since your statement", usd(h.get("payouts_received"), 2) if h.get("payouts") == "cash"
                   else usd(h.get("reinvested"), 2), private=True,
                   sub="paid into your account" if h.get("payouts") == "cash" else "reinvested into units")]
    return _join([ui.kpi_row(kpis), ui.card(chart, title="Payouts per unit, last 3 years", icon="coins",
                                            term="fund-payout"),
                  ui.table([ui.Col("Ex-date", sort="text"), ui.Col("Paid"), ui.Col("Per unit", sort="number"),
                            ui.Col("Yearly rate then", term="distribution-yield")], rows, table_id="t-fund-payouts",
                           show_first=12),
                  Markup('<p class="footnote">"Provisional": the payout date has passed but the fund manager hasn\'t '
                         'published the amount yet — the last amount is used until it does, then corrected '
                         'automatically.</p>')])


def _your_money_tab(h, today):
    tiles = [
        ui.kpi("Units", f'{h["units"]:,.3f}', private=True, term="units",
               sub=f'statement: {h["statement_units"]:,.3f} on {_when(h["as_of"])}'),
        ui.kpi("Average unit cost", usd(h.get("avg_cost"), 4), private=True, term="avg-unit-cost"),
        ui.kpi("Money you put in", usd(h["invested"], 2), private=True, term="cost-basis"),
        ui.kpi("Value today", usd(h["market_value"], 2), private=True,
               sub=f'≈ KES {h["market_value_kes"]:,.0f}' if h.get("market_value_kes") else None),
        ui.kpi("Gain / loss", usd(h["gain"], 2, sign=True), tone=tone(h["gain"]), private=True, term="gain-loss",
               delta_html=ui.delta(h["gain_pct"], fmt="{:+.2f}%") if h.get("gain_pct") is not None else None),
        ui.kpi("Your yearly return", f'{h["your_return_annual"]:+.1f}%' if h.get("your_return_annual") is not None
               else "—", term="your-annual-return",
               sub=None if h.get("your_return_annual") is not None
               else f'after 3 months of tracking ({h.get("tracked_days", 0)} days so far)'),
    ]
    pl = h.get("pl_periods") or {}
    strip = [ui.kpi(lab, usd(pl.get(k), 2, sign=True) if pl.get(k) is not None else "—", tone=tone(pl.get(k)),
                    private=True) for k, lab in (("1W", "1 week"), ("1M", "1 month"), ("3M", "3 months"),
                                                ("YTD", "This year"), ("1Y", "1 year"))]
    charts = Markup('<div class="grid-3">'
                    + str(ui.card(_pl_bars("c-fund-daily", h.get("daily_pl") or [], title="Daily", when="days"),
                                  title="Day by day", sub="last 30 price days"))
                    + str(ui.card(_pl_bars("c-fund-weekly", h.get("weekly_pl") or [], title="Weekly", when="weeks"),
                                  title="Week by week", sub="last 13 weeks"))
                    + str(ui.card(_pl_bars("c-fund-monthly", h.get("monthly_pl") or [], title="Monthly", when="months"),
                                  title="Month by month", sub="last 12 months")) + "</div>")
    value = _value_chart("c-fund-value", h.get("value_series"), title="Value since your statement")
    plan = []
    if h.get("monthly_amount") and h.get("next_topup"):        # none after you sold the whole fund
        d = dt.date.fromisoformat(h["next_topup"])
        for _k in range(6):
            plan.append([ui.cell(_when(d), sort=d.isoformat()), ui.cell(usd(h["monthly_amount"]), cls="private"),
                         ui.cell(usd(h["monthly_amount"] * h["fee_pct"] / 100, 2)),
                         ui.cell(f'≈ {h["next_topup_units_est"]:,.3f}', cls="private")])
            d = fp.next_topup(h["monthly_day"], d)
    txns = [[ui.cell(_when(x["date"]), sort=x["date"]), ui.cell(TYPE_WORDS.get(x["type"], x["type"])),
             ui.cell(usd(x.get("amount"), 2) if x.get("amount") is not None else "—", cls="private"),
             ui.cell(f'{x["units"]:,.3f}' if x.get("units") is not None else "—", cls="private"),
             ui.cell(nav_text(x.get("price")) if x.get("price") else "—"),
             ui.cell(ui.pill(x.get("status") or "", {"confirmed": "up", "estimated": "info",
                                                      "pending": "warn"}.get(x.get("status"), "neutral"))),
             ui.cell(x.get("note") or "", cls="small muted")]
            for x in sorted(h.get("transactions") or [], key=lambda x: x["date"], reverse=True)]
    parts = [ui.kpi_row(tiles, cls="kpis-position"), ui.section(_join([ui.kpi_row(strip, cls="kpis-5"), charts]),
                                            title="What it made you", sec_id="fund-profit", icon="trend-up",
                                            term="profit-periods"),
             ui.card(value or Markup('<p class="muted small">This line starts on your statement date and grows with '
                                     'every run.</p>'), title="Value since your statement", icon="trend-up")]
    if plan:
        parts.append(ui.section(ui.table([ui.Col("Date", sort="text"), ui.Col("Amount"),
                                          ui.Col("Sales charge", term="sales-charge"),
                                          ui.Col("Units at today's price", term="estimated-units")], plan,
                                         table_id="t-fund-plan"),
                                title=f'Monthly top-up: {usd(h["monthly_amount"])} on day {h["monthly_day"]}',
                                sec_id="fund-plan", icon="calendar", term="top-up",
                                sub="Recorded automatically on the day (or the next run after it), once."))
    parts.append(ui.section(ui.table([ui.Col("Date", sort="text"), ui.Col("Type"), ui.Col("Cash"), ui.Col("Units"),
                                      ui.Col("Price used", term="nav"), ui.Col("Status", term="estimated-units"),
                                      ui.Col("Note")], txns, table_id="t-fund-txns",
                                     empty="Nothing since your statement yet"),
                            title="Transactions since your statement", sec_id="fund-txns", icon="news"))
    return _join(parts)


def _news_tab(h):
    news = h.get("news") or []
    if not news:
        return ui.empty_state("No recent headlines", "Nothing naming this fund in the last 6 months — try again next "
                              "run.", icon="news")
    from page_portfolio import _news_list
    return _join([Markup('<p class="section-sub">Recent headlines naming this fund or its manager, newest first — '
                         'shown neutral on purpose; read and judge for yourself.</p>'),
                  _news_list([dict(n, symbol="") for n in news])])


def _glossary_tab():
    import glossary
    keys = ["mutual-fund", "nav", "units", "avg-unit-cost", "fund-payout", "reinvest", "accumulating", "sales-charge",
            "ongoing-charge", "fund-total-return", "annualised-return", "calendar-return", "volatility", "max-drawdown",
            "risk-class", "peer-funds", "fund-category", "multi-asset", "asset-mix", "top-up", "estimated-units",
            "your-annual-return", "distribution-yield", "price-cross-check", "profit-periods"]
    rows = [[ui.cell(Markup(f"<strong>{escape(glossary.title(k))}</strong>")), ui.cell(glossary.text(k))]
            for k in keys if glossary.has(k)]
    return ui.table([ui.Col("Term", sort=None), ui.Col("What it means", sort=None)], rows, sortable=False,
                    cls="wrap glossary")


def build_fund_page(rg, h, market, *, generated_at="", today=None):
    """(title, subtitle, body) of one fund's private page."""
    today = today or dt.date.today()
    f = h.get("facts") or {}
    if not h.get("data_available"):
        body = ui.empty_state("No price for this fund today", "It's left out of your totals rather than guessed. "
                              "Check the ISIN in portfolio/funds.csv and run again.", icon="alert")
        return h["name"], f"{h['isin']} · page generated {generated_at}", body
    rows = _peer_rows(h, market)
    rank = _rank(rows, "3Y")
    navs = h["navs"]
    year = [v for d, v in navs if d > navs[-1][0] - dt.timedelta(days=365)]
    marks = [(h["avg_cost"], "Your average cost", "cost", False)] if h.get("avg_cost") and year and \
        min(year) <= h["avg_cost"] <= max(year) else []
    rng = (Markup('<div class="hero-range"><div class="hero-range-head"><span>1-year range'
                  f'{ui.info("range-52w")}</span></div>'
                  f'{ui.range_bar(min(year), max(year), h["nav"], marks=marks, fmt=lambda v: "$" + nav_text(v), label="1-year range")}'
                  '</div>') if len(year) > 1 else "")
    p = h["returns"]["periods"]
    pills = (f'{ui.pill(PAYOUT_WORDS.get(h.get("payouts"), ""), "info") if h.get("payouts") else ""}'
             f'{ui.pill(f.get("category"), "neutral") if f.get("category") else ""}')
    hero = Markup(
        '<section class="card hero" aria-label="At a glance"><div class="hero-main">'
        f'<div class="hero-id"><div class="hero-name"><h2>{escape(h["name"])}</h2>'
        f'<p>{escape(h["isin"])}{" · " + escape(h.get("source")) if h.get("source") else ""}</p></div></div>'
        f'<div class="hero-price" data-kpi="Price per unit"><span class="kpi-value num">${escape(nav_text(h["nav"]))}</span>'
        f'{ui.delta(h.get("nav_change_pct")) if h.get("nav_change_pct") is not None else ""}'
        f'<small class="num">per unit · {escape(_when(h["nav_date"]))}</small></div>'
        f'<p class="hero-meta">1 year {escape(pct(p.get("1Y"), 2))} · 3 years {escape(pct(p.get("3Y"), 2))} a year · '
        f'payouts included</p></div>'
        f'<div class="hero-side"><div class="hero-pills">{pills}</div>'
        f'<p><b>Official risk class</b>{ui.info("risk-class")}<br>{risk_dots(f.get("risk_class"))}</p>{rng}</div></section>')
    position = ui.section(ui.kpi_row([
        ui.kpi("Value", usd(h["market_value"], 2), private=True,
               sub=f'≈ KES {h["market_value_kes"]:,.0f}' if h.get("market_value_kes") else None),
        ui.kpi("Gain / loss", usd(h["gain"], 2, sign=True), tone=tone(h["gain"]), private=True, term="gain-loss",
               delta_html=ui.delta(h["gain_pct"], fmt="{:+.2f}%") if h.get("gain_pct") is not None else None,
               sub=f'vs {usd(h["invested"], 2)} put in'),
        ui.kpi("Today", usd(h.get("day_change_value"), 2, sign=True), tone=tone(h.get("day_change_value")),
               private=True, term="day-change"),
        ui.kpi("Units", f'{h["units"]:,.3f}', private=True, term="units"),
        ui.kpi("Next top-up", _when(h["next_topup"]) if h.get("next_topup") else "none", private=True,
               sub=f'{usd(h["monthly_amount"])} ≈ {h["next_topup_units_est"]:,.3f} units' if h.get("next_topup") else None),
    ], cls="kpis-position"), title="Your position", sec_id="position", icon="briefcase",
        sub="Private: from portfolio/funds.csv and fund_transactions.csv on this computer.",
        actions=Markup('<a href="portfolio.html#funds" class="small">Open in My Portfolio ›</a>'))
    insights = _insights(h, [r for r in rows if not r["you"]])
    summary = _join([
        Markup('<div class="grid-2">'),
        ui.card(_plain_summary(h, (rank[0], rank[1], "3 years") if rank and rank[1] > 1 else None),
                title="In plain English", icon="info", term="mutual-fund"),
        ui.card(insights or Markup('<p class="muted small">Nothing unusual to point out.</p>'),
                title="Worth knowing", sub="Facts, not advice.", icon="flag"),
        Markup('</div>'),
        ui.card(_facts_list(h), title="Key facts", icon="layers", term="price-cross-check"),
    ])
    tabs = ui.tabs("fund", [p for p in [
        ("summary", "Summary", summary, None),
        ("performance", "Performance", _performance_tab(h), None),
        ("holdings", "Holdings", _holdings_tab(h), None),
        ("income", "Income", _income_tab(h), None) if (h.get("distributions") or h.get("payouts") in
                                                         ("reinvest", "cash")) else None,
        ("competitors", "Competitors", _competitors_tab(h, rows), (len(rows) - 1) or None),
        ("money", "Your money", _your_money_tab(h, today), len(h.get("transactions") or []) or None),
        ("news", "News", _news_tab(h), len(h.get("news") or []) or None),
        ("glossary", "Glossary", _glossary_tab(), None),
    ] if p], label="Fund sections")
    body = _join([hero, position, tabs])
    sub = (f"{h['isin']} · prices: {h.get('source') or 'FT'} · price of {_when(h['nav_date'])} · "
           f"page generated {generated_at}")
    return h["name"], sub, body
