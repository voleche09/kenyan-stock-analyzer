#!/usr/bin/env python3
"""
Test suite for the Kenyan Stock Analyzer.

Tests data acquisition, analysis engine, report generation,
sector analysis, email notifier, and config loading.
"""

import sys
import os
import re
import json
import unittest
import tempfile
from unittest import mock
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

from config import Config
from logger import setup_logging, get_logger
from utils import retry, safe_float, detect_support_resistance
from analysis_engine import AnalysisEngine
from sector_analysis import SectorAnalyzer
import portfolio_csv
import portfolio as portfolio_mod
import international_portfolio
import bonds_portfolio
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
import fake_bonds  # made-up bonds: the tests never name anyone's real ones

# Quiet logging during tests
import logging
logging.disable(logging.CRITICAL)


def make_sample_data(periods=200, seed=42):
    """Generate reproducible sample OHLCV data."""
    np.random.seed(seed)
    dates = pd.date_range('2025-01-01', periods=periods, freq='B')
    close = np.random.randn(periods).cumsum() + 100
    return pd.DataFrame({
        'open': close + np.random.randn(periods) * 0.5,
        'high': close + abs(np.random.randn(periods)) * 2,
        'low': close - abs(np.random.randn(periods)) * 2,
        'close': close,
        'volume': np.random.randint(50000, 500000, periods),
    }, index=dates)


class TestConfig(unittest.TestCase):
    """Test configuration loading."""

    def test_config_loads(self):
        config = Config()
        self.assertIsNotNone(config.stock_symbols)
        self.assertGreater(len(config.stock_symbols), 0)
        self.assertIn('SCOM', config.stock_symbols)
        self.assertIsNotNone(config.data_sources)
        self.assertIn('nse_pdf', config.data_sources)

    def test_analysis_params(self):
        config = Config()
        self.assertEqual(config.rsi_period, 14)
        self.assertEqual(config.macd_fast, 12)
        self.assertEqual(config.macd_slow, 26)


class TestUtils(unittest.TestCase):
    """Test utility functions."""

    def test_safe_float(self):
        self.assertEqual(safe_float('123.45'), 123.45)
        self.assertEqual(safe_float('abc'), 0.0)
        self.assertEqual(safe_float(None), 0.0)

    def test_support_resistance(self):
        prices = np.array([10, 11, 12, 11, 10, 9, 10, 11, 12, 13, 12, 11, 10, 11, 12])
        supports, resistances = detect_support_resistance(prices, window=3)
        self.assertIsInstance(supports, list)
        self.assertIsInstance(resistances, list)

    def test_retry_decorator(self):
        call_count = [0]

        @retry(max_attempts=3, backoff=0.01, exceptions=(ValueError,))
        def flaky():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("fail")
            return "success"

        result = flaky()
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 3)


class TestAnalysisEngine(unittest.TestCase):
    """Test technical analysis calculations."""

    @classmethod
    def setUpClass(cls):
        cls.engine = AnalysisEngine()
        cls.data = make_sample_data(200)
        cls.result = cls.engine.analyze_stock(cls.data)

    def test_indicators_present(self):
        df = self.result['data']
        expected = ['sma_20', 'sma_50', 'ema_12', 'ema_26', 'rsi',
                     'macd', 'macd_signal', 'macd_hist',
                     'bb_upper', 'bb_middle', 'bb_lower',
                     'atr', 'obv', 'stoch_k', 'stoch_d', 'volume_sma_20']
        for col in expected:
            self.assertIn(col, df.columns, f"Missing column: {col}")

    def test_signals_present(self):
        signals = self.result['signals']
        expected = ['ma_crossover', 'rsi', 'macd', 'bollinger', 'trend',
                     'stochastic', 'volume', 'overall']
        for sig in expected:
            self.assertIn(sig, signals, f"Missing signal: {sig}")

    def test_signal_values(self):
        signals = self.result['signals']
        valid = {'bullish', 'bearish', 'overbought', 'oversold', 'neutral',
                 'above_upper', 'below_lower', 'within_bands', 'undefined',
                 'golden_cross', 'death_cross', 'bullish_cross', 'bearish_cross',
                 'high_volume', 'low_volume', 'normal'}
        for name, value in signals.items():
            self.assertIn(value, valid, f"Invalid signal '{value}' for '{name}'")

    def test_support_resistance(self):
        supports = self.result.get('support', [])
        resistances = self.result.get('resistance', [])
        self.assertIsInstance(supports, list)
        self.assertIsInstance(resistances, list)

    def test_daily_change(self):
        chg = self.result.get('daily_change_pct')
        self.assertIsNotNone(chg)
        self.assertIsInstance(chg, float)

    def test_multi_stock_analysis(self):
        data_dict = {
            'SCOM': make_sample_data(100, seed=1),
            'EQTY': make_sample_data(100, seed=2),
        }
        results = self.engine.analyze_multiple_stocks(data_dict)
        self.assertIn('SCOM', results)
        self.assertIn('EQTY', results)

    def test_market_breadth(self):
        data_dict = {
            'SCOM': make_sample_data(100, seed=1),
            'EQTY': make_sample_data(100, seed=2),
            'KCB': make_sample_data(100, seed=3),
        }
        results = self.engine.analyze_multiple_stocks(data_dict)
        breadth = self.engine.calculate_market_breadth(results)
        self.assertIn('total_stocks', breadth)
        self.assertEqual(breadth['total_stocks'], 3)
        self.assertIn('pct_above_sma50', breadth)

    def test_rsi_range(self):
        df = self.result['data']
        rsi = df['rsi'].dropna()
        if len(rsi) > 0:
            self.assertTrue((rsi >= 0).all(), "RSI should be >= 0")
            self.assertTrue((rsi <= 100).all(), "RSI should be <= 100")

    def test_empty_data(self):
        result = self.engine.analyze_stock(pd.DataFrame())
        self.assertEqual(result, {})


class TestSectorAnalysis(unittest.TestCase):
    """Test sector analysis."""

    @classmethod
    def setUpClass(cls):
        cls.analyzer = SectorAnalyzer()
        cls.engine = AnalysisEngine()

    def test_sector_mapping(self):
        self.assertEqual(self.analyzer.get_sector('SCOM'), 'Telecommunication')
        self.assertEqual(self.analyzer.get_sector('EQTY'), 'Banking')
        self.assertEqual(self.analyzer.get_sector('KCB'), 'Banking')
        self.assertEqual(self.analyzer.get_sector('EABL'), 'Manufacturing')
        self.assertEqual(self.analyzer.get_sector('UNKNOWN'), 'Other')

    def test_analyze_sectors(self):
        data_dict = {
            'SCOM': make_sample_data(100, seed=1),
            'EQTY': make_sample_data(100, seed=2),
            'KCB': make_sample_data(100, seed=3),
        }
        results = self.engine.analyze_multiple_stocks(data_dict)
        sectors = self.analyzer.analyze_sectors(data_dict, results)

        self.assertIn('Telecommunication', sectors)
        self.assertIn('Banking', sectors)

        banking = sectors['Banking']
        self.assertIn('EQTY', banking['symbols'])
        self.assertIn('KCB', banking['symbols'])
        self.assertEqual(banking['count'], 2)
        self.assertIn('avg_change_pct', banking)
        self.assertIn('avg_rsi', banking)
        self.assertIn('bullish_ratio', banking)


class TestReportGenerator(unittest.TestCase):
    """Test report generation."""

    @classmethod
    def setUpClass(cls):
        from report_generator import ReportGenerator
        cls.engine = AnalysisEngine()
        cls.tempdir = tempfile.mkdtemp()
        cls.rg = ReportGenerator(output_dir=cls.tempdir)

    def test_stock_report_html(self):
        data = make_sample_data(60)
        result = self.engine.analyze_stock(data)
        path = self.rg.generate_stock_report('TEST', result, report_type='html')
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        self.assertGreater(os.path.getsize(path), 500)

    def test_market_summary_html(self):
        data_dict = {
            'SCOM': make_sample_data(60, seed=1),
            'EQTY': make_sample_data(60, seed=2),
        }
        results = self.engine.analyze_multiple_stocks(data_dict)
        path = self.rg.generate_market_summary(results, report_type='html')
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        self.assertGreater(os.path.getsize(path), 500)

    def test_excel_export(self):
        data_dict = {
            'SCOM': make_sample_data(60, seed=1),
            'EQTY': make_sample_data(60, seed=2),
        }
        results = self.engine.analyze_multiple_stocks(data_dict)
        path = self.rg.export_to_excel(results)
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        self.assertGreater(os.path.getsize(path), 1000)


class TestEmailNotifier(unittest.TestCase):
    """Test email notification module."""

    def test_email_body_generation(self):
        from email_notifier import EmailNotifier
        config = Config()
        notifier = EmailNotifier(config)

        engine = AnalysisEngine()
        data_dict = {
            'SCOM': make_sample_data(60, seed=1),
            'EQTY': make_sample_data(60, seed=2),
        }
        results = engine.analyze_multiple_stocks(data_dict)
        breadth = engine.calculate_market_breadth(results)

        from sector_analysis import SectorAnalyzer
        sa = SectorAnalyzer()
        sectors = sa.analyze_sectors(data_dict, results)

        body = notifier.generate_email_body(results, sectors, breadth)
        self.assertIsInstance(body, str)
        self.assertIn('SCOM', body)
        self.assertIn('EQTY', body)
        self.assertIn('NSE Daily Market Report', body)


