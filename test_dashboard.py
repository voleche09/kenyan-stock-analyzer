#!/usr/bin/env python3
"""
Whole-dashboard tests: build every page offline from made-up data and check
what the pages promise — the numbers, the hooks the local app relies on,
sound structure — plus the frozen market-summary page (it becomes the PDF).

Runs with the rest of the suite (python test.py), or on its own:
    ./venv/bin/python3 -m unittest test_dashboard -v
"""

import contextlib
import glob
import hashlib
import logging
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))

from analysis_engine import AnalysisEngine           # noqa: E402
import bonds_portfolio                                 # noqa: E402
import company_logos                                   # noqa: E402
import fake_bonds                                      # noqa: E402  made-up bonds, never real ones
import international_portfolio                         # noqa: E402
import portfolio as portfolio_mod                      # noqa: E402

logging.disable(logging.CRITICAL)

AS_OF = "2026-01-15"
FX = {"rate": 129.5, "updated": "Thu, 15 Jan 2026 00:02:31 +0000", "source": "test"}

# Placeholder companies only — nothing here is anyone's real portfolio.
NSE = {  # symbol: (seed, start price, sector, name, dividend per share)
    "EQTY": (11, 45.0, "Banking", "Equity Group Holdings", 4.25),
    "EABL": (12, 160.0, "Manufacturing & Allied", "East African Breweries", 6.0),
    "ABSA": (13, 15.0, "Banking", "Absa Bank Kenya", 1.55),
    "BKG": (14, 32.0, "Banking", "BK Group", None),
    "CARB": (15, 18.0, "Energy & Petroleum", "Carbacid Investments", 1.7),
}
NSE_LOTS = [
    {"symbol": "EQTY", "quantity": 200, "buy_price": 40.0, "buy_date": "2025-11-03", "note": ""},
    {"symbol": "ABSA", "quantity": 300, "buy_price": 12.0, "buy_date": "2025-06-01", "note": ""},
    {"symbol": "ABSA", "quantity": 100, "buy_price": 16.5, "buy_date": "2025-12-01", "note": "top-up"},
    {"symbol": "CARB", "quantity": 150, "buy_price": 20.0, "buy_date": "2025-09-01", "note": ""},
    {"symbol": "ZZZZ", "quantity": 10, "buy_price": 5.0, "buy_date": "2025-10-01", "note": "no data"},
]
BOND_LOTS = [
    {"issue": "IFB9/2021/7", "face_value": 250000.0, "purchase_price_pct": 100.0,
     "purchase_date": "2024-01-10", "note": ""},
    {"issue": "FXD9/2020/020", "face_value": 400000.0, "purchase_price_pct": 101.2,
     "purchase_date": "2023-02-01", "note": ""},
]
INTL = {"AAPL": (21, 190.0, "Technology", "Apple Inc."), "MSFT": (22, 410.0, "Technology", "Microsoft")}
INTL_LOTS = [
    {"symbol": "AAPL", "quantity": 10, "buy_price": 150.25, "buy_date": "2025-01-15", "note": ""},
    {"symbol": "MSFT", "quantity": 5, "buy_price": 380.0, "buy_date": "2025-03-01", "note": ""},
]


def analysed(seed, start, periods=140):
    """A real AnalysisEngine result on synthetic prices ending on AS_OF."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp(AS_OF), periods=periods, freq="B")
    close = start * (1 + np.cumsum(rng.normal(0, 0.012, periods)))
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                       "volume": rng.integers(10_000, 90_000, periods)}, index=dates)
    return AnalysisEngine().analyze_stock(df)


@contextlib.contextmanager
def offline():
    """Fetches the page builders make themselves, answered the way each
    loader answers when its source is unreachable (empty, never invented)."""
    flows = {"weeks": [], "source_home": "", "source_note": ""}
    pulse = {"news": [], "cbk": {}, "oil": None, "african_indices": [], "fx": None,
             "generated_at": f"{AS_OF} 16:00 EAT"}
    treasury = {"as_of": AS_OF, "tbills": [], "bonds": [],
                "context": {"cbr": None, "inflation_note": None}, "source": "test"}
    with mock.patch.object(company_logos, "fetch_logo_base64", return_value=None), \
            mock.patch("foreign_flows.load", return_value=flows), \
            mock.patch("market_pulse.load_all", return_value=pulse), \
            mock.patch("treasury_securities.load_treasury", return_value=treasury):
        yield


@contextlib.contextmanager
def captured_warnings():
    """Collect WARNING+ log messages despite the suite's logging.disable()."""
    msgs = []

    class H(logging.Handler):
        def emit(self, record):
            msgs.append(f"{record.name}: {record.getMessage()}")
    h = H(logging.WARNING)
    root = logging.getLogger()
    root.addHandler(h)
    logging.disable(logging.NOTSET)
    try:
        yield msgs
    finally:
        logging.disable(logging.CRITICAL)
        root.removeHandler(h)


def _bond_portfolio():
    with fake_bonds.use():
        return bonds_portfolio.compute_bond_portfolio(BOND_LOTS, as_of=AS_OF)


