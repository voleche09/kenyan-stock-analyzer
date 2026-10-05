"""
💼 My Portfolio — one private page with five tabs instead of 23 stacked
sections: Summary · Kenyan stocks · Bonds · International · News.

Everything the old page showed is still here, laid out tighter (see the
information map in the redesign plan); the Summary tab adds a combined view
of all three parts. The arithmetic lives in the portfolio modules — this
file only arranges it — except networth_summary(), which adds the three
parts together and is shared with the Overview page and the tests.

Honesty rules carried over unchanged:
  - bonds count at their indicative par value and have no "today" move;
  - without a USD/KES rate this run, international holdings are shown in
    USD only and left out of every KES total (never converted at an old or
    guessed rate);
  - holdings without data today are listed and excluded, never guessed.
"""

import datetime as dt

from markupsafe import Markup, escape

import svg_charts as sc
import ui_kit as ui

# Old section anchors, kept so bookmarks and the app's links still land:
# stocks-top/charts/holdings/dividends, bonds-top/charts/detail/calendar,
# intl-top/charts/holdings/dividends, and stocks-news / intl-news (News tab).


# ------------------------------------------------------------------ formatting
def money(v, cur="KES", d=0, sign=False):
    """KES 1,234 · −$1,234 · +KES 1,234 · — when missing."""
    if v is None:
        return "—"
    s = "−" if v < 0 and round(abs(v), d) != 0 else ("+" if sign and v > 0 else "")
    return f"{s}{'$' if cur == 'USD' else 'KES '}{abs(v):,.{d}f}"


def pct(v, d=1, sign=True):
    if v is None:
        return "—"
    s = "−" if v < 0 and round(abs(v), d) != 0 else ("+" if sign and v > 0 else "")
    return f"{s}{abs(v):.{d}f}%"


def qty(q):
    """Whole shares as 1,200; fractions as they are (1.5, not 2)."""
    if q is None:
        return "—"
    return f"{q:,.0f}" if float(q).is_integer() else f"{q:,.4g}"


def tone(v):
    return None if v is None else ("up" if v > 0 else "down" if v < 0 else None)


def _join(parts):
    return Markup("".join(str(p) for p in parts if p))


# ------------------------------------------------------------------ the combined picture
def networth_summary(portfolio_summary, bond_portfolio, intl_portfolio_summary=None, usd_kes=None):
    """Everything you own, added up in KES — the same arithmetic the old
    "at a glance" hero used (bonds at indicative par value; international
    converted at this run's rate, or left out of the KES total without one).
    Returns None when nothing has been set up yet."""
    has_stocks, has_bonds, has_intl = bool(portfolio_summary), bool(bond_portfolio), bool(intl_portfolio_summary)
    if not (has_stocks or has_bonds or has_intl):
        return None
    st = portfolio_summary["totals"] if has_stocks else {}
    bt = bond_portfolio["totals"] if has_bonds else {}
    it = intl_portfolio_summary["totals"] if has_intl else {}
    rate = (usd_kes or {}).get("rate")
    fx_missing = has_intl and not rate

    stocks = {"value": st.get("market_value") or 0.0, "cost": st.get("cost_basis") or 0.0,
              "gain": st.get("gain") or 0.0, "day_pct": st.get("day_change_pct"),
              "day_value": st.get("day_change_value"), "dividend": st.get("est_annual_dividend") or 0.0}
    bonds = {"value": bt.get("indicative_value_par") or 0.0, "cost": bt.get("cost_basis") or 0.0,
             "income_after_tax": bt.get("annual_after_tax_income") or 0.0,
             "face_value": bt.get("face_value") or 0.0}
    intl = {"value_usd": it.get("market_value") or 0.0, "cost_usd": it.get("cost_basis") or 0.0,
            "gain_usd": it.get("gain") or 0.0, "day_pct": it.get("day_change_pct"),
            "day_value_usd": it.get("day_change_value"), "dividend_usd": it.get("est_annual_dividend") or 0.0}
    if has_intl and rate:
        intl.update(value_kes=intl["value_usd"] * rate, cost_kes=intl["cost_usd"] * rate,
                    day_value_kes=(intl["day_value_usd"] * rate if intl["day_value_usd"] is not None else None),
                    dividend_kes=intl["dividend_usd"] * rate)
    else:
        intl.update(value_kes=None if has_intl else 0.0, cost_kes=None if has_intl else 0.0,
                    day_value_kes=None, dividend_kes=None if has_intl else 0.0)

    total = stocks["value"] + bonds["value"] + (intl["value_kes"] or 0.0)
    cost = stocks["cost"] + bonds["cost"] + (intl["cost_kes"] or 0.0)
    gain = total - cost

    # Today: only stocks and international move day to day (bonds have no
    # daily price here), so the % is of yesterday's total net worth.
    day_parts = [v for v in (stocks["day_value"] if has_stocks else None,
                             intl["day_value_kes"] if has_intl else None) if v is not None]
    day_value = sum(day_parts) if day_parts else None
    day_pct = (day_value / (total - day_value) * 100.0) if (day_value is not None and total - day_value) else None
    ups = downs = 0
    for summary in (portfolio_summary, intl_portfolio_summary):
        for h in (summary or {}).get("holdings", []):
            c = h.get("day_change_pct") if h.get("data_available") else None
            if c is not None:
                ups += c > 0
                downs += c < 0

    share = (lambda v: v / total * 100.0) if total else (lambda v: 0.0)
    return {
        "has_stocks": has_stocks, "has_bonds": has_bonds, "has_intl": has_intl,
        "fx_rate": rate, "fx_missing": fx_missing,
        "fx_as_of": ((intl_portfolio_summary or {}).get("fx_as_of") if has_intl else None)
        or (usd_kes or {}).get("updated", ""),
        "total": total, "total_usd": (total / rate) if rate else None,
        "cost": cost, "gain": gain, "gain_pct": (gain / cost * 100.0) if cost else None,
        "day_value": day_value, "day_pct": day_pct, "ups": ups, "downs": downs,
        "stocks": dict(stocks, share=share(stocks["value"])),
        "bonds": dict(bonds, share=share(bonds["value"])),
        "intl": dict(intl, share=share(intl["value_kes"] or 0.0)),
        "dividends_kes": stocks["dividend"] + (intl["dividend_kes"] or 0.0),
        "dividends_intl_unconverted_usd": intl["dividend_usd"] if fx_missing else None,
    }


# ------------------------------------------------------------------ small pieces
def _sym_cell(rg, h, *, international=False, name=True):
    logo = rg._ticker_logo_html(h["symbol"], website=h.get("website"), international=international)
    nm = h.get("name") if name and h.get("name") and h.get("name") != h["symbol"] else ""
    inner = Markup(f'{Markup(logo)}<span>{escape(h["symbol"])}'
                   + (f"<small>{escape(nm)}</small>" if nm else "") + "</span>")
    link = ui.href(h.get("report_file") or "")
    if link:
        return Markup(f'<a class="sym" href="{link}">{inner}</a>')
    return Markup(f'<span class="sym">{inner}</span>')


def _score_chip(score):
    if score is None:
        return Markup("—")
    cls = "score-high" if score >= 70 else "score-mid" if score >= 45 else "score-low"
    return Markup(f'<span class="score {cls}">{escape(score)}</span>')


def _signal_pill(label, cls):
    return Markup(f'<span class="pill {escape(cls or "undefined")}">{escape(label or "—")}</span>')


def _part_row(label, value, *, kpi_label=None, tone_=None, private=True, html=None):
    t = f' data-tone="{tone_}"' if tone_ else ""
    cls = "kpi-value num private" if private else "kpi-value num"
    return Markup(f'<div class="part-row" data-kpi="{escape(kpi_label or label)}"{t}>'
                  f'<span class="pr-label">{escape(label)}</span>'
                  f'<span class="{cls}">{html if html is not None else escape(value)}</span></div>')


def _lookbacks(tracker, today_value, cur):
    """1 Day / 1 Week / 1 Month / 1 Year changes from the daily snapshots."""
    out = []
    for label, days in (("1 Day", 1), ("1 Week", 7), ("1 Month", 30), ("1 Year", 365)):
        lb = tracker.lookback(today_value, days) if tracker is not None else None
        if lb:
            out.append(ui.kpi(label, pct(lb["pct_change"]), tone=tone(lb["pct_change"]),
                              sub=f'since {lb["from_date"]} · {money(lb["change_value"], cur, sign=True)}'))
        else:
            out.append(ui.kpi(label, "—", sub="not enough history yet"))
    return ui.kpi_row(out, cls="kpis-4")


def _value_chart(chart_id, history_rows, cost, cur, title):
    rows = [r for r in (history_rows or []) if r.get("market_value") is not None]
    if len(rows) < 2:
        return None
    f = sc.Fmt("$" if cur == "USD" else "KES ", 0)
    series = [sc.Series("value", "Market value", [r["market_value"] for r in rows], fmt=f)]
    refs = [(cost, "cost", f"What you put in {money(cost, cur)}")] if cost else []
    return sc.chart(chart_id, [r["date"] for r in rows], series, title=title, height=220, ref_lines=refs,
                    y_fmt=sc._short_num, time_axis=True,        # snapshots are irregular: space by date
                    show_title=False)                           # the card around it carries the title


def _weights(holdings):
    avail = [h for h in holdings if h.get("data_available") and h.get("market_value")]
    total = sum(h["market_value"] for h in avail)
    return {h["symbol"]: h["market_value"] / total * 100.0 for h in avail} if total else {}


def _spark(history, sym, money_fmt):
    closes = (history or {}).get(sym)
    if not closes:
        return Markup('<span class="spark spark-empty" aria-label="No price history">—</span>')
    return sc.sparkline(closes, label=f"{sym}, last 6 months", fmt=money_fmt, area=False)


def _upcoming(when, today):
    try:
        d = dt.date.fromisoformat(str(when)[:10])
    except ValueError:
        return None
    return d if d >= today else None


