"""
Tests for recording sales (src/sales.py): whole and partial sales of Kenyan
and international shares, bonds and funds — the arithmetic (average cost,
coupons and accrued interest, the dealing-day price), the checks that stop
a wrong sale being saved, the dashboard app's sale API, the pages, and
sell.py. All on made-up holdings (placeholder tickers, tools/fake_bonds.py,
test-only fund codes) — nothing here is anyone's real portfolio.
"""

import datetime as dt
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "tools")]

import fake_bonds                  # noqa: E402
import funds_portfolio as fp       # noqa: E402
import sales                       # noqa: E402

D = dt.date


def lot(sym, q, price, date=None):
    return {"symbol": sym, "quantity": float(q), "buy_price": float(price), "buy_date": date, "note": ""}


def sale(kind, code, q, price, date, fees=0.0, line=None):
    return {"kind": kind, "code": code, "quantity": float(q), "sell_price": float(price), "date": date,
            "fees": float(fees), "line": line, "note": ""}


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.dir = self._t.name

    def holdings(self, rows, name="holdings.csv"):
        _write(os.path.join(self.dir, name), "symbol,quantity,buy_price,buy_date,note\n"
               + "".join(f"{s},{q},{p},{d or ''},\n" for s, q, p, d in rows))

    def sales_csv(self, rows):
        _write(os.path.join(self.dir, sales.SALES_FILE), "date,kind,code,quantity,sell_price,fees,note\n"
               + "".join(",".join(str(x) for x in r) + "\n" for r in rows))


