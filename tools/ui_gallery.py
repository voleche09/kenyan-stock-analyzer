#!/usr/bin/env python3
"""
Build gallery.html: every dashboard building block on one page — headline
figures, marks, tabs, tables, every chart type — with awkward edge cases
(missing data, a single point, flat lines, gaps, hostile text, huge numbers).
For reviewing the design in light/dark, phone/desktop and print.

  python tools/ui_gallery.py OUT_DIR [--inputs RECORDING_DIR]

With --inputs (a tools/factcheck.py recording) the stock-chart prototype
uses a real NSE stock's price history; otherwise synthetic prices. Only
public market data and made-up figures appear.
"""

import argparse
import os
import pickle
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src")]

import numpy as np                    # noqa: E402
import pandas as pd                   # noqa: E402
from markupsafe import Markup         # noqa: E402

import svg_charts as sc               # noqa: E402
import ui_kit as ui                   # noqa: E402
import ui_theme                       # noqa: E402

HOSTILE = '<img src=x onerror="alert(1)">'


def synthetic(n=180, seed=3, start=45.0):
    from analysis_engine import AnalysisEngine
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp("2026-10-05"), periods=n, freq="B")
    close = start * (1 + np.cumsum(rng.normal(0.0008, 0.013, n)))
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                       "volume": rng.integers(20_000, 2_000_000, n)}, index=dates)
    return AnalysisEngine().analyze_stock(df)["data"]


def recorded_stock(inputs_dir, symbol="EQTY"):
    with open(os.path.join(inputs_dir, "inputs.pkl"), "rb") as f:
        rec = pickle.load(f)
    for blob in rec["calls"]:
        name, a, _k = pickle.loads(blob)
        if name == "generate_stock_report" and a[0] == symbol:
            return a[1]["data"]
    return None


def logo(sym, color):
    return Markup(f'<span class="ticker-logo-fallback" style="background:{color}">{sym[:2]}</span>')


