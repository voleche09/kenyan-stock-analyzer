"""
Tests for dividend_reference.py (company-verified book closure/payment dates,
never re-fetched) and its integration into dividend_calendar.py (checked
before the mystocks.co.ke scrape; mystocks is never hit for a ticker the
reference table already covers).
"""

import datetime as dt
import os
import sys
import unittest
import tempfile
from unittest import mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import dividend_reference as dref      # noqa: E402
from dividend_calendar import DividendCalendar, apply_dividend_calendar  # noqa: E402

import logging
logging.disable(logging.CRITICAL)

D = dt.date


class TestCurrentCycle(unittest.TestCase):
    """dividend_reference.current_cycle() picks the right cycle for a date."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = dict(dref.DIVIDEND_REFERENCE)
        dref.DIVIDEND_REFERENCE.clear()
        dref.DIVIDEND_REFERENCE["ZZZZ"] = [
            {"amount": 1.0, "type": "interim dividend", "status": "paid",
             "book_closure": "2026-02-01", "payment_date": "2026-03-01",
             "source": "test fixture", "confidence": "primary"},
            {"amount": 2.0, "type": "final dividend", "status": "paid",
             "book_closure": "2025-11-01", "payment_date": "2025-12-01",
             "source": "test fixture", "confidence": "primary"},
        ]

    def tearDown(self):
        dref.DIVIDEND_REFERENCE.clear()
        dref.DIVIDEND_REFERENCE.update(self._saved)

    def test_unknown_ticker_is_none(self):
        self.assertIsNone(dref.current_cycle("NOPE"))

    def test_lookup_is_case_insensitive(self):
        self.assertIsNotNone(dref.current_cycle("zzzz"))

    def test_picks_the_soonest_still_pending_payment(self):
        # Both cycles' payments are still ahead of this date -> the EARLIER one.
        cycle = dref.current_cycle("ZZZZ", today=D(2025, 1, 1))
        self.assertEqual(cycle["amount"], 2.0)

    def test_picks_the_most_recently_paid_once_both_are_done(self):
        # Both cycles' payments are in the past -> the MOST RECENT one.
        cycle = dref.current_cycle("ZZZZ", today=D(2026, 6, 1))
        self.assertEqual(cycle["amount"], 1.0)

    def test_mixed_one_pending_one_paid_picks_the_pending_one(self):
        cycle = dref.current_cycle("ZZZZ", today=D(2026, 2, 15))
        self.assertEqual(cycle["amount"], 1.0)  # interim, payment still ahead

    def test_bad_date_in_a_cycle_does_not_crash_the_lookup(self):
        dref.DIVIDEND_REFERENCE["BAD"] = [
            {"amount": 5.0, "type": "x", "status": "paid",
             "book_closure": "not-a-date", "payment_date": "also-not-a-date",
             "source": "test fixture"},
        ]
        cycle = dref.current_cycle("BAD")
        self.assertEqual(cycle["amount"], 5.0)


class TestExtraReferenceLoader(unittest.TestCase):
    """DIVIDEND_REFERENCE_EXTRA — how the sandbox supplies made-up dividends."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = dict(dref.DIVIDEND_REFERENCE)

    def tearDown(self):
        dref.DIVIDEND_REFERENCE.clear()
        dref.DIVIDEND_REFERENCE.update(self._saved)

    def test_loads_a_json_file_and_adds_to_the_table(self):
        import json
        import tempfile
        extra = {"FAKE": [{"amount": 3.0, "type": "final dividend", "status": "paid",
                            "book_closure": "2026-01-01", "payment_date": "2026-02-01",
                            "source": "fixture"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(extra, f)
            path = f.name
        try:
            added = dref.load_extra_reference(path)
            self.assertEqual(added, 1)
            self.assertEqual(dref.current_cycle("FAKE")["amount"], 3.0)
        finally:
            os.unlink(path)

    def test_a_missing_file_is_skipped_not_fatal(self):
        added = dref.load_extra_reference("/no/such/file.json")
        self.assertEqual(added, 0)


class TestValidateChecksReferenceFirst(unittest.TestCase):
    """DividendCalendar.validate() never touches the network for a ticker
    already in the reference table."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = dict(dref.DIVIDEND_REFERENCE)
        dref.DIVIDEND_REFERENCE.clear()
        dref.DIVIDEND_REFERENCE["ZZZZ"] = [
            {"amount": 1.5, "type": "final dividend", "status": "paid",
             "book_closure": "2026-01-10", "payment_date": "2026-02-10",
             "source": "test fixture, verified against the company's own circular",
             "confidence": "primary"},
        ]

    def tearDown(self):
        dref.DIVIDEND_REFERENCE.clear()
        dref.DIVIDEND_REFERENCE.update(self._saved)

    @mock.patch("utils.http_get")
    def test_reference_hit_never_calls_http_get(self, mock_http):
        dc = DividendCalendar(cache_dir=self.tmp.name)
        res = dc.validate("ZZZZ", tv_dps=9.99)
        mock_http.assert_not_called()
        self.assertEqual(res["status"], "company-verified")
        self.assertEqual(res["amount"], 1.5)
        self.assertEqual(res["book_closure"], "2026-01-10")
        self.assertEqual(res["payment_date"], "2026-02-10")

    @mock.patch("utils.http_get")
    def test_secondary_confidence_is_noted_but_still_used(self, mock_http):
        dref.DIVIDEND_REFERENCE["ZZZZ"][0]["confidence"] = "secondary"
        dc = DividendCalendar(cache_dir=self.tmp.name)
        res = dc.validate("ZZZZ", tv_dps=None)
        self.assertEqual(res["amount"], 1.5)
        self.assertIn("wasn't read directly", res["note"])

    @mock.patch("utils.http_get")
    def test_a_ticker_not_in_the_reference_table_falls_back_to_mystocks(self, mock_http):
        mock_http.return_value = None  # network unavailable
        dc = DividendCalendar(cache_dir=self.tmp.name)
        res = dc.validate("NOTREF", tv_dps=None)
        mock_http.assert_called_once()
        self.assertEqual(res["status"], "unavailable")


class TestApplyDividendCalendar(unittest.TestCase):
    """End-to-end: apply_dividend_calendar() against a mix of a
    reference-covered ticker and a plain TradingView-only ticker."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = dict(dref.DIVIDEND_REFERENCE)
        dref.DIVIDEND_REFERENCE.clear()

    def tearDown(self):
        dref.DIVIDEND_REFERENCE.clear()
        dref.DIVIDEND_REFERENCE.update(self._saved)

    @mock.patch("utils.http_get")
    def test_company_verified_sets_book_closure_and_payment_date(self, mock_http):
        mock_http.return_value = None
        dref.DIVIDEND_REFERENCE["ZZZZ"] = [
            {"amount": 2.0, "type": "final dividend", "status": "paid",
             "book_closure": "2020-01-01", "payment_date": "2020-02-01",
             "source": "test fixture", "confidence": "primary"},
        ]
        fundamentals = {"ZZZZ": {"dps_fy": None, "close": 20.0}}
        counts = apply_dividend_calendar(fundamentals, cache_dir=self.tmp.name)

        self.assertEqual(counts["company_verified"], 1)
        f = fundamentals["ZZZZ"]
        self.assertEqual(f["dividend_status"], "company_verified")
        self.assertEqual(f["dividend_book_closure"], "2020-01-01")
        self.assertEqual(f["dividend_payment_date"], "2020-02-01")
        self.assertEqual(f["dividend_amount"], 2.0)
        self.assertIsNone(f["dps_fy"])
        self.assertIsNone(f.get("dividend_yield"))
        self.assertIsNone(f["dividend_ex_date"])
        # Both dates are long past "today" -> neither is flagged upcoming.
        self.assertFalse(f["dividend_ex_date_is_upcoming"])
        self.assertFalse(f["dividend_payment_upcoming"])

    @mock.patch("utils.http_get")
    def test_book_closure_only_cycle_still_drives_the_shared_attention_date(self, mock_http):
        # A cycle with no explicit ex_date (the common case: issuer references
        # and most mystocks entries give book closure, not a separate
        # ex-date) must still populate dividend_next_date — this is what
        # scoring.py's alerts, watchlist.py's "needs attention" and the daily
        # email's "upcoming dividends" section key off, instead of a
        # dividend_ex_date that's almost never set.
        mock_http.return_value = None
        future_book_closure = (dt.date.today() + dt.timedelta(days=5)).isoformat()
        dref.DIVIDEND_REFERENCE["ZZZZ"] = [
            {"amount": 2.0, "type": "final dividend", "status": "declared",
             "book_closure": future_book_closure, "payment_date": None,
             "source": "test fixture", "confidence": "primary"},
        ]
        fundamentals = {"ZZZZ": {"dps_fy": None, "close": 20.0}}
        apply_dividend_calendar(fundamentals, cache_dir="/tmp/_test-div-cache")
        f = fundamentals["ZZZZ"]
        self.assertIsNone(f["dividend_ex_date"])  # no true ex-date in this cycle
        self.assertEqual(f["dividend_next_date"], future_book_closure)
        self.assertTrue(f["dividend_next_date_upcoming"])
        self.assertTrue(f["dividend_book_closure_upcoming"])

    @mock.patch("utils.http_get")
    def test_an_upcoming_book_closure_is_flagged_upcoming(self, mock_http):
        mock_http.return_value = None
        far_future_book_closure = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        far_future_payment = (dt.date.today() + dt.timedelta(days=60)).isoformat()
        dref.DIVIDEND_REFERENCE["ZZZZ"] = [
            {"amount": 2.0, "type": "final dividend", "status": "declared",
             "book_closure": far_future_book_closure, "payment_date": far_future_payment,
             "source": "test fixture", "confidence": "primary"},
        ]
        fundamentals = {"ZZZZ": {"dps_fy": None, "close": 20.0}}
        apply_dividend_calendar(fundamentals, cache_dir=self.tmp.name)
        f = fundamentals["ZZZZ"]
        self.assertFalse(f["dividend_ex_date_is_upcoming"])
        self.assertEqual(f["dividend_book_closure"], far_future_book_closure)
        self.assertTrue(f["dividend_payment_upcoming"])

    @mock.patch("utils.http_get")
    def test_a_ticker_outside_the_reference_table_is_unaffected(self, mock_http):
        mock_http.return_value = None  # mystocks unreachable too
        fundamentals = {"NOTREF": {"dps_fy": 3.0, "close": 20.0}}
        counts = apply_dividend_calendar(fundamentals, cache_dir=self.tmp.name)
        self.assertEqual(counts["unverified"], 1)
        self.assertEqual(fundamentals["NOTREF"]["dividend_status"], "unverified")

    @mock.patch("utils.http_get")
    def test_no_dividend_anywhere_is_reported_as_none_not_guessed(self, mock_http):
        mock_http.return_value = None
        fundamentals = {"NOTREF": {"dps_fy": None, "close": 20.0}}
        apply_dividend_calendar(fundamentals, cache_dir=self.tmp.name)
        self.assertEqual(fundamentals["NOTREF"]["dividend_status"], "unverified")
        self.assertIsNone(fundamentals["NOTREF"]["dps_fy"])


class TestKplcSeedData(unittest.TestCase):
    def test_kplc_final_is_separate_from_proposed_annual_total(self):
        cycle = dref.current_cycle("KPLC", today=D(2026, 10, 22))
        self.assertEqual(cycle["amount"], 1.20)
        self.assertEqual(cycle["annual_dps"], 1.50)
        self.assertEqual(cycle["status"], "recommended")
        self.assertEqual(cycle["confidence"], "primary")
        self.assertIsNone(cycle["payment_date"])
        self.assertIsNone(cycle["book_closure"])

    def test_every_seeded_cycle_cites_a_source(self):
        for ticker, cycles in dref.DIVIDEND_REFERENCE.items():
            for c in cycles:
                self.assertTrue(c.get("source"), f"{ticker} cycle missing a source citation")


class TestSeededIssuerCycles(unittest.TestCase):
    """Dividend dates for seeded NSE issuers — cross-checked against stockanalysis.com's dividend table, Kenyan Wall
    Street and/or CDSC Kenya's corporate-actions registry."""

    def test_kegn_matches_cdsc_and_business_daily(self):
        cycle = dref.current_cycle("KEGN", today=D(2026, 10, 22))
        self.assertEqual((cycle["amount"], cycle["book_closure"], cycle["payment_date"]),
                         (0.75, "2026-10-29", "2027-01-21"))
        self.assertIsNone(cycle.get("ex_date"))  # not independently confirmed — not guessed

    def test_scom_picks_the_later_paid_final_over_the_earlier_interim(self):
        cycle = dref.current_cycle("SCOM", today=D(2026, 10, 22))
        self.assertEqual(cycle["amount"], 1.15)
        self.assertEqual(cycle["ex_date"], "2026-08-05")

    def test_imh_sbic_nse_have_real_confirmed_dates(self):
        for ticker, amount in (("IMH", 2.25), ("SBIC", 1.64), ("NSE", 1.00)):
            cycle = dref.current_cycle(ticker, today=D(2026, 10, 22))
            self.assertEqual(cycle["amount"], amount, ticker)
            self.assertIsNotNone(cycle["book_closure"], ticker)
            self.assertIsNotNone(cycle["payment_date"], ticker)

    def test_hfcb_and_bkg_are_deliberately_not_seeded(self):
        # HFCB: researched and reported to pay no dividend — not a fabricated
        # zero-amount cycle. BKG: RWF cross-listing currency mechanics not
        # independently verified yet.
        self.assertNotIn("HFCB", dref.DIVIDEND_REFERENCE)
        self.assertNotIn("BKG", dref.DIVIDEND_REFERENCE)


class TestDividendAccuracy(unittest.TestCase):
    def test_annual_analytics_survive_an_interim_payment(self):
        from dividend_calendar import apply_declaration
        fund = {"dps_fy": 8, "dividend_yield": 10, "dividend_payout_ratio": 40, "close": 80}
        apply_declaration(fund, {"amount": 3, "type": "interim", "currency": "KES"})
        self.assertEqual((fund["dps_fy"], fund["dividend_yield"], fund["dividend_payout_ratio"]), (8, 10, 40))
        self.assertEqual(fund["dividend_amount"], 3)

    def test_issuer_annual_total_drives_yield_but_not_invented_payout(self):
        from dividend_calendar import apply_declaration
        fund = {"dps_fy": 3, "close": 30}
        apply_declaration(fund, {"amount": 1.2, "annual_dps": 1.5, "currency": "KES"})
        self.assertEqual(fund["dividend_yield"], 5)
        self.assertEqual(fund["dps_fy"], 1.5)
        self.assertIsNone(fund.get("dividend_payout_ratio"))

    def test_calendar_shared_date_and_recurring_amount_do_not_mix_years(self):
        html = """<h3>Mar 10 2026</h3><p>ZZZZ Example Plc: Payment of KES 2.00 final dividend</p>
        <p>YYYY Other Plc: Payment of KES 3.00 interim dividend</p>
        <h3>Feb 10 2026</h3><p>ZZZZ Example Plc: Book closure KES 2.00 final dividend</p>
        <h3>Mar 12 2025</h3><p>ZZZZ Example Plc: Payment of KES 2.00 final dividend</p>
        <h3>Feb 12 2025</h3><p>ZZZZ Example Plc: Book closure KES 2.00 final dividend</p>"""
        result = DividendCalendar._parse(html)
        self.assertEqual(result["YYYY"]["payment_date"], "2026-03-10")
        self.assertEqual(result["ZZZZ"]["book_closure"], "2026-02-10")
        self.assertEqual(result["ZZZZ"]["payment_date"], "2026-03-10")

    def test_secondary_is_not_company_verified(self):
        with mock.patch.object(dref, "current_cycle", return_value={"amount": 1, "type": "final",
                              "source": "secondary", "confidence": "secondary"}):
            self.assertEqual(DividendCalendar().validate("ZZZZ", None)["status"], "secondary")

    def test_past_payment_never_appears_as_next_and_missing_is_not_zero(self):
        from page_stock import _dividend_kpi
        html = str(_dividend_kpi({"dividend_amount": 2, "dividend_payment_date": "2026-01-01"}, today=D(2026, 10, 22)))
        self.assertIn("Next Dividend Date</strong><br>Not confirmed", html)
        self.assertIn("Previous scheduled payment", html)
        self.assertNotIn("0 — no dividend", str(_dividend_kpi({})))

    def test_preliminary_and_proposed_are_visible(self):
        from page_stock import _dividend_kpi
        html = str(_dividend_kpi({"dividend_amount": None, "dividend_payment_date": "2026-11-10",
                                 "dividend_declaration_status": "preliminary"}, today=D(2026, 10, 22)))
        self.assertIn("in 19 days", html)
        self.assertIn("preliminary", html)
        self.assertIn("Dividend Amount</strong><br>Not confirmed", html)

    def test_source_markup_is_escaped_and_unsafe_links_are_not_emitted(self):
        from page_stock import _dividend_kpi
        html = str(_dividend_kpi({"dividend_source": "<script>bad</script>", "dividend_source_url": "javascript:alert(1)"}))
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript:', html)

    def test_intl_annual_rate_is_not_a_quarterly_amount(self):
        from international_data import _apply_dividend_reference
        with mock.patch.object(dref, "current_cycle", return_value={"amount": .25, "currency": "USD",
                          "type": "quarterly", "source": "Fixture", "confidence": "primary"}):
            fund = _apply_dividend_reference("ZZZZ", {"dividend_rate": 1, "dividend_yield": 2})
        self.assertEqual(fund["dividend_rate"], 1)
        self.assertEqual(fund["dividend_amount"], .25)
        self.assertEqual(fund["dividend_currency"], "USD")
        self.assertEqual(fund["dividend_yield"], 2)

    def test_kcb_parser_combines_special_with_final_and_keeps_interim_separate(self):
        from dividend_sources import parse_kcb
        html = """<table><tr><td>2026</td><td>Cash Dividend</td><td>Interim</td><td>Ksh. 3.00</td><td>2 September 2026</td><td>10 November 2026</td></tr>
        <tr><td>2025</td><td>Cash Dividend</td><td>Final</td><td>Ksh. 2.00</td><td>2 April 2026</td><td>22 May 2026</td></tr>
        <tr><td>Cash Dividend</td><td>Special</td><td>Ksh. 1.00</td><td>2 April 2026</td><td>22 May 2026</td></tr></table>"""
        rows = parse_kcb(html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["amount"], 3)
        self.assertEqual(rows[1]["type"], "final + special")
        with self.assertRaises(ValueError):
            parse_kcb("<html>Service unavailable</html>")


class TestDividendCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_repeated_runs_reuse_disk_and_daily_wipe_preserves_it(self):
        from dividend_cache import cached_public_data
        from utils import enforce_daily_cache
        load = mock.Mock(return_value={"payment_date": "2026-12-31"})
        cached_public_data(self.tmp.name, "fixture", load, today=D(2026, 10, 22))
        enforce_daily_cache(self.tmp.name)
        data, state = cached_public_data(self.tmp.name, "fixture", load, today=D(2026, 10, 23))
        self.assertEqual(load.call_count, 1)
        self.assertEqual(data["payment_date"], "2026-12-31")
        self.assertFalse(state["stale"])

    def test_due_check_updates_changed_date_and_keeps_original_change_date_when_equal(self):
        from dividend_cache import cached_public_data
        load = mock.Mock(side_effect=[{"date": "2026-12-30"}, {"date": "2026-12-31"}, {"date": "2026-12-31"}])
        for date in (D(2026, 10, 1), D(2026, 10, 8), D(2026, 10, 15)):
            data, state = cached_public_data(self.tmp.name, "fixture", load, today=date)
        self.assertEqual(data["date"], "2026-12-31")
        self.assertEqual(state["changed"], "2026-10-08")
        self.assertEqual(state["checked"], "2026-10-15")

    def test_failed_check_retains_saved_data_and_backs_off(self):
        from dividend_cache import cached_public_data
        load = mock.Mock(side_effect=[{"amount": 1}, OSError("offline")])
        cached_public_data(self.tmp.name, "fixture", load, today=D(2026, 10, 1))
        data, state = cached_public_data(self.tmp.name, "fixture", load, today=D(2026, 10, 8))
        self.assertEqual(data, {"amount": 1})
        self.assertTrue(state["stale"])
        cached_public_data(self.tmp.name, "fixture", load, today=D(2026, 10, 8))
        self.assertEqual(load.call_count, 2)

    def test_force_refresh_bypasses_weekly_cache(self):
        from dividend_cache import cached_public_data
        load = mock.Mock(return_value=[])
        cached_public_data(self.tmp.name, "fixture", load)
        cached_public_data(self.tmp.name, "fixture", load, force_refresh=True)
        self.assertEqual(load.call_count, 2)

    def test_international_history_is_cached_across_calls(self):
        import pandas as pd
        import international_data
        series = pd.Series([.1], index=pd.to_datetime(["2026-01-01"]))
        with mock.patch("yfinance.Ticker") as ticker:
            ticker.return_value.dividends = series
            one = international_data.fetch_dividend_history("ZZZZ", cache_dir=self.tmp.name)
            two = international_data.fetch_dividend_history("ZZZZ", cache_dir=self.tmp.name)
            self.assertEqual(one, two)
            ticker.assert_called_once_with("ZZZZ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