# ------------------------------------------------------------------ Summary tab
def _summary_tab(rg, nw, *, portfolio_summary, bond_portfolio, intl_portfolio_summary, fundamentals,
                 intl_fundamentals, nse_history, intl_history, today):
    if nw is None:
        return ui.empty_state("Nothing here yet", "Add your Kenyan shares, bonds or international shares — "
                              "see the tabs above for how — and this summary fills in.", icon="briefcase")
    rate = nw["fx_rate"]
    attention = []
    for label, summary in (("Kenyan stocks", portfolio_summary), ("International", intl_portfolio_summary)):
        miss = (summary or {}).get("missing_symbols") or []
        if miss:
            attention.append(f"{label}: no live data today for {', '.join(miss)} — left out of the totals.")
    if bond_portfolio and bond_portfolio.get("missing_issues"):
        attention.append(f"Bonds: no verified terms for {', '.join(bond_portfolio['missing_issues'])} — "
                         "left out of the totals.")
    parts = [ui.banner(Markup("<br>".join(escape(a) for a in attention)), tone="danger",
                       title="Not counted today:")] if attention else []

    # ---- totals row
    usd_line = f"≈ ${nw['total_usd']:,.0f} USD" if nw["total_usd"] is not None else None
    gain_sub = (f'{money(nw["gain"], sign=True)} ({pct(nw["gain_pct"])}) vs. KES {nw["cost"]:,.0f} you put in'
                if nw["cost"] else None)
    kpis = [
        ui.kpi("Total net worth", f"KES {nw['total']:,.0f}", big=True, private=True, term="net-worth",
               delta_html=ui.delta(nw["day_pct"]) if nw["day_pct"] is not None else None,
               sub=" · ".join(x for x in (usd_line, "stocks + bonds + international, today") if x)),
        ui.kpi("Today", money(nw["day_value"], sign=True) if nw["day_value"] is not None else "—",
               tone=tone(nw["day_value"]), private=True, term="day-change",
               sub=f'{nw["ups"]} up · {nw["downs"]} down · bonds don\'t move daily'),
        ui.kpi("Total gain", money(nw["gain"], sign=True), tone=tone(nw["gain"]), private=True, term="gain-loss",
               delta_html=ui.delta(nw["gain_pct"], fmt="{:+.1f}%") if nw["gain_pct"] is not None else None,
               sub=f"vs. KES {nw['cost']:,.0f} you put in" if nw["cost"] else None),
    ]
    if nw["has_bonds"]:
        nxt = (bond_portfolio.get("upcoming_12m") or [None])[0]
        kpis.append(ui.kpi("Bond coupons a year (after tax)", money(nw["bonds"]["income_after_tax"]),
                           private=True, term="coupon",
                           sub=(f'next: KES {nxt["total"]:,.2f} on {nxt["date"]}' if nxt else None)))
    if nw["has_stocks"] or nw["has_intl"]:
        div_sub = None
        if nw["dividends_intl_unconverted_usd"]:
            div_sub = f'+ ${nw["dividends_intl_unconverted_usd"]:,.0f} international (no FX rate this run)'
        elif nw["has_intl"] and nw["intl"]["dividend_kes"]:
            div_sub = (f'Kenyan KES {nw["stocks"]["dividend"]:,.0f} · international '
                       f'${nw["intl"]["dividend_usd"]:,.0f}')
        kpis.append(ui.kpi("Dividends a year (before tax)", money(nw["dividends_kes"]), private=True,
                           term="est-dividend", sub=div_sub))
    parts.append(ui.kpi_row(kpis, cls="kpis-summary"))
    if rate:
        parts.append(Markup(f'<p class="footnote">USD/KES {rate:.2f}'
                            + (f" · {escape(nw['fx_as_of'])}" if nw["fx_as_of"] else "") + "</p>"))
    if nw["fx_missing"]:
        parts.append(ui.banner("⚠️ * FX rate unavailable this run — international holdings are shown in USD only "
                               "and excluded from the combined KES total above (not guessed at a stale rate).",
                               tone="warn"))

    # ---- allocation + the three parts
    segs = []
    if nw["has_stocks"]:
        segs.append(("Kenyan stocks", nw["stocks"]["value"], "stocks"))
    if nw["has_bonds"]:
        segs.append(("Bonds", nw["bonds"]["value"], "bonds"))
    if nw["has_intl"] and nw["intl"]["value_kes"] is not None:
        segs.append(("International", nw["intl"]["value_kes"], "intl"))
    donut = sc.donut("c-pf-alloc", segs, title="Where your money is", fmt=sc.Fmt("KES ", 0),
                     center_value=sc._short_num(nw["total"]), center_label="net worth",
                     legend_values=False)              # the amounts are in "Your three parts", beside it
    legend_note = Markup(
        '<p class="footnote">🔵 Stocks (real, verified market price) &nbsp; 🟢 Bonds (indicative par value — '
        "Kenya's secondary bond market has no live retail quote feed, so this assumes each bond is worth face "
        "value; see the bonds section for the honest yield-sensitivity lookup) &nbsp; 🟠 International (real, "
        'verified market price, converted from USD at the rate shown above). "Today" applies to stocks and '
        "international holdings; bonds don't reprice daily in this model.</p>")
    alloc_card = ui.card(_join([donut, legend_note]), title="Where your money is", term="allocation", icon="pie")

    part_blocks = []
    st = nw["stocks"]
    if nw["has_stocks"]:
        part_blocks.append(_join([
            '<div class="part"><h4><i class="sw stocks"></i>Stocks<a class="part-link" href="#kenyan">Open ›</a></h4>',
            _part_row("Market value", f"KES {st['value']:,.0f}", kpi_label="Stocks · Market value"),
            _part_row("Gain / loss", money(st["gain"], sign=True), kpi_label="Stocks · Gain / loss",
                      tone_=tone(st["gain"])),
            _part_row("Today", pct(st["day_pct"], 2), kpi_label="Stocks · Today", tone_=tone(st["day_pct"]),
                      private=False),
            _part_row("Share of net worth", f"{st['share']:.0f}%", kpi_label="Stocks · Share of net worth",
                      private=False), "</div>"]))
    else:
        part_blocks.append(Markup('<div class="part"><h4><i class="sw stocks"></i>Stocks</h4><p class="muted small">'
                                  'None added yet — add a row to <code>portfolio/holdings.csv</code></p></div>'))
    bd = nw["bonds"]
    if nw["has_bonds"]:
        part_blocks.append(_join([
            '<div class="part"><h4><i class="sw bonds"></i>Bonds<a class="part-link" href="#bonds">Open ›</a></h4>',
            _part_row("Indicative value", f"KES {bd['value']:,.0f}", kpi_label="Bonds · Indicative value"),
            _part_row("Annual income (after tax)", f"KES {bd['income_after_tax']:,.0f}",
                      kpi_label="Bonds · Annual income (after tax)", tone_="up"),
            _part_row("Today", "n/a — see note", kpi_label="Bonds · Today", private=False),
            _part_row("Share of net worth", f"{bd['share']:.0f}%", kpi_label="Bonds · Share of net worth",
                      private=False), "</div>"]))
    else:
        part_blocks.append(Markup('<div class="part"><h4><i class="sw bonds"></i>Bonds</h4><p class="muted small">'
                                  'None added yet — add a row to <code>portfolio/bonds.csv</code></p></div>'))
    it = nw["intl"]
    if nw["has_intl"]:
        kes_sub = (f'<small>KES {it["value_kes"]:,.0f}</small>' if it["value_kes"] is not None else "")
        part_blocks.append(_join([
            '<div class="part"><h4><i class="sw intl"></i>International<a class="part-link" href="#international">'
            'Open ›</a></h4>',
            _part_row("Market value", None, kpi_label="International · Market value",
                      html=Markup(f"${it['value_usd']:,.0f}{kes_sub}")),
            _part_row("Gain / loss", money(it["gain_usd"], "USD", sign=True), kpi_label="International · Gain / loss",
                      tone_=tone(it["gain_usd"])),
            _part_row("Today", pct(it["day_pct"], 2), kpi_label="International · Today", tone_=tone(it["day_pct"]),
                      private=False),
            _part_row("Share of net worth", f"{it['share']:.0f}%" + (" *" if nw["fx_missing"] else ""),
                      kpi_label="International · Share of net worth", private=False), "</div>"]))
    else:
        part_blocks.append(Markup('<div class="part"><h4><i class="sw intl"></i>International</h4><p class="muted small">'
                                  'None added yet — add a row to <code>portfolio/international_holdings.csv</code>'
                                  '</p></div>'))
    parts_card = ui.card(Markup(f'<div class="part-grid">{_join(part_blocks)}</div>'), title="Your three parts",
                         icon="layers")
    parts.append(Markup(f'<div class="grid-2 summary-mid">{alloc_card}{parts_card}</div>'))

    # ---- insights
    parts.append(Markup('<div class="grid-3 insights">'
                        + str(_moves_card(rg, nw, portfolio_summary, intl_portfolio_summary))
                        + str(_coming_up_card(bond_portfolio, portfolio_summary, intl_portfolio_summary,
                                              fundamentals, intl_fundamentals, today))
                        + str(_worth_knowing_card(nw, portfolio_summary, intl_portfolio_summary))
                        + "</div>"))

    # ---- everything you own
    parts.append(_everything_table(rg, nw, portfolio_summary, bond_portfolio, intl_portfolio_summary,
                                   nse_history, intl_history, fundamentals))
    parts.append(Markup('<p class="footnote">Information, not financial advice — each tab has the full note on '
                        'where its figures come from. These files are never committed to git.</p>'))
    return _join(parts)


def _moves_card(rg, nw, ps, ips):
    """Today's biggest moves in shillings (quantity × today's change)."""
    rate = nw["fx_rate"]
    moves = []
    for h in (ps or {}).get("holdings", []):
        if h.get("data_available") and h.get("day_change_value") is not None:
            moves.append((h["symbol"], h["day_change_value"], h.get("day_change_pct"), "KES"))
    for h in (ips or {}).get("holdings", []):
        if h.get("data_available") and h.get("day_change_value") is not None:
            v = h["day_change_value"] * rate if rate else None
            moves.append((h["symbol"], v if v is not None else h["day_change_value"], h.get("day_change_pct"),
                          "KES" if rate else "USD"))
    moves = [m for m in moves if m[1]]
    moves.sort(key=lambda m: abs(m[1]), reverse=True)
    if not moves:
        body = Markup('<p class="muted small">No price moves to show today.</p>')
    else:
        items = "".join(
            f'<li><span class="sym">{escape(sym)}</span><span class="num muted">{escape(pct(p, 2))}</span>'
            f'<b class="num private" data-tone="{tone(v) or "flat"}">{escape(money(v, cur, sign=True))}</b></li>'
            for sym, v, p, cur in moves[:6])
        body = Markup(f'<ul class="insight-list">{items}</ul>')
    return ui.card(body, title="Biggest moves today", sub="In shillings: your shares × today's change.",
                   icon="activity", term="day-change")