def build(out_dir, inputs=None):
    df = recorded_stock(inputs) if inputs else None
    real = df is not None
    if not real:
        df = synthetic()
    money = sc.Fmt("KES ", 2)
    last = float(df["close"].iloc[-1])

    # ---- headline figures
    kpis = ui.kpi_row([
        ui.kpi("Net worth", "KES 1,284,560", big=True, private=True, term="net-worth",
               delta_html=ui.delta(0.84, private=True), sub="≈ $9,919 · stocks, bonds and international"),
        ui.kpi("Today", "+KES 10,690", tone="up", private=True, term="day-change",
               delta_html=ui.delta(0.84), sub="3 up · 2 down · bonds don't move daily"),
        ui.kpi("Total gain", "+KES 214,300", tone="up", private=True, term="gain-loss",
               delta_html=ui.delta(20.0, fmt="{:+.1f}%"), sub="vs KES 1,070,260 you put in"),
        ui.kpi("Bond coupons a year (after tax)", "KES 96,410", private=True, term="coupon",
               sub="next: KES 22,410 on 9 Nov 2026"),
        ui.kpi("Dividends a year (before tax)", "KES 18,520", private=True, term="est-dividend"),
        ui.kpi("No data case", "—", sub="never a guessed number"),
    ])

    marks = ui.card(Markup(
        '<div class="row">' + "".join(str(x) for x in (
            ui.delta(2.35), ui.delta(-1.1), ui.delta(0.0), ui.delta(None),
            ui.pill("Strong buy", "up"), ui.pill("Neutral"), ui.pill("Sell", "down"), ui.pill("Unverified", "warn"),
            ui.pill("Bonds", "info"))) + "</div>"
        + '<p class="small" style="margin-top:12px">Every term has an explanation: P/E ' + str(ui.info("pe"))
        + " · RSI " + str(ui.info("rsi")) + " · Bollinger bands " + str(ui.info("bollinger"))
        + " · small-holder rule " + str(ui.info("small-holder-rule")) + "</p>"
        + str(ui.banner("Prices are the NSE's official close; bonds use their indicative value.", tone="info"))
        + str(ui.banner("FX rate unavailable this run — international holdings are shown in USD only.", tone="warn",
                        title="Heads up:"))
        + str(ui.banner("No reference terms for FXD9/2099/001 — left out of the totals, not guessed.", tone="danger"))
        + str(ui.banner("All 4 price checks passed.", tone="ok"))), title="Marks, pills and notices", icon="flag")

    # ---- table
    rows, rng = [], np.random.default_rng(7)
    syms = [("EQTY", "Equity Group Holdings", "#7c2d12"), ("EABL", "East African Breweries", "#b45309"),
            ("ABSA", "Absa Bank Kenya", "#be123c"), ("BKG", "BK Group", "#0369a1"),
            ("CARB", "Carbacid Investments", "#15803d"), ("BAT", "BAT Kenya", "#334155"),
            ("JUB", "Jubilee Holdings", "#7e22ce"), ("NMG", "Nation Media Group", "#0f766e"),
            ("TOTL", "TotalEnergies Marketing Kenya", "#b91c1c"), ("SASN", "Sasini", "#4d7c0f"),
            ("XSHN", "A company with a very long name that has to wrap " + HOSTILE, "#475569"),
            ("ZZZZ", "No data today", "#64748b")]
    for i, (sym, name, color) in enumerate(syms):
        if sym == "ZZZZ":
            rows.append([ui.cell(Markup(f'<span class="sym">{logo(sym, color)}<span>{sym}<small>{name}</small></span></span>'),
                                 sort=sym), ui.cell("—"), ui.cell(ui.delta(None)), ui.cell(sc.sparkline([], label=sym)),
                         ui.cell("—"), ui.cell(sc.ring(None, size=30))])
            continue
        hist = 20 * (1 + np.cumsum(rng.normal(0, 0.02, 60)))
        chg = float((hist[-1] / hist[-2] - 1) * 100)
        w = float(rng.uniform(2, 30))
        rows.append([
            ui.cell(Markup(f'<span class="sym">{logo(sym, color)}<span>{ui.esc(sym)}<small>{ui.esc(name)}</small></span></span>'),
                    sort=sym),
            ui.cell(money(hist[-1]), sort=round(float(hist[-1]), 2)),
            ui.cell(ui.delta(chg), sort=round(chg, 2), tone=ui.tone_of(chg)),
            ui.cell(sc.sparkline(hist, label=f"{sym} 3 months", fmt=money)),
            ui.cell(Markup(f'{ui.weight_bar(w)} <span class="num">{w:.1f}%</span>'), sort=round(w, 1)),
            ui.cell(sc.ring(rng.integers(20, 95), size=30, label="Factor score"), sort=int(rng.integers(20, 95))),
        ])
    cols = [ui.Col("Stock", sort="text"), ui.Col("Price", sort="number"), ui.Col("Today", sort="number"),
            ui.Col("3 months", sort=None), ui.Col("Weight", sort="number", term="weight"),
            ui.Col("Score", sort="number", align="center", term="factor-score")]
    table = ui.table(cols, rows, table_id="demo-table", filter_placeholder="Filter stocks…", show_first=6)

    # ---- the stock-chart prototype
    refs = [(round(last * 0.93, 2), "buy", "Your buy price"), (round(last * 1.6, 2), "sell", "Sell above")]
    price = sc.price_chart("c-demo-price", df, money=money, refs=refs,
                           title=("EQTY — real NSE prices" if real else "Price (synthetic)"),
                           head_extra=ui.info("sma"))
    small = sc.indicator_charts("c-demo", df, money=money, x_ref="c-demo-price",
                                extras={"rsi": ui.info("rsi"), "macd": ui.info("macd"),
                                        "stoch": ui.info("stochastic"), "atr": ui.info("atr"),
                                        "volume": ui.info("volume")})
    charts = ui.card(Markup(
        f'{price}<div class="grid-2" style="margin-top:8px">{small["volume"]}{small["rsi"]}{small["macd"]}'
        f'{small["stoch"]}{small["atr"]}'
        + str(ui.range_bar(float(df["close"].min()), float(df["close"].max()), last,
                           marks=[(last * 0.93, "Your buy price", "buy"), (last * 0.98, "Your average cost", "cost")],
                           fmt=money, label="Range over the chart"))
        + '</div>'), title="Price & indicators", icon="activity",
        sub="Hover or touch a chart to read exact values; use the arrow keys when it's focused.")

    alloc = sc.donut("c-demo-alloc", [("Kenyan stocks", 412000, "stocks"), ("Bonds", 655000, "bonds"),
                                      ("International", 217560, "intl")],
                     title="Allocation", center_value="KES 1.28M", center_label="net worth",
                     fmt=sc.Fmt("KES ", 0))
    gains = sc.hbars([{"label": "EQTY", "value": 23.4, "sub": "+KES 9,410"},
                      {"label": "ABSA", "value": 12.1}, {"label": "CARB", "value": -8.6},
                      {"label": "EABL", "value": -21.3, "href": "#demo-table"},
                      {"label": HOSTILE, "value": 0.0}],
                     fmt=sc.Fmt(suffix="%", decimals=1, sign=True), diverging=True)
    sectors = sc.hbars([{"label": "Banking", "value": 1.42}, {"label": "Telecommunication", "value": -0.55},
                        {"label": "Investment", "value": -0.83}, {"label": "Energy", "value": -0.9},
                        {"label": "Manufacturing & Allied", "value": 0.31}],
                       fmt=sc.Fmt(suffix="%", decimals=2, sign=True), diverging=True)
    small_row = Markup(
        '<div class="grid-3">'
        + str(ui.card(alloc, title="Donut", sub="A single 100% slice works too:"))
        + str(ui.card(gains, title="Gain / loss by holding", sub="Diverging bars from a centre line"))
        + str(ui.card(sectors, title="Sector moves today", term="breadth"))
        + "</div>")

    # ---- edge cases
    edge = Markup(
        '<div class="grid-3">'
        + str(ui.card(Markup(
            f'<p>One point: {sc.sparkline([5.0], label="one")}</p>'
            f'<p>Flat: {sc.sparkline([3.0] * 30, label="flat")}</p>'
            f'<p>Gaps: {sc.sparkline([1, 2, None, None, 3, float("nan"), 2, 4], label="gaps")}</p>'
            f'<p>Falling: {sc.sparkline(np.linspace(10, 4, 40), label="falling")}</p>'
            f'<p>Far-away target left out: {sc.sparkline(np.linspace(10, 11, 40), label="t", refs=[(30, "sell")])}</p>'),
            title="Sparklines"))
        + str(ui.card(sc.donut("c-one", [("Bonds", 1, "bonds")], title="All bonds", center_value="100%",
                               center_label="bonds"), title="One slice"))
        + str(ui.card(Markup(
            str(sc.chart("c-empty", [], [sc.Series("a", "Nothing", [])], title="An empty chart"))
            + str(sc.chart("c-neg", pd.date_range("2026-01-01", periods=30, freq="B"),
                           [sc.Series("macd", "Below zero", np.linspace(-5, -1, 30), fmt=sc.Fmt(decimals=2, sign=True))],
                           title="All-negative values", include_zero=True, height=120, small=True))),
            title="Charts with odd data"))
        + "</div>"
        + str(ui.kpi_row([ui.kpi(HOSTILE, HOSTILE, sub=HOSTILE),
                          ui.kpi("A huge number", "KES 98,765,432,109"),
                          ui.kpi("Rounds to zero", sc.Fmt("KES ", 2)(-0.001))]))
        + str(ui.empty_state("Your watchlist is empty", "Search for a stock above and click Add.", icon="star")))

    tabs = ui.tabs("gallery", [
        ("overview", "Overview", Markup(str(kpis) + str(marks) + str(small_row)), None),
        ("charts", "Charts", charts, 6),
        ("table", "Table", ui.section(table, title="A sortable table", sec_id="table-demo",
                                      sub="Click a column header to sort; the filter box searches every column."), 12),
        ("edge", "Edge cases", edge, None),
    ])
    body = Markup('<p class="page-intro">Every building block of the redesigned dashboard on one page. '
                  'Made-up figures; the price chart uses public market data.</p>') + tabs
    html = ui_theme.page(title="Design gallery", active_file="index.html",
                         subtitle="Design review page · not part of the dashboard", body=body, private=True)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "gallery.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {path} ({len(html) / 1024:.0f} KB)")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir")
    ap.add_argument("--inputs")
    a = ap.parse_args()
    build(a.out_dir, a.inputs)
