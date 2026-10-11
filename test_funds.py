"""
Tests for the mutual funds: the arithmetic (funds_portfolio), the automatic
top-ups and payouts (each exactly once), the price sources' parsers
(fund_data, offline), the Funds tab and the fund pages — all on made-up
funds (tools/fake_funds.py). Plus one LOCAL-ONLY check that none of your
real funds is named in any file git tracks.
"""

import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "tools")]

import fake_funds                  # noqa: E402
import fund_data                   # noqa: E402
import funds_portfolio as fp       # noqa: E402

D = dt.date
TODAY = D(2026, 6, 10)


def _isin(n):
    body = f"XS8{n:08d}"
    return body + fp.isin_check_digit(body)


A, B = _isin(1), _isin(2)


def _navs(start, end, price=lambda d: 10.0):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append((d, round(price(d), 4)))
        d += dt.timedelta(days=1)
    return out


def _write_funds(folder, rows):
    head = "isin,name,units,avg_cost,as_of,statement_nav,payouts,monthly_amount,monthly_day,fee_pct,peers\n"
    with open(os.path.join(folder, fp.FUNDS_FILE), "w", encoding="utf-8") as f:
        f.write(head + "".join(",".join(str(x) for x in r) + "\n" for r in rows))


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.dir = self._t.name


# ------------------------------------------------------------------ small rules
class TestFundRules(unittest.TestCase):
    def test_isin_check_digit(self):
        self.assertTrue(fp.valid_isin(A))
        self.assertFalse(fp.valid_isin(A[:-1] + str((int(A[-1]) + 1) % 10)))     # one digit off
        self.assertFalse(fp.valid_isin("XS12345"))
        self.assertFalse(fp.valid_isin("1S0000000001"))

    def test_topups_fall_on_the_day_or_the_months_last_day(self):
        self.assertEqual(fp.topup_dates(31, "2027-01-31", "2027-04-30"),
                         [D(2027, 2, 28), D(2027, 3, 31), D(2027, 4, 30)])
        self.assertEqual(fp.topup_dates(30, "2028-01-31", "2028-03-01"), [D(2028, 2, 29)])     # leap year
        self.assertEqual(fp.topup_dates(15, "2026-01-15", "2026-01-31"), [])      # strictly after the statement
        self.assertEqual(fp.next_topup(15, D(2026, 1, 15)), D(2026, 2, 15))

    def test_units_after_the_sales_charge(self):
        self.assertEqual(fp._units_for(50, 2.0, 8.0), 6.125)                     # 49 / 8
        self.assertEqual(fp._units_for(100, 0, 3.0), 33.333)                     # 3 decimals, like statements

    def test_total_return_reinvests_payouts_on_the_ex_date(self):
        navs = [(D(2026, 1, 1), 10.0), (D(2026, 1, 2), 9.5), (D(2026, 1, 3), 9.5)]
        idx = fp.total_return_index(navs, [{"ex_date": D(2026, 1, 2), "amount": 0.5}])
        self.assertAlmostEqual(idx[-1][1], 1.0)                                  # the drop is the payout, not a loss
        self.assertAlmostEqual(fp.total_return_index(navs)[-1][1], 0.95)        # price only

    def test_annualised_and_calendar_returns(self):
        # doubling in exactly 3 years = 2^(1/3) − 1 a year
        navs = [(D(2023, 6, 10), 10.0), (D(2025, 12, 31), 15.0), (D(2026, 6, 10), 20.0)]
        r = fp.returns_from_index(fp.total_return_index(navs))
        self.assertAlmostEqual(r["periods"]["3Y"], (2 ** (1 / 3) - 1) * 100, places=6)
        self.assertIsNone(r["periods"]["5Y"])                                    # not enough history
        cal = fp.returns_from_index(fp.total_return_index(
            [(D(2024, 12, 31), 10.0), (D(2025, 12, 31), 11.0), (D(2026, 3, 1), 12.1)]))["calendar"]
        self.assertAlmostEqual(cal[2025], 10.0)
        self.assertAlmostEqual(cal[2026], 10.0)

    def test_your_yearly_return(self):
        self.assertAlmostEqual(fp.xirr([(D(2025, 1, 1), -100), (D(2026, 1, 1), 110)]), 10.0, places=1)
        self.assertIsNone(fp.xirr([(D(2025, 1, 1), -100)]))

    def test_risk_class_from_weekly_swings(self):
        navs = _navs(D(2021, 6, 1), D(2026, 6, 1), lambda d: 10 + (0.6 if d.isocalendar()[1] % 2 else 0))
        rk = fp.returns_from_index(fp.total_return_index(navs))["risk"]
        self.assertIn(rk["srri"], range(1, 8))
        self.assertGreater(rk["volatility_weekly"], 25)                          # ±6% every week → class 7
        self.assertEqual(rk["srri"], 7)


