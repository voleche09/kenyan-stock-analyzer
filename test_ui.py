#!/usr/bin/env python3
"""
Tests for the design system: svg_charts, ui_kit, glossary and ui_theme —
valid SVG with no NaN/inf, escaping everywhere, the awkward data every chart
must survive, the hooks the local app needs, and WCAG colour contrast.

Runs with the rest of the suite (python test.py), or on its own:
    ./venv/bin/python3 -m unittest test_ui -v
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import unittest
import xml.dom.minidom

import numpy as np
import pandas as pd
from markupsafe import Markup

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import glossary          # noqa: E402
import svg_charts as sc  # noqa: E402
import ui_kit as ui      # noqa: E402
import ui_theme          # noqa: E402

HOSTILE = '<img src=x onerror="alert(1)">'


def svgs_of(html):
    return re.findall(r"<svg[\s\S]*?</svg>", str(html))


def chart_json(html):
    return [json.loads(m) for m in re.findall(r'<script type="application/json" class="chart-data">(.*?)</script>',
                                               str(html))]


class _Checks(unittest.TestCase):
    def assertSound(self, html):
        """Valid SVG, no NaN/inf anywhere, parseable chart data, no raw hostile markup."""
        html = str(html)
        for s in svgs_of(html):
            xml.dom.minidom.parseString(s)                       # raises on invalid XML
        self.assertIsNone(re.search(r"\b(nan|NaN|inf|Infinity)\b", html))
        for data in chart_json(html):
            self.assertIn("s", data)
        self.assertNotIn(HOSTILE, html)


def price_frame(n=180, seed=3, start=45.0, warmup=True):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-02", periods=n, freq="B")
    close = start * (1 + np.cumsum(rng.normal(0, 0.012, n)))
    df = pd.DataFrame({"close": close, "volume": rng.integers(10_000, 900_000, n).astype(float)}, index=dates)
    df["sma_20"] = df["close"].rolling(20).mean()          # NaN warm-up, like the real indicators
    df["sma_50"] = df["close"].rolling(50).mean()
    df["bb_upper"] = df["sma_20"] + 2 * df["close"].rolling(20).std()
    df["bb_lower"] = df["sma_20"] - 2 * df["close"].rolling(20).std()
    df["volume_sma_20"] = df["volume"].rolling(20).mean()
    df["rsi"] = 50 + rng.normal(0, 12, n)
    df["macd"] = rng.normal(0, 1, n)
    df["macd_signal"] = df["macd"].rolling(9).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["stoch_k"] = np.clip(50 + rng.normal(0, 25, n), 0, 100)
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()
    df["atr"] = 1 + rng.random(n)
    if not warmup:
        df = df.fillna(0)
    return df


class TestNumbers(unittest.TestCase):
    def test_nice_ticks(self):
        self.assertEqual(sc.nice_ticks(97.6, 109.5), ([95, 100, 105, 110], 0))
        self.assertEqual(sc.nice_ticks(0, 100)[0], [0, 25, 50, 75, 100])
        ticks, dec = sc.nice_ticks(0.0012, 0.0019)
        self.assertEqual(dec, 4)
        self.assertTrue(ticks[0] <= 0.0012 and ticks[-1] >= 0.0019)
        flat, _d = sc.nice_ticks(5, 5)                           # flat line still gets a range
        self.assertLess(flat[0], 5)
        self.assertGreater(flat[-1], 5)
        neg, _d = sc.nice_ticks(-3.2, -0.4)
        self.assertTrue(neg[0] <= -3.2 and neg[-1] >= -0.4)
        self.assertEqual(sc.nice_ticks(float("nan"), 3), ([], 0))

    def test_fmt_reads_like_the_tables(self):
        self.assertEqual(sc.Fmt("KES ", 2)(1234.5), "KES 1,234.50")
        self.assertEqual(sc.Fmt("KES ", 2)(-1234.5), "KES −1,234.50")
        self.assertEqual(sc.Fmt(suffix="%", decimals=1, sign=True)(2.45), "+2.5%")
        self.assertEqual(sc.Fmt(suffix="%", decimals=1, sign=True)(-0.04), "0.0%")   # no "−0.0%"
        self.assertEqual(sc.Fmt(decimals=0, short=True)(1_234_567), "1.2M")
        self.assertEqual(sc.Fmt()(None), "—")
        self.assertEqual(sc.Fmt()(float("nan")), "—")

    def test_clean_turns_bad_values_into_gaps(self):
        self.assertEqual(sc.clean([1, None, float("nan"), float("inf"), "x", 2.5, True]),
                         [1.0, None, None, None, None, 2.5, None])


class TestCharts(_Checks):
    def test_price_chart_and_indicators_survive_real_shaped_data(self):
        df = price_frame()
        main = sc.price_chart("c-t-price", df, money=sc.Fmt("KES ", 2),
                              refs=[(float(df["close"].iloc[-1]) * 0.97, "buy", "Buy below"),
                                    (10_000.0, "sell", "Sell " + HOSTILE)])
        self.assertSound(main)
        default = str(main).split("data-default>", 1)[1].split("<script", 1)[0]
        self.assertIn("too far from the price to fit: Sell &lt;img", default)   # the far-away target, in words
        self.assertNotIn("Buy below", default.split("fit:", 1)[1])              # …but not a line that is drawn
        self.assertIn("Sell &lt;img", main)
        small = sc.indicator_charts("c-t", df, money=sc.Fmt("KES ", 2), x_ref="c-t-price")
        self.assertEqual(set(small), {"volume", "rsi", "macd", "stoch", "atr"})
        for html in small.values():
            self.assertSound(html)
            self.assertEqual(chart_json(html)[0].get("xref"), "c-t-price")
        self.assertLess(len(main) + sum(len(v) for v in small.values()), 110_000)

    def test_views_share_their_values_and_only_the_full_history_is_visible(self):
        df = price_frame()
        html = str(sc.price_chart("c-v", df, money=sc.Fmt("KES ", 2)))
        views = re.findall(r'<div class="chart-variant" data-tf="([^"]+)"( hidden)?( data-default)?', html)
        self.assertEqual([v[0] for v in views], ["1M", "3M", "6M", "8M"])
        self.assertEqual([bool(v[1]) for v in views], [True, True, True, False])   # only the last shows
        self.assertTrue(views[-1][2])
        data = chart_json(html)
        self.assertEqual(len(data), 1)                            # one copy of the values for all views
        self.assertEqual(len(data[0]["x"]), len(df))
        offsets = [int(o) for o in re.findall(r'data-o="(\d+)"', html)]
        self.assertEqual(offsets, [len(df) - 21, len(df) - 63, len(df) - 126, 0])
        self.assertEqual(data[0]["s"][0]["v"][-1], round(float(df["close"].iloc[-1]), 2))

    def test_odd_data(self):
        dates = pd.date_range("2026-01-02", periods=30, freq="B")
        cases = {
            "empty": ([], [sc.Series("a", "A", [])]),
            "one point": (dates[:1], [sc.Series("a", "A", [3.0])]),
            "all missing": (dates, [sc.Series("a", "A", [None] * 30)]),
            "flat": (dates, [sc.Series("a", "A", [7.0] * 30)]),
            "gaps": (dates, [sc.Series("a", "A", [1, 2, None, None, 3, float("nan")] * 5)]),
            "negative": (dates, [sc.Series("a", "A", np.linspace(-5, -1, 30))]),
            "hostile label": (dates, [sc.Series("a", HOSTILE, np.linspace(1, 2, 30))]),
        }
        for name, (x, series) in cases.items():
            with self.subTest(name):
                html = sc.chart("c-" + name, x, series, title=HOSTILE, include_zero=True)
                self.assertSound(html)
        gaps = str(sc.chart("c-g", dates, cases["gaps"][1], title="g"))
        path = re.search(r'class="ln s-a" d="([^"]*)"', gaps).group(1)
        self.assertGreater(path.count("M"), 1)                    # the pen lifts over the gaps

    def test_fixed_scale_and_reference_lines(self):
        dates = pd.date_range("2026-01-02", periods=30, freq="B")
        html = str(sc.chart("c-rsi", dates, [sc.Series("rsi", "RSI", np.linspace(40, 60, 30))], title="RSI",
                            y_range=(0, 100), ref_lines=[(70, "sell", "70"), (30, "buy", "30")]))
        self.assertIn('data-lo="0" data-hi="100"', html)
        self.assertEqual(html.count('class="ref '), 2)

    def test_sparkline(self):
        self.assertIn("spark-empty", sc.sparkline([1.0], label="x"))
        self.assertIn("spark-empty", sc.sparkline([], label="x"))
        up = sc.sparkline(np.linspace(1, 2, 20), label=HOSTILE)
        self.assertIn('data-tone="up"', up)
        self.assertNotIn(HOSTILE, up)
        self.assertNotIn("spark-area", sc.sparkline([1, None, 2, 3], label="g"))   # no shading across a gap
        far = sc.sparkline(np.linspace(10, 11, 20), label="t", refs=[(30, "sell"), (10.5, "buy")])
        self.assertEqual(str(far).count('class="ref '), 1)       # the far-away one is left out

    def test_donut_ring_and_bars(self):
        one = sc.donut("c-one", [("Bonds", 5, "bonds")], title="t")
        self.assertIn('stroke-dasharray="100.000 0.000"', one)
        self.assertIn("chart-empty", sc.donut("c-zero", [("A", 0, "c1")], title="t"))
        self.assertSound(sc.donut("c-h", [(HOSTILE, 1, "c1"), ("B", 3, "c2")], title=HOSTILE))
        self.assertIn('aria-label="No score"', sc.ring(None))
        self.assertIn(">100<", sc.ring(140))                     # clamped to the maximum
        bars = str(sc.hbars([{"label": HOSTILE, "value": -5, "href": "javascript:alert(1)"},
                             {"label": "EQTY", "value": 10, "href": "EQTY_report.html"}], diverging=True))
        self.assertNotIn("javascript:", bars)
        self.assertIn('href="EQTY_report.html"', bars)
        self.assertIn('left:25.00%;width:25.00%', bars)          # −5 of a ±10 scale, left of centre


class TestUiKit(_Checks):
    def test_escaping_and_markup(self):
        self.assertEqual(str(ui.esc(HOSTILE)), "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;")
        self.assertEqual(str(ui.esc(Markup("<b>ok</b>"))), "<b>ok</b>")
        self.assertEqual(str(ui.esc(None)), "")
        k = str(ui.kpi(HOSTILE, HOSTILE, sub=HOSTILE))
        self.assertNotIn(HOSTILE, k)
        self.assertIn('data-kpi="&lt;img', k)

    def test_links(self):
        for bad in ("javascript:alert(1)", " JavaScript:x", "data:text/html,x", "vbscript:x", "//evil.example"):
            self.assertEqual(str(ui.href(bad)), "", bad)
        for good in ("https://example.com/a?b=1&c=2", "#bonds", "portfolio.html#bonds", "EQTY_report_x.html"):
            self.assertNotEqual(str(ui.href(good)), "", good)
        self.assertEqual(str(ui.href("https://e.x/?a=1&b=2")), "https://e.x/?a=1&amp;b=2")

    def test_delta_never_relies_on_colour_alone(self):
        self.assertIn("▲", ui.delta(1.5))
        self.assertIn("▼", ui.delta(-1.5))
        self.assertIn("−1.50%", ui.delta(-1.5))
        self.assertIn(">0.00%", ui.delta(0.0))
        self.assertIn("—", ui.delta(None))

    def test_table(self):
        cols = [ui.Col("Stock", sort="text"), ui.Col("Price", sort="number")]
        rows = [[ui.cell(HOSTILE, sort="z"), ui.cell("KES 1.00", sort=1.0, tone="up")] for _ in range(8)]
        html = str(ui.table(cols, rows, table_id="t1", filter_placeholder="Filter", show_first=5))
        self.assertNotIn(HOSTILE, html)
        self.assertIn('data-sort="1.0"', html)
        self.assertIn('data-show-first="5"', html)
        self.assertIn('data-filter-for="t1"', html)
        self.assertIn("Show all 8", html)
        self.assertIn("empty", str(ui.table(cols, [], empty="Nothing yet")))

    def test_tabs_keep_every_panel_in_the_page(self):
        html = str(ui.tabs("p", [("summary", "Summary", Markup("<p id='x1'>one</p>"), None),
                                 ("bonds", "Bonds", Markup("<p>two</p>"), 3)]))
        self.assertEqual(html.count('class="tab-panel"'), 2)
        self.assertIn('href="#bonds"', html)
        self.assertIn('<span class="tab-count">3</span>', html)
        self.assertIn('aria-selected="true"', html)

    def test_info_and_glossary_popovers(self):
        self.assertEqual(str(ui.info("not-a-real-term")), "")
        body = Markup(str(ui.info("pe")) + str(ui.info("pe")) + str(ui.info("rsi")))
        pops = str(ui.glossary_popovers(body))
        self.assertEqual(pops.count('popover id="g-pe"'), 1)       # once per page, however often used
        self.assertIn('id="g-rsi"', pops)


class TestGlossary(unittest.TestCase):
    def test_every_term_has_a_title_and_an_explanation(self):
        terms = glossary.all_terms()
        self.assertGreater(len(terms), 40)
        for k, (title, text) in terms.items():
            self.assertTrue(title and len(text) > 30, k)
            self.assertEqual(glossary.key(k), k)
        self.assertTrue(glossary.has("P/E") or glossary.has("pe"))
        self.assertTrue(glossary.has("roe"))                      # from fundamental_analysis


class TestShell(unittest.TestCase):
    APP_HEAD = ('<link rel="stylesheet" href="/app-assets/manage.css">'
                "<script>/* app-pending */</script>")

    def page(self, body, **kw):
        return ui_theme.page(title="T", active_file="portfolio.html", subtitle="sub", body=body,
                             app_head=self.APP_HEAD, app_body='<script src="/app-assets/manage.js" defer></script>', **kw)

    def test_shell_keeps_the_local_apps_hooks(self):
        html = self.page(Markup("<p>x</p>"), private=True)
        self.assertEqual(html.count('class="header-actions"'), 1)
        self.assertIn('<link rel="stylesheet" href="/app-assets/manage.css">', html)   # the PDF step strips this exact string
        self.assertIn('<script src="/app-assets/manage.js" defer></script>', html)
        self.assertIn('href="portfolio.html" class="nav-item active"', html)
        button = '<button type="button" class="icon-btn" data-hide-toggle'
        self.assertIn(button, html)
        self.assertNotIn(button, self.page(Markup("<p>x</p>")))      # only pages with your money on them
        for name in ui_theme.NAV_FILES:
            self.assertIn(f'href="{name}"', html)
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_dashboard_is_named_for_everything_it_covers(self):
        html = self.page(Markup("<p>x</p>"))
        self.assertIn("<title>T · Finances Dashboard</title>", html)
        self.assertIn("Finances Dashboard<small>NSE · Bonds · International</small>", html)
        self.assertNotIn("NSE Dashboard", html)
        # The saved light/dark choice and Hide amounts keep their old keys, so
        # the rename doesn't reset anyone's settings.
        self.assertIn("localStorage.getItem('nse-theme')", html)
        self.assertIn("localStorage.getItem('nse-hide')", html)

    def test_tabs_are_chosen_before_paint_and_old_anchors_still_work(self):
        body = ui.tabs("p", [("summary", "Summary", Markup('<div id="networth">a</div>'), None),
                             ("kenyan", "Kenyan stocks", Markup('<div id="stocks-holdings">b</div>'), None)])
        rules, mapping, default = ui_theme.tab_layout(body)
        self.assertEqual(default, "summary")
        self.assertEqual(mapping["stocks-holdings"], "kenyan")
        self.assertEqual(mapping["kenyan"], "kenyan")
        self.assertIn('html.js[data-tab="kenyan"] .tabs>.tab-panel:not([data-panel="kenyan"]){display:none}', rules)
        html = self.page(body)
        self.assertIn('"stocks-holdings":"kenyan"', html)

    def test_old_colour_names_survive_for_the_apps_stylesheet(self):
        css = ui_theme.css()
        for var in ("--bg", "--card-bg", "--surface", "--text", "--text-muted", "--border", "--accent"):
            self.assertIn(f"{var}:", css)

    @unittest.skipUnless(shutil.which("node"), "node isn't installed")
    def test_page_script_is_valid_javascript(self):
        for name in ("ui_runtime.js",):
            r = subprocess.run(["node", "--check", os.path.join(HERE, "src", name)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        head = ui_theme.head_script({"a": "b"}, "b")
        r = subprocess.run(["node", "--check", "-"], input=head, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


def _tokens(css, selector):
    block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def _rgb(value, under=None):
    v = value.strip()
    if v.startswith("#"):
        v = v[1:]
        return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))
    m = re.match(r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)", v)
    if m and under:                                   # composite a translucent colour over its background
        r, g, b, a = int(m[1]) / 255, int(m[2]) / 255, int(m[3]) / 255, float(m[4])
        return tuple(c * a + u * (1 - a) for c, u in zip((r, g, b), under))
    raise ValueError(value)


def _lum(rgb):
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class TestContrast(unittest.TestCase):
    """WCAG AA in both themes: text 4.5:1 on page and cards, chart marks 3:1."""

    def themes(self):
        css = ui_theme.css()
        light = _tokens(css, ":root")
        dark = dict(light, **_tokens(css, ':root[data-theme="dark"]'))
        return {"light": light, "dark": dark}

    def test_text_colours(self):
        for name, t in self.themes().items():
            for bg in ("--bg", "--card-bg", "--surface"):
                for fg in ("--text", "--text-2", "--text-muted", "--accent", "--up", "--down", "--warn"):
                    ratio = contrast(_rgb(t[fg]), _rgb(t[bg]))
                    with self.subTest(theme=name, fg=fg, bg=bg):
                        self.assertGreaterEqual(ratio, 4.5, f"{name}: {fg} on {bg} is {ratio:.2f}:1")

    def test_pill_text_on_its_tint(self):
        for name, t in self.themes().items():
            card = _rgb(t["--card-bg"])
            for fg, tint in (("--up", "--up-soft"), ("--down", "--down-soft"), ("--warn", "--warn-soft"),
                             ("--accent", "--accent-soft")):
                bg = _rgb(t[tint], under=card)
                ratio = contrast(_rgb(t[fg]), bg)
                with self.subTest(theme=name, fg=fg):
                    self.assertGreaterEqual(ratio, 4.5, f"{name}: {fg} on {tint} is {ratio:.2f}:1")

    def test_heatmap_text_stays_readable_at_the_strongest_colour(self):
        # The pages mix up / down into --heat-0 by at most 72% (light) and 42% (dark).
        for name, t in self.themes().items():
            share = 0.72 if name == "light" else 0.42
            base = _rgb(t["--heat-0"])
            for colour in ("--heat-up", "--heat-down"):
                mixed = tuple(a * share + b * (1 - share) for a, b in zip(_rgb(t[colour]), base))
                ratio = contrast(_rgb(t["--text"]), mixed)
                with self.subTest(theme=name, colour=colour):
                    self.assertGreaterEqual(ratio, 4.5, f"{name}: text on {colour} at {share:.0%} is {ratio:.2f}:1")

    def test_chart_marks(self):
        for name, t in self.themes().items():
            for fg in ("--up-line", "--down-line", "--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7",
                       "--c-stocks", "--c-bonds", "--c-intl"):
                ratio = contrast(_rgb(t[fg]), _rgb(t["--card-bg"]))
                with self.subTest(theme=name, fg=fg):
                    self.assertGreaterEqual(ratio, 3.0, f"{name}: {fg} on the card is {ratio:.2f}:1")


if __name__ == "__main__":
    unittest.main()