def coming_up_events(bp, ps, ips, fundamentals, intl_fundamentals, today):
    """[(date, what, amount text, colour class)] — dated payments and company
    events for things you own, soonest first (also shown on the Overview)."""
    events = []
    for r in (bp or {}).get("upcoming_12m") or []:
        what = " + ".join(x for x in (("coupon" if r.get("coupon") else ""),
                                      ("principal" if r.get("principal") else "")) if x)
        events.append((r["date"], f"{', '.join(r['bonds'])} {what}", f"KES {r['total']:,.2f}", "bonds"))
    for h in (ps or {}).get("holdings", []):
        f = (fundamentals or {}).get(h["symbol"]) or {}
        unverified = f.get("dividend_status") == "unverified"
        tag = " (unverified)" if unverified else ""
        for key, label in (("dividend_book_closure", "book closure"), ("dividend_payment_date", "dividend paid")):
            d = _upcoming(f.get(key), today) if f.get(key) else None
            if d:
                amt = f"KES {f['dps_fy']:.2f}/share" if f.get("dps_fy") else ""
                events.append((d.isoformat(), f"{h['symbol']} {label}{tag}", amt, "stocks"))
        d = _upcoming(f.get("earnings_next_date"), today) if f.get("earnings_next_date") else None
        if d:
            events.append((d.isoformat(), f"{h['symbol']} results due", "", "stocks"))
    for h in (ips or {}).get("holdings", []):
        f = (intl_fundamentals or {}).get(h["symbol"]) or {}
        for key, label in (("ex_dividend_date", "ex-dividend"), ("next_earnings_date", "results due")):
            d = _upcoming(f.get(key), today) if f.get(key) else None
            if d:
                events.append((d.isoformat(), f"{h['symbol']} {label}", "", "intl"))
    events.sort()
    return events


def _coming_up_card(bp, ps, ips, fundamentals, intl_fundamentals, today):
    """Dated payments and company events for things you own, soonest first."""
    events = coming_up_events(bp, ps, ips, fundamentals, intl_fundamentals, today)
    if not events:
        body = Markup('<p class="muted small">No dated payments or company events in the next few months.</p>')
    else:
        items = "".join(
            f'<li><span class="when num">{escape(when)}</span><span><i class="sw {cls}"></i>{escape(what)}</span>'
            f'<b class="num private">{escape(amt)}</b></li>' for when, what, amt, cls in events[:6])
        more = (f'<p class="footnote">+ {len(events) - 6} more — see the Bonds tab and each stock\'s page.</p>'
                if len(events) > 6 else "")
        body = Markup(f'<ul class="insight-list dated">{items}</ul>{more}')
    return ui.card(body, title="Coming up", sub="Coupons, dividend dates and results for things you own.",
                   icon="calendar", term="dividend-dates")


def _worth_knowing_card(nw, ps, ips):
    """Four plain facts about how your money is spread."""
    facts = []
    total = nw["total"]
    holdings = []
    for h in (ps or {}).get("holdings", []):
        if h.get("data_available") and h.get("market_value"):
            holdings.append((h["symbol"], h["market_value"], h.get("sector") or "Other", h.get("tv_signal_class")))
    if nw["fx_rate"]:
        for h in (ips or {}).get("holdings", []):
            if h.get("data_available") and h.get("market_value"):
                holdings.append((h["symbol"], h["market_value"] * nw["fx_rate"], h.get("sector") or "Other",
                                 h.get("recommendation_class")))
    if holdings and total:
        sym, v, _s, _c = max(holdings, key=lambda x: x[1])
        facts.append(("Largest holding", f"{sym} — {v / total * 100:.0f}% of your net worth"))
        by_sector = {}
        for _sym, v, sector, _c in holdings:
            by_sector[sector] = by_sector.get(sector, 0.0) + v
        sector, sv = max(by_sector.items(), key=lambda x: x[1])
        facts.append(("Largest sector", f"{sector} — {sv / total * 100:.0f}% of your net worth"))
    negative = sorted(sym for sym, _v, _s, c in holdings if c in ("bearish", "sell", "strong_sell"))
    facts.append(("Mostly negative signals", ", ".join(negative) if negative else "none of your holdings"))
    missing = sorted(((ps or {}).get("missing_symbols") or []) + ((ips or {}).get("missing_symbols") or []))
    facts.append(("No data today", ", ".join(missing) if missing else "every holding was priced"))
    items = "".join(f'<li><span>{escape(k)}</span><b>{escape(v)}</b></li>' for k, v in facts)
    return ui.card(Markup(f'<ul class="insight-list facts">{items}</ul>'), title="Worth knowing",
                   sub="How spread out your money is — useful to know, not advice.", icon="info",
                   term="weight")


def _everything_table(rg, nw, ps, bp, ips, nse_history, intl_history, fundamentals=None):
    """One table: every holding of every kind, valued in KES."""
    total = nw["total"] or 0.0
    rate = nw["fx_rate"]
    kes0 = sc.Fmt("KES ", 2)
    rows = []
    for h in sorted((ps or {}).get("holdings", []), key=lambda x: x.get("market_value") or -1, reverse=True):
        ok = h.get("data_available")
        v = h.get("market_value") if ok else None
        named = dict(h, name=((fundamentals or {}).get(h["symbol"]) or {}).get("name"))
        rows.append([
            ui.cell(ui.pill("Kenyan stock", "info"), sort="1"),
            ui.cell(_sym_cell(rg, named), sort=h["symbol"]),
            ui.cell(f"{v:,.0f}" if v is not None else "no data today", sort=v, cls="private"),
            ui.cell(Markup(f'{ui.weight_bar(v / total * 100, tone="stocks")} <span class="num">{v / total * 100:.1f}%</span>')
                    if v and total else "—", sort=round(v / total * 100, 2) if v and total else None),
            ui.cell(money(h.get("gain"), sign=True) if ok else "—", sort=h.get("gain") if ok else None,
                    tone=tone(h.get("gain") if ok else None), cls="private"),
            ui.cell(pct(h.get("gain_pct")) if ok else "—", sort=h.get("gain_pct") if ok else None,
                    tone=tone(h.get("gain_pct") if ok else None)),
            ui.cell(pct(h.get("day_change_pct"), 2) if ok else "—", sort=h.get("day_change_pct") if ok else None,
                    tone=tone(h.get("day_change_pct") if ok else None)),
            ui.cell(_spark(nse_history, h["symbol"], kes0)),
        ])
    for b in sorted((bp or {}).get("bonds", []), key=lambda x: x.get("face_value") or 0, reverse=True):
        if not b.get("data_available"):
            rows.append([ui.cell(ui.pill("Bond", "accent"), sort="2"),
                         ui.cell(Markup(f'<span class="sym"><span>{escape(b["issue"])}<small>no verified terms</small>'
                                        '</span></span>'), sort=b["issue"]),
                         ui.cell("—"), ui.cell("—"), ui.cell("—"), ui.cell("—"), ui.cell("—"), ui.cell("—")])
            continue
        v = b.get("indicative_value_par")
        gain = (v - b["cost_basis"]) if v is not None and b.get("cost_basis") is not None else None
        rows.append([
            ui.cell(ui.pill("Bond", "accent"), sort="2"),
            ui.cell(Markup(f'<a class="sym" href="#bonds-detail"><span>{escape(b["issue"])}'
                           f'<small>{escape(b.get("type_label") or "")} · {b["coupon_pct"]:.2f}% coupon</small>'
                           '</span></a>'), sort=b["issue"]),
            ui.cell(f"{v:,.0f}" if v is not None else "—", sort=v, cls="private"),
            ui.cell(Markup(f'{ui.weight_bar(v / total * 100, tone="bonds")} <span class="num">{v / total * 100:.1f}%</span>')
                    if v and total else "—", sort=round(v / total * 100, 2) if v and total else None),
            ui.cell(money(gain, sign=True) if gain is not None else "—", sort=gain, tone=tone(gain), cls="private"),
            ui.cell("—"), ui.cell("n/a", tip="Bonds don't reprice daily in this model"),
            ui.cell(Markup('<span class="muted small">no daily price</span>')),
        ])
    for h in sorted((ips or {}).get("holdings", []), key=lambda x: x.get("market_value") or -1, reverse=True):
        ok = h.get("data_available")
        v_usd = h.get("market_value") if ok else None
        v = v_usd * rate if (v_usd is not None and rate) else None
        rows.append([
            ui.cell(ui.pill("International", "warn"), sort="3"),
            ui.cell(_sym_cell(rg, h, international=True), sort=h["symbol"]),
            ui.cell((f"{v:,.0f}" if v is not None else (f"${v_usd:,.0f} (no FX rate)" if v_usd is not None
                                                         else "no data today")), sort=v, cls="private"),
            ui.cell(Markup(f'{ui.weight_bar(v / total * 100, tone="intl")} <span class="num">{v / total * 100:.1f}%</span>')
                    if v and total else "—", sort=round(v / total * 100, 2) if v and total else None),
            ui.cell(money(h["gain"] * rate, sign=True) if (ok and rate and h.get("gain") is not None)
                    else (money(h.get("gain"), "USD", sign=True) if ok else "—"),
                    sort=h.get("gain") if ok else None, tone=tone(h.get("gain") if ok else None), cls="private"),
            ui.cell(pct(h.get("gain_pct")) if ok else "—", sort=h.get("gain_pct") if ok else None,
                    tone=tone(h.get("gain_pct") if ok else None)),
            ui.cell(pct(h.get("day_change_pct"), 2) if ok else "—", sort=h.get("day_change_pct") if ok else None,
                    tone=tone(h.get("day_change_pct") if ok else None)),
            ui.cell(_spark(intl_history, h["symbol"], sc.Fmt("$", 2))),
        ])
    cols = [ui.Col("Kind", sort="text"), ui.Col("Holding", sort="text"), ui.Col("Value (KES)", sort="number"),
            ui.Col("Share of net worth", sort="number", term="weight"), ui.Col("Gain (KES)", sort="number"),
            ui.Col("Gain %", sort="number"), ui.Col("Today", sort="number", term="day-change"),
            ui.Col("6 months", sort=None)]
    return ui.section(ui.table(cols, rows, table_id="t-everything", filter_placeholder="Filter your holdings…",
                               show_first=12, empty="Nothing to show yet"),
                      title="Everything you own", sec_id="everything", icon="briefcase",
                      sub="Kenyan stocks, bonds and international shares in one place, valued in shillings. "
                          "Click a column header to sort; click a holding for its full page.")