# ------------------------------------------------------------------ the automatic rows
class TestAutomaticTransactions(_Tmp):
    def market(self, end=TODAY, price=lambda d: 10.0, dists=()):
        return {A: {"navs": _navs(D(2025, 1, 1), end, price), "distributions": list(dists)}}

    def test_each_topup_once_and_a_missed_month_caught_up(self):
        _write_funds(self.dir, [(A, "Test Fund", 100, 10, "2026-03-20", "", "none", 50, 15, 2, "")])
        funds = fp.load_funds(self.dir)
        first = fp.sync_transactions(self.dir, funds, self.market(), TODAY)
        again = fp.sync_transactions(self.dir, funds, self.market(), TODAY)
        rows = [t for _l, t in fp.load_transactions(self.dir)]
        self.assertEqual([t["date"] for t in rows], ["2026-04-15", "2026-05-15"])      # Mar 15 is before the statement
        self.assertEqual(len(first), 2)
        self.assertEqual(again, [])                                                    # never twice
        self.assertEqual({t["ref"] for t in rows}, {"topup:2026-04", "topup:2026-05"})
        self.assertEqual(rows[0]["units"], 4.9)                                        # 49 / 10
        self.assertEqual(rows[0]["status"], "estimated")

    def test_a_topup_waits_for_its_price_then_is_priced_once(self):
        _write_funds(self.dir, [(A, "Test Fund", 100, 10, "2026-05-01", "", "none", 50, 15, 0, "")])
        funds = fp.load_funds(self.dir)
        fp.sync_transactions(self.dir, funds, self.market(end=D(2026, 5, 14)), D(2026, 5, 15))
        (_l, t), = fp.load_transactions(self.dir)
        self.assertEqual((t["status"], t["units"]), ("pending", None))
        s = fp.compute_fund_portfolio(funds, fp.load_transactions(self.dir), self.market(end=D(2026, 5, 14)),
                                      today=D(2026, 5, 15))
        self.assertAlmostEqual(s["funds"][0]["market_value"], 100 * 10 + 50)           # counted at its cash value
        fp.sync_transactions(self.dir, funds, self.market(end=D(2026, 5, 18), price=lambda d: 12.5),
                             D(2026, 5, 18))
        (_l, t), = fp.load_transactions(self.dir)
        self.assertEqual((t["status"], t["units"], t["price"]), ("estimated", 4.0, 12.5))

    def test_a_confirmed_row_is_never_changed(self):
        _write_funds(self.dir, [(A, "Test Fund", 100, 10, "2026-05-01", "", "none", 50, 15, 0, "")])
        with open(os.path.join(self.dir, fp.TXN_FILE), "w", encoding="utf-8") as f:
            f.write("date,isin,type,amount,units,price,fee_pct,status,ref,note\n"
                    f"2026-05-15,{A},topup,50,4.876,,,confirmed,topup:2026-05,from my statement\n")
        before = open(os.path.join(self.dir, fp.TXN_FILE), encoding="utf-8").read()
        fp.sync_transactions(self.dir, fp.load_funds(self.dir), self.market(end=D(2026, 5, 29)), D(2026, 5, 29))
        self.assertEqual(open(os.path.join(self.dir, fp.TXN_FILE), encoding="utf-8").read(), before)

    def test_payouts_reinvested_or_paid_on_the_entitled_units(self):
        dists = [{"ex_date": D(2026, 5, 29), "amount": 0.05, "pay_date": D(2026, 6, 2)}]
        _write_funds(self.dir, [(A, "Reinvested", 100, 10, "2026-05-01", "", "reinvest", 50, 15, 0, ""),
                                (B, "Paid out", 200, 10, "2026-05-01", "", "cash", "", "", 0, "")])
        m = self.market(dists=dists)
        m[B] = dict(m[A])
        funds = fp.load_funds(self.dir)
        fp.sync_transactions(self.dir, funds, m, TODAY)
        rows = {(t["isin"], t["type"]): t for _l, t in fp.load_transactions(self.dir)}
        re_row = rows[(A, "reinvest")]
        self.assertAlmostEqual(re_row["amount"], round(105 * 0.05, 2))                # 100 + the 15 May top-up's 5
        self.assertAlmostEqual(re_row["units"], round(re_row["amount"] / 10, 3))
        self.assertAlmostEqual(rows[(B, "payout")]["amount"], 10.0)                    # 200 × 0.05
        s = fp.compute_fund_portfolio(funds, fp.load_transactions(self.dir), m, today=TODAY)
        a, b = s["funds"]
        self.assertAlmostEqual(a["invested"], 1000 + 50)            # a reinvested payout is return, not new money
        self.assertAlmostEqual(a["units"], 105 + re_row["units"])
        self.assertAlmostEqual(b["payouts_received"], 10.0)
        self.assertAlmostEqual(b["units"], 200)

    def test_a_provisional_payout_is_corrected_when_published(self):
        _write_funds(self.dir, [(A, "Reinvested", 100, 10, "2026-05-01", "", "reinvest", "", "", 0, "")])
        funds = fp.load_funds(self.dir)
        prov = [{"ex_date": D(2026, 5, 15), "amount": 0.05, "pay_date": None, "provisional": True}]
        fp.sync_transactions(self.dir, funds, self.market(dists=prov), TODAY)
        (_l, t), = fp.load_transactions(self.dir)
        self.assertIn("provisional", t["note"])
        real = [{"ex_date": D(2026, 5, 15), "amount": 0.06, "pay_date": D(2026, 5, 19)}]
        fp.sync_transactions(self.dir, funds, self.market(dists=real), TODAY)
        (_l, t), = fp.load_transactions(self.dir)
        self.assertAlmostEqual(t["amount"], 6.0)
        self.assertAlmostEqual(t["units"], 0.6)
        self.assertNotIn("provisional", t["note"])

    def test_rows_on_or_before_the_statement_are_ignored(self):
        _write_funds(self.dir, [(A, "Test Fund", 100, 10, "2026-05-01", "", "none", "", "", 0, "")])
        with open(os.path.join(self.dir, fp.TXN_FILE), "w", encoding="utf-8") as f:
            f.write(f"date,isin,type,amount,units\n2026-04-01,{A},buy,100,10\n")
        s = fp.compute_fund_portfolio(fp.load_funds(self.dir), fp.load_transactions(self.dir), self.market(),
                                      today=TODAY)
        self.assertEqual(s["funds"][0]["units"], 100)
        self.assertTrue(any("ignored" in w for w in s["warnings"]))


