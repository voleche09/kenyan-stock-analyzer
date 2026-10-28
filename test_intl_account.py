"""
Tests for the international account (src/broker_account.py): money deposited,
cash, and growth on the deposits — on made-up figures only.
"""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import broker_account as ba     # noqa: E402
import page_portfolio           # noqa: E402


def _ips(value=1200.0, cost=1500.0, missing=()):
    return {"as_of": "2026-01-15", "holdings": [], "missing_symbols": list(missing),
            "totals": {"market_value": value, "cost_basis": cost, "gain": value - cost, "gain_pct": None,
                       "day_change_value": 5.0, "day_change_pct": 0.4, "est_annual_dividend": 0.0,
                       "n_holdings": 2, "n_available": 2, "fx_available": True}}


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.dir = self._t.name

    def write(self, text):
        with open(os.path.join(self.dir, ba.ACCOUNT_FILE), "w", encoding="utf-8") as f:
            f.write(text)


class TestLoadAccount(_Tmp):
    def test_no_file_changes_nothing(self):
        self.assertIsNone(ba.load_account(self.dir))
        self.assertIsNone(ba.summarise(None, _ips()))

    def test_deposits_withdrawals_and_the_latest_cash(self):
        self.write("date,type,amount,note\n2025-03-01,deposit,600,\n2025-06-01,Deposit,\"$500.00\",second\n"
                   "2025-08-01,withdrawal,100,\n2025-09-30,cash,40,\n2026-01-10,cash,12.5,newest\n"
                   "2025-12-01,cash,99,older than the newest\n2025-05-01,dividend,3,not a type\n"
                   "2025-05-02,deposit,0,zero\n")
        a = ba.load_account(self.dir)
        self.assertEqual((a["deposits"], a["withdrawals"]), (1100.0, 100.0))
        self.assertEqual((a["cash"], a["cash_as_of"]), (12.5, "2026-01-10"))
        self.assertEqual(a["first_date"], "2025-03-01")
        self.assertEqual(a["rows"], 6)                       # the bad type and the zero deposit are skipped

    def test_header_aliases(self):
        self.write("Date,Kind,Amount (USD),Notes\n15/02/2026,transfer in,250,\n")
        self.assertEqual(ba.load_account(self.dir)["deposits"], 250.0)


class TestAccountFigures(unittest.TestCase):
    def acct(self, **over):
        a = {"deposits": 1100.0, "withdrawals": 100.0, "cash": 12.5, "cash_as_of": "2026-01-10",
             "first_date": "2025-03-01", "rows": 3}
        a.update(over)
        return a

    def test_growth_is_against_the_money_deposited(self):
        s = ba.summarise(self.acct(), _ips(value=1200.0, cost=1500.0), realized_usd=80.0)
        self.assertEqual(s["net_deposited"], 1000.0)
        self.assertAlmostEqual(s["value"], 1212.5)                  # shares + cash
        self.assertAlmostEqual(s["growth"], 212.5)
        self.assertAlmostEqual(s["growth_pct"], 21.25)
        self.assertAlmostEqual(s["unrealized"], -300.0)             # paper loss on today's shares
        self.assertAlmostEqual(s["other"], 512.5)                   # growth − paper loss
        self.assertAlmostEqual(s["other_unrecorded"], 432.5)        # … less the recorded sales
        self.assertTrue(s["complete"])

    def test_a_holding_without_a_price_makes_it_incomplete(self):
        self.assertFalse(ba.summarise(self.acct(), _ips(missing=["ZZZ"]))["complete"])

    def test_networth_counts_cash_and_deposits(self):
        ips = dict(_ips(), account=ba.summarise(self.acct(), _ips()))
        nw = page_portfolio.networth_summary(None, None, ips, {"rate": 130.0})
        self.assertAlmostEqual(nw["intl"]["value_usd"], 1212.5)
        self.assertAlmostEqual(nw["intl"]["cost_usd"], 1000.0)
        self.assertAlmostEqual(nw["intl"]["gain_usd"], 212.5)
        self.assertAlmostEqual(nw["total"], 1212.5 * 130.0)
        self.assertAlmostEqual(nw["gain"], 212.5 * 130.0)
        before = page_portfolio.networth_summary(None, None, _ips(), {"rate": 130.0})   # no account: as before
        self.assertAlmostEqual(before["intl"]["value_usd"], 1200.0)
        self.assertAlmostEqual(before["intl"]["cost_usd"], 1500.0)

    def test_incomplete_keeps_the_holdings_cost(self):
        ips = dict(_ips(missing=["ZZZ"]), account=ba.summarise(self.acct(), _ips(missing=["ZZZ"])))
        nw = page_portfolio.networth_summary(None, None, ips, {"rate": 130.0})
        self.assertAlmostEqual(nw["intl"]["cost_usd"], 1500.0)
        self.assertAlmostEqual(nw["intl"]["value_usd"], 1212.5)


class TestAccountOnThePage(unittest.TestCase):
    def build(self, ips):
        from report_generator import ReportGenerator
        with tempfile.TemporaryDirectory() as out:
            return str(page_portfolio._intl_tab(ReportGenerator(output_dir=out, cache_dir=out), ips,
                                                history_rows=[], tracker=None, intl_history={}))

    def test_section_and_figures(self):
        a = ba.summarise({"deposits": 1000.0, "withdrawals": 0.0, "cash": 12.5, "cash_as_of": "2026-01-10",
                          "first_date": "2025-03-01", "rows": 2}, _ips(), realized_usd=80.0)
        html = self.build(dict(_ips(), account=a))
        for bit in ('id="intl-account"', "Money you deposited", "$1,000.00", "Account value", "$1,212.50",
                    "Growth on your deposits", "+$212.50", "Cash in the account", "$12.50", "since 01 Mar 2025",
                    "−$300.00", "+$80.00", "+$432.50", "includes profits reinvested", "dashboard's prices"):
            self.assertIn(bit, html)

    def test_incomplete_hides_growth(self):
        a = ba.summarise({"deposits": 1000.0, "withdrawals": 0.0, "cash": None, "cash_as_of": None,
                          "first_date": "2025-03-01", "rows": 1}, _ips(missing=["ZZZ"]))
        html = self.build(dict(_ips(missing=["ZZZ"]), account=a))
        self.assertNotIn('data-kpi="Growth on your deposits"', html)
        self.assertIn("shown rather than understated", html)

    def test_without_an_account_nothing_changes(self):
        self.assertNotIn('id="intl-account"', self.build(_ips()))


class TestAccountStaysPrivate(unittest.TestCase):
    def test_ignored_by_git_and_docker(self):
        out = subprocess.run(["git", "check-ignore", "portfolio/" + ba.ACCOUNT_FILE], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "portfolio/" + ba.ACCOUNT_FILE)
        with open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8") as f:
            self.assertIn("portfolio/*.csv", f.read())


if __name__ == "__main__":
    unittest.main()