# ------------------------------------------------------------------ Kenyan stocks tab
def _kenyan_tab(rg, ps, *, history_rows, tracker, nse_history):
    if not ps:
        return Markup(
            '<a id="stocks-top"></a>'
            + str(ui.empty_state(
                "You haven't added any Kenyan shares yet",
                "This page is 100% private — your holdings live in portfolio/holdings.csv on your own machine "
                "and are never committed to git.", icon="briefcase"))
            + '<div class="card"><div class="card-body"><strong>Add your first purchase — easiest way, a '
              'spreadsheet:</strong><p>Copy <code>portfolio/holdings.example.csv</code> to '
              '<code>portfolio/holdings.csv</code>, replace the example rows with your own (one row per purchase; '
              'columns <code>symbol, quantity, buy_price, buy_date, note</code>) in Excel, Numbers or Google '
              'Sheets, and save it as CSV. Exact format: <code>portfolio/README.md</code>.</p>'
              '<p>Prefer the terminal? From the project folder:</p>'
              '<pre>./venv/bin/python3 add_holding.py EQTY 500 34.50</pre>'
              '<p>Then re-run <code>./run.sh</code> and this page fills in: live value, gain/loss, dividends, '
              'sector allocation, charts, news on your holdings, and daily/weekly/monthly/yearly performance as '
              'history builds up.</p></div></div>')
    t = ps["totals"]
    holdings = ps["holdings"]
    parts = [Markup('<a id="stocks-top"></a>'),
             ui.banner(Markup('⚠️ <strong>Your private portfolio — educational information, not financial advice.'
                              '</strong> Values are computed fresh every run from the same verified prices and '
                              'fundamentals used throughout this dashboard. The "TV Signal" and "Score" per holding '
                              'are the same transparent, mechanical screens shown dashboard-wide — not a personalized '
                              'buy/sell recommendation. This file is never committed to git; see '
                              '<code>portfolio/README.md</code>.'), tone="warn")]
    missing = ps.get("missing_symbols") or []
    if missing:
        parts.append(ui.banner(Markup(
            f'⚠️ <strong>No live data today for: {escape(", ".join(missing))}.</strong> These holdings are excluded '
            "from the totals below rather than shown with a guessed price. Try running the pipeline again — this is "
            "usually a temporary gap in the day's data pull."), tone="danger"))
    best, worst = ps.get("best"), ps.get("worst")
    kpis = [
        ui.kpi("Market Value Today", f"KES {t['market_value']:,.0f}", private=True, big=True,
               sub=f"As of {ps.get('as_of', '')} · {t['n_available']} of {t['n_holdings']} holding(s) priced today."),
        ui.kpi("Total Cost Basis", f"KES {t['cost_basis']:,.0f}", private=True, term="cost-basis"),
        ui.kpi("Total Gain / Loss", f"{'+' if (t['gain'] or 0) >= 0 else ''}KES {t['gain']:,.0f}",
               tone=tone(t["gain"]), private=True, term="gain-loss",
               delta_html=ui.delta(t.get("gain_pct"), fmt="{:+.1f}%") if t.get("gain_pct") is not None else None),
        ui.kpi("Today", pct(t.get("day_change_pct"), 2), tone=tone(t.get("day_change_pct")), term="day-change",
               sub=(f"KES {t['day_change_value']:+,.0f}" if t.get("day_change_value") is not None else None)),
        ui.kpi("Est. Annual Dividend", f"KES {t['est_annual_dividend']:,.0f}", private=True, term="est-dividend",
               sub=(f"{t['dividend_yield_on_cost']:.2f}% yield on what you paid"
                    if t.get("dividend_yield_on_cost") is not None else None)),
        ui.kpi("Total Return incl. Dividend",
               f"{t['total_return_incl_div_pct']:+.1f}%" if t.get("total_return_incl_div_pct") is not None else "—",
               tone=tone(t.get("total_return_incl_div_pct")), term="total-return"),
    ]
    if best and worst and best is not worst:
        kpis.append(ui.kpi("Best / worst", f"{best['symbol']} {pct(best['gain_pct'])}",
                           sub=f"weakest: {worst['symbol']} {pct(worst['gain_pct'])}", tone=tone(best["gain_pct"])))
    parts.append(ui.kpi_row(kpis))
    parts.append(Markup('<p class="footnote">"Total Return incl. Dividend" = your unrealized price gain % + this '
                        "year's declared dividend yield on your cost. It assumes the full declared dividend is paid "
                        "— an indicative estimate, not a record of dividends actually received.</p>"))

    # ---- performance over time
    if tracker is not None:
        days, first = tracker.days_recorded(), tracker.first_date()
        intro = ("Computed from real snapshots recorded each day you run this tool"
                 + (f" — tracking since {first} ({days} day(s) recorded so far)." if first else ".")
                 + " A window shows \"not enough history yet\" rather than a guess when there isn't a real "
                   "snapshot that far back.")
        parts.append(ui.section(_lookbacks(tracker, t["market_value"], "KES"), title="Performance Over Time",
                                sub=intro, icon="trend-up", term="lookback"))

    # ---- charts
    value = _value_chart("c-pf-nse-value", history_rows, t.get("cost_basis"), "KES", "Portfolio value over time")
    weights = _weights(holdings)
    sector_alloc = ps.get("sector_allocation") or {}
    sector_donut = sc.donut("c-pf-nse-sectors",
                            [(k, v, f"c{i % 7 + 1}") for i, (k, v) in
                             enumerate(sorted(sector_alloc.items(), key=lambda x: -x[1]))],
                            title="Portfolio by sector", fmt=sc.Fmt("KES ", 0),
                            center_value=sc._short_num(sum(sector_alloc.values())) if sector_alloc else "",
                            center_label="by sector")
    gl = sc.hbars([{"label": h["symbol"], "value": h["gain_pct"], "href": ui.href(h.get("report_file") or "") or None}
                   for h in sorted(holdings, key=lambda x: x.get("gain_pct") or 0, reverse=True)
                   if h.get("data_available") and h.get("gain_pct") is not None],
                  fmt=sc.Fmt(suffix="%", decimals=1, sign=True), diverging=True)
    wt = sc.hbars([{"label": s, "value": w} for s, w in sorted(weights.items(), key=lambda x: -x[1])],
                  fmt=sc.Fmt(suffix="%", decimals=1), tone_from_sign=False)
    charts = [ui.card(value if value else Markup(
        '<p class="muted small">📈 Portfolio value chart will appear once you\'ve run this tool on at least 2 '
        'different days — building real history, not a guess.</p>'), title="Portfolio value over time",
        icon="trend-up")]
    charts.append(Markup('<div class="grid-3">'
                         + str(ui.card(sector_donut, title="By sector", term="allocation"))
                         + str(ui.card(gl, title="Unrealized gain / loss by holding",
                                       sub="% since your average cost"))
                         + str(ui.card(wt, title="Portfolio weight by holding",
                                       sub="% of total portfolio market value", term="weight"))
                         + "</div>"))
    parts.append(ui.section(_join(charts), title="Portfolio Charts", sec_id="stocks-charts", icon="bar"))

    # ---- holdings table
    kes2 = sc.Fmt("KES ", 2)
    rows, attrs = [], []
    for h in sorted(holdings, key=lambda x: (x["market_value"] or -1), reverse=True):
        tip = rg._portfolio_holding_tip(h)
        sym = ui.cell(_sym_cell(rg, h, name=False), sort=h["symbol"])
        if not h["data_available"]:
            rows.append([sym, ui.cell(qty(h["quantity"]), sort=h["quantity"]),
                         ui.cell(f'{h["avg_cost"]:.2f}', sort=h["avg_cost"]),
                         ui.cell("No live data today — see warning above", tone="down")]
                        + [ui.cell("—") for _ in range(10)])
            attrs.append(f'data-tip="{tip}" class="row-nodata"')
            continue
        roe = h.get("roe")
        roe_cls = rg._fund_color("roe", roe) if roe is not None else ""
        w = weights.get(h["symbol"])
        rows.append([
            sym,
            ui.cell(qty(h["quantity"]), sort=h["quantity"]),
            ui.cell(f'{h["avg_cost"]:.2f}', sort=h["avg_cost"]),
            ui.cell(f'{h["price"]:.2f}', sort=h["price"]),
            ui.cell(f'{h["market_value"]:,.0f}', sort=h["market_value"], cls="private"),
            ui.cell(Markup(f'{ui.weight_bar(w, tone="stocks")} <span class="num">{w:.1f}%</span>') if w else "—",
                    sort=round(w, 2) if w else None),
            ui.cell(f'{h["gain"]:+,.0f}', sort=h["gain"], tone=tone(h["gain"]), cls="private"),
            ui.cell(f'{h["gain_pct"]:+.1f}%', sort=h["gain_pct"], tone=tone(h["gain_pct"])),
            ui.cell(f'{h["day_change_pct"]:+.2f}%' if h.get("day_change_pct") is not None else "—",
                    sort=h.get("day_change_pct"), tone=tone(h.get("day_change_pct"))),
            ui.cell(_spark(nse_history, h["symbol"], kes2)),
            ui.cell(Markup(f'<span class="{escape(roe_cls)}">{roe:.1f}%</span>') if roe is not None else "N/A",
                    sort=roe),
            ui.cell(f'{h["dividend_yield"]:.1f}%' if h.get("dividend_yield") is not None else "—",
                    sort=h.get("dividend_yield")),
            ui.cell(_signal_pill(h["tv_signal_label"], h["tv_signal_class"]), sort=h["tv_signal_label"]),
            ui.cell(_score_chip(h.get("score")), sort=h.get("score")),
        ])
        attrs.append(f'data-tip="{tip}"')
    cols = [ui.Col("Symbol", sort="text"), ui.Col("Qty", sort="number"), ui.Col("Avg Cost", sort="number"),
            ui.Col("Price", sort="number"), ui.Col("Value (KES)", sort="number"),
            ui.Col("Weight", sort="number", term="weight"), ui.Col("Gain (KES)", sort="number"),
            ui.Col("Gain %", sort="number"), ui.Col("Today", sort="number"), ui.Col("6 months", sort=None),
            ui.Col("Company ROE", sort="number", term="roe",
                   title="Return on Equity — how efficiently the company uses shareholder capital. Not your personal return."),
            ui.Col("Div Yield", sort="number", term="dividend-yield"),
            ui.Col("TV Signal", sort="signal", term="tv-rating", title="TradingView technical rating"),
            ui.Col("Score", sort="number", term="factor-score",
                   title="0-100 transparent factor screen — hover for the breakdown")]
    parts.append(ui.section(
        ui.table(cols, rows, table_id="t-nse-holdings", row_attrs=attrs),
        title="Your Holdings", sec_id="stocks-holdings", icon="briefcase",
        sub="💡 Click any column header to sort. Hover a row for the full breakdown behind its score. \"ROE\" is "
            "the company's own Return on Equity (green ≥15%, red <5%) — a company-quality metric, not your personal "
            "return (that's the \"Gain %\" column)."))

    # ---- dividends
    div_rows = []
    for h in sorted(holdings, key=lambda x: (x.get("est_annual_dividend") or 0), reverse=True):
        if not h["data_available"]:
            continue
        status = h.get("dividend_status")
        if h.get("dps_fy") and h["dps_fy"] > 0:
            badge = ('<span class="div-pay">Pays dividend</span>' if status != "unverified"
                     else '<span class="div-unverified">Unverified</span>')
        else:
            badge = '<span class="div-zero">0 — none declared</span>'
        ex = h.get("dividend_ex_date")
        if ex:
            ex_html = (f'<span class="exdate-upcoming">{escape(ex)} (upcoming)</span>' if h.get("dividend_ex_upcoming")
                       else f'<span class="exdate-past">{escape(ex)} (passed)</span>')
        else:
            ex_html = '<span class="exdate-none">—</span>'
        div_rows.append([
            ui.cell(_sym_cell(rg, h, name=False), sort=h["symbol"]),
            ui.cell(Markup(badge)),
            ui.cell(f"KES {h['dps_fy']:.2f}/share" if h.get("dps_fy") else "—", sort=h.get("dps_fy")),
            ui.cell(f"{h['dividend_yield']:.1f}%" if h.get("dividend_yield") is not None else "—",
                    sort=h.get("dividend_yield")),
            ui.cell(f"KES {(h.get('est_annual_dividend') or 0):,.0f}/yr", sort=h.get("est_annual_dividend") or 0,
                    cls="private"),
            ui.cell(Markup(ex_html), sort=ex or ""),
        ])
    if div_rows:
        dcols = [ui.Col("Symbol", sort="text"), ui.Col("Status", sort="text"), ui.Col("Per Share", sort="number"),
                 ui.Col("Yield", sort="number"), ui.Col("Your Est. Income", sort="number"),
                 ui.Col("Ex-Date", sort="text", term="dividend-dates")]
        parts.append(ui.details("💵 Dividends on Your Holdings", Markup(
            "<p class=\"section-sub\">The declared dividend per share, cross-checked against the NSE dividend "
            "calendar, and what it's worth on your position.</p>") + ui.table(dcols, div_rows, table_id="t-nse-div"),
            det_id="stocks-dividends"))

    parts.append(ui.details("➕ Add a New Purchase", Markup(
        '<p>Every time you buy — even more of a stock you already hold — add one row to '
        '<code>portfolio/holdings.csv</code> (columns <code>symbol, quantity, buy_price, buy_date, note</code>) in '
        'your spreadsheet app, save it as CSV, and re-run <code>./run.sh</code>. Or let a helper script add the '
        'row for you (it writes to <code>holdings.csv</code> if you have one):</p>'
        '<pre>./venv/bin/python3 add_holding.py SYMBOL QUANTITY PRICE [DATE]\n'
        './venv/bin/python3 add_holding.py EQTY 500 34.50 2026-09-20</pre>'
        '<p class="footnote">Multiple purchases of the same stock combine automatically into one row with a '
        'correctly weighted average cost. Full details in portfolio/README.md. Nothing here is ever committed to '
        'git.</p>'), cls="no-app-only"))
    return _join(parts)