class TestPortfolioCsv(unittest.TestCase):
    """CSV input for the private portfolio files — this parses money, so the
    tricky spreadsheet-export cases (BOM, locale dates, decimal commas, broker
    columns) are pinned down explicitly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.enterContext(fake_bonds.use())

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, content):
        """Write text (or raw bytes) into the temp portfolio dir; return its path."""
        path = os.path.join(self.dir, name)
        if isinstance(content, bytes):
            with open(path, "wb") as f:
                f.write(content)
        else:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        return path

    # ---- parsing ----

    def test_basic_stock_csv(self):
        self.write("holdings.csv",
                   "symbol,quantity,buy_price,buy_date,note\n"
                   "scom , 1000,23.50,2026-03-14,first\n"
                   "KCB,800,44,,\n")
        self.assertEqual(portfolio_mod.load_holdings(self.dir), [
            {"symbol": "SCOM", "quantity": 1000.0, "buy_price": 23.5,
             "buy_date": "2026-03-14", "note": "first"},
            {"symbol": "KCB", "quantity": 800.0, "buy_price": 44.0, "buy_date": None, "note": ""},
        ])

    def test_broker_style_headers_and_extra_columns(self):
        # Header case/units/aliases are forgiven; Market Value and Unrealized P&L
        # are ignored on purpose — only what you PAID is ever read.
        self.write("international_holdings.csv",
                   'Instrument,Position,Market Value,Avg Price (USD),Unrealized P&L\n'
                   'AAPL,7,"1,500.25",190.25,+168.50\n'
                   'F,100,364.00,10.50,-686.00\n')
        lots = international_portfolio.load_holdings(self.dir)
        self.assertEqual([(l["symbol"], l["quantity"], l["buy_price"]) for l in lots],
                         [("AAPL", 7.0, 190.25), ("F", 100.0, 10.5)])

    def test_excel_bom_and_crlf(self):
        self.write("holdings.csv", b"\xef\xbb\xbfsymbol,quantity,buy_price\r\nSCOM,10,20\r\n\r\n")
        lots = portfolio_mod.load_holdings(self.dir)
        self.assertEqual([(l["symbol"], l["quantity"], l["buy_price"]) for l in lots],
                         [("SCOM", 10.0, 20.0)])

    def test_currency_symbols_and_thousands_separators(self):
        self.write("holdings.csv",
                   'symbol,quantity,buy_price\n'
                   'A,"1,000","$1,234.50"\n'
                   'B,2,KES 99.5\n'
                   'C,3,USD 10\n')
        lots = {l["symbol"]: l for l in portfolio_mod.load_holdings(self.dir)}
        self.assertEqual((lots["A"]["quantity"], lots["A"]["buy_price"]), (1000.0, 1234.5))
        self.assertEqual(lots["B"]["buy_price"], 99.5)
        self.assertEqual(lots["C"]["buy_price"], 10.0)

    def test_decimal_comma_is_rejected_not_misread_as_thousands(self):
        # "224,3" must never silently become 2243 — a 10x cost-basis error.
        self.write("holdings.csv", 'symbol,quantity,buy_price\nA,1,"224,3"\nB,1,10\n')
        with mock.patch.object(portfolio_csv.logger, "warning") as warn:
            lots = portfolio_mod.load_holdings(self.dir)
        self.assertEqual([l["symbol"] for l in lots], ["B"])
        self.assertTrue(warn.called)

    def test_bare_price_header_is_not_accepted_as_cost(self):
        # In a broker export "Price" is usually TODAY's price — using it as the
        # cost basis would make every gain/loss zero.
        path = self.write("holdings.csv", "symbol,quantity,price\nA,1,10\n")
        with self.assertRaises(portfolio_csv.CsvFormatError) as ctx:
            portfolio_csv.read_rows(path, portfolio_csv.STOCK_LOT_FIELDS)
        self.assertIn("buy_price", str(ctx.exception))
        self.assertEqual(portfolio_mod.load_holdings(self.dir), [])

    def test_semicolon_separated_file_gets_clear_error(self):
        path = self.write("holdings.csv", "symbol;quantity;buy_price\nA;1;2\n")
        with self.assertRaises(portfolio_csv.CsvFormatError) as ctx:
            portfolio_csv.read_rows(path, portfolio_csv.STOCK_LOT_FIELDS)
        self.assertIn("semicolon", str(ctx.exception))

    def test_unambiguous_dates_parse(self):
        cases = {
            "2026-09-20": "2026-09-20", "2026/09/20": "2026-09-20",
            "20 Sep 2026": "2026-09-20", "Sep 20, 2026": "2026-09-20",
            "20-Sep-26": "2026-09-20", "2026-09-20 00:00:00": "2026-09-20",
            "20/09/2026": "2026-09-20",   # 20 can't be a month -> day-first
            "09/20/2026": "2026-09-20",   # 20 can't be a month -> month-first
            "05/05/2026": "2026-05-05",   # same either way
        }
        for raw, expected in cases.items():
            self.assertEqual(portfolio_csv._parse_date(raw), expected, raw)

    def test_ambiguous_or_invalid_dates_are_refused_not_guessed(self):
        for raw in ("05/06/2026", "31/02/2026", "tomorrow", "45920"):
            with self.assertRaises(ValueError, msg=raw):
                portfolio_csv._parse_date(raw)

    def test_bad_optional_date_keeps_the_lot(self):
        self.write("holdings.csv", "symbol,quantity,buy_price,buy_date\nA,1,10,05/06/2026\n")
        with mock.patch.object(portfolio_csv.logger, "warning") as warn:
            lots = portfolio_mod.load_holdings(self.dir)
        self.assertEqual(len(lots), 1)
        self.assertIsNone(lots[0]["buy_date"])
        self.assertIn("ambiguous", warn.call_args[0][0])

    def test_bad_rows_are_skipped_without_losing_the_rest(self):
        self.write("holdings.csv",
                   "symbol,quantity,buy_price\n"
                   "OK,10,5\nZERO,0,5\nNEG,5,-1\n,5,5\nTXT,abc,5\nOK2,1.5,2\n")
        lots = portfolio_mod.load_holdings(self.dir)
        self.assertEqual([l["symbol"] for l in lots], ["OK", "OK2"])
        self.assertEqual(lots[1]["quantity"], 1.5)       # fractional shares are fine

    def test_blank_and_trailing_empty_rows_are_ignored_quietly(self):
        self.write("holdings.csv", "symbol,quantity,buy_price\nA,1,2\n,,\n\n   ,  ,\n")
        with mock.patch.object(portfolio_csv.logger, "warning") as warn:
            lots = portfolio_mod.load_holdings(self.dir)
        self.assertEqual(len(lots), 1)
        self.assertFalse(warn.called)

    def test_empty_file_and_header_only_are_empty_portfolios(self):
        self.write("holdings.csv", "")
        self.assertEqual(portfolio_mod.load_holdings(self.dir), [])
        self.write("holdings.csv", "symbol,quantity,buy_price\n")
        self.assertEqual(portfolio_mod.load_holdings(self.dir), [])

    # ---- CSV vs JSON ----

    def test_csv_wins_over_json_and_says_so(self):
        self.write("holdings.json", json.dumps(
            {"holdings": [{"symbol": "OLD", "quantity": 1, "buy_price": 1}]}))
        self.write("holdings.csv", "symbol,quantity,buy_price\nNEW,2,3\n")
        with mock.patch.object(portfolio_csv.logger, "warning") as warn:
            lots = portfolio_mod.load_holdings(self.dir)
        self.assertEqual([l["symbol"] for l in lots], ["NEW"])
        self.assertIn("IGNORED", warn.call_args[0][0])

    def test_json_still_works_when_there_is_no_csv(self):
        self.write("holdings.json", json.dumps(
            {"holdings": [{"symbol": "OLD", "quantity": 1, "buy_price": 2}]}))
        self.assertEqual([l["symbol"] for l in portfolio_mod.load_holdings(self.dir)], ["OLD"])
        self.assertEqual(portfolio_mod.load_holdings(os.path.join(self.dir, "nope")), [])

    def test_csv_and_json_describing_the_same_portfolio_load_identically(self):
        lots = [{"symbol": "SCOM", "quantity": 500, "buy_price": 34.5,
                 "buy_date": "2026-09-20", "note": "n"},
                {"symbol": "KCB", "quantity": 200, "buy_price": 92, "buy_date": None, "note": ""}]
        a, b = os.path.join(self.dir, "a"), os.path.join(self.dir, "b")
        os.makedirs(a)
        os.makedirs(b)
        with open(os.path.join(a, "holdings.json"), "w") as f:
            json.dump({"holdings": lots}, f)
        with open(os.path.join(b, "holdings.csv"), "w") as f:
            f.write("symbol,quantity,buy_price,buy_date,note\n"
                    "SCOM,500,34.5,2026-09-20,n\nKCB,200,92,,\n")
        self.assertEqual(portfolio_mod.load_holdings(a), portfolio_mod.load_holdings(b))

    # ---- adding a purchase when a CSV is the portfolio ----

    def test_add_lot_appends_a_row_to_the_csv_instead_of_a_shadowed_json(self):
        path = self.write("holdings.csv", "symbol,quantity,buy_price,buy_date,note\nSCOM,10,20,,\n")
        lots = portfolio_mod.add_lot(self.dir, "kcb", 5, 40.5, "2026-01-02", "top up")
        with open(path, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), "symbol,quantity,buy_price,buy_date,note\n"
                                       "SCOM,10,20,,\nKCB,5,40.5,2026-01-02,top up\n")
        self.assertEqual([l["symbol"] for l in lots], ["SCOM", "KCB"])          # returned list = full portfolio
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".json")], [])   # no ignored JSON written

    def test_append_keeps_bom_windows_line_endings_and_handles_a_missing_final_newline(self):
        # Exactly what Excel's "CSV UTF-8" produces — and a hand-saved file with no trailing newline.
        original = b"\xef\xbb\xbfsymbol,quantity,buy_price\r\nSCOM,10,20"          # no final CRLF
        path = self.write("holdings.csv", original)
        portfolio_mod.add_lot(self.dir, "KCB", 5, 40)
        with open(path, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))                          # BOM kept, not duplicated
        self.assertEqual(raw.count(b"\xef\xbb\xbf"), 1)
        self.assertEqual(raw.replace(b"\r\n", b"").count(b"\n"), 0)               # no stray LF-only breaks
        self.assertTrue(raw.startswith(original))                                   # existing bytes untouched
        self.assertEqual([(l["symbol"], l["quantity"]) for l in portfolio_mod.load_holdings(self.dir)],
                         [("SCOM", 10.0), ("KCB", 5.0)])                            # rows not glued together

    def test_append_follows_the_files_own_column_order_and_spelling(self):
        path = self.write("international_holdings.csv",
                          "Notes,Avg Price (USD),Ticker,Qty,Date\nold,100,AAPL,2,2026-01-01\n")
        international_portfolio.add_lot(self.dir, "msft", 3, 410.5, "2026-02-03", "new")
        with open(path, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read().splitlines()[-1], "new,410.5,MSFT,3,2026-02-03")
        lots = international_portfolio.load_holdings(self.dir)
        self.assertEqual((lots[1]["symbol"], lots[1]["quantity"], lots[1]["buy_price"], lots[1]["buy_date"]),
                         ("MSFT", 3.0, 410.5, "2026-02-03"))

    def test_append_round_trips_notes_containing_commas_and_quotes(self):
        self.write("holdings.csv", "symbol,quantity,buy_price,buy_date,note\n")
        portfolio_mod.add_lot(self.dir, "SCOM", 1, 2, None, 'He said "buy, then hold"')
        self.assertEqual(portfolio_mod.load_holdings(self.dir)[0]["note"], 'He said "buy, then hold"')

    def test_append_says_so_when_the_csv_has_no_column_for_a_value(self):
        self.write("holdings.csv", "symbol,quantity,buy_price\nSCOM,10,20\n")      # no buy_date / note columns
        with mock.patch.object(portfolio_mod.logger, "warning") as warn:
            portfolio_mod.add_lot(self.dir, "KCB", 5, 40, "2026-01-02", "hello")
        self.assertIn("buy_date", warn.call_args[0][0])
        self.assertIn("note", warn.call_args[0][0])
        self.assertEqual(len(portfolio_mod.load_holdings(self.dir)), 2)             # the purchase itself still saved

    def test_append_refuses_a_csv_it_cannot_safely_extend(self):
        self.write("holdings.csv", "")                                              # no header at all
        with self.assertRaises(ValueError):
            portfolio_mod.add_lot(self.dir, "A", 1, 2)
        self.write("holdings.csv", "symbol,quantity,price\nA,1,2\n")              # bare 'price' is not a cost column
        with self.assertRaises(ValueError):
            portfolio_mod.add_lot(self.dir, "B", 1, 2)

    def test_add_bond_appends_to_the_csv_and_canonicalises_the_issue(self):
        path = self.write("bonds.csv", "issue,face_value,purchase_price_pct,purchase_date,note\n")
        bonds = bonds_portfolio.add_bond(self.dir, "fdx9/2020/020", 250000, 100.0, None, "")
        with open(path, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read().splitlines()[-1], "FXD9/2020/020,250000,100,,")
        self.assertEqual([b["issue"] for b in bonds], ["FXD9/2020/020"])
        self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".json")], [])

    def test_add_bond_without_a_price_column_is_fine_because_par_is_the_default(self):
        self.write("bonds.csv", "issue,face_value\n")
        with mock.patch.object(bonds_portfolio.logger, "warning") as warn:
            bonds = bonds_portfolio.add_bond(self.dir, "IFB9/2021/7", 150000)      # price defaults to par
        self.assertFalse(warn.called)
        self.assertEqual(bonds[0]["purchase_price_pct"], 100.0)

    def test_international_add_goes_to_its_own_csv_not_the_nse_one(self):
        nse = self.write("holdings.csv", "symbol,quantity,buy_price\n")
        intl = self.write("international_holdings.csv", "symbol,quantity,buy_price\n")
        international_portfolio.add_lot(self.dir, "AAPL", 1, 190.25)
        self.assertEqual(open(nse).read(), "symbol,quantity,buy_price\n")           # untouched
        self.assertIn("AAPL,1,190.25", open(intl).read())

    def test_add_lot_starts_a_csv_when_there_is_no_portfolio_file_yet(self):
        portfolio_mod.add_lot(self.dir, "a", 3, 4.5, "2026-01-02", "x")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "holdings.csv")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "holdings.json")))
        with open(os.path.join(self.dir, "holdings.csv")) as f:
            self.assertEqual(f.read(), "symbol,quantity,buy_price,buy_date,note\nA,3,4.5,2026-01-02,x\n")
        self.assertEqual(portfolio_mod.load_holdings(self.dir),
                         [{"symbol": "A", "quantity": 3.0, "buy_price": 4.5,
                           "buy_date": "2026-01-02", "note": "x"}])
        bonds_portfolio.add_bond(self.dir, "ifb9/2021/7", 50000)
        international_portfolio.add_lot(self.dir, "msft", 5, 410)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "bonds.csv")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "international_holdings.csv")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "bonds.json")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "international_holdings.json")))

    def test_add_lot_still_appends_to_an_existing_json_when_there_is_no_csv(self):
        self.write("holdings.json", json.dumps({"holdings": [
            {"symbol": "B", "quantity": 1, "buy_price": 2, "buy_date": None, "note": ""}]}))
        portfolio_mod.add_lot(self.dir, "a", 3, 4.5, "2026-01-02", "x")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "holdings.csv")))
        self.assertEqual([l["symbol"] for l in portfolio_mod.load_holdings(self.dir)], ["B", "A"])

    # ---- bonds ----

    def test_bonds_csv_aliases_defaults_and_downstream_compatibility(self):
        self.write("bonds.csv",
                   'Bond,Face Value,Purchase Price Pct,Purchase Date,Note\n'
                   'FDX9/2020/020,"250,000",100,2023-03-15,typo alias\n'   # FDX -> FXD
                   'IFB9/2021/7,150000,,,\n')                             # blank -> par
        bonds = bonds_portfolio.load_bonds(self.dir)
        self.assertEqual([b["issue"] for b in bonds], ["FXD9/2020/020", "IFB9/2021/7"])
        self.assertEqual(bonds[0]["face_value"], 250000.0)
        self.assertEqual(bonds[1]["purchase_price_pct"], 100.0)
        self.assertIsNone(bonds[1]["purchase_date"])
        summary = bonds_portfolio.compute_bond_portfolio(bonds)
        self.assertEqual(summary["totals"]["n_available"], 2)

    def test_extra_reference_terms_load_from_a_file(self):
        """BOND_REFERENCE_EXTRA (sandbox runs): made-up bonds join the table."""
        path = os.path.join(self.dir, "extra.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ifb9/2019/9": dict(fake_bonds.FAKE_BOND_REFERENCE["IFB9/2021/7"])}, f)
        with mock.patch.dict(bonds_portfolio.BOND_REFERENCE):
            self.assertEqual(bonds_portfolio.load_extra_reference(path), 1)
            self.assertIn("IFB9/2019/9", bonds_portfolio.BOND_REFERENCE)      # canonical (upper case)
        self.assertNotIn("IFB9/2019/9", bonds_portfolio.BOND_REFERENCE)
        with mock.patch.object(bonds_portfolio.logger, "warning") as warn:
            self.assertEqual(bonds_portfolio.load_extra_reference(os.path.join(self.dir, "missing.json")), 0)
        self.assertTrue(warn.called)

    # ---- the shipped templates ----

    def test_example_csv_templates_load_cleanly(self):
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio")
        with mock.patch.object(portfolio_csv.logger, "warning") as warn:
            stocks = portfolio_csv.read_rows(os.path.join(folder, "holdings.example.csv"),
                                             portfolio_csv.STOCK_LOT_FIELDS)
            intl = portfolio_csv.read_rows(os.path.join(folder, "international_holdings.example.csv"),
                                           portfolio_csv.STOCK_LOT_FIELDS)
            bonds = portfolio_csv.read_rows(os.path.join(folder, "bonds.example.csv"),
                                            portfolio_csv.BOND_FIELDS)
        self.assertFalse(warn.called)
        self.assertEqual((len(stocks), len(intl), len(bonds)), (3, 3, 2))
        for _, row in bonds:   # every example bond must be one the dashboard has reference data for
            self.assertIn(bonds_portfolio._canonical_issue(row["issue"]),
                          bonds_portfolio.BOND_REFERENCE)
        # Load the template from a temp copy — never the real portfolio/watchlist.csv.
        import shutil
        shutil.copy(os.path.join(folder, "watchlist.example.csv"), os.path.join(self.dir, "watchlist.csv"))
        with mock.patch.object(watchlist_mod.logger, "warning") as warn:
            entries = watchlist_mod.load_watchlist(self.dir, nse_symbols={"EQTY", "EABL", "ABSA"})
        self.assertFalse(warn.called)
        self.assertEqual([(e["symbol"], e["market"]) for e in entries],
                         [("EQTY", "NSE"), ("EABL", "NSE"), ("AAPL", "INTL"), ("MSFT", "INTL")])


# ============================================================================
# Watchlist + dashboard app
# ============================================================================
import datetime as _dt
import http.client
import threading
import types

import watchlist as watchlist_mod
import watchlist_report
import symbol_lookup
import dashboard_app
import international_data
import company_logos


def _analysed(periods=140, seed=7, start=100.0, drift=0.0):
    """A real AnalysisEngine result on synthetic prices (no network)."""
    np.random.seed(seed)
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=periods, freq='B')
    close = start + np.cumsum(np.random.randn(periods) * 0.8 + drift)
    df = pd.DataFrame({'open': close, 'high': close + 1, 'low': close - 1, 'close': close,
                       'volume': np.random.randint(10_000, 90_000, periods)}, index=dates)
    return AnalysisEngine().analyze_stock(df)


def _facts(**over):
    f = watchlist_mod.extract_facts("NSE", {}, {})
    f.update(over)
    return f


def _config(base, **over):
    """A Config-like object pointing every folder into a temp dir."""
    cfg = types.SimpleNamespace(
        portfolio_dir=os.path.join(base, "portfolio"), report_directory=os.path.join(base, "reports"),
        cache_dir=os.path.join(base, "data"), log_file=os.path.join(base, "logs", "analyzer.log"),
        template_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"),
        enable_fx=False, enable_scoring=True, enable_price_validation=False, enable_official_close=False,
        price_disagree_threshold_pct=1.0, data_sources=["tradingview"],
    )
    for d in (cfg.portfolio_dir, cfg.report_directory, cfg.cache_dir, os.path.dirname(cfg.log_file)):
        os.makedirs(d, exist_ok=True)
    cfg.__dict__.update(over)
    return cfg


class TestCsvRowEdits(unittest.TestCase):
    """remove_row / update_row change exactly one record — every other byte of a
    file someone may also be editing in Excel must survive untouched."""

    F = portfolio_csv.STOCK_LOT_FIELDS

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, "holdings.csv")

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, raw):
        with open(self.path, "wb") as f:
            f.write(raw)

    def get(self):
        with open(self.path, "rb") as f:
            return f.read()

    def line_of(self, symbol):
        return [ln for ln, r in portfolio_csv.read_rows(self.path, self.F) if r["symbol"] == symbol][0]

    def test_remove_keeps_bom_crlf_multiline_cells_extra_columns_and_missing_final_newline(self):
        raw = (b'\xef\xbb\xbfTicker,Qty,Avg Price (USD),Notes,Broker\r\n'
               b'AAPL,10,190.25,"line one\r\nline two",X\r\n'
               b'"MSFT",5,410,,Y\r\n'
               b'NVDA,1.5,120,"a, b",Z')
        self.put(raw)
        removed = portfolio_csv.remove_row(self.path, self.F, self.line_of("MSFT"),
                                           expect={"symbol": "MSFT", "quantity": 5, "buy_price": 410})
        self.assertEqual(removed["symbol"], "MSFT")
        self.assertEqual(self.get(), raw.replace(b'"MSFT",5,410,,Y\r\n', b''))

    def test_remove_the_last_row(self):
        self.put(b"symbol,quantity,buy_price\nEQTY,1,2\nEABL,3,4")
        portfolio_csv.remove_row(self.path, self.F, 3, expect={"symbol": "EABL"})
        self.assertEqual(self.get(), b"symbol,quantity,buy_price\nEQTY,1,2\n")

    def test_update_changes_only_that_row_and_keeps_its_other_columns(self):
        raw = b"symbol,quantity,buy_price,note,broker\r\nEQTY,1,2,,keep me\r\nEABL,3,4,old,x\r\n"
        self.put(raw)
        values, dropped = portfolio_csv.update_row(self.path, self.F, 2, {"symbol": "EQTY"},
                                                   {"note": 'new "quoted", note', "buy_price": 2.5})
        self.assertEqual(dropped, [])
        self.assertEqual(values["buy_price"], 2.5)
        self.assertEqual(self.get(), b'symbol,quantity,buy_price,note,broker\r\nEQTY,1,2.5,"new ""quoted"", note",keep me\r\n'
                                     b"EABL,3,4,old,x\r\n")

    def test_update_reports_a_value_the_file_has_no_column_for(self):
        self.put(b"symbol,quantity,buy_price\nEQTY,1,2\n")
        _values, dropped = portfolio_csv.update_row(self.path, self.F, 2, {"symbol": "EQTY"}, {"note": "hi"})
        self.assertEqual(dropped, ["note"])
        self.assertEqual(self.get(), b"symbol,quantity,buy_price\nEQTY,1,2\n")

    def test_a_row_that_changed_since_the_page_loaded_is_never_touched(self):
        raw = b"symbol,quantity,buy_price\nEQTY,1,2\nEABL,3,4\n"
        self.put(raw)
        with self.assertRaises(portfolio_csv.RowChangedError):
            portfolio_csv.remove_row(self.path, self.F, 2, expect={"symbol": "EQTY", "quantity": 9})
        with self.assertRaises(portfolio_csv.RowChangedError):
            portfolio_csv.remove_row(self.path, self.F, 3, expect={"symbol": "EQTY"})
        with self.assertRaises(portfolio_csv.RowChangedError):
            portfolio_csv.update_row(self.path, self.F, 99, {"symbol": "EQTY"}, {"note": "x"})
        with self.assertRaises(portfolio_csv.RowChangedError):     # the header is not a data row
            portfolio_csv.remove_row(self.path, self.F, 1, expect={"symbol": "symbol"})
        self.assertEqual(self.get(), raw)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "backups")))

    def test_windows_1252_bytes_in_other_rows_survive(self):
        raw = b"symbol,quantity,buy_price,note\nAAPL,1,100,price \x80 note \x81 odd\nMSFT,2,200,keep\n"
        self.put(raw)
        portfolio_csv.remove_row(self.path, self.F, 3, expect={"symbol": "MSFT"})
        self.assertEqual(self.get(), b"symbol,quantity,buy_price,note\nAAPL,1,100,price \x80 note \x81 odd\n")

    def test_backup_first_and_only_the_newest_copies_are_kept(self):
        self.put(b"symbol,quantity,buy_price\n" + b"".join(b"S%d,1,2\n" % i for i in range(5)))
        with mock.patch.object(portfolio_csv, "MAX_BACKUPS_PER_FILE", 3):
            for _ in range(5):
                portfolio_csv.remove_row(self.path, self.F, 2, expect={})
        backups = sorted(os.listdir(os.path.join(self.dir, "backups")))
        self.assertEqual(len(backups), 3)
        self.assertTrue(all(b.startswith("holdings.csv.") and b.endswith(".bak") for b in backups))
        with open(os.path.join(self.dir, "backups", backups[-1]), "rb") as f:
            self.assertEqual(f.read(), b"symbol,quantity,buy_price\nS4,1,2\n")   # the state just before the last removal

    def test_permissions_kept_and_no_temp_files_left_even_if_the_write_fails(self):
        self.put(b"symbol,quantity,buy_price\nEQTY,1,2\nEABL,3,4\n")
        os.chmod(self.path, 0o600)
        portfolio_csv.remove_row(self.path, self.F, 2, expect={"symbol": "EQTY"})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        with mock.patch("portfolio_csv.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                portfolio_csv.update_row(self.path, self.F, 2, {"symbol": "EABL"}, {"buy_price": 5})
        self.assertEqual([f for f in os.listdir(self.dir) if f.startswith(".tmp")], [])
        self.assertEqual(self.get(), b"symbol,quantity,buy_price\nEABL,3,4\n")

    def test_temp_files_are_named_so_the_portfolio_ignore_rules_cover_them(self):
        # If the process died mid-write, the leftover temp file holds private data:
        # it must match the portfolio/*.csv rule in .gitignore and .dockerignore.
        seen = []
        real = portfolio_csv.tempfile.mkstemp

        def spy(*a, **k):
            fd, p = real(*a, **k)
            seen.append(os.path.basename(p))
            return fd, p
        self.put(b"symbol,quantity,buy_price\nEQTY,1,2\n")
        with mock.patch.object(portfolio_csv.tempfile, "mkstemp", side_effect=spy):
            portfolio_csv.update_row(self.path, self.F, 2, {"symbol": "EQTY"}, {"buy_price": 3}, backup=False)
        self.assertTrue(seen and seen[0].endswith(".csv"))

    def test_create_csv_and_unstorable_fields(self):
        p = os.path.join(self.dir, "new.csv")
        portfolio_csv.create_csv(p, self.F)
        with open(p) as f:
            self.assertEqual(f.read(), "symbol,quantity,buy_price,buy_date,note\n")
        with self.assertRaises(portfolio_csv.CsvFormatError):
            portfolio_csv.create_csv(p, self.F)
        self.put(b"Ticker,Qty,Cost Price\nEQTY,1,2\n")
        self.assertEqual(portfolio_csv.unstorable_fields(self.path, self.F,
                                                         {"symbol": "X", "buy_date": "2026-01-01", "note": ""}),
                         ["buy_date"])
        self.assertEqual(portfolio_csv.unstorable_fields(os.path.join(self.dir, "nope.csv"), self.F,
                                                         {"note": "x"}), [])


class TestWatchlistStorage(unittest.TestCase):
    NSE = {"EQTY", "EABL", "ABSA"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, "watchlist.csv")

    def tearDown(self):
        self._tmp.cleanup()

    def add(self, *a, **k):
        k.setdefault("nse_symbols", self.NSE)
        return watchlist_mod.add_to_watchlist(self.dir, *a, **k)

    def load(self):
        return watchlist_mod.load_watchlist(self.dir, nse_symbols=self.NSE)

    def test_first_add_creates_the_file_with_its_header(self):
        self.add("eqty", "NSE", 40, 60, "  wait for\nresults ", added="2026-09-01", added_price=45.1)
        with open(self.path) as f:
            self.assertEqual(f.read(), "symbol,market,buy_below,sell_above,note,added,added_price\n"
                                       "EQTY,NSE,40,60,wait for results,2026-09-01,45.1\n")
        self.assertEqual(self.load()[0]["note"], "wait for results")

    def test_same_ticker_can_be_watched_on_both_markets_but_not_twice_on_one(self):
        self.add("EQTY", "NSE")
        self.add("EQTY", "INTL")       # Equity Group in Nairobi vs an ETF in New York
        with self.assertRaises(watchlist_mod.AlreadyWatching):
            self.add("eqty", "Kenya")
        self.assertEqual([(e["symbol"], e["market"]) for e in self.load()], [("EQTY", "NSE"), ("EQTY", "INTL")])

    def test_market_words_and_automatic_choice(self):
        nm = watchlist_mod.normalize_market
        self.assertEqual([nm(x) for x in ("nse", "Kenya", "Nairobi Securities Exchange", "US", "nasdaq",
                                          "International", "", None)],
                         ["NSE", "NSE", "NSE", "INTL", "INTL", "INTL", None, None])
        self.assertIsNone(nm("Mars"))
        rm = watchlist_mod.resolve_market
        self.assertEqual(rm("EABL", "", self.NSE), "NSE")
        self.assertEqual(rm("AAPL", "", self.NSE), "INTL")
        self.assertEqual(rm("EABL", "INTL", self.NSE), "INTL")     # explicit always wins

    def test_impossible_tickers_are_refused_with_a_reason(self):
        for sym, market in (("EQ.TY", "NSE"), ("^GSPC", "INTL"), ("ES=F", "INTL"), ("", "NSE"),
                            ("WAY-TOO-LONG-TICKER", "INTL")):
            with self.assertRaises(ValueError, msg=sym):
                self.add(sym, market)
        self.add("BRK-B", "INTL")
        self.add("VOD.L", "INTL")
        self.assertFalse(os.path.exists(self.path + ".bak"))

    def test_target_prices_are_checked(self):
        for buy, sell in ((-1, None), (0, None), (None, "abc"), ("224,3", None), (50, 50), (60, 40)):
            with self.assertRaises(ValueError, msg=(buy, sell)):
                self.add("EQTY", "NSE", buy, sell)
        entry, _ = self.add("EQTY", "NSE", "1,234.50", "KES 2,000")
        self.assertEqual((entry["buy_below"], entry["sell_above"]), (1234.5, 2000.0))

    def test_a_hand_written_file_with_other_headers_and_duplicates(self):
        with open(self.path, "w") as f:
            f.write("Ticker,Exchange,Buy Target,Sell Target,Date Added\n"
                    "eabl,,250,400,2026-09-15\n"
                    "AAPL,,,,\n"
                    "EABL,nse,1,2,\n"
                    "BAD TICKER,NSE,,,\n"
                    "ABSA,NSE,-5,,\n")
        entries = self.load()
        self.assertEqual([(e["symbol"], e["market"], e["buy_below"], e["sell_above"]) for e in entries],
                         [("EABL", "NSE", 250.0, 400.0), ("AAPL", "INTL", None, None), ("ABSA", "NSE", None, None)])
        removed = watchlist_mod.remove_from_watchlist(self.dir, "EABL", "NSE", nse_symbols=self.NSE)
        self.assertEqual(removed["buy_below"], 250.0)
        with open(self.path) as f:    # both EABL rows gone, everything else exactly as typed
            self.assertEqual(f.read(), "Ticker,Exchange,Buy Target,Sell Target,Date Added\n"
                                       "AAPL,,,,\nBAD TICKER,NSE,,,\nABSA,NSE,-5,,\n")

    def test_update_in_place_and_checked_against_the_other_target(self):
        self.add("EQTY", "NSE", 40, 60, "a")
        self.add("AAPL", "INTL")
        before = open(self.path).read().splitlines()[2]
        entry, _ = watchlist_mod.update_watchlist_entry(self.dir, "EQTY", "NSE", buy_below=45,
                                                        nse_symbols=self.NSE)
        self.assertEqual((entry["buy_below"], entry["sell_above"], entry["note"]), (45.0, 60.0, "a"))
        with self.assertRaises(ValueError):    # 70 isn't below the existing sell price of 60
            watchlist_mod.update_watchlist_entry(self.dir, "EQTY", "NSE", buy_below=70, nse_symbols=self.NSE)
        watchlist_mod.update_watchlist_entry(self.dir, "EQTY", "NSE", sell_above="", note="",
                                             nse_symbols=self.NSE)
        e = self.load()[0]
        self.assertEqual((e["buy_below"], e["sell_above"], e["note"]), (45.0, None, ""))
        self.assertEqual(open(self.path).read().splitlines()[2], before)
        with self.assertRaises(watchlist_mod.NotOnWatchlist):
            watchlist_mod.update_watchlist_entry(self.dir, "EABL", "NSE", buy_below=1, nse_symbols=self.NSE)

    def test_remove_missing_stock_and_long_notes(self):
        with self.assertRaises(watchlist_mod.NotOnWatchlist):
            watchlist_mod.remove_from_watchlist(self.dir, "EQTY", "NSE", nse_symbols=self.NSE)
        with self.assertRaises(ValueError):
            self.add("EQTY", "NSE", note="x" * 301)


class TestWatchlistSignals(unittest.TestCase):
    """The checklist is the 'should I buy?' information — its rules are pinned down."""

    def lean(self, items, key):
        return {i["key"]: i["lean"] for i in items}.get(key)

    def test_target_status_boundaries(self):
        ts = watchlist_mod.target_status
        self.assertEqual(ts(40, 40, 60)["status"], "buy_zone")       # exactly at the buy price
        self.assertEqual(ts(60, 40, 60)["status"], "sell_zone")      # exactly at the sell price
        self.assertEqual(ts(39.99, 40, None)["status"], "buy_zone")
        between = ts(50, 40, 60)
        self.assertEqual(between["status"], "between")
        self.assertIn("fall 20.0%", between["text"])
        self.assertIn("rise 20.0%", between["text"])
        self.assertEqual(ts(50, None, None)["status"], "none")
        self.assertEqual(ts(None, 40, None)["status"], "unknown")
        self.assertIn("$40.00", ts(50, 40, None, "USD")["text"])

    def test_trend_averages_momentum_and_rsi(self):
        items = watchlist_mod.signal_checklist(_facts(price=110, sma50=100, ma_signal="golden_cross",
                                                      macd_signal="bearish_cross", rsi=25))
        self.assertEqual([self.lean(items, k) for k in ("trend", "ma", "macd", "rsi")],
                         ["buy", "buy", "sell", "buy"])
        items = watchlist_mod.signal_checklist(_facts(price=90, sma50=100, ma_signal="bearish", rsi=75))
        self.assertEqual([self.lean(items, k) for k in ("trend", "ma", "macd", "rsi")],
                         ["sell", "sell", "na", "sell"])
        self.assertEqual(self.lean(watchlist_mod.signal_checklist(_facts(rsi=50)), "rsi"), "neutral")

    def test_valuation(self):
        medians = {"Finance": {"pe_ratio": 10.0}}
        cheap = watchlist_mod.signal_checklist(_facts(pe=5, sector="Finance"), sector_medians=medians)
        pricey = watchlist_mod.signal_checklist(_facts(pe=20, sector="Finance"), sector_medians=medians)
        loss = watchlist_mod.signal_checklist(_facts(pe=-3))
        self.assertEqual([self.lean(x, "value") for x in (cheap, pricey, loss)], ["buy", "sell", "sell"])
        intl = lambda **k: watchlist_mod.signal_checklist(dict(_facts(**k), market="INTL", currency="USD"))  # noqa: E731
        self.assertEqual(self.lean(intl(peg=0.8), "value"), "buy")
        self.assertEqual(self.lean(intl(peg=2.5), "value"), "sell")
        self.assertEqual(self.lean(intl(pe=30, forward_pe=20), "value"), "buy")
        self.assertEqual(self.lean(intl(pe=20, forward_pe=30), "value"), "sell")
        self.assertEqual(self.lean(intl(), "value"), "na")       # an ETF: no earnings valuation

    def test_analysts_dividend_score_and_liquidity(self):
        intl = dict(_facts(price=100, rec_key="buy", num_analysts=12, target_mean=90), market="INTL", currency="USD")
        item = [i for i in watchlist_mod.signal_checklist(intl) if i["key"] == "analysts"][0]
        self.assertEqual(item["lean"], "buy")
        self.assertIn("already above that target", item["text"])
        self.assertEqual(self.lean(watchlist_mod.signal_checklist(_facts(analyst_mark=4.0)), "analysts"), "sell")
        self.assertEqual(self.lean(watchlist_mod.signal_checklist(_facts(tech_rating=0.6)), "analysts"), "buy")
        self.assertEqual(self.lean(watchlist_mod.signal_checklist(_facts(dividend_yield=9, payout=120)), "dividend"), "sell")
        self.assertEqual(self.lean(watchlist_mod.signal_checklist(_facts(dividend_yield=9, payout=60)), "dividend"), "buy")
        self.assertEqual(self.lean(watchlist_mod.signal_checklist(_facts(), score={"overall": 30}), "score"), "sell")
        thin = watchlist_mod.signal_checklist(_facts(value_traded=50_000))
        self.assertEqual(self.lean(thin, "liquidity"), "sell")
        intl_thin = watchlist_mod.signal_checklist(dict(_facts(value_traded=50_000), market="INTL"))
        self.assertIsNone(self.lean(intl_thin, "liquidity"))

    def test_tally_ignores_your_targets_and_the_52_week_position(self):
        items = watchlist_mod.signal_checklist(_facts(price=40, week52_high=100, week52_low=39), buy_below=50)
        self.assertEqual(self.lean(items, "targets"), "buy")
        self.assertEqual(watchlist_mod.tally(items), {"buy": 0, "sell": 0, "neutral": 1})
        s = watchlist_mod.summarize
        self.assertEqual(s({"buy": 4, "sell": 1, "neutral": 2})[0], "positive")
        self.assertEqual(s({"buy": 3, "sell": 2, "neutral": 0})[0], "mixed")
        self.assertEqual(s({"buy": 0, "sell": 3, "neutral": 1})[0], "negative")
        self.assertEqual(s({"buy": 0, "sell": 0, "neutral": 0})[0], "none")

    def test_since_added_and_returns(self):
        hist = pd.DataFrame({"close": [10.0, 11.0, 12.0, 13.0]},
                            index=pd.to_datetime(["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03"]))
        self.assertAlmostEqual(watchlist_mod.since_added(15, 12, "2026-09-01", hist)["pct"], 25.0)
        fallback = watchlist_mod.since_added(15, None, "2026-09-01", hist)
        self.assertAlmostEqual(fallback["pct"], (15 / 11 - 1) * 100)
        self.assertIn("2026-09-01", fallback["basis"])
        self.assertIsNone(watchlist_mod.since_added(15, None, "2026-09-10", hist))   # after the history ends
        self.assertIsNone(watchlist_mod.since_added(None, 12, None, hist))
        # Added today: a "change since" of a few minutes is noise, so none is shown
        self.assertIsNone(watchlist_mod.since_added(15, 14.9, "2026-10-04", hist, today=_dt.date(2026, 10, 4)))
        self.assertIsNotNone(watchlist_mod.since_added(15, 14.9, "2026-10-03", hist, today=_dt.date(2026, 10, 4)))
        days = pd.date_range("2025-12-01", "2026-03-31", freq="D")       # closes 100 (Dec 1) … 220 (Mar 31)
        series = pd.DataFrame({"close": np.arange(len(days), dtype=float) + 100}, index=days)
        r = watchlist_mod.returns_from_history(series)
        self.assertAlmostEqual(r["1W"], (220 / 213 - 1) * 100)          # 7 calendar days back
        self.assertAlmostEqual(r["1M"], (220 / 190 - 1) * 100)          # 30 calendar days back
        self.assertAlmostEqual(r["YTD"], (220 / 130 - 1) * 100)         # vs the last close of 2025 (Dec 31)
        self.assertNotIn("6M", r)                                       # history is ~2 months short: no figure
        # A "6 months" download that starts a couple of days short still gets a 6M figure
        short = pd.DataFrame({"close": np.arange(180, dtype=float) + 100},
                             index=pd.date_range("2025-10-05", periods=180, freq="D"))
        self.assertAlmostEqual(watchlist_mod.returns_from_history(short)["6M"], (279 / 100 - 1) * 100)
        nse = watchlist_mod.performance("NSE", {"perf_1w": 1.5, "perf_1y": 20}, series)
        self.assertEqual((nse["1W"], nse["1Y"]), (1.5, 20))
        self.assertAlmostEqual(nse["YTD"], r["YTD"])                    # filled from history when TradingView has none

    def test_attention_items(self):
        today = _dt.date(2026, 10, 5)
        fund = {"price_52w_high": 101, "price_52w_low": 50, "earnings_next_date": "2026-10-08",
                "dividend_ex_date": "2026-10-15", "close": 100, "change_pct": 6.0}
        view = watchlist_mod.build_entry_view({"symbol": "EQTY", "market": "NSE", "buy_below": 100},
                                              None, fund, today=today)
        texts = " | ".join(t for _, t in view["attention"])
        for bit in ("at or below your buy price", "52-week high", "rose 6.0% today",
                    "earnings in 3 days", "ex-dividend on 2026-10-15"):
            self.assertIn(bit, texts)
        empty = watchlist_mod.build_entry_view({"symbol": "ZZZ", "market": "INTL"}, None, {}, today=today)
        self.assertFalse(empty["has_data"])
        self.assertIn("check the ticker", empty["attention"][0][1])

    def test_money_formatting(self):
        fm, fb = watchlist_mod.fmt_money, watchlist_mod.fmt_big
        self.assertEqual([fm(1234.5, "USD"), fm(34.5, "KES"), fm(126.8, "GBp"), fm(0.05, "KES"), fm(None, "USD")],
                         ["$1,234.50", "KES 34.50", "126.80 GBp", "KES 0.0500", "—"])
        self.assertEqual([fb(3.21e12, "USD"), fb(29.4e9, "GBp"), fb(1.4e12, "KES"), fb(None, "USD")],
                         ["$3.21T", "29.40B GBP", "KES 1.40T", "—"])

    def test_full_view_from_real_analysis_nse_and_international(self):
        res = _analysed(drift=0.3)
        view = watchlist_mod.build_entry_view(
            {"symbol": "AAPL", "market": "INTL", "buy_below": 1, "sell_above": 10_000, "added": None},
            res, {"currency": "USD", "name": "Apple Inc.", "week52_high": 200, "week52_low": 50,
                  "recommendation_key": "buy", "num_analysts": 5},
            holding={"quantity": 10, "avg_cost": 50}, score={"overall": 75})
        self.assertTrue(view["has_data"])
        self.assertEqual(view["name"], "Apple Inc.")
        self.assertEqual(view["target"]["status"], "between")
        self.assertIn("You own 10 shares", view["holding"]["text"])
        self.assertTrue(set(view["performance"]) >= {"1W", "1M", "3M"})
        self.assertEqual(sum(view["tally"].values()), sum(1 for i in view["checklist"] if i["in_tally"] and i["lean"] != "na"))


class _NoLogos:
    """Keep the page tests off the network (logo look-ups)."""

    def setUp(self):
        p = mock.patch.object(company_logos, "fetch_logo_base64", return_value=None)
        p.start()
        self.addCleanup(p.stop)


class TestWatchlistPage(_NoLogos, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = _config(self._tmp.name)
        from report_generator import ReportGenerator
        self.rg = ReportGenerator(template_dir=self.cfg.template_dir, output_dir=self.cfg.report_directory,
                                  clean_old=False, cache_dir=self.cfg.cache_dir)

    def html(self):
        with open(os.path.join(self.cfg.report_directory, "watchlist.html"), encoding="utf-8") as f:
            return f.read()

    def generate(self, preloaded):
        return watchlist_report.generate_watchlist_page(self.cfg, self.rg, AnalysisEngine(), preloaded=preloaded,
                                                        fetch_news=False)

    def test_empty_watchlist_page(self):
        info = self.generate({"fundamentals": {"EQTY": {}}, "nse_lots": [], "intl_lots": []})
        self.assertEqual(info["count"], 0)
        page = self.html()
        self.assertIn("Your watchlist is empty", page)
        self.assertIn('href="watchlist.html" class="nav-item active" aria-current="page"', page)
        self.assertIn("<span>Watchlist</span>", page)
        self.assertRegex(page, r'id="wl-add-panel" class="[^"]*app-only')
        self.assertIn("Open Dashboard.command", page)

    def test_page_keeps_every_hook_the_app_script_uses(self):
        """app_assets/manage.js finds its way around this page by structure:
        the add panel, the status-pill slot, each Edit/Remove button's own
        card (whose id it rebuilds with anchorFor()) and each table row's
        link to that card. A redesign must keep all of them."""
        from bs4 import BeautifulSoup
        self.build_three_card_page()
        page = self.html()
        soup = BeautifulSoup(page, "html.parser")

        def anchor_for(symbol, market):          # the same rule as manage.js's anchorFor()
            return "wl-" + re.sub(r"[^A-Za-z0-9_.-]", "_", f"{symbol}-{market}")

        self.assertEqual(len(soup.select("#wl-add-panel")), 1)
        self.assertEqual(len(soup.select(".header-actions")), 1)
        ids = [el["id"] for el in soup.select("[id]")]
        self.assertEqual(len(ids), len(set(ids)), "duplicate ids")
        buttons = soup.select(".wl-btn[data-wl-action]")
        self.assertEqual(len(buttons), 6)                      # edit + remove on each of 3 cards
        for b in buttons:
            card = b.find_parent(class_="wl-card")
            self.assertIsNotNone(card, "button outside its card")
            self.assertEqual(card.get("id"), anchor_for(b["data-symbol"], b["data-market"]))
        for card in soup.select(".wl-card[id]"):
            links = soup.select(f'tr a[href="#{card["id"]}"]')
            self.assertTrue(links, f"no table row links to {card['id']}")

    def build_three_card_page(self):
        with open(os.path.join(self.cfg.portfolio_dir, "watchlist.csv"), "w") as f:
            f.write("symbol,market,buy_below,sell_above,note,added,added_price\n"
                    'EQTY,NSE,1,1000,"<script>alert(1)</script> & co",,\n'
                    "AAPL,INTL,,,,,\n"
                    "GONE,INTL,,,,,\n")
        nse_res, intl_res = _analysed(seed=1), _analysed(seed=2)
        fund = {"name": "Equity Group & Co", "sector": "Finance", "pe_ratio": 5, "price_52w_high": 500,
                "price_52w_low": 1, "_data_date": "2026-10-03"}
        guard = mock.patch.multiple(international_data, fetch_dividend_history=mock.DEFAULT,
                                    fetch_earnings_calendar=mock.DEFAULT, fetch_history=mock.DEFAULT,
                                    fetch_fundamentals=mock.DEFAULT)
        with guard as fakes:
            fakes["fetch_dividend_history"].return_value = []
            fakes["fetch_earnings_calendar"].return_value = {}
            fakes["fetch_history"].return_value = None
            fakes["fetch_fundamentals"].return_value = {}
            return self.generate({
                "fundamentals": {"EQTY": fund}, "nse_results": {"EQTY": nse_res},
                "intl_results": {"AAPL": intl_res},
                "intl_fundamentals": {"AAPL": {"name": "Apple Inc.", "currency": "USD", "website": None}},
                "nse_lots": [], "intl_lots": [{"symbol": "AAPL", "quantity": 10, "buy_price": 190.25}],
                "usd_kes": None, "sector_medians": {"Finance": {"pe_ratio": 7}},
            })

    def test_cards_built_from_preloaded_data_without_any_network(self):
        with open(os.path.join(self.cfg.portfolio_dir, "watchlist.csv"), "w") as f:
            f.write("symbol,market,buy_below,sell_above,note,added,added_price\n"
                    'EQTY,NSE,1,1000,"<script>alert(1)</script> & co",,\n'
                    "AAPL,INTL,,,,,\n"
                    "GONE,INTL,,,,,\n")
        nse_res, intl_res = _analysed(seed=1), _analysed(seed=2)
        fund = {"name": "Equity Group & Co", "sector": "Finance", "pe_ratio": 5, "price_52w_high": 500,
                "price_52w_low": 1, "_data_date": "2026-10-03"}
        guard = mock.patch.multiple(international_data, fetch_dividend_history=mock.DEFAULT,
                                    fetch_earnings_calendar=mock.DEFAULT, fetch_history=mock.DEFAULT,
                                    fetch_fundamentals=mock.DEFAULT)
        with guard as fakes:
            fakes["fetch_dividend_history"].return_value = []
            fakes["fetch_earnings_calendar"].return_value = {}
            fakes["fetch_history"].return_value = None          # GONE: Yahoo has nothing
            fakes["fetch_fundamentals"].return_value = {}
            info = self.generate({
                "fundamentals": {"EQTY": fund}, "nse_results": {"EQTY": nse_res},
                "intl_results": {"AAPL": intl_res},
                "intl_fundamentals": {"AAPL": {"name": "Apple Inc.", "currency": "USD", "website": None}},
                "nse_lots": [], "intl_lots": [{"symbol": "AAPL", "quantity": 10, "buy_price": 190.25}],
                "usd_kes": None, "sector_medians": {"Finance": {"pe_ratio": 7}},
            })
        self.assertEqual((info["count"], info["missing"]), (3, ["GONE"]))
        page = self.html()
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; co", page)
        self.assertIn("Equity Group &amp; Co", page)
        self.assertEqual(page.count('<article class="wl-card"'), 3)
        self.assertEqual(len(re.findall(r'<figure class="chart" id="c-wl-', page)), 2)   # SVG price charts
        self.assertIn("You own 10 shares", page)
        self.assertIn("No price data for <b>GONE</b>", page)
        # Each stock with data got a full page, linked from its card
        files = os.listdir(self.cfg.report_directory)
        for prefix in ("EQTY_report_", "intl_AAPL_report_"):
            name = [f for f in files if f.startswith(prefix)][0]
            self.assertIn(f'href="{name}"', page)

    def test_unreadable_watchlist_file_shows_a_clear_message(self):
        with open(os.path.join(self.cfg.portfolio_dir, "watchlist.csv"), "w") as f:
            f.write("symbol;market\nEQTY;NSE\n")
        info = self.generate({"fundamentals": {"EQTY": {}}})
        self.assertIsNotNone(info["load_error"])
        self.assertIn("watchlist file couldn", self.html())

    def test_edit_buttons_prefill_plain_numbers(self):
        self.assertEqual([watchlist_report._plain_number(v) for v in (200.0, 45.1, 0.0525, None)],
                         ["200", "45.1", "0.0525", ""])

    def test_error_page(self):
        watchlist_report.write_error_page(self.rg, "boom <b>")
        page = self.html()
        self.assertIn("couldn't be built", page)
        self.assertIn("boom &lt;b&gt;", page)

    def test_international_page_prices_in_the_stocks_own_currency(self):
        res = _analysed(seed=3)
        common = {"week52_low": 80.0, "week52_high": 140.0, "target_mean_price": 130.0, "eps_ttm": 2.5}
        usd = self.rg.generate_international_stock_report("AAPL", res, fundamentals=dict(common, currency="USD"))
        gbp = self.rg.generate_international_stock_report("VOD.L", res, fundamentals=dict(common, currency="GBp"),
                                                          context="watchlist")
        with open(usd, encoding="utf-8") as f:
            u = f.read()
        with open(gbp, encoding="utf-8") as f:
            g = f.read()
        self.assertIn("$80.00 – $140.00", u)
        self.assertIn("one of your private <strong>international (US-listed)</strong> holdings", u)
        self.assertIn("80.00 GBp – 140.00 GBp", g)
        self.assertIn("on your private <strong>watchlist</strong>", g)
        self.assertNotIn("$80.00", g)
        self.assertIn('data-market="INTL"', g)


class TestSymbolLookup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = self._tmp.name
        symbol_lookup._universe_memo.clear()
        symbol_lookup._intl_cache.clear()
        self.addCleanup(symbol_lookup._universe_memo.clear)
        self.addCleanup(symbol_lookup._intl_cache.clear)
        today = _dt.date.today().strftime("%Y%m%d")
        with open(os.path.join(self.cache, f"fundamentals_{today}.json"), "w") as f:
            json.dump({"EQTY": {"name": "Equity Group Holdings Limited", "close": 45.1, "sector": "Finance"},
                       "EABL": {"name": "East African Breweries Plc", "close": 250.0},
                       "ABSA": {"name": "Absa Bank Kenya Plc", "close": 20.0}}, f)
        self.yahoo = mock.patch.object(symbol_lookup, "_yahoo_search", return_value=[
            {"symbol": "EQTY", "shortname": "Kovitz Core Equity ETF", "quoteType": "ETF", "exchDisp": "NYSEArca"},
            {"symbol": "ES=F", "shortname": "E-Mini S&P", "quoteType": "FUTURE"},
            {"symbol": "^GSPC", "shortname": "S&P 500", "quoteType": "INDEX"},
            {"symbol": "AAPL", "longname": "Apple Inc.", "quoteType": "EQUITY", "exchDisp": "NASDAQ"},
        ])
        self.yahoo.start()
        self.addCleanup(self.yahoo.stop)

    def test_india_is_never_labelled_plain_nse(self):
        with mock.patch.object(symbol_lookup, "_yahoo_search", return_value=[
                {"symbol": "IDEA.NS", "longname": "Vodafone Idea", "quoteType": "EQUITY", "exchDisp": "NSE"}]):
            r = symbol_lookup.search("idea", self.cache)
        self.assertEqual([(x["symbol"], x["exchange"]) for x in r["results"]], [("IDEA.NS", "NSE India")])

    def test_search_by_name_or_ticker_groups_both_markets(self):
        r = symbol_lookup.search("equity", self.cache)
        self.assertEqual([(x["market"], x["symbol"]) for x in r["results"]],
                         [("NSE", "EQTY"), ("INTL", "EQTY"), ("INTL", "AAPL")])   # futures + index dropped
        self.assertEqual(r["results"][0]["price"], 45.1)
        self.assertEqual([x["symbol"] for x in symbol_lookup.search("brew", self.cache)["results"] if x["market"] == "NSE"],
                         ["EABL"])
        self.assertEqual(symbol_lookup.search("  ", self.cache)["results"], [])

    def test_international_search_down_still_finds_nse(self):
        with mock.patch.object(symbol_lookup, "_yahoo_search",
                               side_effect=symbol_lookup.LookupUnavailable("Couldn't reach Yahoo Finance.")):
            r = symbol_lookup.search("absa", self.cache)
        self.assertEqual([x["symbol"] for x in r["results"]], ["ABSA"])
        self.assertIn("Yahoo", r["intl_error"])

    def test_quote_nse_found_and_typo(self):
        self.assertEqual(symbol_lookup.quote("eqty", "NSE", self.cache)["name"], "Equity Group Holdings Limited")
        miss = symbol_lookup.quote("EQTX", "NSE", self.cache)
        self.assertFalse(miss["found"])
        self.assertIn("Did you mean EQTY", miss["message"])

    def test_quote_international_found_missing_and_unreachable(self):
        info = {"quoteType": "EQUITY", "regularMarketPrice": 126.8, "currency": "GBp", "longName": "Vodafone",
                "fullExchangeName": "LSE", "a": 1, "b": 2}
        with mock.patch.object(international_data, "fetch_info", return_value=info):
            q = symbol_lookup.quote("vod.l", "INTL", self.cache)
        self.assertEqual((q["found"], q["currency"], q["price"]), (True, "GBp", 126.8))
        with mock.patch.object(international_data, "fetch_info", return_value={"trailingPegRatio": None}):
            self.assertFalse(symbol_lookup.quote("NOPE1", "INTL", self.cache)["found"])
        with mock.patch.object(international_data, "fetch_info", side_effect=ConnectionError("offline")):
            with self.assertRaises(symbol_lookup.LookupUnavailable):
                symbol_lookup.quote("AAPL", "INTL", self.cache)
        # Yahoo answered "no such ticker" — but only trust that if Yahoo is reachable at all
        with mock.patch.object(international_data, "fetch_info", return_value={}), \
                mock.patch.object(symbol_lookup, "_yahoo_search", side_effect=symbol_lookup.LookupUnavailable("x")):
            with self.assertRaises(symbol_lookup.LookupUnavailable):
                symbol_lookup.quote("AAPL", "INTL", self.cache)
        with mock.patch.object(international_data, "fetch_info", return_value=dict(info, quoteType="FUTURE")):
            self.assertEqual(symbol_lookup.quote("ES=F", "INTL", self.cache)["reason"], "not_a_stock")


class _StubLookup:
    """Stands in for symbol_lookup in the app tests — no network."""
    LookupUnavailable = symbol_lookup.LookupUnavailable

    def __init__(self):
        self.offline = False
        self.prices = {("NSE", "EQTY"): 45.0, ("NSE", "EABL"): 250.0, ("INTL", "AAPL"): 200.0,
                       ("INTL", "VOD.L"): 126.8}

    def search(self, q, cache_dir):
        return {"query": q, "results": [{"symbol": "EQTY", "market": "NSE", "name": "Equity Group"}],
                "intl_error": None, "nse_error": None, "nse_stale": False}

    def quote(self, symbol, market, cache_dir):
        if self.offline:
            raise self.LookupUnavailable("Couldn't reach Yahoo Finance.")
        price = self.prices.get((market, symbol.upper()))
        if price is None:
            return {"found": False, "reason": "not_found", "message": f"No such ticker {symbol}.",
                    "suggestions": [{"symbol": "EQTY", "market": "NSE", "name": "Equity Group"}]}
        return {"found": True, "symbol": symbol.upper(), "market": market, "name": symbol.upper() + " Inc",
                "price": price, "currency": "GBp" if symbol.upper() == "VOD.L" else ("KES" if market == "NSE" else "USD")}


class _FakeJobs:
    def __init__(self):
        self.requests = []

    def request(self, kind, delay=0):
        self.requests.append((kind, delay))

    def status(self):
        return {"running": False, "job": None, "queued": [], "last": None, "generation": 0}

    def stop(self):
        pass


class TestDashboardApp(unittest.TestCase):
    """The real HTTP server on a free port, with stubbed look-ups and update jobs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = _config(self._tmp.name)
        with open(os.path.join(self.cfg.report_directory, "index.html"), "w") as f:
            f.write("<html>overview</html>")
        with open(os.path.join(self.cfg.report_directory, ".cache_date"), "w") as f:
            f.write("secret")
        os.makedirs(os.path.join(self.cfg.report_directory, "sub"))
        self.lookup, self.jobs = _StubLookup(), _FakeJobs()
        nse = mock.patch.object(symbol_lookup, "nse_symbols", return_value={"EQTY", "EABL"})
        nse.start()
        self.addCleanup(nse.stop)
        self.app = dashboard_app.DashboardApp(self.cfg, jobs=self.jobs, lookup=self.lookup)
        self.server = dashboard_app.make_server(self.app, 0)
        self.port = self.app.port
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def req(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None or raw is not None:
            h.update({"Content-Type": "application/json", "X-Dashboard-Token": self.app.token,
                      "Origin": f"http://127.0.0.1:{self.port}"})
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=payload, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        try:
            data = json.loads(data)
        except ValueError:
            data = data.decode("utf-8", "replace")
        return r.status, data, dict(r.getheaders())

    def watchlist_text(self):
        with open(os.path.join(self.cfg.portfolio_dir, "watchlist.csv")) as f:
            return f.read()

    def test_ping_pages_and_security_headers(self):
        self.assertEqual(self.req("GET", "/api/ping")[1], {"app": "kenyan-stock-analyzer", "version": 1})
        status, body, headers = self.req("GET", "/index.html")
        self.assertEqual((status, body), (200, "<html>overview</html>"))
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.req("GET", "/")[0], 302)
        self.assertEqual(self.req("HEAD", "/index.html")[0], 200)

    def test_other_hosts_are_refused(self):
        # A DNS-rebinding page would arrive with its own host name in the Host header.
        for host in ("evil.example", f"evil.example:{self.port}", f"127.0.0.1:{self.port + 1}", "127.0.0.1"):
            self.assertEqual(self.req("GET", "/index.html", headers={"Host": host})[0], 403, host)
            self.assertEqual(self.req("GET", "/api/session", headers={"Host": host})[0], 403, host)
        self.assertEqual(self.req("GET", "/index.html", headers={"Host": f"localhost:{self.port}"})[0], 200)
        # No Host header at all (http.client always adds one, so send this by hand)
        import socket
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(b"GET /api/session HTTP/1.0\r\n\r\n")
            reply = s.recv(4096).decode()
        self.assertTrue(reply.startswith("HTTP/1.0 403"), reply[:60])
        self.assertNotIn(self.app.token, reply)

    def test_no_way_out_of_the_reports_folder(self):
        with open(os.path.join(self.cfg.portfolio_dir, "watchlist.csv"), "w") as f:
            f.write("symbol\nEQTY\n")
        for path in ("/../portfolio/watchlist.csv", "/%2e%2e/portfolio/watchlist.csv", "/.cache_date",
                     "/%2Ecache_date", "/sub/", "/sub", "/..%2fportfolio%2fwatchlist.csv",
                     "/app-assets/../src/dashboard_app.py", "/app-assets/manage.py", "/nope.html"):
            status, body, _ = self.req("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn("EQTY", str(body))

    def test_app_assets(self):
        status, body, headers = self.req("GET", "/app-assets/manage.js")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/javascript"))
        self.assertIn("app-on", body)
        self.assertEqual(self.req("GET", "/app-assets/manage.css")[0], 200)

    def test_changes_need_our_token_json_and_origin(self):
        add = {"symbol": "EQTY", "market": "NSE"}
        self.assertEqual(self.req("POST", "/api/watchlist/add", add, headers={"X-Dashboard-Token": "wrong"})[0], 403)
        self.assertEqual(self.req("POST", "/api/watchlist/add", add, headers={"X-Dashboard-Token": None})[0], 403)
        self.assertEqual(self.req("POST", "/api/watchlist/add", add, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/watchlist/add", add, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.req("POST", "/api/watchlist/add", raw=b"x" * (dashboard_app.MAX_BODY + 1))[0], 413)
        self.assertEqual(self.req("POST", "/api/watchlist/add", raw=b"{not json")[0], 400)
        self.assertEqual(self.req("POST", "/api/watchlist/add", raw=b"[1, 2]")[0], 400)
        self.assertEqual(self.req("POST", "/index.html", add)[0], 405)
        self.assertEqual(self.req("GET", "/api/watchlist/add")[0], 405)
        self.assertEqual(self.req("GET", "/api/nope")[0], 404)
        self.assertFalse(os.path.exists(os.path.join(self.cfg.portfolio_dir, "watchlist.csv")))
        self.assertEqual(self.jobs.requests, [])

    def test_watchlist_add_update_remove(self):
        status, body, _ = self.req("POST", "/api/watchlist/add",
                                   {"symbol": "eqty", "market": "NSE", "buy_below": "40", "note": "hi"})
        self.assertEqual(status, 200, body)
        self.assertIn("EQTY", body["message"])
        today = _dt.date.today().isoformat()
        self.assertEqual(self.watchlist_text(), "symbol,market,buy_below,sell_above,note,added,added_price\n"
                                                f"EQTY,NSE,40,,hi,{today},45\n")
        self.assertEqual(self.req("POST", "/api/watchlist/add", {"symbol": "EQTY", "market": "NSE"})[0], 409)
        self.assertEqual(self.req("GET", "/api/watchlist")[1]["entries"][0]["buy_below"], 40.0)
        status, body, _ = self.req("POST", "/api/watchlist/update",
                                   {"symbol": "EQTY", "market": "NSE", "sell_above": "60", "note": ""})
        self.assertEqual(status, 200, body)
        self.assertEqual((body["entry"]["buy_below"], body["entry"]["sell_above"]), (40.0, 60.0))
        self.assertEqual(self.req("POST", "/api/watchlist/update",
                                  {"symbol": "EQTY", "market": "NSE", "buy_below": "70"})[0], 400)
        status, body, _ = self.req("POST", "/api/watchlist/remove", {"symbol": "EQTY", "market": "NSE"})
        self.assertEqual(status, 200)
        self.assertEqual(body["removed"]["sell_above"], 60.0)
        self.assertEqual(self.req("POST", "/api/watchlist/remove", {"symbol": "EQTY", "market": "NSE"})[0], 404)
        # Undo puts it back exactly, with its original date and price
        undo = dict(body["removed"], undo=True)
        self.assertEqual(self.req("POST", "/api/watchlist/add", undo)[0], 200)
        self.assertIn(f"EQTY,NSE,40,60,,{today},45", self.watchlist_text())
        self.assertEqual([k for k, _ in self.jobs.requests], ["watchlist"] * 4)

    def test_watchlist_add_unknown_ticker_and_offline(self):
        status, body, _ = self.req("POST", "/api/watchlist/add", {"symbol": "EQTX", "market": "NSE"})
        self.assertEqual(status, 400)
        self.assertEqual(body["suggestions"][0]["symbol"], "EQTY")
        self.assertEqual(self.req("POST", "/api/watchlist/add", {"symbol": "^GSPC", "market": "INTL"})[0], 400)
        self.assertEqual(self.req("POST", "/api/watchlist/add", {"symbol": "AAPL"})[0], 400)    # market missing
        self.lookup.offline = True
        status, body, _ = self.req("POST", "/api/watchlist/add", {"symbol": "AAPL", "market": "INTL"})
        self.assertEqual(status, 503)
        self.assertTrue(body["offline"])
        status, body, _ = self.req("POST", "/api/watchlist/add", {"symbol": "AAPL", "market": "INTL", "force": True})
        self.assertEqual(status, 200)
        self.assertIn("once the connection is back", body["message"])

    def test_record_a_purchase_review_then_save(self):
        p = {"kind": "nse", "symbol": "EQTY", "quantity": "1,000", "buy_price": "44.5",
             "buy_date": "2026-09-20", "note": "test", "dry_run": True}
        status, body, _ = self.req("POST", "/api/holdings/add", p)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["summary"], "1,000 shares of EQTY (EQTY Inc) at KES 44.50 each = KES 44,500.00, bought 2026-09-20")
        self.assertEqual(body["warnings"], [])
        self.assertFalse(os.path.exists(os.path.join(self.cfg.portfolio_dir, "holdings.csv")))   # review saves nothing
        p["dry_run"] = False
        self.assertEqual(self.req("POST", "/api/holdings/add", p)[0], 200)
        with open(os.path.join(self.cfg.portfolio_dir, "holdings.csv")) as f:
            self.assertEqual(f.read(), "symbol,quantity,buy_price,buy_date,note\nEQTY,1000,44.5,2026-09-20,test\n")
        self.assertEqual(self.jobs.requests, [("full", dashboard_app.FULL_UPDATE_DELAY)])
        entries = self.req("GET", "/api/holdings?kind=nse")[1]
        self.assertEqual((entries["source"], entries["entries"][0]["line"]), ("csv", 2))

    def test_purchase_checks(self):
        def add(**k):
            base = {"kind": "nse", "symbol": "EQTY", "quantity": "100", "buy_price": "45", "dry_run": True}
            base.update(k)
            return self.req("POST", "/api/holdings/add", base)
        future = (_dt.date.today() + _dt.timedelta(days=3)).isoformat()
        for bad in ({"quantity": "0"}, {"quantity": "abc"}, {"buy_price": "-2"}, {"buy_price": "224,3"},
                    {"buy_date": future}, {"buy_date": "1985-01-01"}, {"buy_date": "05/06/2026"},
                    {"symbol": "EQTX"}, {"kind": "crypto"}, {"note": "x" * 400}):
            self.assertEqual(add(**bad)[0], 400, bad)
        warns = add(buy_price="450", quantity="10.5")[1]["warnings"]
        self.assertTrue(any("trades at KES 45.00 today" in w for w in warns))
        self.assertTrue(any("whole numbers" in w for w in warns))
        status, body, _ = add(kind="intl", symbol="VOD.L", buy_price="120")
        self.assertEqual(status, 400)
        self.assertIn("priced in GBp", body["error"])
        self.lookup.offline = True
        self.assertEqual(add()[0], 503)
        self.assertEqual(add(undo=True)[0], 200)     # undo re-adds without needing the network

    def test_bond_purchase(self):
        issue = next(i["issue"] for i in self.req("GET", "/api/bonds/issues")[1]["issues"])
        status, body, _ = self.req("POST", "/api/holdings/add", {"kind": "bonds", "issue": issue.lower(),
                                                                  "face_value": "100000", "purchase_price_pct": ""})
        self.assertEqual(status, 200, body)
        self.assertIn(f"KES 100,000 face value of {issue} at 100% of face", body["summary"])
        self.assertEqual(self.req("POST", "/api/holdings/add", {"kind": "bonds", "issue": "XYZ/2099/1",
                                                                 "face_value": "100000"})[0], 400)
        warn = self.req("POST", "/api/holdings/add", {"kind": "bonds", "issue": issue, "face_value": "1000",
                                                      "purchase_price_pct": "1013.3", "dry_run": True})[1]["warnings"]
        self.assertEqual(len(warn), 2)

    def test_remove_an_entry_only_if_it_is_still_what_the_page_showed(self):
        path = os.path.join(self.cfg.portfolio_dir, "holdings.csv")
        with open(path, "w") as f:
            f.write("symbol,quantity,buy_price,buy_date,note\nEQTY,100,45,,\nEABL,5,250,,\n")
        stale = {"kind": "nse", "line": 2, "expect": {"symbol": "EQTY", "quantity": 999, "buy_price": 45}}
        self.assertEqual(self.req("POST", "/api/holdings/remove", stale)[0], 409)
        self.assertEqual(self.req("POST", "/api/holdings/remove", {"kind": "nse", "line": 2, "expect": {}})[0], 400)
        ok = {"kind": "nse", "line": 2, "expect": {"symbol": "EQTY", "quantity": 100, "buy_price": 45,
                                                    "buy_date": None, "note": None, "line": 2}}
        status, body, _ = self.req("POST", "/api/holdings/remove", ok)
        self.assertEqual(status, 200, body)
        with open(path) as f:
            self.assertEqual(f.read(), "symbol,quantity,buy_price,buy_date,note\nEABL,5,250,,\n")
        self.assertEqual(len(os.listdir(os.path.join(self.cfg.portfolio_dir, "backups"))), 1)

    def test_older_json_portfolio_is_listed_but_not_edited_here(self):
        with open(os.path.join(self.cfg.portfolio_dir, "holdings.json"), "w") as f:
            json.dump({"holdings": [{"symbol": "EQTY", "quantity": 1, "buy_price": 2}]}, f)
        body = self.req("GET", "/api/holdings?kind=nse")[1]
        self.assertEqual((body["source"], body["editable"], body["entries"][0]["symbol"]), ("json", False, "EQTY"))
        status, body, _ = self.req("POST", "/api/holdings/remove", {"kind": "nse", "line": 2, "expect": {"symbol": "EQTY"}})
        self.assertEqual(status, 400)
        self.assertIn("holdings.json", body["error"])

    def test_missing_page_while_updating_shows_the_preparing_page(self):
        status, body, _ = self.req("GET", "/watchlist.html")
        self.assertEqual(status, 200)
        self.assertIn("This page hasn't been built yet", body)
        os.remove(os.path.join(self.cfg.report_directory, "index.html"))
        status, body, _ = self.req("GET", "/portfolio.html")
        self.assertIn("Preparing your dashboard", body)
        self.assertEqual(self.jobs.requests, [("full", 0)])     # nothing there at all: build it

    def test_refresh_endpoint(self):
        self.assertEqual(self.req("POST", "/api/refresh", {"kind": "full"})[0], 200)
        self.assertEqual(self.req("POST", "/api/refresh", {"kind": "rm -rf"})[0], 400)
        self.assertEqual(self.jobs.requests, [("full", 0)])


class TestUpdateJobs(unittest.TestCase):
    """The background runner, with a fake pipeline (a tiny Python script)."""

    SCRIPT = (
        "import os, sys, time\n"
        "mode = sys.argv[1]\n"
        "out = os.environ.get('REPORT_DIRECTORY', '')\n"
        "print('KENYAN STOCK ANALYZER', flush=True)\n"
        "print('Fetched 2 stocks', flush=True)\n"
        "print('Generating individual stock reports...', flush=True)\n"
        "print('Saved HTML: x', flush=True)\n"
        "if mode == 'slow': time.sleep(3)\n"
        "if mode == 'fail': sys.exit(3)\n"
        "if mode in ('ok', 'slow'):\n"
        "    for name in ('index.html', 'EQTY_report_new.html', 'earnings.ics'):\n"
        "        open(os.path.join(out, name), 'w').write('new ' + name)\n"
        "print('Done!', flush=True)\n"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = _config(self._tmp.name)
        self.mode = "ok"
        self.ran = []
        for name, text in (("index.html", "old"), ("EQTY_report_old.html", "old"), ("notes.xlsx", "keep")):
            with open(os.path.join(self.cfg.report_directory, name), "w") as f:
                f.write(text)

    def make(self, **k):
        def factory(kind):
            self.ran.append(kind)
            return [sys.executable, "-c", self.SCRIPT, self.mode if kind == "full" else "wl"]
        jobs = dashboard_app.UpdateJobs(self.cfg, command_factory=factory, **k)
        jobs.start()
        self.addCleanup(jobs.stop)
        return jobs

    def files(self):
        return sorted(os.listdir(self.cfg.report_directory))

    def test_a_full_update_is_swapped_in_only_when_it_finished(self):
        jobs = self.make()
        jobs.request("full")
        self.assertTrue(jobs.wait_idle(20))
        s = jobs.status()
        self.assertTrue(s["last"]["ok"], s)
        self.assertEqual(s["generation"], 1)
        self.assertEqual(self.files(), ["EQTY_report_new.html", "earnings.ics", "index.html", "notes.xlsx"])
        with open(os.path.join(self.cfg.report_directory, "index.html")) as f:
            self.assertEqual(f.read(), "new index.html")
        self.assertFalse(os.path.exists(jobs.staging))
        self.assertTrue(os.path.exists(os.path.join(self.cfg.cache_dir, "..", "logs", "last_update.log")))

    def test_a_failed_update_changes_nothing(self):
        self.mode = "fail"
        jobs = self.make()
        jobs.request("full")
        self.assertTrue(jobs.wait_idle(20))
        s = jobs.status()
        self.assertFalse(s["last"]["ok"])
        self.assertIn("wasn't changed", s["last"]["message"])
        self.assertIn("Saved HTML: x", s["last"]["log_tail"])      # the page can show what went wrong
        self.assertEqual(s["generation"], 0)
        self.assertEqual(self.files(), ["EQTY_report_old.html", "index.html", "notes.xlsx"])

    def test_an_update_that_never_writes_the_dashboard_is_not_published(self):
        self.mode = "empty"
        jobs = self.make()
        jobs.request("full")
        self.assertTrue(jobs.wait_idle(20))
        self.assertFalse(jobs.status()["last"]["ok"])
        self.assertEqual(self.files(), ["EQTY_report_old.html", "index.html", "notes.xlsx"])

    def test_timeouts_and_progress(self):
        self.mode = "slow"
        jobs = self.make(timeouts={"full": 1, "watchlist": 1})
        jobs.request("full")
        seen = 0
        for _ in range(100):
            job = jobs.status()["job"]
            if job:
                seen = max(seen, job["progress"])
            time.sleep(0.02)
        self.assertGreaterEqual(seen, 55)          # parsed "Generating individual stock reports"
        self.assertTrue(jobs.wait_idle(20))
        self.assertIn("took too long", jobs.status()["last"]["message"])
        self.assertEqual(self.files(), ["EQTY_report_old.html", "index.html", "notes.xlsx"])

    def test_requests_are_coalesced(self):
        self.mode = "slow"
        jobs = self.make()
        jobs.request("watchlist", delay=0.3)
        jobs.request("watchlist")
        jobs.request("full")
        jobs.request("full")
        self.assertTrue(jobs.wait_idle(30))
        self.assertEqual(self.ran, ["full"])       # the full update covers the watchlist one


import time  # noqa: E402  (used by TestUpdateJobs)


_BOND_COUNT_WORDS = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|"
                     r"eleven|twelve|\d+)")
# Sentences that describe a specific person's holdings ("Three of your four
# bonds…", "written for exactly the four bonds you hold"). These must only
# ever be generated from the private portfolio files at runtime.
_HOLDING_SENTENCE = re.compile(
    rf"\b{_BOND_COUNT_WORDS}\s+of\s+your\s+{_BOND_COUNT_WORDS}\s+(?:bonds|stocks|holdings)\b"
    rf"|\bexactly\s+the\s+{_BOND_COUNT_WORDS}\s+(?:bonds?|stocks?|holdings?)\s+you\s+(?:hold|own)\b",
    re.IGNORECASE)
# Python joins adjacent string literals ('… four '\n    'bonds …'), so a
# sentence in source code is usually split across lines and quotes.
_LITERAL_JOIN = re.compile(r"""['"]\s*\n\s*(?:[rRfFbBuU]{1,2})?['"]""")


def _holding_sentences(text):
    """Line numbers of holding-specific sentences in `text`, with split
    string literals joined back together first."""
    pieces, segments, pos, flat_len = [], [], 0, 0   # segments: (start in flat, start in text)
    for m in list(_LITERAL_JOIN.finditer(text)) + [None]:
        end = m.start() if m else len(text)
        segments.append((flat_len, pos))
        pieces.append(text[pos:end])
        flat_len += end - pos
        pos = m.end() if m else pos
    flat = "".join(pieces)

    def line_of(i):
        flat_start, text_start = max(s for s in segments if s[0] <= i)
        return text.count("\n", 0, text_start + (i - flat_start)) + 1
    return [line_of(m.start()) for m in _HOLDING_SENTENCE.finditer(flat)]


def _tracked_text_files(root):
    """(relative path, text) for every git-tracked text file, or None when git
    isn't available."""
    import subprocess
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True,
                             check=True).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError):
        return None
    files = []
    for rel in filter(None, out.split("\0")):
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                files.append((rel, f.read()))
        except (UnicodeDecodeError, OSError):
            continue                      # binary or unreadable — not source text
    return files


