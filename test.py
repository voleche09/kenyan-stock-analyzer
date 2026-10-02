#!/usr/bin/env python3
"""
Test suite for the Kenyan Stock Analyzer.

Tests data acquisition, analysis engine, report generation,
sector analysis, email notifier, and config loading.
"""

import sys
import os
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

    def test_add_scripts_refuse_to_write_to_json_when_a_csv_is_the_portfolio(self):
        cases = [("holdings.csv", portfolio_mod.add_lot, ("A", 1, 2)),
                 ("international_holdings.csv", international_portfolio.add_lot, ("A", 1, 2)),
                 ("bonds.csv", bonds_portfolio.add_bond, ("IFB1/2023/6.5", 100000))]
        for csv_name, fn, args in cases:
            path = self.write(csv_name, "header\n")
            with self.assertRaises(ValueError, msg=csv_name) as ctx:
                fn(self.dir, *args)
            self.assertIn(csv_name, str(ctx.exception))
            self.assertEqual([f for f in os.listdir(self.dir) if f.endswith(".json")], [])
            os.remove(path)

    def test_add_lot_still_creates_json_when_no_csv_exists(self):
        portfolio_mod.add_lot(self.dir, "a", 3, 4.5, "2026-01-02", "x")
        self.assertEqual(portfolio_mod.load_holdings(self.dir),
                         [{"symbol": "A", "quantity": 3.0, "buy_price": 4.5,
                           "buy_date": "2026-01-02", "note": "x"}])

    # ---- bonds ----

    def test_bonds_csv_aliases_defaults_and_downstream_compatibility(self):
        self.write("bonds.csv",
                   'Bond,Face Value,Purchase Price Pct,Purchase Date,Note\n'
                   'FDX1/2022/025,"250,000",100,2022-09-23,typo alias\n'   # FDX -> FXD
                   'IFB1/2023/6.5,150000,,,\n')                             # blank -> par
        bonds = bonds_portfolio.load_bonds(self.dir)
        self.assertEqual([b["issue"] for b in bonds], ["FXD1/2022/025", "IFB1/2023/6.5"])
        self.assertEqual(bonds[0]["face_value"], 250000.0)
        self.assertEqual(bonds[1]["purchase_price_pct"], 100.0)
        self.assertIsNone(bonds[1]["purchase_date"])
        summary = bonds_portfolio.compute_bond_portfolio(bonds)
        self.assertEqual(summary["totals"]["n_available"], 2)

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


def run_tests():
    """Run all tests and print results."""
    print("=" * 60)
    print("KENYAN STOCK ANALYZER — TEST SUITE")
    print("=" * 60)
    print()

    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestUtils))
    suite.addTests(loader.loadTestsFromTestCase(TestAnalysisEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestSectorAnalysis))
    suite.addTests(loader.loadTestsFromTestCase(TestReportGenerator))
    suite.addTests(loader.loadTestsFromTestCase(TestEmailNotifier))
    suite.addTests(loader.loadTestsFromTestCase(TestPortfolioCsv))

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