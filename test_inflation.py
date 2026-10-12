"""
Tests for Kenya's inflation (inflation_data: the sources' parsers, offline)
and "after inflation" (inflation: today's-shillings arithmetic for every kind
of holding), plus the Market Pulse section and the portfolio section built
from them. Made-up figures only.
"""

import datetime as dt
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "tools")]

import inflation               # noqa: E402
import inflation_data as idata  # noqa: E402

D = dt.date
TODAY = D(2026, 6, 20)


def _cpi(start=D(2020, 1, 1), months=78, monthly=0.005):
    out, v, d = [], 100.0, start
    for _ in range(months):
        out.append((d, round(v, 4)))
        v *= 1 + monthly
        d = D(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


class _Resp:
    def __init__(self, text):
        self.text, self.status_code, self.content = text, 200, text.encode()


# ------------------------------------------------------------------ the arithmetic
class TestTodaysShillings(unittest.TestCase):
    def setUp(self):
        self.cpi = inflation.CPI([(D(2024, 1, 1), 100.0), (D(2025, 1, 1), 110.0), (D(2026, 1, 1), 121.0)])

    def test_index_by_month(self):
        self.assertEqual(self.cpi.at("2024-01-31"), 100.0)
        self.assertEqual(self.cpi.at("2025-06-15"), 110.0)          # latest published at or before the month
        self.assertIsNone(self.cpi.at("2023-12-31"))                # before the series: unknown, not guessed
        self.assertAlmostEqual(self.cpi.factor("2024-01-10", "2026-01-01"), 1.21)
        self.assertAlmostEqual(self.cpi.change_pct("2025-01-01"), 10.0)

    def test_real_gain_in_shillings(self):
        r = inflation.real_result([(D(2024, 1, 15), -100.0), (D(2025, 1, 15), 10.0)], 120.0, self.cpi, D(2026, 1, 20))
        self.assertAlmostEqual(r["put_in_today"], 121.0)           # 100 then = 121 now
        self.assertAlmostEqual(r["inflation_cost"], 21.0)
        self.assertAlmostEqual(r["income"], 10.0)
        self.assertAlmostEqual(r["gain"], 30.0)                      # 120 + 10 − 100
        self.assertAlmostEqual(r["real_gain"], 120.0 + 11.0 - 121.0) # income grown by 10% too
        self.assertAlmostEqual(r["real_gain_pct"], 10.0 / 121.0 * 100)
        self.assertIsNotNone(r["real_yearly"])

    def test_dollars_use_the_rate_of_the_day(self):
        fx = lambda d: 100.0 if d.year == 2024 else 130.0        # noqa: E731
        r = inflation.real_result([(D(2024, 1, 15), -10.0)], 10.0, self.cpi, D(2026, 1, 20), fx=fx, currency="USD")
        self.assertAlmostEqual(r["put_in"], 1000.0)                 # $10 at 100 then
        self.assertAlmostEqual(r["value"], 1300.0)                  # $10 at 130 now
        self.assertAlmostEqual(r["real_gain"], 1300.0 - 1210.0)     # the shilling's fall counts
        self.assertIsNone(inflation.real_result([(D(2024, 1, 15), -10.0)], 10.0, self.cpi, D(2026, 1, 20),
                                                fx=lambda d: None, currency="USD"))

    def test_needed_today(self):
        cpi = inflation.CPI(_cpi())
        self.assertAlmostEqual(inflation.needed_today(cpi, 1, D(2026, 1, 1)), 100000 * 1.005 ** 12, places=0)
        self.assertIsNone(inflation.needed_today(cpi, 10, D(2026, 1, 1)))


class TestPortfolioAfterInflation(unittest.TestCase):
    def setUp(self):
        self.macro = {"cpi": _cpi(), "fx": [(d, 100.0 + i * 0.5) for i, (d, _v) in enumerate(_cpi())],
                      "latest": {"month": "2026-05-01", "yoy": 6.0}, "tbill_latest": (D(2026, 5, 1), 8.0)}

    def test_each_kind_of_holding(self):
        ps = {"holdings": [{"symbol": "EQTY", "data_available": True, "price": 60.0,
                            "lots": [{"quantity": 100, "buy_price": 40.0, "buy_date": "2021-03-02"},
                                     {"quantity": 50, "buy_price": 50.0, "buy_date": None}]}]}
        ips = {"holdings": [{"symbol": "AAPL", "data_available": True, "price": 200.0,
                             "lots": [{"quantity": 2, "buy_price": 150.0, "buy_date": "2023-06-01"}]}]}
        bp = {"bonds": [{"data_available": True, "issue": "FXD9/2020/020", "purchase_date": "2022-01-10",
                         "purchase_date_assumed": True, "cost_basis": 100000.0, "tax_free": False,
                         "withholding_pct": 10.0, "indicative_value_par": 100500.0,
                         "cashflows_past": [{"date": "2022-07-10", "type": "coupon", "amount": 6000.0},
                                            {"date": "2021-12-10", "type": "coupon", "amount": 6000.0}]}],
              "totals": {"indicative_value_par": 100500.0, "annual_after_tax_income": 10800.0}}
        fs = {"funds": [{"name": "Made-up Fund", "data_available": True, "invested_since": "2024-01-29",
                         "as_of": "2024-04-30", "monthly_amount": 50.0, "statement_units": 40.0,
                         "statement_avg_cost": 10.0, "market_value": 500.0,
                         "returns": {"periods": {"1Y": 12.0}},
                         "transactions": [{"date": "2024-05-29", "type": "topup", "amount": 50.0},
                                          {"date": "2024-06-30", "type": "payout", "amount": 2.0}]},
                        {"name": "Undated Fund", "data_available": True, "invested_since": None}]}
        real = inflation.portfolio_real(ps, bp, ips, fs, self.macro, {"rate": 140.0}, TODAY)
        st, b, it, f = (real["parts"][k] for k in ("stocks", "bonds", "intl", "funds"))
        self.assertEqual(st["undated"], ["EQTY · 50 @ 50"])
        self.assertAlmostEqual(st["result"]["put_in"], 4000.0)
        self.assertAlmostEqual(st["result"]["value"], 6000.0)       # only the dated lot
        self.assertAlmostEqual(b["result"]["income"], 5400.0)       # one coupon after purchase, after 10% tax
        self.assertEqual(b["assumed"], ["FXD9/2020/020"])
        self.assertAlmostEqual(it["result"]["value"], 400.0 * 140.0)
        self.assertAlmostEqual(f["result"]["put_in"],               # $400 over Jan–Apr + a $50 top-up, in KES
                               sum(100.0 * (100.0 + 0.5 * ((m.year - 2020) * 12 + m.month - 1))
                                   for m in (D(2024, 1, 1), D(2024, 2, 1), D(2024, 3, 1), D(2024, 4, 1)))
                               + 50.0 * (100.0 + 0.5 * 52), places=6)
        self.assertEqual(f["undated"], ["Undated Fund"])
        self.assertEqual(real["undated"], 2)
        t = real["total"]
        self.assertAlmostEqual(t["put_in"], sum(p["result"]["put_in"] for p in real["parts"].values()))
        self.assertGreater(t["put_in_today"], t["put_in"])           # prices rose

    def test_race_needs_no_dates(self):
        bp = {"totals": {"indicative_value_par": 100000.0, "annual_after_tax_income": 12000.0}}
        fs = {"funds": [{"data_available": True, "market_value": 100.0, "returns": {"periods": {"1Y": 10.0}}}]}
        rc = inflation.race(bp, fs, dict(self.macro, fx_1y_pct=2.0), tbill_pct=9.0)
        rows = {w: (r, e) for w, r, _n, e in rc["rows"]}
        self.assertEqual(rows["91-day Treasury bill"], (9.0, 3.0))
        self.assertEqual(rows["Your bonds"], (12.0, 6.0))
        self.assertAlmostEqual(rows["Your funds, last 12 months"][0], (1.1 * 1.02 - 1) * 100)
        self.assertIsNone(inflation.race(bp, fs, {}))


# ------------------------------------------------------------------ the sources (offline)
class TestInflationSources(unittest.TestCase):
    def test_cbk_inflation_csv(self):
        csv = (",,Inflation (month-on-month) %,Inflation (annual average) %\n2025,Jan,3.3,4.1\n,Feb,3.5,4.0\n"
               ",December,4.5,4.07\n2026,January,4.4,4.1\n,Source: Kenya National Bureau of Statistics,,\n")
        with mock.patch.object(idata, "_link", return_value="x"), \
                mock.patch.object(idata, "_get", return_value=_Resp(csv)):
            rows = idata.kenya_headline()
        self.assertEqual(rows, [(D(2025, 1, 1), 3.3, 4.1), (D(2025, 2, 1), 3.5, 4.0), (D(2025, 12, 1), 4.5, 4.07),
                                (D(2026, 1, 1), 4.4, 4.1)])

    def test_imf_index_csv(self):
        csv = "DATAFLOW,REF_AREA,TIME_PERIOD,OBS_VALUE\nx,KEN,2026-M08,155.85\nx,KEN,2026-M09,156.47\n"
        with mock.patch.object(idata, "_get", return_value=_Resp(csv)):
            self.assertEqual(idata.kenya_cpi(), [(D(2026, 8, 1), 155.85), (D(2026, 9, 1), 156.47)])

    def test_cbk_rates_and_fx(self):
        rates = (",,CENTRAL BANK RATES,,,,,,,\nYEAR,MONTH,Repo,Reverse Repo,Interbank Rate,91-Day Tbill,182-days Tbill,"
                 "364-days Tbill,Cash Reserve Requirement,Central Bank Rate\n2026,Jul, - , - ,,8.81,9.0,9.6,4.25,8.75\n"
                 ",Aug, - , - ,,8.78,8.9,9.5,4.25,8.75\n")
        fx = (",Kenya Shilling Monthly Average Exchange Rates\\1\nYear,Month,United States dollar,Sterling pound\n"
              "2025,9,129.24,170.1\n2026,9,129.54,173.89\n")
        with mock.patch.object(idata, "_link", return_value="x"), \
                mock.patch.object(idata, "_get", side_effect=[_Resp(rates), _Resp(fx)]):
            self.assertEqual(idata.cbk_rates(), [(D(2026, 7, 1), 8.81, 8.75), (D(2026, 8, 1), 8.78, 8.75)])
            self.assertEqual(idata.usd_kes_monthly(), [(D(2025, 9, 1), 129.24), (D(2026, 9, 1), 129.54)])

    def test_knbs_release_text(self):
        text = "\n".join([
            "Table 1: One and Twelve-Month Percentage Changes in the Consumer Price Indices",
            "                                                                                   (September 2026",
            "                                                                                    / August 2026)",
            " Food and Non-Alcoholic Beverages                                          32.9094          0.9           9.5",
            " Transport                                                                  9.6468         -0.4          15.6",
            "Table 3: National Average Retail Prices of Selected Commodities",
            " Petrol                                 1        Litre            185.59        214.95        214.95         0.0          15.8",
            " Electricity-50kwh                     50       kWh             1,274.06      1,289.47      1,258.21        -2.4           -1.2",
            "Table 4: Core and Non-Core Index and Inflation",
            " Aug-26        134.01             3.4        245.39               14.7",
            " Sep-26        134.66             4.0        246.09               14.0",
            "Table 5: Core and Non-Core Contributions, September 2026",
        ])
        k = idata.parse_knbs(text)
        self.assertEqual(k["month"], "2026-09-01")
        self.assertEqual([d["name"] for d in k["divisions"]], ["Food and Non-Alcoholic Beverages", "Transport"])
        self.assertEqual(k["divisions"][1]["year_pct"], 15.6)
        self.assertEqual(k["core"][-1], {"month": "2026-09-01", "core_pct": 4.0, "noncore_pct": 14.0})
        self.assertEqual([(p["name"], p["now"], p["year_pct"]) for p in k["prices"]],
                         [("Petrol", 214.95, 15.8), ("Electricity-50kwh", 1258.21, -1.2)])

    def test_summary_and_cross_check(self):
        cpi = _cpi(D(2020, 1, 1), 81, 0.005)
        imf = {d: (v / dict(cpi)[D(d.year - 1, d.month, 1)] - 1) * 100 for d, v in cpi if d.year >= 2021}
        headline = [(d, round(y, 2), 6.0) for d, y in sorted(imf.items())]
        headline[-6] = (headline[-6][0], headline[-6][1] + 0.3, 6.0)            # one typo in CBK's file
        s = idata.summarise(headline, cpi, [(D(2026, 8, 1), 8.8, 8.75)], [(D(2025, 9, 1), 100.0), (D(2026, 9, 1), 103.0)],
                            [], None, TODAY)
        self.assertEqual(len(s["cross_check"]["differences"]), 1)              # reported, not fixed
        self.assertAlmostEqual(s["latest"]["month_pct"], 0.5, places=3)
        self.assertAlmostEqual(s["fx_1y_pct"], 3.0)
        self.assertAlmostEqual(s["yearly_index"][2021]["avg_pct"], (1.005 ** 12 - 1) * 100, places=3)
        self.assertTrue(s["latest"]["in_band"])
        self.assertEqual(idata.summarise([], [], [], [], [], None).get("latest"), None)


# ------------------------------------------------------------------ pages
class TestInflationPages(unittest.TestCase):
    def macro(self):
        cpi = _cpi(D(2015, 1, 1), 141, 0.005)
        by = dict(cpi)
        headline = [(d, round((v / by[D(d.year - 1, d.month, 1)] - 1) * 100, 2), 6.0) for d, v in cpi if d.year >= 2016]
        s = idata.summarise(headline, cpi, [(d, 9.0, 8.75) for d, _y, _a in headline], [(d, 120.0) for d, _v in cpi],
                            [], {"month": "2026-09-01", "url": "https://example.com/r.pdf",
                                 "divisions": [{"name": "Food <b>x</b>", "weight": 33.0, "month_pct": 0.9,
                                                "year_pct": 9.5}],
                                 "core": [{"month": f"2026-0{i}-01", "core_pct": 3.5, "noncore_pct": 12.0}
                                          for i in range(1, 10)],
                                 "prices": [{"name": "Petrol", "qty": "1", "unit": "Litre", "year_ago": 190.0,
                                             "last_month": 214.0, "now": 214.95, "month_pct": 0.0, "year_pct": 15.8}]},
                            TODAY)
        return s

    def test_market_pulse_section(self):
        import page_market
        html = str(page_market.inflation_section(self.macro(), tbill_pct=8.76))
        for bit in ('id="inflation"', 'id="c-inflation"', 'id="c-inflation-yearly"', 'id="c-core"',
                    'id="t-retail-prices"', "What inflation does to savings", "Real T-bill rate"):
            self.assertIn(bit, html)
        self.assertNotIn("<b>x</b>", html)                                       # third-party text is text
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("unavailable", str(page_market.inflation_section(idata.summarise([], [], [], [], [], None))))

    def test_portfolio_section(self):
        import page_portfolio
        m = self.macro()
        real = {"parts": {"stocks": {"label": "Kenyan shares", "undated": ["EQTY · 10 @ 40"], "too_early": [],
                                     "assumed": [], "result": None}}, "total": None, "undated": 1}
        html = str(page_portfolio._after_inflation({"macro": m, "real": real, "race": None}))
        self.assertIn('id="after-inflation"', html)
        self.assertIn("buy_date", html)                                          # tells you where to add the date
        ps = {"holdings": [{"symbol": "EQTY", "data_available": True, "price": 60.0,
                            "lots": [{"quantity": 100, "buy_price": 40.0, "buy_date": "2021-03-02"}]}]}
        real = inflation.portfolio_real(ps, None, None, None, m, {"rate": 130.0}, TODAY)
        html = str(page_portfolio._after_inflation({"macro": m, "real": real,
                                                    "race": inflation.race(None, None, m, 9.0)}))
        self.assertIn('id="t-after-inflation"', html)
        self.assertIn('id="t-inflation-race"', html)
        self.assertIn("better off", html)


if __name__ == "__main__":
    unittest.main()