# ------------------------------------------------------------------ Bonds tab
def _bonds_tab(rg, bp):
    if not bp:
        return Markup(
            '<a id="bonds-top"></a>'
            + str(ui.empty_state("You haven't added any bond holdings yet",
                                 "Like your stock holdings, this is 100% private — your bonds live in "
                                 "portfolio/bonds.csv on your own machine and are never committed to git.",
                                 icon="bank"))
            + '<div class="card"><div class="card-body"><strong>Add your first bond — easiest way, a spreadsheet:'
              '</strong><p>Copy <code>portfolio/bonds.example.csv</code> to <code>portfolio/bonds.csv</code>, '
              'replace the example rows with your own (one row per purchase; columns <code>issue, face_value, '
              'purchase_price_pct, purchase_date, note</code>) and save it as CSV. Exact format: '
              '<code>portfolio/README.md</code>.</p><p>Prefer the terminal? From the project folder:</p>'
              '<pre>./venv/bin/python3 add_bond.py ISSUE FACE_VALUE</pre>'
              '<p>Then re-run <code>./run.sh</code> and this section fills in: accrued interest, next payment date '
              '&amp; amount, the full cash-flow schedule to redemption, after-tax income, running yield and total '
              'return — plus charts.</p></div></div>')
    t, bonds = bp["totals"], bp["bonds"]
    avail = [b for b in bonds if b["data_available"]]
    parts = [Markup('<a id="bonds-top"></a>'),
             ui.banner(Markup('⚠️ <strong>Your private bond portfolio — educational information, not financial or '
                              'tax advice.</strong> Coupon rates, dates and redemption rules are sourced from official '
                              'CBK prospectuses (cited per bond below) — contractual facts, not estimates. Anything '
                              'marked "indicative" depends on where market yields are trading today, which this '
                              'dashboard cannot observe live for these specific bonds — use it as a starting point, '
                              'not a quote. This file is never committed to git; see <code>portfolio/README.md</code>.'),
                       tone="warn")]
    if bp.get("missing_issues"):
        parts.append(ui.banner(Markup(
            f'⚠️ <strong>No verified reference data for: {escape(", ".join(bp["missing_issues"]))}.</strong> These '
            'are excluded from the totals below rather than shown with guessed terms. Add them to '
            '<code>BOND_REFERENCE</code> in <code>src/bonds_portfolio.py</code>, citing the official CBK prospectus, '
            'to include them.'), tone="danger"))
    nxt = (bp.get("upcoming_12m") or [None])[0]
    by = t.get("blended_running_yield_after_tax_pct")
    parts.append(ui.kpi_row([
        ui.kpi("Indicative Value (at par)", f"KES {t['indicative_value_par']:,.0f}", big=True, private=True,
               term="indicative-value", tone="up",
               sub=f"As of {bp.get('as_of', '')} · {t['n_available']} of {t['n_bonds']} bond(s) with verified terms."),
        ui.kpi("Total Face Value", f"KES {t['face_value']:,.0f}", private=True, term="face-value"),
        ui.kpi("Accrued Interest Today", f"KES {t['accrued_interest']:,.0f}", private=True, term="accrued-interest"),
        ui.kpi("Annual Income (pre-tax)", f"KES {t['annual_pretax_income']:,.0f}", private=True, term="coupon"),
        ui.kpi("Annual Income (after tax)", f"KES {t['annual_after_tax_income']:,.0f}", private=True, tone="up",
               term="withholding-tax"),
        ui.kpi("Blended After-Tax Yield", f"{by:.2f}%" if by is not None else "—", term="running-yield"),
        ui.kpi("Next payment", f"KES {nxt['total']:,.2f}" if nxt else "—", private=True,
               sub=(f"on {nxt['date']} · {', '.join(nxt['bonds'])}" if nxt else "none in the next 12 months")),
    ]))
    parts.append(Markup('<p class="footnote">Kenya Government Treasury &amp; Infrastructure Bonds — a fixed-income '
                        'complement to your stock holdings. "Indicative Value (at par)" = outstanding face value '
                        'still owed to you + interest already accrued since your last coupon. It assumes each bond '
                        'is currently worth exactly its face value — a standard, conservative simplification for '
                        'buy-and-hold retail bonds. The chart below shows how this would change if market yields '
                        "were actually higher or lower than each bond's own coupon.</p>"))

    # ---- charts
    cal = bp.get("calendar") or []
    by_year = {}
    for r in cal:
        a = by_year.setdefault(r["date"][:4], {"coupon": 0.0, "principal": 0.0})
        a["coupon"] += r["coupon"]
        a["principal"] += r["principal"]
    chart_parts = []
    if by_year:
        years = sorted(by_year)
        chart_parts.append(ui.card(_stacked_years(years, by_year), title="Projected bond cash flow by year",
                                   sub="Coupon (interest) income and principal repaid — when you get paid, and how "
                                       "much.", icon="bar"))
    alloc = sc.donut("c-pf-bond-alloc", [(b["issue"], b["face_value"], "bonds" if b["tax_free"] else "c1")
                                         for b in sorted(avail, key=lambda x: -x["face_value"])],
                     title="Bond portfolio by face value", fmt=sc.Fmt("KES ", 0),
                     center_value=sc._short_num(t["face_value"]), center_label="face value")
    chart_parts.append(ui.card(_join([alloc, '<p class="footnote">Teal = tax-free Infrastructure Bond (IFB) · '
                                             'indigo = taxable Fixed-coupon Bond (FXD) — the single biggest driver '
                                             'of your after-tax return.</p>']),
                               title="Bond portfolio by face value", icon="pie"))
    parts.append(ui.section(Markup(f'<div class="grid-2">{_join(chart_parts)}</div>'), title="Bond Charts",
                            sec_id="bonds-charts", icon="bar"))

    # ---- per-bond detail cards
    cards = []
    for b in sorted(avail, key=lambda x: x["face_value"], reverse=True):
        tax = ('<span class="div-pay">Tax-free (IFB)</span>' if b["tax_free"]
               else f'<span class="div-unverified">{b["withholding_pct"]:.0f}% withholding tax</span>')
        note = ""
        if b["small_holder_accelerated"]:
            note = (f'<p class="eg">⚡ <strong>Accelerated:</strong> your KES {b["face_value"]:,.0f} is below CBK\'s '
                    f'KES 1,000,000 small-holder threshold, so this bond redeems in full on '
                    f'{escape(b["effective_redemption_date"])} — not at its legal final maturity of '
                    f'{escape(b["legal_maturity_date"])}.</p>')
        elif b["legal_maturity_date"] != b["effective_redemption_date"]:
            note = (f'<p class="eg">🧩 Amortizes in tranches — see the schedule below. Legal final maturity: '
                    f'{escape(b["legal_maturity_date"])}.</p>')
        ne = b.get("next_event")
        next_txt = f'KES {ne["amount"]:,.2f} ({escape(ne["type"])}) on {escape(ne["date"])}' if ne else "None — fully redeemed"
        mine = f'<p class="eg">📝 {escape(b["note"])}</p>' if b.get("note") else ""
        src = ui.href(b.get("source_url"))
        tr = f'{b["total_return_pct"]:+.1f}%' if b["total_return_pct"] is not None else "—"
        cards.append(
            f'<article class="bond-card" data-tone="{"up" if b["tax_free"] else "accent"}">'
            f'<h4>{escape(b["issue"])} <span class="muted">· {escape(b["type_label"])} · {escape(b["tenor_label"])}</span></h4>'
            f'<p><strong>Face value:</strong> <span class="private">KES {b["face_value"]:,.0f}</span> &nbsp;·&nbsp; '
            f'<strong>Coupon:</strong> {b["coupon_pct"]:.4f}% &nbsp;·&nbsp; {tax}</p>'
            f'<p><strong>Next payment:</strong> <span class="private">{next_txt}</span></p>'
            f'<p><strong>Accrued interest today:</strong> <span class="private">KES {b["accrued_interest"]:,.2f}</span> '
            f'({b["days_accrued"]} days since last coupon)</p>'
            f'<p><strong>Annual income:</strong> <span class="private">KES {b["annual_pretax_income"]:,.0f} pre-tax / '
            f'KES {b["after_tax_annual_income"]:,.0f} after tax</span></p>'
            f'<p><strong>Total return to redemption:</strong> {tr} over {b["years_from_purchase_to_redemption"]:.1f} '
            f'years (not annualized; coupons only, excludes return of principal)</p>{note}{mine}'
            + (f'<p class="eg"><a href="{src}" target="_blank" rel="noopener">📄 {escape(b["source"])}</a></p>' if src
               else f'<p class="eg">📄 {escape(b["source"])}</p>')
            + "</article>")
    if cards:
        parts.append(ui.section(Markup(f'<div class="grid-auto">{"".join(cards)}</div>'),
                                title="Your Bond Holdings — Detail", sec_id="bonds-detail", icon="bank"))

    # ---- next 12 months
    upcoming = bp.get("upcoming_12m") or []
    if upcoming:
        rows = []
        for r in upcoming:
            what = " + ".join(x for x in ((f'Coupon KES {r["coupon"]:,.2f}' if r["coupon"] else ""),
                                          (f'Principal KES {r["principal"]:,.2f}' if r["principal"] else "")) if x)
            rows.append([ui.cell(Markup(f"<strong>{escape(r['date'])}</strong>"), sort=r["date"]),
                         ui.cell(what, cls="private"),
                         ui.cell(f'KES {r["total"]:,.2f}', sort=r["total"], tone="up", cls="private"),
                         ui.cell(", ".join(r["bonds"]))])
        cols = [ui.Col("Date", sort="text"), ui.Col("What", sort=None), ui.Col("Total", sort="number"),
                ui.Col("Bond(s)", sort="text")]
        parts.append(ui.section(ui.table(cols, rows, table_id="t-bond-cal"),
                                title="Payments Expected in the Next 12 Months", sec_id="bonds-calendar",
                                icon="calendar", sub="Every coupon and principal repayment due across all your "
                                                     "bonds, soonest first."))

    # ---- full schedules (collapsed — long)
    for b in sorted(avail, key=lambda x: x["face_value"], reverse=True):
        future = b.get("cashflows_future") or []
        if not future:
            continue
        by_date = {}
        for cf in future:
            by_date.setdefault(cf["date"], {"coupon": 0.0, "principal": 0.0})[cf["type"]] += cf["amount"]
        rows = []
        for d in sorted(by_date):
            v = by_date[d]
            ev = " + ".join(x for x in ((f'Coupon: KES {v["coupon"]:,.2f}' if v["coupon"] else ""),
                                        (f'Principal: KES {v["principal"]:,.2f}' if v["principal"] else "")) if x)
            rows.append([ui.cell(d, sort=d), ui.cell(ev, cls="private"),
                         ui.cell(f'KES {v["coupon"] + v["principal"]:,.2f}', sort=v["coupon"] + v["principal"],
                                 tone="up", cls="private")])
        cols = [ui.Col("Date", sort="text"), ui.Col("Payment", sort=None), ui.Col("Amount", sort="number")]
        parts.append(ui.details(f'🗓️ {b["issue"]} — Full Remaining Cash-Flow Schedule', Markup(
            f'<p class="section-sub">Every payment left on this bond, from today to '
            f'{escape(b["effective_redemption_date"])}.</p>') + ui.table(cols, rows, sortable=False)))

    # ---- price / yield sensitivity (collapsed — a reference lookup)
    series = []
    try:
        from bonds_portfolio import bond_price_sensitivity
        series = [(b["issue"], bond_price_sensitivity(b)) for b in avail if not b.get("is_matured")]
    except Exception:
        series = []
    series = [(issue, rows) for issue, rows in series if rows]
    if series:
        blocks = []
        for i, (issue, rows) in enumerate(series):
            trs = []
            for r in rows:
                tag = " (= this bond's coupon)" if r["is_coupon_yield"] else ""
                trs.append([ui.cell(f'{r["yield_pct"]:.4f}%{tag}', cls="hl" if r["is_coupon_yield"] else ""),
                            ui.cell(f'{r["clean_price_per_100"]:.4f}', cls="hl" if r["is_coupon_yield"] else ""),
                            ui.cell(f'{r["dirty_price_per_100"]:.4f}', cls="hl" if r["is_coupon_yield"] else ""),
                            ui.cell(f'KES {r["value_for_holding"]:,.2f}',
                                    cls="private hl" if r["is_coupon_yield"] else "private")])
            cols = [ui.Col("If market yield were...", sort=None), ui.Col("Clean price /100", sort=None),
                    ui.Col("Dirty price /100", sort=None), ui.Col("Your holding would be worth", sort=None)]
            chart = sc.chart(f"c-pf-sens-{i}", [f'{r["yield_pct"]:.2f}%' for r in rows],
                             [sc.Series("value", "Dirty price per 100", [r["dirty_price_per_100"] for r in rows],
                                        fmt=sc.Fmt(decimals=2))],
                             title=f"{issue} — price if market yields move", height=160,
                             ref_lines=[(100, "level", "par (100)")], small=True)
            blocks.append(f'<div class="sens"><h4>{escape(issue)}</h4><div class="grid-2">'
                          f'<div>{ui.table(cols, trs, sortable=False)}</div><div>{chart}</div></div></div>')
        parts.append(ui.details("🔍 Price / Yield Sensitivity — Look Up Today's Real Value", Markup(
            '<p class="section-sub">Find a recent Central Bank of Kenya Treasury/Infrastructure bond auction result '
            'for a <strong>similar remaining tenor</strong> (published at '
            '<a href="https://www.centralbank.go.ke/bills-bonds/treasury-bonds/" target="_blank" rel="noopener">'
            'centralbank.go.ke</a> after every auction), and find the closest yield row below for an honest, '
            "methodology-shown estimate of what your holding is worth today — rather than trusting a single invented "
            "number. The highlighted row is priced at par (100), the standard reference point.</p>"
            + "".join(blocks) +
            '<p class="footnote">Computed with the standard bond-pricing formula (present value of remaining coupons + '
            'principal, discounted semi-annually) — the same convention CBK itself uses in its own prospectus pricing '
            'tables. "Clean price" excludes accrued interest; "dirty price" (what you\'d actually pay or receive) '
            'includes it.</p>'), det_id="bonds-sensitivity"))

    # ---- understanding your bonds (generated — see ReportGenerator._bonds_explainer_facts)
    parts.append(ui.details("📘 Understanding Your Bonds", Markup(
        rg._bonds_explainer_facts(avail)
        + '<div class="explain-card"><h4>📊 Running Yield vs. Total Return</h4>'
          '<p><strong>Running yield</strong> is simply your coupon rate (after tax, if taxable) — the % of face value '
          'you receive every year while you hold the bond. <strong>Total return</strong> below is all coupons '
          "you'll ever receive (past + future, from your purchase date to redemption) as a % of what you paid — it is "
          "<em>not</em> annualized, and deliberately excludes the return of your own principal, which isn't profit."
          '</p></div><div class="explain-card"><h4>❓ Why is there no single "current market price"?</h4>'
          "<p>Kenya's secondary bond market doesn't publish live retail quotes per bond the way the NSE does for "
          'shares. Rather than invent one number, the sensitivity table &amp; chart below give you the honest tool: '
          'look up a recent CBK auction/re-opening yield for a similar tenor, find the closest row, and read off a '
          'grounded estimate.</p></div></div>'), det_id="bonds-explainer"))

    parts.append(ui.details("➕ Add a New Bond", Markup(
        '<p>Every time you buy a new bond, or top up an existing one, add one row to <code>portfolio/bonds.csv'
        '</code> (columns <code>issue, face_value, purchase_price_pct, purchase_date, note</code>) in your '
        'spreadsheet app, save it as CSV, and re-run <code>./run.sh</code>. Or let a helper script add the row '
        'for you (it writes to <code>bonds.csv</code> if you have one):</p>'
        '<pre>./venv/bin/python3 add_bond.py ISSUE FACE_VALUE [PURCHASE_PRICE_PCT] [PURCHASE_DATE]</pre>'
        '<p class="footnote">Full details, including how the small-holder amortization rule and tax treatment are '
        'determined, in <code>portfolio/README.md</code> and <code>src/bonds_portfolio.py</code>. Nothing here is '
        'ever committed to git.</p>'), cls="no-app-only"))
    return _join(parts)