# ------------------------------------------------------------------ the figures
class TestFundFigures(_Tmp):
    def setUp(self):
        super().setUp()
        self.m = fake_funds.market(TODAY)
        with open(os.path.join(self.dir, fp.FUNDS_FILE), "w", encoding="utf-8") as f:
            f.write(fake_funds.funds_csv(TODAY))
        self.funds = fp.load_funds(self.dir)
        fp.sync_transactions(self.dir, self.funds, self.m, TODAY)
        self.s = fp.compute_fund_portfolio(self.funds, fp.load_transactions(self.dir), self.m,
                                           usd_kes={"rate": 130.0}, today=TODAY)

    def test_value_gain_and_kes(self):
        t = self.s["totals"]
        self.assertEqual(t["n_available"], 3)
        self.assertAlmostEqual(t["market_value"], sum(h["market_value"] for h in self.s["funds"]))
        self.assertAlmostEqual(t["gain"], t["market_value"] - t["invested"])
        self.assertAlmostEqual(t["market_value_kes"], t["market_value"] * 130.0)
        for h in self.s["funds"]:
            self.assertAlmostEqual(h["market_value"], h["units"] * h["nav"] + h["pending_value"], places=6)
            self.assertTrue(h["check"]["ok"])                       # the statement price matches the source

    def test_profit_since_the_statement_adds_up(self):
        # Σ daily profit after the statement = value now − value then − money added + cash paid out
        for h in self.s["funds"]:
            start = D.fromisoformat(h["as_of"])
            nav0 = fp.nav_on_or_before(h["navs"], start)[1]
            added = sum(t["amount"] for t in h["transactions"] if t["type"] in ("topup", "buy"))
            pl = sum(v for d, v in fp._position(dict(h, units=h["statement_units"], avg_cost=h["statement_avg_cost"]),
                                                h["transactions"], h["navs"], h["distributions"])["daily"] if d > start)
            expect = h["market_value"] - h["statement_units"] * nav0 - added + h["payouts_received"]
            self.assertAlmostEqual(pl, expect, delta=0.05, msg=h["name"])

    def test_networth_includes_funds_in_kes(self):
        import page_portfolio
        nw = page_portfolio.networth_summary(None, None, None, {"rate": 130.0}, self.s)
        self.assertTrue(nw["has_funds"])
        self.assertAlmostEqual(nw["total"], self.s["totals"]["market_value"] * 130.0)
        no_fx = page_portfolio.networth_summary(None, None, None, None, self.s)
        self.assertTrue(no_fx["fx_missing"])
        self.assertEqual(no_fx["total"], 0.0)                      # never converted at a guessed rate
        self.assertIsNone(page_portfolio.networth_summary(None, None, None, None, None))

    def test_a_fund_without_prices_is_left_out(self):
        m = dict(self.m)
        m[self.funds[0]["isin"]] = {"navs": [], "distributions": []}
        s = fp.compute_fund_portfolio(self.funds, [], m, today=TODAY)
        self.assertEqual(s["missing"], [self.funds[0]["name"]])
        self.assertEqual(s["totals"]["n_available"], 2)