def build_inputs(fx=True, with_portfolio=True):
    import scoring
    nse = {s: analysed(seed, start) for s, (seed, start, *_r) in NSE.items()}
    fundamentals = {}
    for s, (_seed, _start, sector, name, dps) in NSE.items():
        close = nse[s]["latest"]["close"]
        fundamentals[s] = {
            "name": name, "sector": sector, "close": close, "tech_rating": 0.2,
            "dps_fy": dps, "dividend_yield": round(dps / close * 100, 2) if dps else None,
            "dividend_status": "verified" if dps else "none", "pe_ratio": 6.5, "roe": 18.0,
            "market_cap": 1.2e11, "volume": 250000,
        }
    scores = {s: scoring.score_stock(s, nse[s], fundamentals[s]) for s in nse}
    inputs = {"analysis_results": nse, "fundamentals_data": fundamentals, "scores": scores,
              "breadth": AnalysisEngine().calculate_market_breadth(nse),
              "usd_kes": dict(FX) if fx else None}
    if with_portfolio:
        intl = {s: analysed(seed, start) for s, (seed, start, *_r) in INTL.items()}
        intl_f = {s: {"currency": "USD", "name": name, "sector": sector, "close": intl[s]["latest"]["close"],
                      "target_mean_price": intl[s]["latest"]["close"] * 1.1, "recommendation_key": "buy",
                      "num_analysts": 30} for s, (_sd, _st, sector, name) in INTL.items()}
        inputs.update(
            portfolio_summary=portfolio_mod.compute_portfolio(NSE_LOTS, nse, fundamentals, scores, {},
                                                              as_of=AS_OF),
            bond_portfolio=_bond_portfolio(),
            intl_portfolio_summary=international_portfolio.compute_international_portfolio(
                INTL_LOTS, intl, intl_f, {}, inputs["usd_kes"]),
        )
    return inputs


def build_dashboard(out_dir, **opts):
    """Write every dashboard page into out_dir; returns {file name: html}."""
    from report_generator import ReportGenerator
    rg = ReportGenerator(output_dir=out_dir, cache_dir=out_dir)
    inputs = build_inputs(**opts)
    with offline():
        rg.generate_index(inputs["analysis_results"], breadth=inputs["breadth"], report_files={},
                          fundamentals_data=inputs["fundamentals_data"], validations={},
                          scores=inputs["scores"], alerts={}, usd_kes=inputs["usd_kes"],
                          portfolio_summary=inputs.get("portfolio_summary"),
                          bond_portfolio=inputs.get("bond_portfolio"),
                          intl_portfolio_summary=inputs.get("intl_portfolio_summary"))
    pages = {}
    for path in glob.glob(os.path.join(out_dir, "*.html")):
        with open(path, encoding="utf-8") as f:
            pages[os.path.basename(path)] = f.read()
    return pages, inputs


class _Built:
    """Builds the dashboard once per test class."""
    opts = {}

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        with captured_warnings() as warnings:
            cls.pages, cls.inputs = build_dashboard(cls._tmp.name, **cls.opts)
        cls.warnings = warnings

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class TestDashboardBuilds(_Built, unittest.TestCase):
    """Every page renders from its real template/code path — never the
    fallback page — without warnings, and keeps the hooks the local app
    (app_assets/manage.js) attaches to."""

    def test_every_page_is_written(self):
        for name in ("index.html", "portfolio.html", "technicals.html", "fundamentals.html",
                     "dividends.html"):
            self.assertIn(name, self.pages)

    def test_no_page_fell_back_and_nothing_warned(self):
        for name, html in self.pages.items():
            self.assertNotIn('name="render-fallback"', html, name)
            self.assertTrue(html.rstrip().endswith("</html>"), name)
        self.assertEqual(self.warnings, [])

    def test_hooks_the_local_app_needs(self):
        for name, html in self.pages.items():
            self.assertEqual(html.count('class="header-actions"'), 1, f"{name}: status-pill slot")
            ids = re.findall(r'\sid="([^"]+)"', html)
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            self.assertEqual(dupes, [], f"{name}: duplicate ids")
            self.assertIn('<link rel="stylesheet" href="/app-assets/manage.css">', html, name)
        p = self.pages["portfolio.html"]
        for hook in ('id="purchase-panel"', 'id="entries-panel"'):
            self.assertIn(hook, p)


