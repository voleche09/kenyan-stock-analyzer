#!/usr/bin/env python3
"""
Daily summary emailer.

Runs a lean version of the pipeline (data → analysis → fundamentals →
price validation → scoring), builds a compact one-page PDF summary of key
metrics, and emails it as an attachment. Intended to run unattended on a
schedule (e.g. GitHub Actions at 22:00 EAT), independent of any laptop.

Does NOT generate the full dashboard or per-stock reports — it is a subset.
Run manually to test:  python send_summary.py
"""

import os
import sys
from datetime import datetime

# Ensure Homebrew libs are findable for WeasyPrint on macOS (no-op on Linux)
if sys.platform == 'darwin':
    hb = '/opt/homebrew/lib'
    if os.path.isdir(hb):
        os.environ['DYLD_LIBRARY_PATH'] = f"{hb}:{os.environ.get('DYLD_LIBRARY_PATH', '')}"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

from dotenv import load_dotenv
load_dotenv()

from config import Config
from logger import get_logger, setup_logging
from data_acquisition import DataAcquisition
from analysis_engine import AnalysisEngine
from fundamental_analysis import FundamentalAnalysis
from report_generator import ReportGenerator
from sector_analysis import SectorAnalyzer

config = Config()
setup_logging(config)
logger = get_logger(__name__)


def market_closed_today():
    """
    Return (closed: bool, reason: str|None) for the NSE today, evaluated in
    Nairobi time. The NSE does not trade on weekends or Kenyan public
    holidays (New Year, Easter, Labour Day, Madaraka, Mashujaa, Jamhuri,
    Christmas, Boxing Day, Eid, etc.).
    """
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Africa/Nairobi")).date()
    except Exception:
        today = datetime.now().date()

    if today.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return True, "weekend"

    try:
        import holidays
        ke = holidays.Kenya(years=today.year)
        if today in ke:
            return True, ke.get(today)
    except Exception as e:
        # If the holiday check is unavailable, don't block the report.
        logger.warning(f"Holiday check unavailable ({e}); proceeding anyway.")

    return False, None


def main():
    logger.info("=" * 60)
    logger.info(f"NSE DAILY SUMMARY EMAIL — {datetime.now():%Y-%m-%d %H:%M}")
    logger.info("=" * 60)

    # Skip weekends and Kenyan public holidays (NSE is closed — no new data).
    # Pass --ignore-calendar to force a run anyway (e.g. manual testing).
    if '--ignore-calendar' not in sys.argv:
        closed, reason = market_closed_today()
        if closed:
            logger.info(f"NSE is closed today ({reason}) — skipping summary email.")
            print(f"Skipped: NSE closed today ({reason}). No email sent.")
            return

    force = '--force-refresh' in sys.argv

    data_acq = DataAcquisition(data_sources=config.data_sources, cache_dir=config.cache_dir)
    engine = AnalysisEngine(config=config)
    report_gen = ReportGenerator(template_dir=config.template_dir, output_dir=config.report_directory)

    # ---- Data + analysis ----
    logger.info("Fetching stock data...")
    stock_data = data_acq.fetch_all_stocks(period='6mo', interval='1d', force_refresh=force)
    if not stock_data:
        logger.error("No stock data — aborting summary")
        sys.exit(1)
    analysis_results = engine.analyze_multiple_stocks(stock_data)
    breadth = engine.calculate_market_breadth(analysis_results)
    sector_data = SectorAnalyzer().analyze_sectors(stock_data, analysis_results)

    # ---- Fundamentals ----
    fund_analyzer = FundamentalAnalysis(cache_dir=config.cache_dir)
    fundamentals_data = fund_analyzer.fetch_all_fundamentals(force_refresh=force)

    # ---- Price validation ----
    validations = {}
    if config.enable_price_validation:
        try:
            from price_validation import PriceValidator
            pv = PriceValidator(cache_dir=config.cache_dir,
                                disagree_threshold_pct=config.price_disagree_threshold_pct)
            pv.fetch_reference_prices()
            for sym, r in analysis_results.items():
                if r:
                    validations[sym] = pv.validate(sym, r.get('latest', {}).get('close'), stock_data.get(sym))
        except Exception as e:
            logger.warning(f"Price validation skipped: {e}")

    # ---- Context, scoring, alerts ----
    usd_kes = None
    try:
        from market_context import fetch_usd_kes
        if config.enable_fx:
            usd_kes = fetch_usd_kes()
    except Exception as e:
        logger.warning(f"FX skipped: {e}")

    scores, alerts = {}, {}
    try:
        from scoring import score_stock, generate_alerts
        for sym, r in analysis_results.items():
            if r:
                f = fundamentals_data.get(sym, {})
                scores[sym] = score_stock(sym, r, f)
                a = generate_alerts(sym, r, f, validations.get(sym))
                if a:
                    alerts[sym] = a
    except Exception as e:
        logger.warning(f"Scoring skipped: {e}")

    # ---- Build the summary PDF ----
    logger.info("Building summary PDF...")
    result = report_gen.generate_summary(
        analysis_results, fundamentals_data=fundamentals_data, validations=validations,
        scores=scores, alerts=alerts, breadth=breadth, sector_data=sector_data,
        usd_kes=usd_kes, watchlist=config.stock_symbols, report_type='both',
    )
    # result is (html, pdf) for report_type='both', or a single path
    pdf_path = None
    if isinstance(result, tuple):
        for p in result:
            if p and p.endswith('.pdf'):
                pdf_path = p
    elif isinstance(result, str) and result.endswith('.pdf'):
        pdf_path = result

    # ---- Email ----
    if not config.enable_email_notifications:
        logger.warning("ENABLE_EMAIL_NOTIFICATIONS is false — summary built but not emailed.")
        print(f"Summary built: {result}")
        return

    from email_notifier import EmailNotifier
    notifier = EmailNotifier(config)
    body = notifier.generate_email_body(analysis_results, sector_data, breadth)
    attachments = [pdf_path] if pdf_path else []
    subject = f"NSE Daily Summary — {datetime.now():%Y-%m-%d}"
    ok = notifier.send_report(subject, body, attachments=attachments)
    if ok:
        logger.info(f"Summary emailed (attachment: {pdf_path})")
        print("Summary emailed successfully.")
    else:
        logger.error("Failed to email summary")
        sys.exit(1)


if __name__ == "__main__":
    main()