def _bond_pairs_in(files, lots, window=40):
    """Lines (as "path:line") where one of `lots`' issues and that same
    lot's face value appear within `window` lines of each other. Returns
    locations only — never the values — so a failure can be shown safely."""
    variants = {}
    for lot in lots:
        canon = lot["issue"]
        names = {canon} | {a for a, c in bonds_portfolio._ALIASES.items() if c == canon}
        face = float(lot["face_value"])
        amounts = {f"{face:,.0f}", f"{face:.0f}"}
        variants.setdefault(canon, (names, set()))[1].update(amounts)
    hits = []
    for rel, text in files:
        lines = text.splitlines()
        for names, amounts in variants.values():
            name_lines = [i for i, ln in enumerate(lines) if any(n in ln for n in names)]
            if not name_lines:
                continue
            amount_re = re.compile(r"(?<![\d,.])(?:" + "|".join(re.escape(a) for a in amounts)
                                   + r")(?![\d,])")
            for i, ln in enumerate(lines):
                if amount_re.search(ln) and any(abs(i - j) <= window for j in name_lines):
                    hits.append(f"{rel}:{i + 1}")
    return sorted(set(hits))


class TestBondsExplainerPrivacy(unittest.TestCase):
    """The "📘 Understanding Your Bonds" explainer is worked out from the
    bonds being shown. Nothing about a real holding may live in the source —
    the repository is public."""

    AS_OF = "2026-01-15"
    ROOT = os.path.dirname(os.path.abspath(__file__))

    def setUp(self):
        self.enterContext(fake_bonds.use())

    def explainer(self, lots):
        from report_generator import ReportGenerator
        pf = bonds_portfolio.compute_bond_portfolio(lots, as_of=self.AS_OF)
        return ReportGenerator._bonds_explainer_facts(
            [b for b in pf["bonds"] if b["data_available"]])

    @staticmethod
    def lot(issue, face):
        return {"issue": issue, "face_value": face, "purchase_price_pct": 100.0,
                "purchase_date": "2024-03-01", "note": ""}

    def test_the_explainer_follows_the_portfolio_it_is_given(self):
        a = self.explainer([self.lot("IFB9/2020/15", 120000.0), self.lot("FXD9/2020/020", 340000.0)])
        b = self.explainer([self.lot("IFB9/2021/7", 760000.0)])

        self.assertIn("exactly the two bonds you hold", a)
        self.assertIn("One of your two bonds is an IFB.", a)
        self.assertIn("Your FXD9/2020/020 is a standard taxable Treasury bond.", a)
        self.assertIn("FXD9/2020/020 10%", a)
        self.assertIn("full KES 340,000 back only at maturity in 2040", a)
        self.assertIn("the single biggest finding here", a)        # the small-holder rule applies…
        self.assertIn("Your holding (KES 120,000) is well under that threshold", a)

        self.assertIn("exactly the one bond you hold", b)
        self.assertIn("Your bond is an IFB.", b)
        self.assertIn("None of your bonds is taxed.", b)
        self.assertIn("Your IFB9/2021/7 repays principal in tranches", b)
        self.assertIn("None of your current bonds qualifies", b)   # …and here it doesn't
        for from_a in ("FXD9/2020/020", "340,000", "120,000", "two bonds", "biggest finding"):
            self.assertNotIn(from_a, b)

    def test_several_small_holder_bonds_read_naturally(self):
        out = self.explainer([self.lot("IFB9/2020/15", 70000.0), self.lot("IFB9/2022/11", 70000.0)])
        self.assertIn("Both of your bonds are IFBs.", out)
        self.assertIn("both state", out)
        self.assertIn("(KES 70,000 each)", out)
        self.assertIn("None of your bonds is taxed.", out)

    def test_names_from_the_file_are_escaped(self):
        from report_generator import ReportGenerator
        out = ReportGenerator._bonds_explainer_facts([{
            "issue": "<b>X</b>", "type": "FXD", "tax_free": False, "withholding_pct": 10.0,
            "small_holder_accelerated": False, "redemption_structure": [{}],
            "face_value": 1000.0, "legal_maturity_date": "2040-01-01"}])
        self.assertNotIn("<b>X</b>", out)
        self.assertIn("&lt;b&gt;X&lt;/b&gt;", out)

    def test_no_tracked_file_describes_specific_holdings(self):
        files = _tracked_text_files(self.ROOT)
        if files is None:
            self.skipTest("git not available")
        bad = [f"{rel}:{line}"
               for rel, text in files if not os.path.basename(rel).startswith("test")
               for line in _holding_sentences(text)]
        self.assertEqual(bad, [], "holding-specific sentences must be generated at runtime")

    def test_your_real_bonds_are_not_in_any_tracked_file(self):
        # LOCAL ONLY. Reads your private portfolio/bonds.csv — which is never
        # committed — and checks no tracked file pairs one of your issues with
        # its amount. Skipped wherever that file doesn't exist (e.g. CI).
        # A failure names files and lines only, never the values.
        path = os.path.join(self.ROOT, "portfolio", "bonds.csv")
        if not os.path.exists(path):
            self.skipTest("no private portfolio/bonds.csv on this machine")
        files = _tracked_text_files(self.ROOT)
        if files is None:
            self.skipTest("git not available")
        lots = bonds_portfolio._load_bonds_csv(path)
        self.assertEqual(_bond_pairs_in(files, lots), [],
                         "a tracked file pairs one of your bond issues with its amount")