class TestPortfolioNumbers(_Built, unittest.TestCase):
    """The figures My Portfolio shows, worked out independently from the
    portfolio modules. Pinned before the redesign so a new layout can't
    quietly drop or change them."""

    def setUp(self):
        self.page = self.pages["portfolio.html"]
        self.ps = self.inputs["portfolio_summary"]["totals"]
        self.bp = self.inputs["bond_portfolio"]["totals"]
        self.ip = self.inputs["intl_portfolio_summary"]["totals"]

    def test_net_worth_adds_the_three_parts_in_kes(self):
        total = self.ps["market_value"] + self.bp["indicative_value_par"] + self.ip["market_value"] * FX["rate"]
        self.assertIn(f"KES {total:,.0f}", self.page)
        self.assertIn(f"≈ ${total / FX['rate']:,.0f} USD", self.page)
        cost = self.ps["cost_basis"] + self.bp["cost_basis"] + self.ip["cost_basis"] * FX["rate"]
        self.assertIn(f"KES {total - cost:,.0f}", self.page)
        self.assertIn(f"vs. KES {cost:,.0f} you put in", self.page)
        self.assertIn(f"USD/KES {FX['rate']:.2f}", self.page)

    def test_each_part_is_shown(self):
        self.assertIn(f"KES {self.ps['market_value']:,.0f}", self.page)
        self.assertIn(f"KES {self.bp['indicative_value_par']:,.0f}", self.page)
        self.assertIn(f"KES {self.bp['annual_after_tax_income']:,.0f}", self.page)
        self.assertIn(f"${self.ip['market_value']:,.0f}", self.page)
        self.assertIn(f"KES {self.bp['face_value']:,.0f}", self.page)

    def test_holdings_with_no_data_are_listed_not_guessed(self):
        self.assertEqual(self.inputs["portfolio_summary"]["missing_symbols"], ["ZZZZ"])
        self.assertIn("ZZZZ", self.page)

    def test_bonds_have_no_daily_move(self):
        self.assertIn("n/a — see note", self.page)


class TestPortfolioWithoutFx(_Built, unittest.TestCase):
    """No exchange rate this run: international is shown in USD only and
    left out of the KES total — never converted at a guessed rate."""
    opts = {"fx": False}

    def test_international_is_excluded_from_the_kes_total(self):
        page = self.pages["portfolio.html"]
        ps = self.inputs["portfolio_summary"]["totals"]
        bp = self.inputs["bond_portfolio"]["totals"]
        self.assertIn(f"KES {ps['market_value'] + bp['indicative_value_par']:,.0f}", page)
        self.assertIn("FX rate unavailable this run", page)
        self.assertNotIn("≈ $", page)


class TestDashboardWithoutPortfolio(_Built, unittest.TestCase):
    """Someone who hasn't set up a portfolio gets the how-to-start pages."""
    opts = {"with_portfolio": False}

    def test_portfolio_page_explains_how_to_start(self):
        page = self.pages["portfolio.html"]
        self.assertIn("holdings.csv", page)
        self.assertNotIn('name="render-fallback"', page)


class TestPortfolioPage(_Built, unittest.TestCase):
    """The tabbed My Portfolio page: structure, old anchors, private amounts."""

    OLD_ANCHORS = {
        "kenyan": ["stocks-top", "stocks-charts", "stocks-holdings", "stocks-dividends"],
        "bonds": ["bonds-top", "bonds-charts", "bonds-detail", "bonds-calendar"],
        "international": ["intl-top", "intl-charts", "intl-holdings"],
        "news": ["stocks-news", "intl-news"],
    }

    def setUp(self):
        self.page = self.pages["portfolio.html"]

    def test_five_tabs_every_panel_in_the_page(self):
        for key in ("summary", "kenyan", "bonds", "international", "news"):
            self.assertIn(f'data-panel="{key}"', self.page)
            self.assertIn(f'href="#{key}"', self.page)
        self.assertNotIn('class="legacy"', self.page)

    def test_old_section_links_still_land_in_the_right_tab(self):
        m = re.search(r"var M=(\{.*?\}),D=", self.page)
        mapping = __import__("json").loads(m.group(1))
        for tab, anchors in self.OLD_ANCHORS.items():
            for a in anchors:
                self.assertIn(f'id="{a}"', self.page, a)
                self.assertEqual(mapping.get(a), tab, a)

    def test_amounts_are_marked_private_but_percentages_are_not(self):
        total = re.search(r'data-kpi="Total net worth"[^>]*>.*?<div class="([^"]*kpi-value[^"]*)">', self.page, re.S)
        self.assertIn("private", total.group(1))
        self.assertIn('data-hide-toggle', self.page)

    def test_record_a_purchase_and_your_entries_open_in_place(self):
        self.assertIn('data-toggle-panel="purchase-panel"', self.page)
        self.assertIn('data-toggle-panel="entries-panel"', self.page)
        self.assertRegex(self.page, r'id="purchase-panel"[^>]*hidden')

    def test_summary_shows_every_holding_in_one_table(self):
        everything = self.page[self.page.index('id="t-everything"'):]
        everything = everything[:everything.index("</table>")]
        for sym in ("EQTY", "ABSA", "CARB", "ZZZZ", "AAPL", "MSFT", "IFB9/2021/7", "FXD9/2020/020"):
            self.assertIn(sym, everything)