# ------------------------------------------------------------------ pages
class TestFundPages(TestFundFigures):
    def rg(self):
        from report_generator import ReportGenerator
        return ReportGenerator(output_dir=self.dir, cache_dir=self.dir)

    def test_tab_and_pages_render(self):
        import page_portfolio
        rg = self.rg()
        written = rg.generate_fund_pages(self.s, self.m)
        self.assertEqual(len(written), 3)
        body = str(page_portfolio.build(rg, funds_summary=self.s, usd_kes={"rate": 130.0}, today=TODAY))
        for anchor in ('id="funds-top"', 'id="funds-profit"', 'id="funds-cards"', 'id="funds-compare"',
                       'id="funds-plan"', 'href="#funds"', 'data-panel="funds"'):
            self.assertIn(anchor, body)
        for h in self.s["funds"]:
            self.assertIn(f'href="fund_{h["isin"]}.html"', body)
        for fn in written.values():
            html = open(os.path.join(self.dir, fn), encoding="utf-8").read()
            ids = re.findall(r'\sid="([^"]+)"', html)
            self.assertEqual(len(ids), len(set(ids)), fn)
            for tab in ("summary", "performance", "holdings", "competitors", "money", "news", "glossary"):
                self.assertIn(f'data-panel="{tab}"', html)
            self.assertIn("data-hide-toggle", html)                 # private: amounts can be hidden

    def test_hostile_names_and_headlines_are_text(self):
        import page_funds
        hostile = '<img src=x onerror="alert(1)">'
        h = dict(self.s["funds"][0], name="Fund " + hostile,
                 news=[{"title": "News " + hostile, "url": "javascript:alert(1)", "source": hostile,
                        "published_utc": "01 Jan 2026"}])
        h["facts"] = dict(h["facts"], objective=hostile, top_holdings=[{"name": hostile, "weight": 5.0}])
        _t, _s, body = page_funds.build_fund_page(self.rg(), h, self.m)
        self.assertNotIn(hostile, str(body))
        self.assertNotIn("javascript:alert", str(body))

    def test_funds_sharing_a_topup_day(self):
        import page_funds
        same = dict(self.s, funds=[dict(h, monthly_amount=40.0, monthly_day=20, next_topup="2026-06-20",
                                        next_topup_units_est=1.0, fee_pct=1.0) for h in self.s["funds"]])
        out = str(page_funds.funds_tab(self.rg(), same, today=TODAY))
        self.assertIn("next: 20 Jun 2026 · $40 + $40 + $40", out)

    def test_a_broken_funds_tab_never_stops_the_page(self):
        import logging
        import page_funds
        import page_portfolio
        previous = logging.root.manager.disable                 # the suite silences logging; listen for this one
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, previous)
        with mock.patch.object(page_funds, "funds_tab", side_effect=KeyError("boom")), \
                self.assertLogs("page_portfolio", level="ERROR"):
            body = str(page_portfolio.build(self.rg(), funds_summary=self.s, usd_kes={"rate": 130.0}, today=TODAY))
        self.assertIn("be built this run (KeyError)", body)
        self.assertIn('data-panel="summary"', body)

    def test_empty_state_without_funds(self):
        import page_funds
        out = str(page_funds.funds_tab(self.rg(), None))
        self.assertIn('id="funds-top"', out)
        self.assertIn("funds.csv", out)