# ------------------------------------------------------------------ shares: the arithmetic
class TestShareSales(unittest.TestCase):
    def test_partial_sale_at_average_cost(self):
        left, done, problems = sales.apply_stock_sales([lot("EQTY", 200, 40, "2025-11-03")],
                                                       [sale("nse", "EQTY", 50, 48, "2026-01-10", fees=10)], "nse")
        self.assertEqual(problems, [])
        (r,) = done
        self.assertAlmostEqual(r["proceeds"], 50 * 48 - 10)
        self.assertAlmostEqual(r["cost"], 50 * 40)
        self.assertAlmostEqual(r["gain"], 390.0)
        self.assertAlmostEqual(r["gain_pct"], 19.5)
        self.assertEqual(r["left_after"], 150)
        (rest,) = left
        self.assertEqual((rest["quantity"], rest["buy_price"], rest["orig_quantity"]), (150, 40, 200))

    def test_several_purchases_shrink_together_so_the_average_cost_holds(self):
        lots = [lot("ABSA", 300, 12, "2025-06-01"), lot("ABSA", 100, 16.5, "2025-12-01")]
        left, (r,), _p = sales.apply_stock_sales(lots, [sale("nse", "ABSA", 200, 15, "2026-01-05")], "nse")
        self.assertAlmostEqual(r["avg_cost"], 13.125)                        # (3600 + 1650) / 400
        self.assertAlmostEqual(r["cost"], 2625.0)
        self.assertAlmostEqual(r["gain"], 375.0)
        self.assertEqual([l["quantity"] for l in left], [150, 50])
        cost_left = sum(l["quantity"] * l["buy_price"] for l in left)
        self.assertAlmostEqual(cost_left / sum(l["quantity"] for l in left), 13.125)   # unchanged

    def test_selling_everything_leaves_nothing(self):
        left, (r,), _p = sales.apply_stock_sales([lot("AAPL", 10, 150.25), lot("MSFT", 5, 380)],
                                                 [sale("intl", "AAPL", 10, 201.5, "2026-02-02")], "intl")
        self.assertEqual([l["symbol"] for l in left], ["MSFT"])
        self.assertEqual(r["left_after"], 0.0)
        self.assertEqual(r["currency"], "USD")
        self.assertAlmostEqual(r["gain"], 10 * (201.5 - 150.25))

    def test_a_purchase_after_the_sale_is_not_touched(self):
        lots = [lot("EQTY", 100, 40, "2025-01-01"), lot("EQTY", 50, 44, "2025-07-01")]
        left, (r,), problems = sales.apply_stock_sales(lots, [sale("nse", "EQTY", 100, 45, "2025-06-01")], "nse")
        self.assertEqual(problems, [])
        self.assertAlmostEqual(r["avg_cost"], 40.0)                         # only what was held then
        self.assertEqual([(l["quantity"], l["buy_price"]) for l in left], [(50, 44)])

    def test_a_purchase_without_a_date_counts_as_held_before(self):
        _left, (r,), problems = sales.apply_stock_sales([lot("EQTY", 100, 40)],
                                                        [sale("nse", "EQTY", 40, 45, "2020-01-01")], "nse")
        self.assertEqual(problems, [])
        self.assertTrue(r["undated"])

    def test_a_purchase_on_the_sale_day_counts(self):
        _l, done, problems = sales.apply_stock_sales([lot("EQTY", 100, 40, "2026-01-10")],
                                                     [sale("nse", "EQTY", 100, 41, "2026-01-10")], "nse")
        self.assertEqual((len(done), problems), (1, []))

    def test_selling_more_than_you_held_is_left_out_not_guessed(self):
        lots = [lot("EQTY", 100, 40, "2025-01-01"), lot("EQTY", 100, 42, "2026-03-01")]
        left, done, problems = sales.apply_stock_sales(lots, [sale("nse", "EQTY", 150, 45, "2026-02-01", line=4)],
                                                       "nse")
        self.assertEqual(done, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("line 4", problems[0])
        self.assertEqual(sum(l["quantity"] for l in left), 200)            # holdings untouched

    def test_two_sales_in_a_row(self):
        left, done, _p = sales.apply_stock_sales(
            [lot("EQTY", 100, 40, "2025-01-01")],
            [sale("nse", "EQTY", 30, 50, "2025-03-01"), sale("nse", "EQTY", 70, 35, "2025-04-01")], "nse")
        self.assertEqual(left, [])
        self.assertEqual([round(r["gain"], 2) for r in done], [300.0, -350.0])

    def test_flows_for_after_inflation(self):
        _l, (r,), _p = sales.apply_stock_sales([lot("EQTY", 100, 40, "2025-01-01"), lot("EQTY", 100, 50, "2025-02-01")],
                                               [sale("nse", "EQTY", 100, 60, "2025-06-01")], "nse")
        self.assertFalse(r["undated"])
        self.assertEqual(r["flows"], [("2025-01-01", -2000.0), ("2025-02-01", -2500.0), ("2025-06-01", 6000.0)])

    def test_apply_never_takes_the_holdings_down(self):
        lots = [lot("EQTY", 100, 40)]
        with mock.patch.object(sales, "_run", side_effect=KeyError("boom")):
            left, done = sales.apply("/nowhere", "nse", lots, sales=[])
        self.assertEqual((left, done), (lots, []))


# ------------------------------------------------------------------ bonds: the arithmetic
class TestBondSales(unittest.TestCase):
    def setUp(self):
        self.ctx = fake_bonds.use()
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def test_part_of_a_taxed_bond_with_coupons_and_accrued_interest(self):
        lots = [{"issue": "FXD9/2020/020", "face_value": 400000.0, "purchase_price_pct": 101.2,
                 "purchase_date": "2023-02-01", "note": ""}]
        left, (r,), problems = sales.apply_bond_sales(lots, [sale("bond", "FXD9/2020/020", 100000, 102, "2026-01-15")])
        self.assertEqual(problems, [])
        self.assertAlmostEqual(r["clean"], 102000.0)
        self.assertAlmostEqual(r["cost"], 101200.0)
        # 24 Aug 2025 → 15 Jan 2026 = 144 days at 12.9% a year on KES 100,000
        self.assertAlmostEqual(r["accrued"], 100000 * 0.129 / 365 * 144, delta=0.02)
        self.assertAlmostEqual(r["proceeds"], r["clean"] + r["accrued"])
        # the sale's result: clean price + accrued interest − cost
        self.assertAlmostEqual(r["gain"], 800.0 + r["accrued"])
        self.assertAlmostEqual(r["total_gain"], r["gain"])
        self.assertAlmostEqual(r["price_gain"], 800.0)
        # beside it, not in it: six coupons since the purchase (Feb/Aug 2023–2025), 10% withholding tax
        self.assertAlmostEqual(r["coupons_received"], 6 * 6450 * 0.9, places=2)
        (rest,) = left
        self.assertEqual(rest["face_value"], 300000.0)

    def test_a_bond_partly_repaid_is_priced_on_what_is_still_owed(self):
        lots = [{"issue": "IFB9/2021/7", "face_value": 250000.0, "purchase_price_pct": 100.0,
                 "purchase_date": "2024-01-10", "note": ""}]
        left, (r,), _p = sales.apply_bond_sales(lots, [sale("bond", "IFB9/2021/7", 250000, 100.5, "2026-06-01")])
        self.assertEqual(left, [])
        self.assertAlmostEqual(r["principal_received"], 100000.0)          # 40% repaid on 12 Apr 2026
        self.assertAlmostEqual(r["clean"], 150000 * 1.005)
        self.assertAlmostEqual(r["accrued"], 150000 * 0.128 / 365 * 50, delta=0.02)
        self.assertAlmostEqual(r["gain"], 750.0 + r["accrued"])           # 150,750 + 100,000 repaid − 250,000
        self.assertAlmostEqual(r["coupons_received"], 5 * 16000.0)          # tax-free

    def test_more_than_held_or_before_buying_is_left_out(self):
        lots = [{"issue": "FXD9/2020/020", "face_value": 100000.0, "purchase_price_pct": 100.0,
                 "purchase_date": "2024-01-01", "note": ""}]
        for s in (sale("bond", "FXD9/2020/020", 150000, 100, "2025-01-01"),
                  sale("bond", "FXD9/2020/020", 50000, 100, "2023-06-01")):
            left, done, problems = sales.apply_bond_sales(lots, [s])
            self.assertEqual((done, len(problems), left[0]["face_value"]), ([], 1, 100000.0))


# ------------------------------------------------------------------ checking a sale before it's saved
class TestCheckSale(_Tmp):
    def setUp(self):
        super().setUp()
        self.holdings([("EQTY", 100, 40, "2025-01-01"), ("EQTY", 100, 50, "2025-06-01"), ("ABSA", 300, 12, "")])
        self.today = D(2026, 3, 1)

    def check(self, *a, **k):
        k.setdefault("today", self.today)
        return sales.check_sale(self.dir, *a, **k)

    def test_sell_all_saves_and_the_holding_is_gone(self):
        values, summary, warnings = self.check("nse", "eqty", "all", "52.5", "2026-02-10", fees="25")
        self.assertEqual((values["code"], values["quantity"], values["sell_price"], values["fees"]),
                         ("EQTY", 200, 52.5, 25.0))
        self.assertIn("That's all of it", summary)
        self.assertIn("Profit vs what you paid: +KES 1,475.00", summary)     # 10,500 − 25 − 9,000
        self.assertEqual(warnings, [])
        sales.add_sale(self.dir, values)
        self.assertNotIn("EQTY", sales.positions(self.dir, "nse"))
        self.assertEqual(sales.positions(self.dir, "nse")["ABSA"]["quantity"], 300)

    def test_partial_sale_summary(self):
        _v, summary, _w = self.check("nse", "EQTY", "50", "40", "2026-02-10")
        self.assertIn("Loss vs what you paid: −KES 250.00", summary)         # avg 45
        self.assertIn("You'll still hold 150 shares", summary)

    def test_refusals_in_plain_words(self):
        cases = [
            (("nse", "EQTY", "250", "40", "2026-02-10"), "can't sell more than that"),
            (("nse", "EQTY", "50", "40", "2026-04-01"), "in the future"),
            (("nse", "EQTY", "50", "0", "2026-02-10"), "more than zero"),
            (("nse", "EQTY", "-5", "40", "2026-02-10"), "how much you sold"),
            (("nse", "EQTY", "abc", "40", "2026-02-10"), "isn't a number"),
            (("nse", "SCAN", "5", "40", "2026-02-10"), "didn't hold any SCAN"),
            (("nse", "EQTY", "150", "40", "2025-03-01"), "can't sell more than that"),   # only 100 bought by then
            (("nse", "EQTY", "50", "40", "05/06/2026"), "ambiguous"),
            (("fund", "EQTY", "5", "40", "2026-02-10"), "Choose what you sold"),
            (("nse", "EQTY", "50", "", "2026-02-10"), "Enter the price"),
        ]
        for args, words in cases:
            with self.assertRaises(sales.SaleError, msg=args) as cm:
                self.check(*args)
            self.assertIn(words, str(cm.exception), args)

    def test_a_backdated_sale_cant_break_a_later_one(self):
        sales.add_sale(self.dir, self.check("nse", "EQTY", "180", "45", "2026-02-20")[0])
        with self.assertRaises(sales.SaleError) as cm:
            self.check("nse", "EQTY", "50", "45", "2026-02-01")      # 200 held on 1 Feb, but 180 go on 20 Feb
        self.assertIn("later sale", str(cm.exception))
        self.check("nse", "EQTY", "20", "45", "2026-02-01")           # exactly what's left is fine

    def test_warnings_ask_are_you_sure(self):
        _v, _s, warnings = self.check("nse", "EQTY", "10.5", "450", "2026-02-10", today_price=45.0)
        self.assertTrue(any("trades at KES 45.00" in w for w in warnings))
        self.assertTrue(any("whole numbers" in w for w in warnings))
        _v, _s, warnings = self.check("nse", "EQTY", "1", "40", "2026-02-10", fees="30")
        self.assertTrue(any("Fees" in w for w in warnings))

    def test_undated_purchases_are_flagged(self):
        _v, _s, warnings = self.check("nse", "ABSA", "100", "14", "2026-02-10")
        self.assertTrue(any("no date" in w for w in warnings))
        _v, _s, warnings = self.check("nse", "EQTY", "10", "45", "2026-02-10")
        self.assertFalse(any("no date" in w for w in warnings))

    def test_a_hand_typed_row_that_doesnt_add_up_is_reported_and_left_out(self):
        self.sales_csv([("2026-01-05", "nse", "EQTY", 500, 45, "", "typo"), ("2026-01-06", "nse", "ABSA", 100, 14, "", "")])
        lots = sales.lots_for(self.dir, "nse")
        left, done = sales.apply(self.dir, "nse", lots)
        self.assertEqual([r["code"] for r in done], ["ABSA"])
        self.assertEqual(sum(l["quantity"] for l in left if l["symbol"] == "EQTY"), 200)

    def test_header_aliases_and_bad_rows(self):
        _write(os.path.join(self.dir, sales.SALES_FILE),
               "Sale Date,Market,Ticker,Shares,Price (KES),Fees,Notes\n"
               "10 Feb 2026,NSE,eqty,10,50,,\n2026-02-11,crypto,BTC,1,1,,\n2026-02-12,nse,EQTY,0,50,,\n")
        rows = sales.load_sales(self.dir)
        self.assertEqual([(s["date"], s["code"], s["quantity"]) for _l, s in rows], [("2026-02-10", "EQTY", 10.0)])


class TestCheckBondSale(_Tmp):
    def setUp(self):
        super().setUp()
        self.ctx = fake_bonds.use()
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        _write(os.path.join(self.dir, "bonds.csv"), "issue,face_value,purchase_price_pct,purchase_date,note\n"
               "FXD9/2020/020,400000,101.2,2023-02-01,\nIFB9/2021/7,250000,,,\n")

    def test_sell_part_of_a_bond(self):
        values, summary, warnings = sales.check_sale(self.dir, "bond", "fdx9/2020/020", "100,000", "102",
                                                     "2026-01-15", today=D(2026, 3, 1))
        self.assertEqual((values["code"], values["quantity"]), ("FXD9/2020/020", 100000.0))   # typo alias fixed
        self.assertIn("accrued interest", summary)
        self.assertIn("KES 300,000.00 face value", summary)
        self.assertEqual(warnings, [])

    def test_bond_checks(self):
        with self.assertRaises(sales.SaleError):
            sales.check_sale(self.dir, "bond", "FXD9/2020/020", "500000", "100", "2026-01-15", today=D(2026, 3, 1))
        _v, _s, warnings = sales.check_sale(self.dir, "bond", "FXD9/2020/020", "all", "10200", "2026-01-15",
                                            today=D(2026, 3, 1))
        self.assertTrue(any("unusual price" in w for w in warnings))
        with self.assertRaises(sales.SaleError) as cm:
            sales.check_sale(self.dir, "bond", "IFB9/2021/7", "all", "100", "2028-05-01", today=D(2028, 6, 1))
        self.assertIn("repaid", str(cm.exception))

    def test_positions_after_a_bond_sale(self):
        sales.add_sale(self.dir, sales.check_sale(self.dir, "bond", "FXD9/2020/020", "100000", "102", "2026-01-15",
                                                  today=D(2026, 3, 1))[0])
        pos = sales.positions(self.dir, "bond")
        self.assertEqual(pos["FXD9/2020/020"]["quantity"], 300000.0)
        self.assertAlmostEqual(pos["FXD9/2020/020"]["avg_cost"], 101.2)
        self.assertEqual(pos["IFB9/2021/7"]["quantity"], 250000.0)


# ------------------------------------------------------------------ funds
def _isin(n):
    body = f"XS8{n:08d}"
    return body + fp.isin_check_digit(body)


FUND = _isin(77)


def _navs(start, end, price=lambda d: 10.0):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append((d, round(price(d), 4)))
        d += dt.timedelta(days=1)
    return out


class TestFundSales(_Tmp):
    TODAY = D(2026, 6, 10)

    def setUp(self):
        super().setUp()
        _write(os.path.join(self.dir, fp.FUNDS_FILE),
               "isin,name,units,avg_cost,as_of,statement_nav,payouts,monthly_amount,monthly_day,fee_pct,peers\n"
               f"{FUND},Test Fund,100,10,2026-03-20,,cash,50,15,0,\n")
        self.funds = fp.load_funds(self.dir)
        self.market = {FUND: {"navs": _navs(D(2025, 1, 1), self.TODAY, lambda d: 12.0 if d >= D(2026, 5, 1) else 10.0),
                              "distributions": [{"ex_date": D(2026, 5, 29), "amount": 0.05, "pay_date": None}]}}
        fp.sync_transactions(self.dir, self.funds, self.market, self.TODAY)          # Apr + May top-ups

    def summary(self):
        return fp.compute_fund_portfolio(self.funds, fp.load_transactions(self.dir), self.market, today=self.TODAY)

    def test_part_of_a_fund_priced_on_the_dealing_day(self):
        held = sales.fund_positions(self.dir)[FUND]
        self.assertAlmostEqual(held["units"], 100 + 5 + 50 / 12, places=3)        # Apr at 10, May at 12
        row, summary, _w = sales.check_fund_sale(self.dir, FUND, "40", "2026-05-20", today=self.TODAY)
        self.assertEqual((row["units"], row["amount"], row["status"]), (40.0, None, "pending"))
        sales.add_fund_sale(self.dir, row)
        fp.sync_transactions(self.dir, self.funds, self.market, self.TODAY)
        (t,) = [t for _l, t in fp.load_transactions(self.dir) if t["type"] == "sell"]
        self.assertEqual((t["amount"], t["price"], t["status"]), (480.0, 12.0, "estimated"))
        h = self.summary()["funds"][0]
        before_units = 100 + 5 + round(50 / 12, 3)
        avg = (1000 + 50 + 50) / before_units
        self.assertAlmostEqual(h["realized"], 480 - 40 * avg, places=6)
        self.assertAlmostEqual(h["units"], before_units - 40, places=6)
        self.assertAlmostEqual(h["avg_cost"], avg, places=9)                       # unchanged for what's left
        (s,) = h["sales"]
        self.assertAlmostEqual(s["gain"], h["realized"])
        self.assertAlmostEqual(h["total_return_value"], h["gain"] + h["payouts_received"] + h["realized"])
        self.assertFalse(h["sold_out"])

    def test_sell_everything_stops_topups_and_payouts(self):
        row, summary, warnings = sales.check_fund_sale(self.dir, FUND, "all", "2026-05-20", amount="1310.25",
                                                       today=self.TODAY)
        self.assertIn("That's all of it", summary)
        self.assertTrue(any("stops the automatic monthly top-ups" in w for w in warnings))
        self.assertEqual(row["status"], "confirmed")
        sales.add_fund_sale(self.dir, row)
        later = D(2026, 8, 20)
        m = {FUND: dict(self.market[FUND], navs=_navs(D(2025, 1, 1), later, lambda d: 12.0),
                        distributions=[{"ex_date": D(2026, 5, 29), "amount": 0.05, "pay_date": None},
                                       {"ex_date": D(2026, 6, 29), "amount": 0.05, "pay_date": None}])}
        fp.sync_transactions(self.dir, self.funds, m, later)
        rows = [t for _l, t in fp.load_transactions(self.dir)]
        self.assertEqual([t["ref"] for t in rows if t["type"] == "topup"], ["topup:2026-04", "topup:2026-05"])
        # the 29 May payout was recorded before this (backdated) sale was: corrected to nothing; none for June
        self.assertEqual([(t["ref"], t["amount"]) for t in rows if t["type"] == "payout"],
                         [("payout:2026-05-29", 0.0)])
        self.assertIn("nothing was paid", [t for t in rows if t["type"] == "payout"][0]["note"])
        h = fp.compute_fund_portfolio(self.funds, fp.load_transactions(self.dir), m, today=later)["funds"][0]
        self.assertEqual((h["units"], h["invested"], h["market_value"]), (0.0, 0.0, 0.0))
        self.assertTrue(h["sold_out"])
        self.assertIsNone(h["next_topup"])
        self.assertIsNone(h["gain_pct"])
        self.assertEqual(sales.fund_positions(self.dir)[FUND]["units"], 0.0)

    def test_a_sold_fund_still_renders(self):
        import page_funds
        import page_portfolio
        from report_generator import ReportGenerator
        sales.add_fund_sale(self.dir, sales.check_fund_sale(self.dir, FUND, "all", "2026-05-20", amount="1310.25",
                                                            today=self.TODAY)[0])
        s = self.summary()
        rg = ReportGenerator(output_dir=self.dir, cache_dir=self.dir)
        tab = str(page_funds.funds_tab(rg, s, today=self.TODAY))
        self.assertIn("Sold — you hold none", tab)
        self.assertIn("locked in", tab)
        self.assertNotIn("Payout paid to you · ≈", tab)
        _t, _sub, body = page_funds.build_fund_page(rg, s["funds"][0], self.market)
        self.assertIn("Test Fund", str(body))
        events = page_portfolio.coming_up_events(None, None, None, {}, {}, self.TODAY, s)
        self.assertEqual([e for e in events if "payout" in e[1]], [])

    def test_fund_refusals(self):
        for args, words in (((FUND, "500", "2026-05-20"), "can't sell more"),
                            ((FUND, "5", "2026-03-20"), "statement for this fund is dated"),
                            ((_isin(78), "5", "2026-05-20"), "Pick the fund"),
                            ((FUND, "", "2026-05-20"), "number of units")):
            with self.assertRaises(sales.SaleError, msg=args) as cm:
                sales.check_fund_sale(self.dir, *args, today=self.TODAY)
            self.assertIn(words, str(cm.exception))
        _write(os.path.join(self.dir, fp.TXN_FILE), "date,isin,type,amount,units,status,ref\n"
               f"2026-06-01,{FUND},buy,100,,pending,\n")
        with self.assertRaises(sales.SaleError) as cm:
            sales.check_fund_sale(self.dir, FUND, "all", "2026-06-05", today=self.TODAY)
        self.assertIn("isn't priced yet", str(cm.exception))

    def test_amount_far_from_the_price_warns(self):
        cache = os.path.join(self.dir, "cache")
        with mock.patch("fund_data.cached", return_value=self.market[FUND]):
            _r, _s, warnings = sales.check_fund_sale(self.dir, FUND, "10", "2026-05-20", amount="900",
                                                     today=self.TODAY, cache_dir=cache)
        self.assertTrue(any("10% away" in w for w in warnings))

    def test_money_put_in_line_drops_after_a_sale(self):
        sales.add_fund_sale(self.dir, sales.check_fund_sale(self.dir, FUND, "50", "2026-05-20", amount="600",
                                                            today=self.TODAY)[0])
        h = self.summary()["funds"][0]
        put_in = {d: p for d, _v, p in h["value_series"]}
        self.assertGreater(put_in[D(2026, 5, 19)], put_in[D(2026, 5, 20)])
        self.assertAlmostEqual(put_in[self.TODAY], h["invested"])


# ------------------------------------------------------------------ the page's list, and after inflation
class TestSummaries(unittest.TestCase):
    def test_kes_totals_use_the_month_of_the_sale(self):
        _l, nse, _p = sales.apply_stock_sales([lot("EQTY", 100, 40, "2025-01-01")],
                                              [sale("nse", "EQTY", 100, 50, "2025-06-01")], "nse")
        _l, intl, _p = sales.apply_stock_sales([lot("AAPL", 10, 100, "2025-01-01")],
                                               [sale("intl", "AAPL", 10, 90, "2025-07-15")], "intl")
        macro = {"fx": [(D(2025, 7, 1), 129.0)]}
        s = sales.summarise({"nse": nse, "intl": intl}, None, macro, {"rate": 130.0})
        a, n = s["items"]                                                  # newest first
        self.assertEqual((a["code"], a["rate"], a["gain_kes"]), ("AAPL", 129.0, -100 * 129.0))
        self.assertEqual((n["code"], n["gain_kes"]), ("EQTY", 1000.0))
        self.assertEqual((s["totals"]["count"], s["totals"]["wins"], s["totals"]["losses"]), (2, 1, 1))
        self.assertAlmostEqual(s["totals"]["total_gain_kes"], 1000 - 12900)
        no_month = sales.summarise({"intl": intl}, None, None, {"rate": 130.0})
        self.assertEqual(no_month["items"][0]["rate"], 130.0)              # not published yet: today's rate
        self.assertEqual(sales.summarise({"nse": []}, None), None)

    def test_after_inflation_counts_what_you_sold(self):
        import inflation
        cpi = [(D(2024, m, 1), 100.0 + m) for m in range(1, 13)] + [(D(2025, m, 1), 113.0 + m) for m in range(1, 13)]
        _l, nse, _p = sales.apply_stock_sales([lot("EQTY", 100, 40, "2024-01-15"), lot("EABL", 10, 150)],
                                              [sale("nse", "EQTY", 100, 50, "2025-06-01"),
                                               sale("nse", "EABL", 10, 160, "2025-06-01")], "nse")
        real = inflation.portfolio_real(None, None, None, None, {"cpi": cpi}, None, today=D(2025, 12, 31),
                                        sales={"nse": nse})
        part = real["parts"]["stocks"]
        self.assertEqual(len(part["items"]), 1)
        self.assertEqual(part["undated"], ["EABL · sold 10 on 2025-06-01"])
        r = part["result"]
        self.assertAlmostEqual(r["put_in"], 4000.0)
        self.assertAlmostEqual(r["income"], 5000.0)
        self.assertAlmostEqual(r["value"], 0.0)
        self.assertLess(r["real_gain"], r["gain"])


class TestLookbacksIgnoreMoneyInAndOut(_Tmp):
    """'1 Week: −50%' after selling half would be wrong — the money came back to you."""

    def tracker(self, value, cost, days_ago=7):
        import json
        import portfolio
        day = (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()
        _write(os.path.join(self.dir, portfolio.HISTORY_FILE),
               json.dumps([{"date": day, "market_value": value, "cost_basis": cost, "gain_pct": 0}]))
        return portfolio.PortfolioHistoryTracker(self.dir)

    def test_selling_half_is_not_a_loss(self):
        t = self.tracker(10000.0, 8000.0)                # 200 shares at 40, worth 50
        sold_on = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        lb = t.lookback(5000.0, 7, today_cost=4000.0, realized=[(sold_on, 1000.0)])   # sold 100 at 50
        self.assertEqual((lb["change_value"], lb["pct_change"]), (0.0, 0.0))
        self.assertEqual(t.lookback(5000.0, 7)["pct_change"], -50.0)                   # the old way, for contrast

    def test_buying_more_is_not_a_gain_but_the_market_is(self):
        t = self.tracker(10000.0, 8000.0)
        lb = t.lookback(15500.0, 7, today_cost=13000.0)  # put in 5,000 more; prices rose 500
        self.assertEqual(lb["change_value"], 500.0)
        self.assertEqual(lb["pct_change"], 5.0)

    def test_a_sale_before_the_window_is_not_counted(self):
        t = self.tracker(10000.0, 8000.0)
        old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        lb = t.lookback(10000.0, 7, today_cost=8000.0, realized=[(old, 999.0)])
        self.assertEqual(lb["change_value"], 0.0)


# ------------------------------------------------------------------ the dashboard app
class TestSaleApi(_Tmp):
    def setUp(self):
        super().setUp()
        import dashboard_app
        self.dashboard_app = dashboard_app
        cfg = types.SimpleNamespace(portfolio_dir=os.path.join(self.dir, "portfolio"),
                                    report_directory=os.path.join(self.dir, "reports"),
                                    cache_dir=os.path.join(self.dir, "data"))
        for d in (cfg.portfolio_dir, cfg.report_directory, cfg.cache_dir):
            os.makedirs(d)
        self.pdir = cfg.portfolio_dir
        _write(os.path.join(self.pdir, "holdings.csv"), "symbol,quantity,buy_price,buy_date,note\n"
               "EQTY,100,40,2025-01-01,\nEQTY,100,50,2025-06-01,\n")
        _write(os.path.join(self.pdir, fp.FUNDS_FILE), "isin,name,units,avg_cost,as_of,payouts,fee_pct\n"
               f"{FUND},Test Fund,100,10,2026-01-02,none,0\n")

        class Jobs:
            requests = []

            def request(self, kind, delay=0):
                self.requests.append(kind)

        class Lookup:
            LookupUnavailable = RuntimeError

            def quote(self, symbol, market, cache_dir):
                return {"found": True, "price": 45.0, "currency": "KES"}

        self.app = dashboard_app.DashboardApp(cfg, jobs=Jobs(), lookup=Lookup())

    def test_positions_check_save_list_remove(self):
        pos = self.app.positions("nse")["positions"]
        self.assertEqual([(p["code"], p["quantity"], p["avg_cost"]) for p in pos], [("EQTY", 200.0, 45.0)])
        body = {"kind": "nse", "code": "EQTY", "quantity": "all", "sell_price": "48", "sale_date": "2026-02-02",
                "fees": "", "note": "test sale", "dry_run": True}
        r = self.app.sales_add(body)
        self.assertTrue(r["dry_run"])
        self.assertIn("That's all of it", r["summary"])
        self.assertFalse(os.path.exists(os.path.join(self.pdir, sales.SALES_FILE)))   # a review writes nothing
        r = self.app.sales_add(dict(body, dry_run=False))
        self.assertIn("Saved: Sell 200 EQTY", r["message"])
        self.assertEqual(self.app.jobs.requests[-1], "full")
        self.assertEqual(self.app.positions("nse")["positions"], [])
        listed = self.app.sales_list()["entries"]
        self.assertEqual([(e["code"], e["quantity"]) for e in listed], [("EQTY", 200.0)])
        with self.assertRaises(self.dashboard_app.ApiError) as cm:                      # can't sell it twice
            self.app.sales_add(dict(body, dry_run=False))
        self.assertIn("didn't hold any EQTY", cm.exception.message)
        e = listed[0]
        r = self.app.sales_remove({"kind": "sale", "line": e["line"],
                                   "expect": {"date": e["date"], "code": "EQTY", "quantity": 200}})
        self.assertEqual(r["removed"]["code"], "EQTY")
        self.assertEqual(self.app.positions("nse")["positions"][0]["quantity"], 200.0)

    def test_a_purchase_a_sale_needs_cant_be_removed(self):
        self.app.sales_add({"kind": "nse", "code": "EQTY", "quantity": "150", "sell_price": "48",
                            "sale_date": "2026-02-02"})
        rows = self.app.holdings("nse")["entries"]
        first = rows[0]
        with self.assertRaises(self.dashboard_app.ApiError) as cm:
            self.app.holdings_remove({"kind": "nse", "line": first["line"],
                                      "expect": {k: first[k] for k in ("symbol", "quantity", "buy_price", "buy_date")}})
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("Remove that sale first", cm.exception.message)
        self.assertEqual(len(self.app.holdings("nse")["entries"]), 2)                  # nothing removed

    def test_a_bond_a_sale_needs_cant_be_removed_either(self):
        with fake_bonds.use():
            _write(os.path.join(self.pdir, "bonds.csv"), "issue,face_value,purchase_price_pct,purchase_date,note\n"
                   "FXD9/2020/020,400000,,,\n")
            self.app.sales_add({"kind": "bond", "code": "FXD9/2020/020", "quantity": "all", "sell_price": "101",
                                "sale_date": "2026-02-02"})
            row = self.app.holdings("bonds")["entries"][0]
            with self.assertRaises(self.dashboard_app.ApiError) as cm:
                self.app.holdings_remove({"kind": "bonds", "line": row["line"], "expect": dict(row)})
            self.assertEqual(cm.exception.status, 409)

    def test_fund_sale_through_the_app(self):
        self.assertEqual([p["code"] for p in self.app.positions("fund")["positions"]], [FUND])
        r = self.app.sales_add({"kind": "fund", "code": FUND, "quantity": "25", "sale_date": "2026-02-02",
                                "amount": "260"})
        self.assertIn("for $260.00", r["message"])
        (f,) = self.app.sales_list()["fund_entries"]
        self.assertEqual((f["units"], f["amount"], f["status"]), (25.0, 260.0, "confirmed"))
        self.assertAlmostEqual(self.app.positions("fund")["positions"][0]["quantity"], 75.0)
        with self.assertRaises(self.dashboard_app.ApiError):                            # never a non-sale row
            self.app.sales_remove({"kind": "fund", "line": 999, "expect": {"date": f["date"], "isin": FUND,
                                                                            "units": 25}})
        self.app.sales_remove({"kind": "fund", "line": f["line"], "expect": {"date": f["date"], "isin": FUND,
                                                                            "units": 25}})
        self.assertEqual(self.app.sales_list()["fund_entries"], [])

    def test_an_undated_purchase_after_a_sale_is_flagged(self):
        self.app.sales_add({"kind": "nse", "code": "EQTY", "quantity": "50", "sell_price": "48",
                            "sale_date": "2026-02-02"})
        r = self.app.holdings_add({"kind": "nse", "symbol": "EQTY", "quantity": "10", "buy_price": "45",
                                   "buy_date": "", "dry_run": True})
        self.assertTrue(any("without a date" in w for w in r["warnings"]))
        r = self.app.holdings_add({"kind": "nse", "symbol": "EQTY", "quantity": "10", "buy_price": "45",
                                   "buy_date": "2026-03-01", "dry_run": True})
        self.assertFalse(any("without a date" in w for w in r["warnings"]))

    def test_bad_input_is_a_400(self):
        for body in ({"kind": "nse", "code": "EQTY", "quantity": "999", "sell_price": "48"},
                     {"kind": "shoes", "code": "EQTY", "quantity": "1", "sell_price": "48"},
                     {"kind": "nse", "code": "EQTY", "quantity": "1", "sell_price": "48", "note": "x" * 500}):
            with self.assertRaises(self.dashboard_app.ApiError) as cm:
                self.app.sales_add(body)
            self.assertEqual(cm.exception.status, 400)


# ------------------------------------------------------------------ the pages
class TestSalesOnThePage(unittest.TestCase):
    def test_portfolio_page_lists_sales(self):
        import test_dashboard as td
        from report_generator import ReportGenerator
        hostile = '<img src=x onerror="alert(1)">'
        _l, nse, _p = sales.apply_stock_sales([lot("EQTY", 300, 40, "2025-01-01")],
                                              [sale("nse", "EQTY", 100, 52, "2026-01-05", fees=20)], "nse")
        nse[0]["note"] = hostile
        with fake_bonds.use():
            _l, bond, _p = sales.apply_bond_sales(
                [{"issue": "FXD9/2020/020", "face_value": 400000.0, "purchase_price_pct": 101.2,
                  "purchase_date": "2023-02-01", "note": ""}], [sale("bond", "FXD9/2020/020", 100000, 102, "2026-01-10")])
        with tempfile.TemporaryDirectory() as out:
            rg = ReportGenerator(output_dir=out, cache_dir=out)
            inputs = td.build_inputs()
            with td.offline(), td.captured_warnings() as warnings:
                rg.generate_index(inputs["analysis_results"], breadth=inputs["breadth"], report_files={},
                                  fundamentals_data=inputs["fundamentals_data"], validations={},
                                  scores=inputs["scores"], alerts={}, usd_kes=inputs["usd_kes"],
                                  portfolio_summary=inputs["portfolio_summary"],
                                  bond_portfolio=inputs["bond_portfolio"],
                                  intl_portfolio_summary=inputs["intl_portfolio_summary"],
                                  sales={"nse": nse, "bond": bond})
            with open(os.path.join(out, "portfolio.html"), encoding="utf-8") as f:
                html = f.read()
        self.assertEqual(warnings, [])
        for hook in ('id="sales"', 'id="t-sales"', 'id="sold-nse"', 'id="sold-bond"', 'id="sale-panel"',
                     'data-toggle-panel="sale-panel"', "Record a sale", "Profit from sales"):
            self.assertIn(hook, html)
        self.assertNotIn(hostile, html)
        self.assertIn("locked in by sales", html)                 # Total gain mentions what sales locked in
        self.assertIn("in coupons before you sold", html)
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(sorted({i for i in ids if ids.count(i) > 1}), [])

    def test_nothing_changes_without_sales(self):
        import page_portfolio
        from report_generator import ReportGenerator
        with tempfile.TemporaryDirectory() as out:
            rg = ReportGenerator(output_dir=out, cache_dir=out)
            body = str(page_portfolio.build(rg, sales=None))
        self.assertNotIn('id="sales"', body)
        self.assertIn('id="sale-panel"', body)


# ------------------------------------------------------------------ sell.py
class TestSellScript(_Tmp):
    def run_cli(self, *args, stdin=""):
        env = dict(os.environ, PORTFOLIO_DIR=self.dir, CACHE_DIR=os.path.join(self.dir, "cache"))
        return subprocess.run([sys.executable, os.path.join(ROOT, "sell.py"), *args], input=stdin, env=env,
                              capture_output=True, text=True, timeout=120)

    def test_one_line_and_step_by_step(self):
        self.holdings([("EQTY", 100, 40, "2025-01-01")])
        r = self.run_cli("nse", "EQTY", "30", "45", "--date", "2026-01-05", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Profit vs what you paid: +KES 150.00", r.stdout)
        r = self.run_cli("nse", "EQTY", "500", "45", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("can't sell more than that", r.stderr)
        # step by step: 1 = Kenyan stock, 1 = EQTY, Enter = all, price, date, no fees, no note, yes
        r = self.run_cli(stdin="1\n1\n\n47\n2026-02-01\n\n\ny\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("That's all of it", r.stdout)
        self.assertEqual(sales.positions(self.dir, "nse"), {})
        self.assertEqual(len(sales.load_sales(self.dir)), 2)
        r = self.run_cli(stdin="1\n")
        self.assertIn("don't hold any kenyan stocks", r.stderr.lower() + r.stdout.lower())


# ------------------------------------------------------------------ privacy
class TestSalesStayPrivate(unittest.TestCase):
    def test_sales_files_are_ignored_by_git_and_docker(self):
        out = subprocess.run(["git", "check-ignore", "portfolio/sales.csv", "portfolio/fund_transactions.csv"],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.stdout.split(), ["portfolio/sales.csv", "portfolio/fund_transactions.csv"])
        with open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8") as f:
            self.assertIn("portfolio/*.csv", f.read())


if __name__ == "__main__":
    unittest.main()