class TestOverviewPage(_Built, unittest.TestCase):
    """The home page: today for you, the market, and every stock."""

    def setUp(self):
        self.page = self.pages["index.html"]

    def test_today_for_you_shows_your_money_privately(self):
        self.assertIn('id="for-you"', self.page)
        m = re.search(r'data-kpi="Your net worth"[^>]*>.*?<div class="([^"]*kpi-value[^"]*)">', self.page, re.S)
        self.assertIn("private", m.group(1))
        self.assertIn("data-hide-toggle", self.page)

    def test_every_stock_once_with_its_preview_written_once(self):
        for sym in NSE:
            self.assertEqual(self.page.count(f'<template id="tip-{sym.lower()}">'), 1, sym)
            self.assertIn(f'data-tip-ref="tip-{sym.lower()}"', self.page)
            self.assertIn(f'class="wl-star app-only" data-symbol="{sym}"', self.page)

    def test_market_pulse_and_movers(self):
        for label in ("Stocks", "Bullish", "Bearish", "Neutral"):
            self.assertIn(f'data-kpi="{label}"', self.page)
        self.assertIn("Top Gainers", self.page)
        self.assertIn("Top Losers", self.page)
        self.assertIn("How broadly the market moved", self.page)


class TestOverviewWithoutPortfolio(_Built, unittest.TestCase):
    opts = {"with_portfolio": False}

    def test_no_private_strip_or_hide_button(self):
        page = self.pages["index.html"]
        self.assertNotIn('id="for-you"', page)
        self.assertNotIn('<button type="button" class="icon-btn" data-hide-toggle', page)


class TestNetworthSummary(unittest.TestCase):
    """networth_summary() adds the three parts the way the page explains."""

    @classmethod
    def setUpClass(cls):
        import page_portfolio
        cls.pp = page_portfolio
        cls.inputs = build_inputs()

    def summary(self, **over):
        i = dict(self.inputs, **over)
        return self.pp.networth_summary(i["portfolio_summary"], i["bond_portfolio"],
                                        i["intl_portfolio_summary"], i["usd_kes"])

    def test_totals(self):
        nw = self.summary()
        ps, bp, ip = (self.inputs[k]["totals"] for k in ("portfolio_summary", "bond_portfolio",
                                                           "intl_portfolio_summary"))
        self.assertAlmostEqual(nw["total"], ps["market_value"] + bp["indicative_value_par"] + ip["market_value"] * FX["rate"])
        self.assertAlmostEqual(nw["total_usd"], nw["total"] / FX["rate"])
        self.assertAlmostEqual(sum(nw[p]["share"] for p in ("stocks", "bonds", "intl")), 100.0)
        self.assertAlmostEqual(nw["dividends_kes"], ps["est_annual_dividend"] + ip["est_annual_dividend"] * FX["rate"])

    def test_today_counts_only_what_moves(self):
        nw = self.summary()
        ps, ip = self.inputs["portfolio_summary"]["totals"], self.inputs["intl_portfolio_summary"]["totals"]
        expected = ps["day_change_value"] + ip["day_change_value"] * FX["rate"]
        self.assertAlmostEqual(nw["day_value"], expected)
        self.assertAlmostEqual(nw["day_pct"], expected / (nw["total"] - expected) * 100)
        moving = [h for s in ("portfolio_summary", "intl_portfolio_summary")
                  for h in self.inputs[s]["holdings"] if h["data_available"] and h.get("day_change_pct")]
        self.assertEqual(nw["ups"] + nw["downs"], len(moving))

    def test_no_exchange_rate_leaves_international_out_of_kes(self):
        nw = self.summary(usd_kes=None)
        ps, bp = self.inputs["portfolio_summary"]["totals"], self.inputs["bond_portfolio"]["totals"]
        self.assertTrue(nw["fx_missing"])
        self.assertAlmostEqual(nw["total"], ps["market_value"] + bp["indicative_value_par"])
        self.assertIsNone(nw["total_usd"])
        self.assertIsNone(nw["intl"]["value_kes"])
        self.assertAlmostEqual(nw["dividends_kes"], ps["est_annual_dividend"])
        self.assertIsNotNone(nw["dividends_intl_unconverted_usd"])

    def test_nothing_set_up(self):
        self.assertIsNone(self.pp.networth_summary(None, None, None, FX))


class TestPortfolioInsights(unittest.TestCase):
    def test_worth_knowing_and_coming_up(self):
        import datetime as dt
        import page_portfolio as pp
        from report_generator import ReportGenerator
        inputs = build_inputs()
        nw = pp.networth_summary(inputs["portfolio_summary"], inputs["bond_portfolio"],
                                 inputs["intl_portfolio_summary"], inputs["usd_kes"])
        facts = str(pp._worth_knowing_card(nw, inputs["portfolio_summary"], inputs["intl_portfolio_summary"]))
        self.assertIn("Largest holding", facts)
        self.assertIn("ZZZZ", facts)                                  # listed under "No data today"
        funds = {"EQTY": {"dividend_book_closure": "2026-02-01", "dividend_payment_date": "2026-03-01",
                          "dps_fy": 4.25, "earnings_next_date": "2025-12-01"},
                 "ABSA": {"dividend_book_closure": "2026-01-20", "dividend_status": "unverified", "dps_fy": 1.55}}
        card = str(pp._coming_up_card(inputs["bond_portfolio"], inputs["portfolio_summary"], None, funds, {},
                                      dt.date(2026, 1, 15)))
        self.assertIn("EQTY book closure", card)
        self.assertIn("EQTY dividend paid", card)
        self.assertNotIn("results due", card)                         # 2025-12-01 has passed
        self.assertIn("ABSA book closure (unverified)", card)
        self.assertLess(card.index("2026-01-20"), card.index("2026-02-01"))   # soonest first