# ------------------------------------------------------------------ sources (offline)
class _Resp:
    def __init__(self, text="", data=None, status=200):
        self.text, self._data, self.status_code = text, data, status
        self.content = text.encode("utf-8")

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class TestFundSources(unittest.TestCase):
    def test_ft_history_rows(self):
        html = ('<tr><td><span class="mod-ui-hide-small-below">Thursday, October 08, 2026</span></td>'
                '<td>1,234.56</td><td>1,234.56</td><td>1,234.56</td><td>1,234.56</td><td>0</td></tr>'
                '<tr><td><span class="mod-ui-hide-small-below">Wednesday, October 07, 2026</span></td>'
                '<td>9.5</td><td>9.5</td><td>9.5</td><td>9.5</td><td>0</td></tr>')
        with mock.patch.object(fund_data, "_get", return_value=_Resp(data={"html": html})):
            rows = fund_data.ft_history("1", today=D(2026, 10, 9))
        self.assertEqual(rows, [(D(2026, 10, 7), 9.5), (D(2026, 10, 8), 1234.56)])

    def test_ft_lookup_takes_the_exact_class_and_currency(self):
        found = {"data": {"security": [{"symbol": f"{A}:EUR", "xid": "1", "name": "x"},
                                       {"symbol": f"{A}:USD", "xid": "2", "name": "Fund &amp; Co"}]}}
        with mock.patch.object(fund_data, "_get", return_value=_Resp(data=found)):
            self.assertEqual(fund_data.ft_lookup(A), {"xid": "2", "symbol": f"{A}:USD", "name": "Fund & Co"})

    def test_cross_check_reports_differences(self):
        a = [(D(2026, 1, 1), 10.0), (D(2026, 1, 2), 10.1), (D(2026, 1, 5), 10.2)]
        b = [(D(2026, 1, 1), 10.0), (D(2026, 1, 2), 10.104), (D(2026, 1, 5), 10.3)]
        x = fund_data._cross_check(a, b)
        self.assertEqual((x["compared"], x["mismatches"]), (3, 1))     # 0.004 is rounding; 0.1 is not

    def test_news_drops_noise_old_items_and_unsafe_links(self):
        now = dt.datetime.now(dt.timezone.utc)
        recent = now.strftime("%a, %d %b %Y %H:%M:%S GMT")
        old = (now - dt.timedelta(days=400)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        items = "".join(f"<item><title>{t} - Src</title><link>{u}</link><source>Src</source><pubDate>{p}</pubDate></item>"
                        for t, u, p in (("Fund wins award", "https://example.com/a", recent),
                                        ("Fund wins award", "https://example.com/b", recent),
                                        ("XYZ Stock Price and Quote", "https://example.com/c", recent),
                                        ("Old story", "https://example.com/d", old),
                                        ("Script", "javascript:alert(1)", recent)))
        with mock.patch.object(fund_data, "_get", return_value=_Resp(text=f"<rss><channel>{items}</channel></rss>")):
            news = fund_data.fund_news('"Fund"')
        self.assertEqual([n["title"] for n in news], ["Fund wins award"])

    def test_news_queries(self):
        self.assertEqual(fund_data.news_queries("Example Global Funds - World Bond Opportunities Fund A2"),
                         ['"World Bond Opportunities" Example', '"World Bond" Example'])

    def test_made_up_market_file_for_sandbox_runs(self):
        with tempfile.TemporaryDirectory() as d:
            path = fake_funds.write_market(os.path.join(d, "m.json"), TODAY)
            with mock.patch.dict(os.environ, {"FUND_DATA_FILE": path}):
                m = fund_data.fetch_all([], d, today=TODAY)
        f0 = fake_funds.FUNDS[0]["isin"]
        self.assertIn(f0, m)
        self.assertIsInstance(m[f0]["navs"][0][0], D)
        self.assertIsInstance(m[f0]["distributions"][0]["ex_date"], D)


# ------------------------------------------------------------------ privacy (local only)
def _tracked_text():
    try:
        names = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    out = []
    for rel in names.decode().split("\0"):
        if not rel:
            continue
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                out.append((rel, f.read()))
        except (OSError, UnicodeDecodeError):
            continue
    return out


class TestYourFundsStayPrivate(unittest.TestCase):
    def test_your_real_funds_are_not_in_any_tracked_file(self):
        # LOCAL ONLY. Reads your private portfolio/funds.csv (never committed)
        # and checks no tracked file names one of your funds, its competitors
        # or its manager page. Skipped where that file doesn't exist (e.g. CI).
        # A failure names files only, never the values.
        path = os.path.join(ROOT, "portfolio", fp.FUNDS_FILE)
        if not os.path.exists(path):
            self.skipTest("no private portfolio/funds.csv on this machine")
        files = _tracked_text()
        if files is None:
            self.skipTest("git not available")
        secrets = set()
        for f in fp.load_funds(os.path.join(ROOT, "portfolio")):
            secrets.update([f["isin"], *f["peers"]])
            secrets.add(f["name"].lower())
            if f.get("factsheet_url"):
                tail = f["factsheet_url"].rstrip("/").split("/")[-1]
                if len(tail) > 12 and "." not in tail:          # a fund's page name, not a site's address
                    secrets.add(tail.lower())
        # the statement's precise figures (units, average cost, price) — long enough not to occur by chance
        figures = {f"{x:g}" for f in fp.load_funds(os.path.join(ROOT, "portfolio"))
                   for x in (f["units"], f["avg_cost"], f.get("statement_nav")) if x and len(f"{x:g}") >= 6}
        hits = sorted({rel for rel, text in files for s in secrets if s and s in (text if s.isupper() else text.lower())}
                      | {rel for rel, text in files for n in figures
                         if re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", text)})
        self.assertEqual(hits, [], "a tracked file names one of your funds")


if __name__ == "__main__":
    unittest.main()