def _stacked_years(years, by_year):
    """Coupons and principal by calendar year, as stacked columns."""
    coupon = [by_year[y]["coupon"] for y in years]
    principal = [by_year[y]["principal"] for y in years]
    peak = max((c + p for c, p in zip(coupon, principal)), default=0) or 1.0
    ticks, _d = sc.nice_ticks(0, peak, target=4)
    top = ticks[-1]
    n = len(years)
    w = sc.VB_W / n
    cp, pp = [], []
    for i, (c, p) in enumerate(zip(coupon, principal)):
        x0 = i * w + w * 0.18
        yc = (1 - c / top) * sc.VB_H
        yp = (1 - (c + p) / top) * sc.VB_H
        if c:
            cp.append(f"M{x0:.1f},{yc:.1f}h{w * 0.64:.2f}V{sc.VB_H:.0f}h{-w * 0.64:.2f}Z")
        if p:
            pp.append(f"M{x0:.1f},{yp:.1f}h{w * 0.64:.2f}V{yc:.1f}h{-w * 0.64:.2f}Z")
    grid = "".join(f"M0,{(1 - t / top) * sc.VB_H:.1f}H{sc.VB_W:.0f}" for t in ticks)
    data = {"x": years, "xf": "label", "s": [
        {"k": "coupon", "l": "Coupon (interest) income", "f": {"p": "KES ", "d": 0}, "v": [round(c) for c in coupon],
         "nodot": 1},
        {"k": "principal", "l": "Principal repaid", "f": {"p": "KES ", "d": 0}, "v": [round(p) for p in principal],
         "nodot": 1}]}
    step = max(1, n // 8)
    xs = "".join(f'<span style="left:{(i + 0.5) / n * 100:.2f}%">{escape(y)}</span>'
                 for i, y in enumerate(years) if i % step == 0)
    ys = "".join(f'<span style="top:{(1 - t / top) * 100:.2f}%">{escape(sc._short_num(t))}</span>' for t in ticks)
    title = "Projected bond cash flow by year"
    return Markup(
        f'<figure class="chart chart-sm" id="c-pf-bond-years" data-chart><figcaption class="chart-head">'
        '<div class="chart-legend"><span class="lg s-coupon">Coupon (interest) income</span>'
        '<span class="lg s-principal">Principal repaid</span></div></figcaption>'
        '<div class="chart-variant" data-default><div class="chart-body" style="--h:170px">'
        f'<div class="chart-y" aria-hidden="true">{ys}</div>'
        f'<div class="chart-plot" data-o="0" data-n="{n}" data-lo="0" data-hi="{top:g}" data-bars="1">'
        f'<svg class="chart-svg" viewBox="0 0 {sc.VB_W:.0f} {sc.VB_H:.0f}" preserveAspectRatio="none" role="img" '
        f'aria-label="{escape(title)}"><title>{escape(title)}</title><path class="grid" d="{grid}"/>'
        f'<path class="bar s-coupon" d="{"".join(cp)}"/><path class="bar s-principal" d="{"".join(pp)}"/></svg>'
        f'<div class="chart-x" aria-hidden="true">{xs}</div>'
        '<div class="xhair" hidden><i class="xhair-line"></i><div class="xhair-tip" role="status"></div></div>'
        f'</div></div></div><script type="application/json" class="chart-data">{sc._json(data)}</script></figure>')


# ------------------------------------------------------------------ International tab
def _intl_tab(rg, ips, *, history_rows, tracker, intl_history):
    if not ips:
        return Markup(
            '<a id="intl-top"></a>'
            + str(ui.empty_state("You haven't added any international holdings yet",
                                 "This page is 100% private — your holdings live in "
                                 "portfolio/international_holdings.csv on your own machine and are never committed "
                                 "to git.", icon="globe"))
            + '<div class="card"><div class="card-body"><strong>Add your first position — easiest way, a spreadsheet:'
              '</strong><p>Copy <code>portfolio/international_holdings.example.csv</code> to '
              '<code>portfolio/international_holdings.csv</code>, replace the example rows with your own (one row per '
              'purchase; columns <code>symbol, quantity, buy_price, buy_date, note</code> — <code>buy_price</code> in '
              'USD) and save it as CSV. Exact format: <code>portfolio/README.md</code>.</p>'
              '<p>Prefer the terminal? From the project folder:</p>'
              '<pre>./venv/bin/python3 add_international_holding.py AAPL 10 190.25</pre>'
              '<p>Then re-run <code>./run.sh</code> and this section fills in: live USD value, gain/loss, dividends, '
              'analyst consensus, charts, and news.</p></div></div>')
    t, holdings = ips["totals"], ips["holdings"]
    parts = [Markup('<a id="intl-top"></a>'),
             ui.banner(Markup('⚠️ <strong>Your private international portfolio — educational information, not '
                              'financial advice.</strong> Values are USD, fetched fresh every run from Yahoo Finance '
                              'and run through the exact same technical-analysis and factor-scoring engine as your NSE '
                              'holdings. "Rating" is Wall Street\'s own real analyst consensus (sourced, not invented) '
                              '— not a personalized buy/sell recommendation. This file is never committed to git; see '
                              '<code>portfolio/README.md</code>.'), tone="warn")]
    missing = ips.get("missing_symbols") or []
    if missing:
        parts.append(ui.banner(Markup(f'⚠️ <strong>No live data today for: {escape(", ".join(missing))}.</strong> '
                                      'These holdings are excluded from the totals below rather than shown with a '
                                      'guessed price. Try running the pipeline again.'), tone="danger"))
    if not t.get("fx_available"):
        parts.append(ui.banner("ℹ️ USD/KES exchange rate wasn't fetched this run — figures below are USD only "
                               "(no guessed KES conversion).", tone="info"))
    gp, dp = t.get("gain_pct"), t.get("day_change_pct")
    cost = t.get("cost_basis") or 0.0
    div_on_cost = (t["est_annual_dividend"] / cost * 100.0) if cost and t.get("est_annual_dividend") is not None else None
    total_return = (round((round(gp, 2) if gp is not None else 0) + (round(div_on_cost, 2) if div_on_cost is not None
                                                                       else 0), 2) if gp is not None else None)
    kes = f'≈ KES {t["market_value_kes"]:,.0f} · ' if t.get("market_value_kes") is not None else ""
    kpis = [
        ui.kpi("Market Value Today", f"${t['market_value']:,.0f}", big=True, private=True,
               sub=f"{kes}As of {ips.get('as_of', '')} · {t['n_available']} of {t['n_holdings']} holding(s) priced "
                   f"today · all figures in USD."),
        ui.kpi("Total Cost Basis", f"${t['cost_basis']:,.0f}", private=True, term="cost-basis"),
        ui.kpi("Total Gain / Loss", f"{'-' if (t['gain'] or 0) < 0 else '+'}${abs(t['gain']):,.0f}",
               tone=tone(t["gain"]), private=True, term="gain-loss",
               delta_html=ui.delta(gp, fmt="{:+.1f}%") if gp is not None else None),
        ui.kpi("Today", pct(dp, 2), tone=tone(dp), term="day-change",
               sub=(f'{"-" if t["day_change_value"] < 0 else "+"}${abs(t["day_change_value"]):,.0f}'
                    if t.get("day_change_value") is not None else None)),
        ui.kpi("Est. Annual Dividend", f"${t['est_annual_dividend']:,.0f}", private=True, term="est-dividend",
               sub=(f"{div_on_cost:.2f}% yield on what you paid" if div_on_cost is not None else None)),
        ui.kpi("Total Return incl. Dividend", f"{total_return:+.1f}%" if total_return is not None else "—",
               tone=tone(total_return), term="total-return"),
    ]
    best, worst = ips.get("best"), ips.get("worst")
    if best and worst and best is not worst:
        kpis.append(ui.kpi("Best / worst", f"{best['symbol']} {pct(best['gain_pct'])}",
                           sub=f"weakest: {worst['symbol']} {pct(worst['gain_pct'])}", tone=tone(best["gain_pct"])))
    parts.append(ui.kpi_row(kpis))
    parts.append(Markup('<p class="footnote">"Total Return incl. Dividend" = your unrealized price gain % + the current '
                        'annualized dividend yield on your cost — an indicative estimate, not a record of dividends '
                        'actually received.</p>'))
    if tracker is not None:
        parts.append(ui.section(_lookbacks(tracker, t["market_value"], "USD"), title="Performance Over Time",
                                sub="Computed from real snapshots recorded each day you run this tool.",
                                icon="trend-up", term="lookback"))

    value = _value_chart("c-pf-intl-value", history_rows, t.get("cost_basis"), "USD",
                         "International portfolio value over time")
    weights = _weights(holdings)
    alloc = ips.get("sector_allocation") or {}
    sector_donut = sc.donut("c-pf-intl-sectors", [(k, v, f"c{i % 7 + 1}") for i, (k, v) in
                                                  enumerate(sorted(alloc.items(), key=lambda x: -x[1]))],
                            title="International by sector", fmt=sc.Fmt("$", 0),
                            center_value=sc._short_num(sum(alloc.values())) if alloc else "", center_label="by sector")
    gl = sc.hbars([{"label": h["symbol"], "value": h["gain_pct"], "href": ui.href(h.get("report_file") or "") or None}
                   for h in sorted(holdings, key=lambda x: x.get("gain_pct") or 0, reverse=True)
                   if h.get("data_available") and h.get("gain_pct") is not None],
                  fmt=sc.Fmt(suffix="%", decimals=1, sign=True), diverging=True)
    wt = sc.hbars([{"label": s, "value": w} for s, w in sorted(weights.items(), key=lambda x: -x[1])],
                  fmt=sc.Fmt(suffix="%", decimals=1), tone_from_sign=False)
    charts = [ui.card(value if value else Markup('<p class="muted small">📈 Value chart will appear once you\'ve run '
                                                 'this tool on at least 2 different days.</p>'),
                      title="International portfolio value over time", icon="trend-up"),
              Markup('<div class="grid-3">' + str(ui.card(sector_donut, title="By sector", term="allocation"))
                     + str(ui.card(gl, title="Unrealized gain / loss by holding", sub="% since your average cost"))
                     + str(ui.card(wt, title="Portfolio weight by holding",
                                   sub="% of total international market value", term="weight")) + "</div>")]
    parts.append(ui.section(_join(charts), title="International Portfolio Charts", sec_id="intl-charts", icon="bar"))

    usd2 = sc.Fmt("$", 2)
    rows, attrs = [], []
    for h in sorted(holdings, key=lambda x: (x["market_value"] or -1), reverse=True):
        tip = rg._intl_holding_tip(h)
        sym = ui.cell(_sym_cell(rg, h, international=True), sort=h["symbol"])
        if not h["data_available"]:
            rows.append([sym, ui.cell(qty(h["quantity"]), sort=h["quantity"]),
                         ui.cell(f'{h["avg_cost"]:.2f}', sort=h["avg_cost"]),
                         ui.cell("No live data today — see warning above", tone="down")]
                        + [ui.cell("—") for _ in range(8)])
            attrs.append(f'data-tip="{tip}" class="row-nodata"')
            continue
        w = weights.get(h["symbol"])
        rows.append([
            sym, ui.cell(qty(h["quantity"]), sort=h["quantity"]), ui.cell(f'{h["avg_cost"]:.2f}', sort=h["avg_cost"]),
            ui.cell(f'{h["price"]:.2f}', sort=h["price"]),
            ui.cell(f'{h["market_value"]:,.0f}', sort=h["market_value"], cls="private"),
            ui.cell(Markup(f'{ui.weight_bar(w, tone="intl")} <span class="num">{w:.1f}%</span>') if w else "—",
                    sort=round(w, 2) if w else None),
            ui.cell(f'{h["gain"]:+,.0f}', sort=h["gain"], tone=tone(h["gain"]), cls="private"),
            ui.cell(f'{h["gain_pct"]:+.1f}%', sort=h["gain_pct"], tone=tone(h["gain_pct"])),
            ui.cell(f'{h["day_change_pct"]:+.2f}%' if h.get("day_change_pct") is not None else "—",
                    sort=h.get("day_change_pct"), tone=tone(h.get("day_change_pct"))),
            ui.cell(_spark(intl_history, h["symbol"], usd2)),
            ui.cell(_signal_pill(h["recommendation_label"], h["recommendation_class"]), sort=h["recommendation_label"]),
            ui.cell(_score_chip(h.get("score")), sort=h.get("score")),
        ])
        attrs.append(f'data-tip="{tip}"')
    cols = [ui.Col("Symbol", sort="text"), ui.Col("Qty", sort="number"), ui.Col("Avg Cost", sort="number"),
            ui.Col("Price", sort="number"), ui.Col("Value ($)", sort="number"), ui.Col("Weight", sort="number", term="weight"),
            ui.Col("Gain ($)", sort="number"), ui.Col("Gain %", sort="number"), ui.Col("Today", sort="number"),
            ui.Col("6 months", sort=None),
            ui.Col("Rating", sort="signal", term="analyst-target", title="Wall Street's real analyst consensus"),
            ui.Col("Score", sort="number", term="factor-score",
                   title="0-100 transparent factor screen — hover for the breakdown")]
    parts.append(ui.section(ui.table(cols, rows, table_id="t-intl-holdings", row_attrs=attrs),
                            title="Your International Holdings", sec_id="intl-holdings", icon="globe",
                            sub="💡 Click any column header to sort. Hover a row for the full breakdown. \"Rating\" is "
                                "Wall Street's real analyst consensus for that stock — not your personal return (that's "
                                "the \"Gain %\" column)."))

    div_rows = []
    for h in sorted(holdings, key=lambda x: (x.get("est_annual_dividend") or 0), reverse=True):
        if not h["data_available"] or not h.get("dividend_rate"):
            continue
        div_rows.append([ui.cell(_sym_cell(rg, h, international=True, name=False), sort=h["symbol"]),
                         ui.cell(f'${h["dividend_rate"]:.2f}/share/yr', sort=h["dividend_rate"]),
                         ui.cell(f'{h["dividend_yield"]:.2f}%' if h.get("dividend_yield") is not None else "—",
                                 sort=h.get("dividend_yield")),
                         ui.cell(f'${(h.get("est_annual_dividend") or 0):,.0f}/yr', sort=h.get("est_annual_dividend"),
                                 cls="private")])
    if div_rows:
        cols = [ui.Col("Symbol", sort="text"), ui.Col("Per Share (annualized)", sort="number"),
                ui.Col("Yield", sort="number"), ui.Col("Your Est. Income", sort="number")]
        parts.append(ui.details("💵 Dividends on Your International Holdings", Markup(
            "<p class=\"section-sub\">The current annualized dividend rate per share, and what it's worth on your "
            "position.</p>") + ui.table(cols, div_rows, table_id="t-intl-div"), det_id="intl-dividends"))
    parts.append(ui.details("➕ Add a New International Position", Markup(
        '<p>Every time you buy — even more of a stock you already hold — add one row to '
        '<code>portfolio/international_holdings.csv</code> (columns <code>symbol, quantity, buy_price, buy_date, '
        'note</code>; <code>buy_price</code> in USD) in your spreadsheet app, save it as CSV, and re-run '
        '<code>./run.sh</code>. Or let a helper script add the row for you (it writes to '
        '<code>international_holdings.csv</code> if you have one):</p>'
        '<pre>./venv/bin/python3 add_international_holding.py SYMBOL QUANTITY PRICE_USD [DATE]\n'
        './venv/bin/python3 add_international_holding.py AAPL 10 190.25 2026-09-20</pre>'
        '<p class="footnote">Multiple purchases of the same stock combine automatically into one row with a correctly '
        'weighted average cost. Full details in portfolio/README.md. Nothing here is ever committed to git.</p>'),
        cls="no-app-only"))
    return _join(parts)


# ------------------------------------------------------------------ News tab
def _news_list(items):
    out = []
    for n in items:
        link = ui.href(n.get("url")) if str(n.get("url") or "").lower().startswith(("http://", "https://")) else ""
        title = escape(n.get("title") or "")
        head = Markup(f'<a href="{link}" target="_blank" rel="noopener">{title}</a>') if link else title
        meta = " · ".join(x for x in (n.get("source") or "", (n.get("published_utc") or "")) if x)
        out.append(f'<li><span class="pill">{escape(n.get("symbol") or "")}</span><div>{head}'
                   f'<small>📅 {escape(meta)}</small></div></li>')
    return Markup(f'<ul class="news-list">{"".join(out)}</ul>')


def _news_tab(ps, ips, news, intl_news):
    parts = []
    if ps:
        if news:
            parts.append(ui.section(Markup(
                '<p class="section-sub">Headlines from the <strong>last 7 days</strong> mentioning companies you hold, '
                'newest first. <strong>Shown neutral, on purpose</strong> — reliable automatic positive/negative '
                "tagging of financial headlines isn't possible with free tools (it's wrong often enough to be "
                'dangerous with real money); read the headline and judge for yourself. The "Today" column in your '
                'holdings table and the TV Signal are the closest real, verified signals this dashboard can offer.</p>')
                + _news_list(news[:24]), title=f"News on Your Holdings ({len(news)} in last 7 days)",
                sec_id="stocks-news", icon="news"))
        else:
            parts.append(ui.section(Markup(
                '<p class="muted">No headlines from the last 7 days for these companies (source may be temporarily '
                'unavailable, or nothing recent was published). Try again next run.</p>'),
                title="News on Your Holdings", sec_id="stocks-news", icon="news"))
    if ips:
        if intl_news:
            parts.append(ui.section(Markup(
                '<p class="section-sub">Headlines mentioning companies you hold, newest first. <strong>Shown neutral, '
                'on purpose</strong> — read the headline and judge for yourself. The "Today" column and analyst '
                'Rating are the closest real, verified signals this dashboard can offer.</p>')
                + _news_list(intl_news[:24]), title=f"News on Your International Holdings ({len(intl_news)} recent)",
                sec_id="intl-news", icon="globe"))
        else:
            parts.append(ui.section(Markup('<p class="muted">No recent headlines found for these companies. Try again '
                                           'next run.</p>'), title="News on Your International Holdings",
                                    sec_id="intl-news", icon="globe"))
    if not parts:
        parts.append(ui.empty_state("No news yet", "Headlines about the companies you hold appear here once you've "
                                    "added holdings.", icon="news"))
    return _join(parts)


# ------------------------------------------------------------------ the page
def build(rg, *, portfolio_summary=None, bond_portfolio=None, intl_portfolio_summary=None, usd_kes=None,
          portfolio_history=None, portfolio_news=None, portfolio_history_tracker=None,
          intl_portfolio_history=None, intl_portfolio_news=None, intl_portfolio_history_tracker=None,
          fundamentals=None, intl_fundamentals=None, nse_history=None, intl_history=None, today=None):
    """The whole My Portfolio page body (Markup)."""
    today = today or dt.date.today()
    nw = networth_summary(portfolio_summary, bond_portfolio, intl_portfolio_summary, usd_kes)
    ps, bp, ips = portfolio_summary, bond_portfolio, intl_portfolio_summary
    n_news = len(portfolio_news or []) + len(intl_portfolio_news or [])
    head = Markup(
        '<div class="banner no-app-only" data-tone="info" role="note">➕ <b>Recording a purchase is easiest with the '
        'dashboard app:</b> double-click <b>Open Dashboard.command</b> in the project folder (or run '
        '<code>./venv/bin/python3 app.py</code>) and this page gets a <b>Record a purchase</b> form — search for the '
        'stock, type what you paid, done. You can still add rows to the CSV files described below.</div>'
        '<div class="page-actions app-only">'
        '<button type="button" class="icon-btn primary" data-toggle-panel="purchase-panel" aria-expanded="false">'
        f'{ui.icon_svg("plus")}<span>Record a purchase</span></button>'
        '<button type="button" class="icon-btn" data-toggle-panel="entries-panel" aria-expanded="false">'
        f'{ui.icon_svg("news")}<span>Your entries</span></button></div>'
        '<div id="purchase-panel" class="section app-only toggle-panel card card-pad" hidden></div>'
        '<div id="entries-panel" class="section app-only toggle-panel card card-pad" hidden></div>')
    tabs = ui.tabs("portfolio", [
        ("summary", "Summary", _summary_tab(rg, nw, portfolio_summary=ps, bond_portfolio=bp,
                                            intl_portfolio_summary=ips, fundamentals=fundamentals,
                                            intl_fundamentals=intl_fundamentals, nse_history=nse_history,
                                            intl_history=intl_history, today=today), None),
        ("kenyan", "Kenyan stocks", _kenyan_tab(rg, ps, history_rows=portfolio_history,
                                                tracker=portfolio_history_tracker, nse_history=nse_history),
         len(ps["holdings"]) if ps else None),
        ("bonds", "Bonds", _bonds_tab(rg, bp), len(bp["bonds"]) if bp else None),
        ("international", "International", _intl_tab(rg, ips, history_rows=intl_portfolio_history,
                                                     tracker=intl_portfolio_history_tracker,
                                                     intl_history=intl_history),
         len(ips["holdings"]) if ips else None),
        ("news", "News", _news_tab(ps, ips, portfolio_news or [], intl_portfolio_news or []), n_news or None),
    ], label="Portfolio sections")
    return head + tabs