class TestMarketPages(_Built, unittest.TestCase):
    """The rebuilt market pages: each stock's preview written once per page
    and pointed at, no chart images or old-markup wrapper, and the summary
    strips add up."""
    REBUILT = ("technicals.html", "fundamentals.html", "dividends.html", "earnings.html", "sectors.html",
               "quality.html")

    def test_each_preview_is_written_once_and_pointed_at(self):
        for name in ("technicals.html", "fundamentals.html", "dividends.html"):
            page = self.pages[name]
            for sym in NSE:
                self.assertEqual(page.count(f'<template id="tip-{sym.lower()}">'), 1, (name, sym))
                self.assertIn(f'data-tip-ref="tip-{sym.lower()}"', page)
            self.assertNotIn("data-tip=\"&lt;div class='tt-h'", page)

    def test_no_chart_images_and_no_old_markup(self):
        for name in self.REBUILT:
            self.assertNotIn("chart-img", self.pages[name], name)
            self.assertNotIn('<div class="legacy">', self.pages[name], name)

    def test_heatmaps_follow_the_theme(self):
        page = self.pages["visuals.html"]
        self.assertIn('class="tm-tile heat"', page)
        self.assertNotRegex(page, r'class="(tm-tile|heat-tile)[^"]*"[^>]*style="[^"]*background')

    def kpi(self, page, label):
        m = re.search(rf'data-kpi="{re.escape(label)}"[^>]*>.*?<div class="kpi-value[^"]*">([^<]*)<', page)
        return m.group(1) if m else None

    def test_summary_strips_add_up(self):
        payers = sum(1 for *_r, dps in NSE.values() if dps)
        self.assertEqual(self.kpi(self.pages["dividends.html"], "Paying a dividend"), f"{payers} of {len(NSE)}")
        bullish = sum(1 for r in self.inputs["analysis_results"].values()
                      if r["signals"].get("overall") == "bullish")
        self.assertEqual(self.kpi(self.pages["technicals.html"], "Overall bullish"), str(bullish))
        self.assertEqual(self.kpi(self.pages["earnings.html"], "Upcoming results"), "0")
        self.assertIn("No upcoming earnings dates on record.", self.pages["earnings.html"])