_HOSTILE = '<img src=x onerror="alert(1)">'


class TestThirdPartyTextIsEscaped(_NoLogos, unittest.TestCase):
    """Headlines, company names/summaries and links come from Yahoo, Google
    News and TradingView. Rendered raw, a booby-trapped headline could run
    script on a page served by the local app — which can read the app's
    write token. Every such field must come out as text."""

    def setUp(self):
        super().setUp()
        from report_generator import ReportGenerator
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.rg = ReportGenerator(output_dir=self._tmp.name, cache_dir=self._tmp.name)
        # A template error must fail the test, not quietly become the
        # fallback page (which would pass these checks for the wrong reason).
        self._real_fallback = ReportGenerator._fallback_html
        p = mock.patch.object(ReportGenerator, "_fallback_html",
                              side_effect=AssertionError("template failed to render"))
        p.start()
        self.addCleanup(p.stop)

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def assertNoRawMarkup(self, html):
        self.assertNotIn(_HOSTILE, html)
        self.assertNotIn("<script>alert", html)
        self.assertNotRegex(html, r'href="\s*javascript:')

    def test_international_stock_page(self):
        fundamentals = {"currency": "USD", "name": "Evil " + _HOSTILE, "sector": _HOSTILE,
                        "industry": "<script>alert(2)</script>", "summary": "About us " + _HOSTILE}
        news = [{"title": "Shares soar " + _HOSTILE, "url": "javascript:alert(3)",
                 "source": "<script>alert(4)</script>", "published_utc": "2026-10-01T09:00:00Z"},
                {"title": "Plain headline", "url": "https://example.com/a?b=1&c=2",
                 "source": "Wire", "published_utc": "2026-10-02T09:00:00Z"}]
        page = self.read(self.rg.generate_international_stock_report(
            "AAPL", _analysed(seed=4), fundamentals=fundamentals, news=news))
        self.assertNoRawMarkup(page)
        self.assertIn("Shares soar &lt;img", page)                 # the headline is kept, as text
        self.assertIn('href="https://example.com/a?b=1&amp;c=2"', page)
        self.assertIn("About us &lt;img", page)

    def test_nse_stock_page(self):
        page = self.read(self.rg.generate_stock_report(
            "ABSA", _analysed(seed=5), fundamentals={"sector": _HOSTILE, "name": 'A "B" ' + _HOSTILE}))
        self.assertNoRawMarkup(page)
        self.assertIn('data-name="A &#34;B&#34; &lt;img', page)

    def test_holding_news_on_the_portfolio_page(self):
        import page_portfolio
        card = str(page_portfolio._news_list([{"symbol": "ABSA", "title": _HOSTILE, "url": "javascript:alert(1)",
                                               "source": _HOSTILE, "published_utc": _HOSTILE}]))
        self.assertNoRawMarkup(card)
        self.assertNotIn("<a ", card)                               # bad link -> no link at all
        ok = str(page_portfolio._news_list([{"symbol": "ABSA", "title": "Fine", "url": "https://x.example/1"}]))
        self.assertIn('<a href="https://x.example/1"', ok)

    def test_hover_tips_stay_text_after_the_browser_decodes_them(self):
        from html import unescape          # what getAttribute('data-tip') hands to innerHTML
        tip = self.rg._stock_tip({"symbol": "ABSA" + _HOSTILE, "sector": _HOSTILE, "price": 10.0,
                                  "change": 1.0, "signal_label": _HOSTILE,
                                  "score_detail": {"overall": 50, "reasons": {"value": [_HOSTILE]}}})
        self.assertNotIn("<img", unescape(tip))
        self.assertIn("&lt;img", unescape(tip))

    def test_a_failed_template_does_not_echo_raw_data(self):
        page = self._real_fallback(self.rg, "x.html", {"news": [{"title": _HOSTILE}]})
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img", page)

    def test_only_web_links_survive_the_international_news_fetch(self):
        import international_data
        items = [{"symbol": "AAPL", "title": "a", "url": "javascript:alert(1)", "source": "", "published_utc": ""},
                 {"symbol": "AAPL", "title": "b", "url": " HTTPS://ok.example/x", "source": "", "published_utc": ""}]
        with mock.patch.object(international_data, "_from_yfinance_news", return_value=items), \
                mock.patch.object(international_data, "_from_google_news_rss", return_value=[]):
            got = international_data.fetch_news("AAPL")
        self.assertEqual([n["title"] for n in got], ["b"])


def run_tests():
    """Run all tests and print results."""
    print("=" * 60)
    print("KENYAN STOCK ANALYZER — TEST SUITE")
    print("=" * 60)
    print()

    # Every TestCase in this file, plus any test_*.py module beside it, runs
    # automatically — a hand-written list used to skip new classes silently.
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    here = os.path.dirname(os.path.abspath(__file__))
    suite.addTests(loader.discover(here, pattern="test_*.py", top_level_dir=here))

    # Run
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    print("=" * 60)
    if result.wasSuccessful():
        print("ALL TESTS PASSED!")
    else:
        print(f"FAILURES: {len(result.failures)}, ERRORS: {len(result.errors)}")
    print("=" * 60)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())