class TestMarketContextPages(unittest.TestCase):
    """Govt bonds, Foreign flows and Market pulse from made-up source data:
    an open bond gets its card and the after-tax ranking holds, missing data
    is said rather than guessed, and third-party text stays text."""
    HOSTILE = '<img src=x onerror="alert(1)">'

    def setUp(self):
        from report_generator import ReportGenerator
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.rg = ReportGenerator(output_dir=self._tmp.name, cache_dir=self._tmp.name)

    def test_an_open_bond_gets_its_card_and_after_tax_ranking(self):
        import page_market
        d = {"as_of": "2026-01-15", "context": {"cbr": 9.0, "inflation_note": "4.5% in December"},
             "tbills": [{"label": "91-day", "rate": 8.5, "issue": "2026-01-12"}],
             "bonds": [{"issue": "IFB1/2026/012", "type_label": "Infrastructure Bond", "status": "open",
                        "coupon": 13.0, "tax_free": True, "tenor_years": 12, "maturity": "2038-01-01",
                        "min_invest": 100000, "auction_date": "2026-01-17", "clean_price": 99.5,
                        "dirty_price": 101.2, "accrued_interest": 1.7,
                        "prospectus_url": "https://www.centralbank.go.ke/x.pdf"},
                       {"issue": "FXD1/2026/010", "type_label": "Fixed-coupon Bond", "status": "closed",
                        "coupon": 14.0, "withholding_pct": 10, "tenor_years": 10, "maturity": "2036-01-01"}]}
        html = str(page_market.bonds(self.rg, d))
        for fact in ("🟢 Open now", "★ Highest open yield", "closes in 2d", "You pay ≈ KES 101.20",
                     "≈ 14.44% taxable-equivalent", 'href="https://www.centralbank.go.ke/x.pdf"',
                     "+4.00 percentage points"):
            self.assertIn(fact, html)
        top = re.search(r'id="top-bonds".*?</table>', html, re.S).group(0)
        self.assertLess(top.index("IFB1/2026/012"), top.index("FXD1/2026/010"))   # 13% tax-free beats 14% taxed
        self.assertIn("12.600%", top)                                                # 14% less 10% tax

    def test_missing_sources_are_said_not_guessed(self):
        import page_market
        self.assertIn("temporarily unavailable", str(page_market.bonds(self.rg, {"bonds": [], "tbills": []})))
        self.assertIn("No data yet", str(page_market.foreign(self.rg, {"weeks": []})))
        one = str(page_market.foreign(self.rg, {"weeks": [{
            "week_ending": "2026-01-09", "top_foreign_buys": [{"symbol": "EQTY", "value_kes": 1.8e8}],
            "top_foreign_sells": [], "aggregate": {"net_foreign_flow_kes": -1.45e8, "foreign_buys_kes": 1.2e9,
                                                   "foreign_sells_kes": 1.345e9, "foreign_participation_pct": 59.8}}]}))
        for fact in ("−KES 145.0M", "Only one week recorded so far: 59.8%", "No entries reported this week."):
            self.assertIn(fact, one)

    def test_third_party_text_stays_text_on_the_pulse_page(self):
        import page_market
        html = str(page_market.pulse(self.rg, {
            "cbk": {"cbr_pct": 9.25, "cbr_note": self.HOSTILE}, "oil": [], "fx": [], "african_indices": [],
            "news": [{"topic": "Kenyan Banking", "title": "Bank " + self.HOSTILE, "url": "javascript:alert(1)",
                      "source": self.HOSTILE, "published_utc": "Thu, 15 Jan 2026 10:00:00 GMT"}]}))
        self.assertIn("9.25%", html)
        self.assertIn("Bank &lt;img", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("javascript:", html)


class TestStockPagesRender(unittest.TestCase):
    """Both per-stock pages build — never the fallback page — with full,
    sparse and nearly empty data, and nothing logs a warning."""

    def setUp(self):
        from report_generator import ReportGenerator
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.rg = ReportGenerator(output_dir=self._tmp.name, cache_dir=self._tmp.name)
        p = mock.patch.object(ReportGenerator, "_fallback_html",
                              side_effect=AssertionError("template failed to render"))
        p.start()
        self.addCleanup(p.stop)

    def check(self, make):
        with offline(), captured_warnings() as warnings:
            path = make()
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertTrue(html.rstrip().endswith("</html>"))
        self.assertEqual(warnings, [])
        return html

    def test_nse_page(self):
        inputs = build_inputs()
        full = inputs["fundamentals_data"]["EQTY"]
        from fundamental_analysis import FundamentalAnalysis
        fa = FundamentalAnalysis(cache_dir=self._tmp.name)
        fundamentals = inputs["fundamentals_data"]
        self.check(lambda: self.rg.generate_stock_report(
            "EQTY", inputs["analysis_results"]["EQTY"], fundamentals=full,
            score=inputs["scores"]["EQTY"], usd_kes=FX, alerts=["Near its 52-week high"],
            similar_stocks=fa.find_similar_stocks("EQTY", fundamentals),
            sector_peers=fa.get_sector_peers("EQTY", fundamentals),
            validation={"status": "ok", "note": "Matches the NSE close", "reference_price": full["close"],
                        "reference_source": "NSE"}))
        self.check(lambda: self.rg.generate_stock_report("EABL", inputs["analysis_results"]["EABL"]))
        self.check(lambda: self.rg.generate_stock_report("CARB", analysed(5, 18.0, periods=25)))

    def test_international_page(self):
        res = analysed(21, 190.0)
        self.check(lambda: self.rg.generate_international_stock_report(
            "AAPL", res, fundamentals={"currency": "USD", "name": "Apple Inc.", "sector": "Technology",
                                       "summary": "Makes phones.", "week52_low": 150.0, "week52_high": 240.0,
                                       "target_mean_price": 230.0, "eps_ttm": 6.1},
            news=[{"title": "A headline", "url": "https://example.com/a", "source": "Wire",
                   "published_utc": "2026-01-14T10:00:00Z"}],
            dividend_history=[{"date": "2025-11-10", "amount": 0.26}],
            earnings_calendar={"next_earnings_date": "2026-01-29"}, usd_kes=FX))
        self.check(lambda: self.rg.generate_international_stock_report("MSFT", res))
        self.check(lambda: self.rg.generate_international_stock_report(
            "VOD.L", analysed(23, 70.0, periods=25), fundamentals={"currency": "GBp"}, context="watchlist"))


class TestStockPageContent(unittest.TestCase):
    """What the redesigned stock pages promise: "Your position" only when you
    own the stock, with every amount private; your average cost on the chart
    and range bar but never pinned where it would read as another price; a
    real zero shown as 0.00 and a missing figure never coloured; tabs the old
    deep links open; and the local app's hooks."""

    @classmethod
    def setUpClass(cls):
        from fundamental_analysis import FundamentalAnalysis
        from report_generator import ReportGenerator
        cls._tmp = tempfile.TemporaryDirectory()
        rg = ReportGenerator(output_dir=cls._tmp.name, cache_dir=cls._tmp.name)
        i = cls.inputs = build_inputs()
        held = {h["symbol"]: h for h in i["portfolio_summary"]["holdings"]}
        intl_held = {h["symbol"]: h for h in i["intl_portfolio_summary"]["holdings"]}
        fa = FundamentalAnalysis(cache_dir=cls._tmp.name)
        f = dict(i["fundamentals_data"]["EQTY"], debt_to_equity=0.0, roic=None)
        with offline():
            cls.held = cls.read(rg.generate_stock_report(
                "EQTY", i["analysis_results"]["EQTY"], fundamentals=f, holding=held["EQTY"],
                similar_stocks=fa.find_similar_stocks("EQTY", i["fundamentals_data"]),
                sector_peers=fa.get_sector_peers("EQTY", i["fundamentals_data"])))
            cls.plain = cls.read(rg.generate_stock_report(
                "EABL", i["analysis_results"]["EABL"], fundamentals=i["fundamentals_data"]["EABL"]))
            cls.intl = cls.read(rg.generate_international_stock_report(
                "AAPL", analysed(21, 190.0), usd_kes=FX, holding=intl_held["AAPL"],
                fundamentals={"currency": "USD", "week52_low": 300.0, "week52_high": 400.0}))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @staticmethod
    def read(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_your_position_only_when_you_own_it_with_every_amount_private(self):
        from bs4 import BeautifulSoup
        hide_button = 'class="icon-btn" data-hide-toggle'
        for page in (self.held, self.intl):
            self.assertIn(hide_button, page)
            strip = BeautifulSoup(page, "html.parser").select_one("#position")
            self.assertIsNotNone(strip)
            for el in strip.select(".private, button.info"):
                el.decompose()
            self.assertNotRegex(strip.get_text(" "), r"(KES|\$)\s?[\d,]+")      # nothing left unmasked
        self.assertNotIn('id="position"', self.plain)
        self.assertNotIn(hide_button, self.plain)
        self.assertNotRegex(self.plain, r'class="[^"]*\bprivate\b[^"]*"')       # nothing of yours on it
        self.assertRegex(self.held, r'ref-label cost"[^>]*>Your average cost<|to fit: Your average cost')

    def test_an_average_cost_outside_the_range_is_named_not_pinned_to_its_end(self):
        hero = self.intl.split('class="card hero"', 1)[1].split("</section>", 1)[0]
        self.assertIn("your average cost is below this range", hero)
        self.assertNotIn("rbar-mark cost", hero)

    def test_a_real_zero_reads_0_00_and_a_missing_figure_is_never_coloured(self):
        def figure(label):
            return re.search(rf'data-kpi="{re.escape(label)}">.*?<span class="kpi-value num([^"]*)">([^<]*)<',
                             self.held).groups()
        self.assertEqual(figure("Debt/Equity"), (" positive", "0.00"))
        self.assertEqual(figure("ROIC"), ("", "N/A"))

    def test_tabs_old_deep_links_and_the_apps_hooks(self):
        for key in ("summary", "fundamentals", "technicals", "peers", "history", "glossary"):
            self.assertIn(f'data-tab="{key}"', self.held)
        self.assertIn('"glossary-table":"glossary"', self.held)       # #glossary-table opens its tab
        self.assertNotIn('data-tab="peers"', self.plain)               # no peers given, no empty tab
        for key in ("news", "history"):
            self.assertIn(f'data-tab="{key}"', self.intl)
        for page, market in ((self.held, "NSE"), (self.intl, "INTL")):
            self.assertEqual(page.count('class="header-actions"'), 1)
            self.assertRegex(page, rf'class="wl-star app-only" data-symbol="[A-Z]+" data-market="{market}"')
            self.assertIn("☆ Watch</button>", page)
            ids = re.findall(r'\sid="([^"]+)"', page)
            self.assertEqual(len(ids), len(set(ids)), "duplicate ids")


class TestFactCheckTool(unittest.TestCase):
    """tools/factcheck.py is what proves a redesign kept every fact, so its
    own parsing and comparison are pinned here."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
        import factcheck
        cls.fc = factcheck

    def test_numbers_keep_unit_sign_and_precision(self):
        nums = self.fc._numbers(self.fc._clean(
            "KES 1,234.50 · ▲ 2.5% · −$80.00 · 80.00 GBp · 1.2B · on 2026-10-05 10:32 EAT"))
        self.assertIn(("KES", "+", "1234.50", 2, 1.0), nums)
        self.assertIn(("%", "+", "2.5", 1, 1.0), nums)
        self.assertIn(("USD", "-", "80.00", 2, 1.0), nums)
        self.assertIn(("GBp", "+", "80.00", 2, 1.0), nums)
        self.assertIn(("", "+", "1.2", 1, 1e9), nums)
        big = self.fc._numbers(self.fc._clean("KES 179.51 Billion · KES 4.87 Trillion · KES 922.91 Million"))
        self.assertEqual([(n[2], n[4]) for n in big], [("179.51", 1e9), ("4.87", 1e12), ("922.91", 1e6)])
        self.assertIn(("date", "", "2026-10-05", 0, 1.0), nums)
        self.assertFalse([n for n in nums if n[2] in ("10", "32")], "times are dropped")

    def test_a_number_matches_at_its_own_precision(self):
        index = self.fc._NumberIndex([("KES", "+", "45.1", 1, 1.0), ("", "+", "1.2", 1, 1e9)])
        self.assertEqual(index.match(("KES", "+", "45.10", 2, 1.0)), "ok")
        self.assertEqual(index.match(("KES", "+", "45.14", 2, 1.0)), "precision")
        self.assertIsNone(index.match(("KES", "+", "46.00", 2, 1.0)))
        self.assertIsNone(index.match(("KES", "-", "45.1", 1, 1.0)), "the sign matters")
        self.assertEqual(index.match(("", "+", "1200000000", 0, 1.0)), "ok")       # 1.2B exactly
        self.assertEqual(index.match(("", "+", "1234567890", 0, 1.0)), "precision")

    def write(self, d, name, body):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(f"<html><body>{body}</body></html>")

    def test_compare_reports_losses_and_changes_but_not_moves_or_layout(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old, same, new = (os.path.join(tmp.name, n) for n in ("old", "same", "new"))
        table = ('<table><thead><tr><th>Symbol</th><th>Price</th><th>Today</th></tr></thead><tbody>'
                 '<tr><td>EQTY</td><td>KES 45.10</td><td class="positive">+1.2%</td></tr>'
                 '<tr><td>ABSA</td><td>KES 15.00</td><td class="negative">-0.5%</td></tr></tbody></table>')
        kpi = '<div class="stat-card"><div class="stat-value">KES 9,000</div><div class="stat-label">Value</div></div>'
        text = "<p>Interest builds up daily between coupon dates. ⚠️ Prices are delayed.</p>"
        self.write(old, "p_20260101_000000.html", table + kpi + text)
        self.write(same, "p_20260102_000000.html",   # new layout, same facts: KPI as data-kpi, text moved
                   '<section><p>⚠️ Prices are delayed. Interest builds up daily between coupon dates.</p></section>'
                   '<div data-kpi="Value"><b class="kpi-value">KES 9,000</b></div>' + table)
        self.write(new, "p_20260103_000000.html",
                   table.replace("KES 15.00", "KES 15.50").replace(' class="positive"', "") + text)
        with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull):
            self.assertEqual(self.fc.compare(old, same), 0)
            self.assertEqual(self.fc.compare(old, new), 1)
        out = []
        with mock.patch("builtins.print", side_effect=lambda *a, **k: out.append(" ".join(map(str, a)))):
            self.fc.compare(old, new, details=True)
        report = "\n".join(out)
        self.assertIn("ABSA | price | KES 15.00", report)                 # changed value
        self.assertIn("EQTY | today | +1.2% [pos]", report)                # colour meaning lost
        self.assertIn("headline figure lost", report)
        self.assertNotIn("sentence lost", report)

    def test_comments_and_page_navigation_are_not_facts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.write(tmp.name, "p.html", '<!-- Header: navigation bar below the header --><div class="nav-bar">'
                   '<a href="index.html">Overview</a> 📅 2026-10-05 · TradingView</div>'
                   '<p>Prices are delayed by about fifteen minutes.</p>')
        facts = self.fc.extract(os.path.join(tmp.name, "p.html"))
        self.assertEqual(facts["links"], [])
        self.assertEqual(list(facts["sentences"]), ["prices are delayed by about fifteen minutes"])


# Masked before hashing: the "generated at" time stamps.
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?")
# sha256 of today's market-summary HTML for the fixed inputs below. The PDF
# summary is built from this page, so it must not change by accident. If you
# change it on purpose, re-check the PDF and update the hash.
MARKET_SUMMARY_SHA256 = "f3a76b034d2c27abdce05c761eb1c5b71727c8233c5c54c772a8f019b2597063"


class TestMarketSummaryFrozen(unittest.TestCase):
    def render(self, report_type="html"):
        from report_generator import ReportGenerator
        from sector_analysis import SectorAnalyzer
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rg = ReportGenerator(output_dir=tmp.name, cache_dir=tmp.name)
        results = {s: analysed(seed, start) for s, (seed, start, *_r) in NSE.items()}
        data = {s: r["data"] for s, r in results.items()}
        sectors = SectorAnalyzer().analyze_sectors(data, results)
        breadth = AnalysisEngine().calculate_market_breadth(results)
        with offline():
            out = rg.generate_market_summary(results, sector_data=sectors, breadth=breadth,
                                             report_type=report_type)
        return out

    def test_market_summary_html_is_unchanged(self):
        with open(self.render(), encoding="utf-8") as f:
            html = f.read()
        digest = hashlib.sha256(_STAMP.sub("<stamp>", html).encode("utf-8")).hexdigest()
        self.assertEqual(digest, MARKET_SUMMARY_SHA256)

    def test_market_summary_pdf_still_builds(self):
        import report_generator
        if not report_generator.WEASYPRINT_AVAILABLE:
            self.skipTest("WeasyPrint isn't installed here")
        out = self.render("pdf")
        pdf = out if isinstance(out, str) else next(p for p in out if str(p).endswith(".pdf"))
        with open(pdf, "rb") as f:
            self.assertEqual(f.read(4), b"%PDF")


if __name__ == "__main__":
    unittest.main()
