"""
Enhanced report generator for the Kenyan Stock Analyzer.

Generates:
  - The dashboard pages (laid out by page_overview, page_portfolio,
    page_market and page_stock inside the shared ui_theme shell)
  - Per-stock HTML reports with charts and signals
  - Market summary HTML/PDF with sector performance and breadth
  - Excel export with multi-sheet workbook

Charts are inline SVG (svg_charts.py) — no images.
"""

import os
import sys
import json
from datetime import datetime
from html import escape
import logging

# Fix WeasyPrint on macOS
if sys.platform == 'darwin':
    homebrew_lib = '/opt/homebrew/lib'
    if os.path.isdir(homebrew_lib):
        existing = os.environ.get('DYLD_LIBRARY_PATH', '')
        os.environ['DYLD_LIBRARY_PATH'] = (
            f"{homebrew_lib}:{existing}" if existing else homebrew_lib
        )

from jinja2 import Environment, FileSystemLoader, make_logging_undefined
from fundamental_analysis import FundamentalAnalysis

logger = logging.getLogger(__name__)


def _safe_href(url):
    """An http(s) link escaped for an href="" attribute, or '' for anything
    else (javascript:, data:, relative paths…). Headlines and their links
    come from third parties, so they are never trusted as-is."""
    url = str(url or '').strip()
    if not url.lower().startswith(('http://', 'https://')):
        return ''
    return escape(url, quote=True)


def _tip_text(value):
    """Escape a plain-text value placed inside hover-tip HTML. The finished
    tip is attribute-escaped and the page inserts it with innerHTML, so text
    has to be escaped here as well to stay text."""
    return escape('' if value is None else str(value), quote=False)


# ---- WeasyPrint (graceful) ----
try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except (OSError, ImportError) as e:
    WEASYPRINT_AVAILABLE = False
    logger.warning(
        f"WeasyPrint not available — PDF generation disabled. "
        f"Install system deps: brew install pango glib. Error: {e}"
    )

# ---- openpyxl (graceful) ----
try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False


class ReportGenerator:
    """Generates HTML/PDF reports and Excel exports."""

    def __init__(self, template_dir=None, output_dir=None, clean_old=True, cache_dir='data'):
        if template_dir is None:
            template_dir = os.path.join(
                os.path.dirname(__file__), '..', 'templates'
            )
        if output_dir is None:
            output_dir = os.path.join(
                os.path.dirname(__file__), '..', 'reports'
            )

        self.template_dir = os.path.abspath(template_dir)
        self.output_dir = os.path.abspath(output_dir)

        os.makedirs(self.template_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

        # Clean old reports from previous runs (keep directory, just clear
        # files). Skipped when clean_old=False so a summary-only run does not
        # wipe an existing dashboard sitting in the same folder.
        if clean_old:
            self._clean_old_reports()

        # A template using a value it wasn't given renders as before (empty)
        # but logs a warning, so the tests and fact check can catch it.
        self.env = Environment(loader=FileSystemLoader(self.template_dir),
                               undefined=make_logging_undefined(logger))
        # Third-party links (news) go through this: http(s) only, escaped.
        self.env.filters['safe_url'] = _safe_href
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.cache_dir = cache_dir
        # Memoizes _ticker_logo_html() per symbol for this run — a symbol
        # appearing in several tables (main dashboard, portfolio, dividends)
        # is only resolved/fetched once.
        self._logo_html_cache = {}

        logger.info(
            f"ReportGenerator: templates={self.template_dir}, "
            f"output={self.output_dir}, weasyprint={WEASYPRINT_AVAILABLE}"
        )

    def _clean_old_reports(self):
        """Remove all old report files from previous runs."""
        removed = 0
        for ext in ['.html', '.pdf', '.xlsx']:
            for fname in os.listdir(self.output_dir):
                if fname.endswith(ext):
                    try:
                        os.remove(os.path.join(self.output_dir, fname))
                        removed += 1
                    except OSError:
                        pass
        if removed > 0:
            logger.info(f"Cleaned {removed} old report files")

    # ============================================================
    #  Chart builders
    # ============================================================

    # ============================================================
    #  Stock Report
    # ============================================================

    def generate_stock_report(self, symbol, analysis_result, report_type='html',
                             fundamentals=None, similar_stocks=None,
                             sector_peers=None, validation=None, score=None,
                             alerts=None, sector_medians=None, usd_kes=None,
                             holding=None):
        """
        Generate a per-stock report with charts, signals, fundamentals,
        similar stocks, and plain-English explanations (see page_stock.py).

        Args:
            symbol: Stock symbol.
            analysis_result: Dict from AnalysisEngine.analyze_stock().
            report_type: 'html', 'pdf', or 'both'.
            fundamentals: Dict of fundamental metrics from FundamentalAnalysis.
            similar_stocks: List of similar stock dicts.
            sector_peers: List of sector peer dicts.
            holding: Your holding of this stock (portfolio summary row), if
                any — shown as "Your position" and marked on the charts.

        Returns:
            str or tuple: Path(s) to generated report(s).
        """
        if not analysis_result or 'data' not in analysis_result:
            logger.error(f"Invalid analysis result for {symbol}")
            return None

        # Any metric a stock doesn't have reads as None (shown as N/A).
        from collections import defaultdict
        fundamentals = defaultdict(lambda: None, fundamentals or {})

        data = analysis_result['data']
        signals = analysis_result.get('signals', {})
        latest = analysis_result.get('latest', {})
        supports = analysis_result.get('support', [])
        resistances = analysis_result.get('resistance', [])
        daily_change = analysis_result.get('daily_change_pct')
        similar_stocks = similar_stocks or []
        sector_peers = sector_peers or []

        # Data date: prefer fundamentals date, fallback to today
        data_date = fundamentals.get('_data_date', datetime.now().strftime('%Y-%m-%d'))

        # Recommendation text.
        # Prefer the analyst mark (1=Strong Buy .. 5=Strong Sell) when
        # available; otherwise fall back to TradingView's technical-rating
        # gauge, which covers every stock. The label states the source so
        # it is clear this is TradingView's rating, not our own advice.
        rec = fundamentals.get('recommendation')
        if rec is None:
            label, _ = FundamentalAnalysis.signal_from_tech_rating(
                fundamentals.get('tech_rating')
            )
            if label == 'N/A':
                recommendation_text = 'No analyst coverage'
            else:
                recommendation_text = f'{label} (TradingView technical rating)'
        elif rec <= 1.5:
            recommendation_text = 'Strong Buy'
        elif rec <= 2.5:
            recommendation_text = 'Buy'
        elif rec <= 3.5:
            recommendation_text = 'Hold'
        elif rec <= 4.5:
            recommendation_text = 'Sell'
        else:
            recommendation_text = 'Strong Sell'

        # Buy/Hold/Sell class for colour-coding the executive summary
        rt = recommendation_text.lower()
        if 'sell' in rt:
            rec_class = 'sell'
        elif 'buy' in rt:
            rec_class = 'buy'
        elif 'hold' in rt or 'neutral' in rt:
            rec_class = 'hold'
        else:
            rec_class = 'none'

        import page_stock
        title, subtitle, body = page_stock.build_nse(
            self, symbol=symbol, data=data, latest=latest, signals=signals, supports=supports,
            resistances=resistances, daily_change=daily_change, fundamentals=fundamentals,
            similar_stocks=similar_stocks, sector_peers=sector_peers,
            recommendation_text=recommendation_text, rec_class=rec_class,
            validation=validation or {}, score=score or {}, alerts=alerts or [],
            sector_context=self._sector_context(fundamentals, sector_medians),
            data_date=data_date, generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            holding=holding)
        html_content = self._page_shell(title, '', subtitle, body, private=bool(holding),
                                        footer=self.STOCK_FOOTER)
        return self._save_report(f"{symbol}_report", html_content, report_type)

    def generate_international_stock_report(self, symbol, analysis_result, report_type='html',
                                             fundamentals=None, score=None, dividend_history=None,
                                             earnings_calendar=None, news=None, usd_kes=None,
                                             context='holding', holding=None):
        """
        Per-stock report for an international stock — a US-listed holding, or
        (context='watchlist') any stock on the watchlist, in whatever currency
        Yahoo quotes it (USD, GBp, EUR, ...). Same shape as
        generate_stock_report() (see page_stock.py), with the same charts and
        fund_color thresholds (currency-agnostic). A KES-converted price is
        shown alongside for USD stocks if a live FX rate was fetched this run.
        `holding` is your holding (international portfolio summary row), if any.
        """
        if not analysis_result or 'data' not in analysis_result:
            logger.error(f"Invalid analysis result for international stock {symbol}")
            return None

        from collections import defaultdict
        fundamentals = defaultdict(lambda: None, fundamentals or {})

        data = analysis_result['data']
        signals = analysis_result.get('signals', {})
        latest = analysis_result.get('latest', {})
        supports = analysis_result.get('support', [])
        resistances = analysis_result.get('resistance', [])
        daily_change = analysis_result.get('daily_change_pct')
        data_date = datetime.now().strftime('%Y-%m-%d')

        from international_portfolio import signal_from_recommendation
        recommendation_text, rec_class_raw = signal_from_recommendation(fundamentals.get('recommendation_key'))
        # The NSE page's buy/hold/sell colour convention (rec_class in
        # {'buy','hold','sell','none'}), mapped from our bullish/neutral/bearish/undefined.
        rec_class = {'bullish': 'buy', 'bearish': 'sell', 'neutral': 'hold'}.get(rec_class_raw, 'none')

        price = latest.get('close')
        currency = fundamentals.get('currency') or 'USD'
        fx_rate = usd_kes.get('rate') if (usd_kes and currency == 'USD') else None
        price_kes = (price * fx_rate) if (price is not None and fx_rate) else None

        target_mean = fundamentals.get('target_mean_price')
        target_upside_pct = ((target_mean - price) / price * 100.0) if (target_mean and price) else None

        # Prices in the stock's own currency: "$123.45" for USD (unchanged),
        # "126.80 GBp" for anything else. Big numbers likewise.
        def money(value, decimals=2):
            if value is None:
                return 'N/A'
            sign = '−' if value < 0 and round(abs(value), decimals) != 0 else ''
            if currency == 'USD':
                return f"{sign}${abs(value):,.{decimals}f}"
            return f"{sign}{abs(value):,.{decimals}f} {currency}"

        if currency == 'USD':
            fmt_mcap, fmt_currency = self._fmt_mcap_usd, self._fmt_currency_usd
        else:
            import watchlist as _wl
            fmt_mcap = fmt_currency = (lambda v: _wl.fmt_big(v, currency) if v else 'N/A')

        import page_stock
        title, subtitle, body = page_stock.build_intl(
            self, symbol=symbol, data=data, latest=latest, signals=signals, supports=supports,
            resistances=resistances, daily_change=daily_change, fundamentals=fundamentals,
            recommendation_text=recommendation_text, rec_class=rec_class, score=score or {},
            dividend_history=dividend_history or [], earnings_calendar=earnings_calendar or {},
            news=news or [], price_kes=price_kes, target_upside_pct=target_upside_pct, currency=currency,
            money=money, fmt_mcap=fmt_mcap, fmt_currency=fmt_currency, context=context,
            data_date=data_date, generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            holding=holding)
        html_content = self._page_shell(title, '', subtitle, body, private=bool(holding),
                                        footer=self.STOCK_FOOTER)
        return self._save_report(f"intl_{symbol}_report", html_content, report_type)

    # ---- Formatting helpers ----

    @staticmethod
    def _sector_context(fundamentals, sector_medians):
        """Valuation vs sector median for the stock report (fails safe)."""
        if not fundamentals or not sector_medians:
            return {}
        try:
            from market_context import valuation_vs_sector
            return valuation_vs_sector(fundamentals, sector_medians)
        except Exception:
            return {}

    def _ticker_logo_html(self, symbol, website=None, international=False):
        """
        Circular company-logo <img> for a ticker, or a colored-initial
        fallback badge if no logo is available — see company_logos.py for
        the source, caching and privacy rationale. Memoized per symbol for
        this run (self._logo_html_cache) since the same symbol is rendered
        in several tables.

        international=True: only the stock's own website counts — never the
        NSE ticker->domain map, because the same ticker can be a different
        company abroad (EQTY is Equity Group in Nairobi but an ETF in New York).
        """
        cache_key = f"{symbol}|{website or ''}|{'intl' if international else 'nse'}"
        cached = self._logo_html_cache.get(cache_key)
        if cached is not None:
            return cached

        import company_logos as _logos
        if international and not website:
            domain = None
        else:
            domain = _logos.resolve_domain(symbol, website=website)
        b64 = _logos.fetch_logo_base64(domain, cache_dir=self.cache_dir) if domain else None
        if b64:
            html = (f'<img class="ticker-logo" src="data:image/png;base64,{b64}" '
                    f'alt="{symbol} logo" loading="lazy">')
        else:
            html = _logos.fallback_badge_html(symbol)

        self._logo_html_cache[cache_key] = html
        return html

    @staticmethod
    def _fmt_mcap(value):
        """Format market cap for display."""
        if value is None or not value:
            return 'N/A'
        try:
            v = float(value)
            if v >= 1e12:
                return f"KES {v / 1e12:.2f} Trillion"
            elif v >= 1e9:
                return f"KES {v / 1e9:.2f} Billion"
            elif v >= 1e6:
                return f"KES {v / 1e6:.2f} Million"
            return f"KES {v:,.0f}"
        except (ValueError, TypeError):
            return 'N/A'

    @staticmethod
    def _fmt_currency(value):
        """Format large currency values."""
        if value is None or not value:
            return 'N/A'
        try:
            v = float(value)
            if abs(v) >= 1e12:
                return f"KES {v / 1e12:.2f}T"
            elif abs(v) >= 1e9:
                return f"KES {v / 1e9:.2f}B"
            elif abs(v) >= 1e6:
                return f"KES {v / 1e6:.2f}M"
            return f"KES {v:,.0f}"
        except (ValueError, TypeError):
            return 'N/A'

    @staticmethod
    def _fmt_mcap_usd(value):
        """Same as _fmt_mcap but for USD (international stocks)."""
        if value is None or not value:
            return 'N/A'
        try:
            v = float(value)
            if v >= 1e12:
                return f"${v / 1e12:.2f} Trillion"
            elif v >= 1e9:
                return f"${v / 1e9:.2f} Billion"
            elif v >= 1e6:
                return f"${v / 1e6:.2f} Million"
            return f"${v:,.0f}"
        except (ValueError, TypeError):
            return 'N/A'

    @staticmethod
    def _fmt_currency_usd(value):
        """Same as _fmt_currency but for USD (international stocks)."""
        if value is None or not value:
            return 'N/A'
        try:
            v = float(value)
            if abs(v) >= 1e12:
                return f"${v / 1e12:.2f}T"
            elif abs(v) >= 1e9:
                return f"${v / 1e9:.2f}B"
            elif abs(v) >= 1e6:
                return f"${v / 1e6:.2f}M"
            return f"${v:,.0f}"
        except (ValueError, TypeError):
            return 'N/A'

    @staticmethod
    def _interpret_pe(pe):
        if pe is None or not pe:
            return "No data available"
        try:
            pe = float(pe)
        except (ValueError, TypeError):
            return "No data available"
        if pe < 0:
            return "⚠️ The company is currently unprofitable (negative earnings)."
        if pe < 10:
            return "✅ Low valuation — the stock is priced cheaply relative to earnings. Could be a value opportunity."
        if pe < 15:
            return "✅ Fairly valued — reasonable price for the earnings generated."
        if pe < 20:
            return "📊 Moderately valued — slightly above average, typical for growing companies."
        if pe < 30:
            return "📊 Above-average valuation — investors expect good future growth."
        return "⚠️ High valuation — the market expects very strong future growth. The stock may be expensive."

    @staticmethod
    def _interpret_peg(peg):
        if peg is None or not peg:
            return "No data available"
        try:
            peg = float(peg)
        except (ValueError, TypeError):
            return "No data available"
        if peg < 0:
            return "⚠️ Negative PEG — earnings are declining."
        if peg < 0.5:
            return "✅ Very undervalued relative to growth — potentially a bargain."
        if peg < 1.0:
            return "✅ Undervalued — P/E is lower than the earnings growth rate."
        if peg < 1.5:
            return "📊 Fairly valued — P/E is roughly in line with growth."
        if peg < 2.5:
            return "📊 Slightly overvalued — P/E exceeds growth rate."
        return "⚠️ Overvalued — stock price is high relative to earnings growth."

    @staticmethod
    def _interpret_roe(roe):
        if roe is None or not roe:
            return "No data available"
        try:
            roe = float(roe)
        except (ValueError, TypeError):
            return "No data available"
        if roe < 0:
            return "⚠️ Negative ROE — the company is destroying shareholder value."
        if roe < 5:
            return "⚠️ Weak — very little profit from shareholder money."
        if roe < 10:
            return "📊 Below average — acceptable but not impressive."
        if roe < 15:
            return "📊 Average — decent returns on shareholder capital."
        if roe < 20:
            return "✅ Good — efficiently turns shareholder money into profit."
        if roe < 30:
            return "✅ Excellent — very efficient use of shareholder capital."
        return "🌟 Outstanding — extremely efficient. Verify this is sustainable."

    @staticmethod
    def _interpret_de(ratio):
        if ratio is None or not ratio:
            return "No data available"
        try:
            ratio = float(ratio)
        except (ValueError, TypeError):
            return "No data available"
        if ratio < 0:
            return "⚠️ HIGH RISK — more liabilities than assets."
        if ratio < 0.3:
            return "✅ Very conservative — uses very little debt. Low financial risk."
        if ratio < 0.7:
            return "✅ Conservative — manageable debt levels. Low to moderate risk."
        if ratio < 1.5:
            return "📊 Moderate leverage — reasonable amount of debt."
        if ratio < 3.0:
            return "⚠️ High leverage — significant debt relative to equity."
        return "🚨 Very high leverage — heavily indebted. Proceed with caution."

    @staticmethod
    def _interpret_rsi(rsi):
        if rsi is None or not rsi:
            return "No data available"
        try:
            rsi = float(rsi)
        except (ValueError, TypeError):
            return "No data available"
        if rsi > 80:
            return "🚨 Strongly overbought — price has risen very fast, high risk of pullback."
        if rsi > 70:
            return "⚠️ Overbought — may have risen too quickly, could correct."
        if rsi > 50:
            return "✅ Bullish momentum — price trending upward with moderate strength."
        if rsi > 30:
            return "📊 Bearish momentum — price trending downward."
        if rsi > 20:
            return "⚠️ Oversold — may have fallen too far, could bounce back."
        return "🚨 Strongly oversold — extreme selling, potential for sharp reversal."

    # ============================================================
    #  Market Summary
    # ============================================================

    def generate_market_summary(self, analysis_results, sector_data=None,
                                 breadth=None, report_type='html'):
        """
        Generate market summary report with sector and breadth data.

        Returns:
            str or tuple: Path(s) to generated report(s).
        """
        # Build stock summary table
        stocks = []
        gainers = []
        losers = []

        for symbol, result in analysis_results.items():
            if not result:
                continue
            latest = result.get('latest', {})
            signals = result.get('signals', {})
            chg = result.get('daily_change_pct')

            stock = {
                'symbol': symbol,
                'price': latest.get('close'),
                'rsi': latest.get('rsi'),
                'change': round(chg, 2) if chg is not None else None,
                'signal_ma': signals.get('ma_crossover', 'undefined'),
                'signal_rsi': signals.get('rsi', 'undefined'),
                'signal_macd': signals.get('macd', 'undefined'),
                'signal_bb': signals.get('bollinger', 'undefined'),
                'trend': signals.get('trend', 'undefined'),
                'overall': signals.get('overall', 'neutral'),
            }
            stocks.append(stock)

            if chg is not None:
                entry = {'symbol': symbol, 'price': latest.get('close'), 'change': round(chg, 2)}
                if chg > 0:
                    gainers.append(entry)
                elif chg < 0:
                    losers.append(entry)

        # Sort gainers/losers
        gainers.sort(key=lambda x: x['change'], reverse=True)
        losers.sort(key=lambda x: x['change'])

        # Render. (No sector chart: market_summary.html never showed one, so
        # drawing it was wasted work on every run.)
        template_data = {
            'generated_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'stocks': stocks,
            'gainers': gainers,
            'losers': losers,
            'sectors': sector_data,
            'breadth': breadth,
        }

        html_content = self._render('market_summary.html', template_data)
        return self._save_report('market_summary', html_content, report_type)

    # ============================================================
    #  Excel Export
    # ============================================================

    def export_to_excel(self, analysis_results, sector_data=None, breadth=None):
        """
        Export analysis data to a multi-sheet Excel workbook.

        Returns:
            str: Path to the generated .xlsx file.
        """
        if not OPENPYXL_AVAILABLE:
            logger.warning("openpyxl not installed, skipping Excel export")
            return None

        wb = Workbook()

        # ---- Sheet 1: Summary ----
        ws = wb.active
        ws.title = "Summary"
        self._write_excel_header(ws, ['Symbol', 'Price', 'Change %', 'RSI', 'Trend',
                                       'MA Signal', 'MACD', 'Overall'])
        for i, (symbol, result) in enumerate(analysis_results.items(), 2):
            if not result:
                continue
            latest = result.get('latest', {})
            signals = result.get('signals', {})
            ws.cell(row=i, column=1, value=symbol)
            ws.cell(row=i, column=2, value=latest.get('close'))
            ws.cell(row=i, column=3, value=result.get('daily_change_pct'))
            ws.cell(row=i, column=4, value=latest.get('rsi'))
            ws.cell(row=i, column=5, value=signals.get('trend', ''))
            ws.cell(row=i, column=6, value=signals.get('ma_crossover', ''))
            ws.cell(row=i, column=7, value=signals.get('macd', ''))
            ws.cell(row=i, column=8, value=signals.get('overall', ''))

        self._auto_width(ws)

        # ---- Sheet 2: Signals ----
        ws2 = wb.create_sheet("Signals")
        signal_names = ['ma_crossover', 'rsi', 'macd', 'bollinger', 'trend',
                         'stochastic', 'volume', 'overall']
        self._write_excel_header(ws2, ['Symbol'] + [s.replace('_', ' ').title() for s in signal_names])
        for i, (symbol, result) in enumerate(analysis_results.items(), 2):
            if not result:
                continue
            signals = result.get('signals', {})
            ws2.cell(row=i, column=1, value=symbol)
            for j, sname in enumerate(signal_names, 2):
                ws2.cell(row=i, column=j, value=signals.get(sname, ''))

        self._auto_width(ws2)

        # ---- Sheet 3: Sector Analysis ----
        if sector_data:
            ws3 = wb.create_sheet("Sector Analysis")
            self._write_excel_header(ws3, ['Sector', 'Stocks', 'Avg Change %',
                                            'Avg RSI', 'Bullish %', 'Symbols'])
            for i, (sector, data) in enumerate(sector_data.items(), 2):
                ws3.cell(row=i, column=1, value=sector)
                ws3.cell(row=i, column=2, value=data['count'])
                ws3.cell(row=i, column=3, value=data['avg_change_pct'])
                ws3.cell(row=i, column=4, value=data['avg_rsi'])
                ws3.cell(row=i, column=5, value=data['bullish_ratio'])
                ws3.cell(row=i, column=6, value=', '.join(data['symbols']))
            self._auto_width(ws3)

        # Save
        path = os.path.join(
            self.output_dir, f"nse_analysis_{self.timestamp}.xlsx"
        )
        wb.save(path)
        logger.info(f"Excel exported to {path}")
        return path

    def _write_excel_header(self, ws, headers):
        """Write formatted header row."""
        header_fill = PatternFill(start_color='1e293b', end_color='1e293b', fill_type='solid')
        header_font = Font(color='FFFFFF', bold=True, size=11)
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.fill = header_fill
            cell.font = header_font

    def _auto_width(self, ws):
        """Auto-fit column widths."""
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = min(max_len + 3, 40)

    # ============================================================
    #  Helpers
    # ============================================================

    # ============================================================
    #  Summary report (compact 1-page PDF for daily email)
    # ============================================================

    def generate_summary(self, analysis_results, fundamentals_data=None,
                         validations=None, scores=None, alerts=None,
                         breadth=None, sector_data=None, usd_kes=None,
                         watchlist=None, report_type='pdf', portfolio_summary=None):
        """
        Build a compact, one-page summary of key metrics and render it to PDF
        (and/or HTML). Designed for a daily email — a subset of the dashboard.

        Returns the path(s) from _save_report (str or tuple).
        """
        fundamentals_data = fundamentals_data or {}
        validations = validations or {}
        scores = scores or {}
        alerts = alerts or {}
        now = datetime.now().strftime('%Y-%m-%d %H:%M EAT')

        total = len([r for r in analysis_results.values() if r])
        bullish = sum(1 for r in analysis_results.values()
                      if r and r.get('signals', {}).get('overall') == 'bullish')
        bearish = sum(1 for r in analysis_results.values()
                      if r and r.get('signals', {}).get('overall') == 'bearish')

        # Data quality
        v_ok = sum(1 for v in validations.values() if v.get('status') == 'ok')
        v_mis = sum(1 for v in validations.values() if v.get('status') == 'mismatch')
        v_stale = sum(1 for v in validations.values() if v.get('status') == 'stale')

        fx_str = f"USD/KES {usd_kes['rate']:.2f}" if usd_kes and usd_kes.get('rate') else ''

        # ---- Watchlist rows ----
        watchlist = watchlist or sorted(analysis_results.keys())
        wl_rows = ''
        for sym in watchlist:
            r = analysis_results.get(sym)
            if not r:
                continue
            latest = r.get('latest', {})
            fund = fundamentals_data.get(sym, {})
            label, cls = FundamentalAnalysis.signal_from_tech_rating(fund.get('tech_rating'))
            chg = r.get('daily_change_pct')
            chg_str = f"{chg:+.2f}%" if chg is not None else '—'
            chg_col = '#16a34a' if (chg or 0) >= 0 else '#dc2626'
            price = latest.get('close')
            price_str = f"{price:.2f}" if price else '—'
            dy = fund.get('dividend_yield')
            dy_str = f"{dy:.1f}%" if dy else '—'
            score = scores.get(sym, {}).get('overall')
            score_str = str(score) if score is not None else '—'
            sig_col = {'strong_buy': '#16a34a', 'buy': '#16a34a', 'neutral': '#d97706',
                       'sell': '#dc2626', 'strong_sell': '#dc2626'}.get(cls, '#94a3b8')
            wl_rows += (
                f'<tr><td><b>{sym}</b></td>'
                f'<td style="color:{sig_col};font-weight:700;">{label}</td>'
                f'<td>{price_str}</td>'
                f'<td style="color:{chg_col};">{chg_str}</td>'
                f'<td>{dy_str}</td>'
                f'<td>{score_str}</td></tr>'
            )

        # ---- Top movers ----
        changes = [(s, r['daily_change_pct']) for s, r in analysis_results.items()
                   if r and r.get('daily_change_pct') is not None]
        changes.sort(key=lambda x: x[1], reverse=True)
        gainers = changes[:5]
        losers = changes[-5:][::-1] if len(changes) >= 5 else []
        movers_rows = ''
        for label, rows in [('Gainers', gainers), ('Losers', losers)]:
            for sym, chg in rows:
                col = '#16a34a' if chg >= 0 else '#dc2626'
                movers_rows += (f'<tr><td>{label}</td><td><b>{sym}</b></td>'
                                f'<td style="color:{col};">{chg:+.2f}%</td></tr>')

        # ---- Upcoming ex-dividends (next 30 days) ----
        today = datetime.now().date()
        upcoming = []
        for sym, f in fundamentals_data.items():
            ex = f.get('dividend_ex_date')
            if not ex:
                continue
            try:
                d = datetime.strptime(ex, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                continue
            delta = (d - today).days
            if 0 <= delta <= 30:
                upcoming.append((delta, sym, ex, f.get('dps_fy'), f.get('dividend_yield')))
        upcoming.sort()
        exdiv_rows = ''
        for delta, sym, ex, dps, dy in upcoming:
            dps_str = f"{dps:g}" if dps else '0'
            dy_str = f"{dy:.1f}%" if dy else '—'
            when = 'today' if delta == 0 else f'in {delta}d'
            exdiv_rows += (
                f'<tr><td><b>{sym}</b></td>'
                f'<td>{dps_str}</td>'
                f'<td>{dy_str}</td>'
                f'<td style="color:#166534;font-weight:700;">{ex} ({when})</td></tr>'
            )
        if not exdiv_rows:
            exdiv_rows = '<tr><td colspan="4" style="color:#94a3b8;">None in the next 30 days</td></tr>'

        # ---- Key alerts (cap to keep it one page) ----
        alert_items = ''
        shown = 0
        for sym in sorted(alerts.keys()):
            for a in alerts[sym]:
                if any(k in a for k in ['Strong Buy', 'Strong Sell', 'Oversold',
                                        'High dividend', 'Ex-dividend', '52-week']):
                    alert_items += f'<li><b>{sym}</b>: {a}</li>'
                    shown += 1
            if shown >= 14:
                break

        # ---- My Portfolio (private — compact block, only if holdings exist) ----
        portfolio_block = self._build_summary_portfolio_block(portfolio_summary)

        html = self._build_summary_html(
            now=now, fx_str=fx_str, total=total, bullish=bullish, bearish=bearish,
            v_ok=v_ok, v_mis=v_mis, v_stale=v_stale, breadth=breadth,
            wl_rows=wl_rows, movers_rows=movers_rows, exdiv_rows=exdiv_rows,
            alert_items=alert_items, portfolio_block=portfolio_block,
        )
        return self._save_report('nse_summary', html, report_type)

    @staticmethod
    def _build_summary_portfolio_block(portfolio_summary):
        """Compact 'My Portfolio' block for the one-page PDF summary. '' if
        the user has no private holdings set up."""
        if not portfolio_summary:
            return ''
        t = portfolio_summary['totals']
        gcls = 'g' if (t.get('gain') or 0) >= 0 else 'r'
        dcls = 'g' if (t.get('day_change_pct') or 0) >= 0 else 'r'
        rows = ''
        for h in sorted(portfolio_summary['holdings'],
                        key=lambda x: (x.get('market_value') or -1), reverse=True):
            if not h['data_available']:
                rows += f'<tr><td><b>{h["symbol"]}</b></td><td colspan="4" class="r">No live data today</td></tr>'
                continue
            hc = 'g' if h['gain_pct'] >= 0 else 'r'
            dc = 'g' if (h.get('day_change_pct') or 0) >= 0 else 'r'
            day_txt = f'{h["day_change_pct"]:+.2f}%' if h.get('day_change_pct') is not None else '—'
            rows += (f'<tr><td><b>{h["symbol"]}</b></td><td>{h["price"]:.2f}</td>'
                     f'<td>{h["market_value"]:,.0f}</td>'
                     f'<td class="{hc}">{h["gain_pct"]:+.1f}%</td>'
                     f'<td class="{dc}">{day_txt}</td></tr>')
        return f'''<h2>💼 My Portfolio</h2>
<div class="pills">
  <span class="pill"><b>{t['cost_basis']:,.0f}</b> cost (KES)</span>
  <span class="pill"><b>{t['market_value']:,.0f}</b> value (KES)</span>
  <span class="pill {gcls}"><b>{t['gain']:+,.0f}</b> gain ({t.get('gain_pct', 0):+.1f}%)</span>
  <span class="pill {dcls}"><b>{(f"{t['day_change_pct']:+.2f}%" if t.get('day_change_pct') is not None else '—')}</b> today</span>
</div>
<table><thead><tr><th>Symbol</th><th>Price</th><th>Value (KES)</th><th>Gain %</th><th>Today</th></tr></thead>
<tbody>{rows}</tbody></table>'''

    @staticmethod
    def _build_summary_html(now, fx_str, total, bullish, bearish, v_ok, v_mis,
                            v_stale, breadth, wl_rows, movers_rows, exdiv_rows,
                            alert_items, portfolio_block=''):
        breadth = breadth or {}
        breadth_str = ' · '.join(
            f"{lbl} {breadth[k]}%" for k, lbl in
            [('pct_above_sma50', 'Above SMA50'), ('pct_bullish_macd', 'Bullish MACD'),
             ('pct_rsi_above_50', 'RSI>50')] if k in breadth
        )
        alerts_block = (f'<h2>🔔 Key Alerts</h2><ul>{alert_items}</ul>'
                        if alert_items else '')
        return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 14mm; }}
body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; color: #1e293b; font-size: 11px; }}
h1 {{ font-size: 18px; margin: 0; }}
h2 {{ font-size: 12px; border-bottom: 2px solid #3b82f6; padding-bottom: 3px; margin: 14px 0 6px; }}
.sub {{ color: #64748b; font-size: 10px; margin-bottom: 10px; }}
.pills {{ margin: 8px 0; }}
.pill {{ display: inline-block; background: #f1f5f9; border-radius: 8px; padding: 6px 12px; margin-right: 6px; }}
.pill b {{ font-size: 15px; }}
.g {{ color: #16a34a; }} .r {{ color: #dc2626; }} .a {{ color: #d97706; }}
table {{ width: 100%; border-collapse: collapse; font-size: 10px; }}
th, td {{ padding: 4px 6px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
th {{ background: #f8fafc; color: #64748b; text-transform: uppercase; font-size: 8.5px; }}
ul {{ margin: 4px 0; padding-left: 18px; }} li {{ margin: 2px 0; }}
.note {{ color: #94a3b8; font-size: 9px; margin-top: 14px; border-top: 1px solid #e2e8f0; padding-top: 6px; }}
</style></head><body>
<h1>🇰🇪 NSE Daily Summary</h1>
<div class="sub">{now}{(' · ' + fx_str) if fx_str else ''}</div>
<div class="pills">
  <span class="pill"><b>{total}</b> stocks</span>
  <span class="pill g"><b>{bullish}</b> bullish</span>
  <span class="pill r"><b>{bearish}</b> bearish</span>
  <span class="pill g"><b>{v_ok}</b> price-verified</span>
  <span class="pill r"><b>{v_mis}</b> mismatch</span>
  <span class="pill a"><b>{v_stale}</b> stale</span>
</div>
{f'<div class="sub">Breadth: {breadth_str}</div>' if breadth_str else ''}
{portfolio_block}
<h2>⭐ Watchlist</h2>
<table><thead><tr><th>Symbol</th><th>TV Signal</th><th>Price</th><th>Change</th><th>Yield</th><th>Score</th></tr></thead>
<tbody>{wl_rows}</tbody></table>
<h2>📈 Top Movers</h2>
<table><thead><tr><th>Dir</th><th>Symbol</th><th>Change</th></tr></thead><tbody>{movers_rows}</tbody></table>
<h2>💵 Upcoming Ex-Dividends (next 30 days)</h2>
<table><thead><tr><th>Symbol</th><th>Div KES</th><th>Yield</th><th>Ex-Date</th></tr></thead><tbody>{exdiv_rows}</tbody></table>
{alerts_block}
<div class="note">Data: TradingView (~15 min delayed), cross-checked against afx.kwayisi.org. Prices verified where possible; treat mismatches with caution. This is a mechanical summary, not investment advice.</div>
</body></html>'''

    # ============================================================
    #  Index Dashboard (single entry point)
    # ============================================================

    def generate_index(self, analysis_results, sector_data=None, breadth=None,
                       report_files=None, fundamentals_data=None,
                       validations=None, scores=None, alerts=None, usd_kes=None,
                       portfolio_summary=None, portfolio_history=None, portfolio_news=None,
                       portfolio_history_tracker=None, bond_portfolio=None,
                       intl_portfolio_summary=None, intl_portfolio_history=None,
                       intl_portfolio_news=None, intl_portfolio_history_tracker=None,
                       intl_report_files=None, intl_analysis_results=None, intl_fundamentals_data=None,
                       watchlist_count=None):
        """
        Generate the main index.html dashboard — the single entry point.

        Links to individual stock reports and shows the full market picture
        including fundamental metrics (P/E, Market Cap) from TradingView.

        Args:
            fundamentals_data: dict from FundamentalAnalysis.fetch_all_fundamentals().

        Returns:
            str: Path to index.html.
        """
        # Build stock table rows
        stocks = []
        gainers = []
        losers = []
        for symbol, result in sorted(analysis_results.items()):
            if not result:
                continue
            latest = result.get('latest', {})
            signals = result.get('signals', {})
            chg = result.get('daily_change_pct')

            # Get fundamental data for this stock
            fund = (fundamentals_data or {}).get(symbol, {})

            # TradingView Buy/Sell signal from the technical-rating gauge
            rating_label, rating_class = FundamentalAnalysis.signal_from_tech_rating(
                fund.get('tech_rating')
            )

            stock = {
                'symbol': symbol,
                'name': fund.get('name'),
                'price': latest.get('close'),
                'rsi': latest.get('rsi'),
                'change': round(chg, 2) if chg is not None else None,
                'trend': signals.get('trend', 'undefined'),
                'overall': signals.get('overall', 'neutral'),
                'ma': signals.get('ma_crossover', 'undefined'),
                'macd': signals.get('macd', 'undefined'),
                'stochastic': signals.get('stochastic', 'undefined'),
                'volume_signal': signals.get('volume', 'undefined'),
                'report_file': report_files.get(symbol, '') if report_files else '',
                # Fundamental metrics
                'pe_ratio': fund.get('pe_ratio'),
                'market_cap': fund.get('market_cap'),
                'roe': fund.get('roe'),
                'peg_ratio': fund.get('peg_ratio'),
                'sector': fund.get('sector', ''),
                # Extra fundamentals (from TradingView) for the layman detail table
                'price_to_book': fund.get('price_to_book'),
                'eps': fund.get('eps_ttm'),
                'net_margin': fund.get('net_margin'),
                'debt_to_equity': fund.get('debt_to_equity'),
                'revenue_growth': fund.get('revenue_growth_yoy'),
                # TradingView Buy/Sell signal
                'signal_label': rating_label,
                'signal_class': rating_class,
                # Dividend yield (key for income-focused NSE investors)
                'dividend_yield': fund.get('dividend_yield'),
                # Dividend amount (KES/share) and ex-dividend date
                'dps': fund.get('dps_fy'),
                'ex_date': fund.get('dividend_ex_date'),
                'ex_upcoming': fund.get('dividend_ex_date_is_upcoming'),
                'dividend_status': fund.get('dividend_status'),
                'book_closure': fund.get('dividend_book_closure'),
                'payment_date': fund.get('dividend_payment_date'),
                'dividend_type': fund.get('dividend_type'),
                # Next earnings date (used by the Next Earnings page)
                'earnings_next_date': fund.get('earnings_next_date'),
                # Transparent factor score (0-100) + full breakdown for hover
                'score': (scores or {}).get(symbol, {}).get('overall'),
                'score_detail': (scores or {}).get(symbol, {}),
                # Liquidity (for the market heatmap sizing + most-traded chart)
                'value_traded': fund.get('value_traded'),
                'volume': fund.get('volume'),
                # Price validation (independent cross-check + freshness)
                'validation': (validations or {}).get(symbol, {}),
            }
            stocks.append(stock)

            if chg is not None:
                entry = {'symbol': symbol, 'change': round(chg, 2)}
                if chg > 0:
                    gainers.append(entry)
                elif chg < 0:
                    losers.append(entry)

        gainers.sort(key=lambda x: x['change'], reverse=True)
        losers.sort(key=lambda x: x['change'])

        # Market stats
        bullish = sum(1 for s in stocks if s['overall'] == 'bullish')
        bearish = sum(1 for s in stocks if s['overall'] == 'bearish')
        neutral = sum(1 for s in stocks if s['overall'] == 'neutral')

        # Data date
        data_date = datetime.now().strftime('%Y-%m-%d')
        if fundamentals_data:
            for _, f in fundamentals_data.items():
                if f and f.get('_data_date'):
                    data_date = f['_data_date']
                    break

        # Link each held stock to its detailed report, if one was generated
        # this run (--detailed). Copies the holdings list rather than
        # mutating the caller's portfolio_summary in place.
        if portfolio_summary and report_files:
            portfolio_summary = dict(portfolio_summary)
            portfolio_summary['holdings'] = [
                {**h, 'report_file': report_files.get(h['symbol'], '')}
                for h in portfolio_summary['holdings']
            ]

        # Same linking for international holdings, to their intl_{symbol}_report.html pages.
        if intl_portfolio_summary and intl_report_files:
            intl_portfolio_summary = dict(intl_portfolio_summary)
            intl_portfolio_summary['holdings'] = [
                {**h, 'report_file': intl_report_files.get(h['symbol'], '')}
                for h in intl_portfolio_summary['holdings']
            ]

        index_path = self._build_dashboard_pages(
            stocks=stocks, gainers=gainers, losers=losers, sectors=sector_data,
            breadth=breadth, bullish=bullish,
            bearish=bearish, neutral=neutral, total=len(stocks),
            data_date=data_date, alerts=alerts, usd_kes=usd_kes,
            portfolio_summary=portfolio_summary, portfolio_history=portfolio_history,
            portfolio_news=portfolio_news, portfolio_history_tracker=portfolio_history_tracker,
            bond_portfolio=bond_portfolio,
            intl_portfolio_summary=intl_portfolio_summary, intl_portfolio_history=intl_portfolio_history,
            intl_portfolio_news=intl_portfolio_news,
            intl_portfolio_history_tracker=intl_portfolio_history_tracker,
            nse_results=analysis_results, fundamentals_data=fundamentals_data,
            intl_results=intl_analysis_results, intl_fundamentals=intl_fundamentals_data,
            watchlist_count=watchlist_count,
        )
        return index_path

    @staticmethod
    def page_subtitle(total=None, data_date=None, usd_kes=None):
        """The grey line under the dashboard title (shared by every page)."""
        now = datetime.now().strftime('%Y-%m-%d %H:%M EAT')
        data_date_str = data_date or datetime.now().strftime('%Y-%m-%d')
        fx = f" · 💵 USD/KES {usd_kes['rate']:.2f}" if (usd_kes and usd_kes.get('rate')) else ""
        count = f"{total} stocks · " if total is not None else ""
        return (f"{now} · {count}📅 {data_date_str} · "
                f"Prices: NSE official close · Fundamentals: TradingView{fx}")

    def write_page(self, filename, html):
        """Write one dashboard page atomically (temp file + rename), so the
        dashboard app never serves a half-written page while it's being rebuilt."""
        path = os.path.join(self.output_dir, filename)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(html)
        os.replace(tmp, path)
        return path

    # The local dashboard app (app.py) serves these two files. Opened any other
    # way (file://, the Docker nginx) they simply fail to load and every
    # app-only control stays hidden — see the html.app-on rules in the CSS.
    APP_ASSETS_HEAD = ('<link rel="stylesheet" href="/app-assets/manage.css">'
                       "<script>(function(){if(/^https?:$/.test(location.protocol)){"
                       "var d=document.documentElement;d.classList.add('app-pending');"
                       "setTimeout(function(){d.classList.remove('app-pending');},2500);}})();</script>")
    APP_ASSETS_BODY = '<script src="/app-assets/manage.js" defer></script>'
    STOCK_FOOTER = "Generated by Kenyan Stock Analyzer · Information, not financial advice"

    def _page_shell(self, page_title, active_file, subtitle, body_html, private=None, footer=None):
        """Wrap a page body in the shared shell — sidebar navigation, top bar,
        the design system and the page script (see ui_theme.page()). "Hide
        amounts" appears on the pages that show your money."""
        import ui_theme
        from markupsafe import Markup
        if private is None:
            private = active_file in ('portfolio.html', 'watchlist.html')
        return ui_theme.page(
            title=ui_theme.title_for(active_file) or page_title, active_file=active_file,
            subtitle=subtitle, body=Markup(body_html), app_head=self.APP_ASSETS_HEAD,
            app_body=self.APP_ASSETS_BODY, private=private, footer=footer)

    @staticmethod
    def _fund_verdict(metric, value):
        """
        Judge a fundamental value as 'good', 'bad', or None (neutral), using
        the same layman thresholds shown in the plain-English guide. Used to
        colour cells green (good) / red (bad) so quality is visible at a glance.
        """
        if value is None:
            return None
        try:
            v = float(value)
        except Exception:
            # Covers non-numeric values and Jinja Undefined (stocks with no
            # fundamentals) — treat as neutral (no colour) rather than error.
            return None
        # Three tiers: 'good' (green), 'bad' (red), 'mid' (amber). A numeric
        # value that is neither clearly good nor bad is 'mid'. Non-numeric /
        # missing values returned None above and stay uncoloured (black).
        rules = {
            'pe':    lambda: 'good' if 0 < v <= 15 else 'bad' if (v <= 0 or v > 30) else 'mid',
            'peg':   lambda: 'good' if 0 < v <= 1 else 'bad' if (v < 0 or v > 2.5) else 'mid',
            'pb':    lambda: 'good' if 0 < v <= 1.5 else 'bad' if (v < 0 or v > 5) else 'mid',
            'ps':    lambda: 'good' if 0 < v <= 1.5 else 'bad' if (v < 0 or v > 6) else 'mid',
            'ev_ebitda': lambda: 'good' if 0 < v <= 10 else 'bad' if (v < 0 or v > 20) else 'mid',
            'eps':   lambda: 'good' if v > 0 else 'bad' if v < 0 else 'mid',
            'eps_growth': lambda: 'good' if v >= 10 else 'bad' if v < 0 else 'mid',
            'roe':   lambda: 'good' if v >= 15 else 'bad' if v < 5 else 'mid',
            'roic':  lambda: 'good' if v >= 12 else 'bad' if v < 5 else 'mid',
            'roa':   lambda: 'good' if v >= 8 else 'bad' if v < 2 else 'mid',
            'gm':    lambda: 'good' if v >= 40 else 'bad' if v < 15 else 'mid',
            'om':    lambda: 'good' if v >= 15 else 'bad' if v < 0 else 'mid',
            'nm':    lambda: 'good' if v >= 15 else 'bad' if v < 0 else 'mid',
            'fcfm':  lambda: 'good' if v >= 10 else 'bad' if v < 0 else 'mid',
            'de':    lambda: 'good' if 0 <= v <= 0.5 else 'bad' if (v < 0 or v > 2) else 'mid',
            'cr':    lambda: 'good' if v >= 1.5 else 'bad' if v < 1 else 'mid',
            'qr':    lambda: 'good' if v >= 1 else 'bad' if v < 0.7 else 'mid',
            'rg':    lambda: 'good' if v >= 10 else 'bad' if v < 0 else 'mid',
            'yield': lambda: 'good' if v >= 5 else 'mid',
        }
        fn = rules.get(metric)
        return fn() if fn else None

    @staticmethod
    def _fund_color(metric, value):
        """Return a colour class for templates: green / amber / red / none.
        good→'positive' (green), mid→'midv' (amber), bad→'negative' (red),
        None (N/A)→'' (black)."""
        verdict = ReportGenerator._fund_verdict(metric, value)
        return {'good': 'positive', 'mid': 'midv', 'bad': 'negative'}.get(verdict, '')

    # The market pages that read other sources (see page_market.py) — each
    # loader fails safe to "nothing", and the page says so; never a guess.
    def _load_foreign_flows(self):
        try:
            from foreign_flows import load as load_foreign
            return load_foreign()
        except Exception as e:
            logger.warning(f"Foreign flows: loader failed: {e}")
            return {"weeks": [], "source_home": "", "source_note": ""}

    def _load_market_pulse(self, analysis_results_for_pulse=None):
        try:
            from market_pulse import load_all as load_pulse
            return load_pulse(analysis_results_for_pulse or {})
        except Exception as e:
            logger.warning(f"Market Pulse: loader failed: {e}")
            return {"news": [], "cbk": {}, "oil": [], "african_indices": [], "fx": [], "generated_at": ""}

    def _load_treasury(self):
        try:
            from treasury_securities import load_treasury
            return load_treasury(cache_dir="data", logger=logger)
        except Exception as e:
            logger.warning(f"Bonds page: loader failed: {e}")
            return {"as_of": "", "tbills": [], "bonds": [], "context": {}}

    # ==================================================================
    # GOVERNMENT BONDS (links and the tax rate used on the Govt bonds page)
    # ==================================================================
    _CBK_BONDS_URL = "https://www.centralbank.go.ke/bills-bonds/treasury-bonds/"
    _CBK_BILLS_URL = "https://www.centralbank.go.ke/bills-bonds/treasury-bills/"
    _DHOWCSD_URL = "https://www.centralbank.go.ke/dhow-csd/"
    _BOND_WHT = 0.10  # withholding tax used ONLY for the tax-equivalent illustration

    # ==================================================================
    # VISUALS — market heatmap, snapshot charts, rich hover previews
    # ==================================================================
    def _stock_tip(self, s):
        """Build the rich hover-preview HTML for one stock, escaped for a
        data-tip="" attribute (price, change, signal + full score breakdown
        with the reasons behind each factor). Purely from real data."""
        sd = s.get("score_detail") or {}
        overall = sd.get("overall")
        chg = s.get("change")
        chgc = "#4ade80" if (chg or 0) > 0 else "#f87171" if (chg or 0) < 0 else "#94a3b8"
        chgs = f"{chg:+.2f}%" if chg is not None else "—"
        price = f"KES {s['price']:.2f}" if s.get("price") else "KES —"
        # previous close (yesterday) implied by today's close and % change
        prevc = None
        if s.get("price") and chg is not None and (1 + chg / 100) != 0:
            prevc = s["price"] / (1 + chg / 100)
        p = [f"<div class='tt-h'><span>{_tip_text(s['symbol'])}</span><span style='color:{chgc}'>{chgs}</span></div>",
             f"<div class='tt-sub'>{_tip_text(s.get('sector') or 'NSE')} · {price} · {_tip_text(s.get('signal_label',''))}</div>"]
        if prevc:
            p.append(f"<div class='tt-row'><span class='tt-k'>Today vs yesterday</span>"
                     f"<span>{s['price']:.2f} ← {prevc:.2f}</span></div>")
        if overall is not None:
            oc = "#22c55e" if overall >= 70 else "#f59e0b" if overall >= 45 else "#ef4444"
            p.append(f"<div class='tt-row'><span class='tt-k'>Overall score</span>"
                     f"<span style='color:{oc};font-weight:800'>{overall}/100</span></div>")
            for key, label in [("value", "Value"), ("quality", "Quality"),
                               ("momentum", "Momentum"), ("dividend", "Dividend"),
                               ("liquidity", "Liquidity")]:
                v = sd.get(key)
                if v is None:
                    p.append(f"<div class='tt-row'><span class='tt-k'>{label}</span><span>n/a</span></div>")
                    continue
                col = "#22c55e" if v >= 70 else "#f59e0b" if v >= 45 else "#ef4444"
                p.append(f"<div class='tt-row'><span class='tt-k'>{label}</span><span>{v}/100</span></div>"
                         f"<div class='tt-bar'><span style='width:{v}%;background:{col}'></span></div>")
            reasons = sd.get("reasons") or {}
            allr = []
            for key in ("value", "quality", "momentum", "dividend", "liquidity"):
                allr += (reasons.get(key) or [])
            if allr:
                p.append(f"<div class='tt-note'>Why: {' · '.join(_tip_text(r) for r in allr[:6])}</div>")
            p.append("<div class='tt-note'>Score = weighted blend of Value, Quality, Momentum, "
                     "Dividend &amp; Liquidity (0–100). A transparent screen, not advice.</div>")
        else:
            p.append("<div class='tt-note'>Not enough public data to score this stock yet.</div>")
        html = "".join(p)
        # escape for a double-quoted HTML attribute (inner markup uses ' quotes)
        return html.replace("&", "&amp;").replace('"', "&quot;")

    # ---- squarified treemap (Bruls/Huizing/van Wijk) ----
    @staticmethod
    def _tm_normalize(sizes, dx, dy):
        total = float(sum(sizes)) or 1.0
        return [s * dx * dy / total for s in sizes]

    @staticmethod
    def _tm_layout(sizes, x, y, dx, dy):
        # lay a run of tiles along the shorter side so they stay squarish
        if dx >= dy:  # a row across the top
            w = sum(sizes) / dy
            out, yy = [], y
            for s in sizes:
                out.append({"x": x, "y": yy, "dx": w, "dy": s / w})
                yy += s / w
            return out
        h = sum(sizes) / dx  # a column down the left
        out, xx = [], x
        for s in sizes:
            out.append({"x": xx, "y": y, "dx": s / h, "dy": h})
            xx += s / h
        return out

    @staticmethod
    def _tm_leftover(sizes, x, y, dx, dy):
        if dx >= dy:
            w = sum(sizes) / dy
            return (x + w, y, dx - w, dy)
        h = sum(sizes) / dx
        return (x, y + h, dx, dy - h)

    def _tm_worst(self, sizes, x, y, dx, dy):
        rects = self._tm_layout(sizes, x, y, dx, dy)
        return max(max(r["dx"] / r["dy"], r["dy"] / r["dx"]) for r in rects if r["dy"] and r["dx"])

    def _squarify(self, sizes, x, y, dx, dy):
        """Return [{x,y,dx,dy}] packing `sizes` (already area-normalised, desc)
        into the rect with near-square aspect ratios and NO gaps."""
        sizes = [float(s) for s in sizes if s > 0]
        if not sizes:
            return []
        if len(sizes) == 1:
            return self._tm_layout(sizes, x, y, dx, dy)
        i = 1
        while i < len(sizes) and self._tm_worst(sizes[:i], x, y, dx, dy) >= self._tm_worst(sizes[:i + 1], x, y, dx, dy):
            i += 1
        current, remaining = sizes[:i], sizes[i:]
        lx, ly, ldx, ldy = self._tm_leftover(current, x, y, dx, dy)
        return self._tm_layout(current, x, y, dx, dy) + self._squarify(remaining, lx, ly, ldx, ldy)

    # ==================================================================
    # MY PORTFOLIO — private holdings tracker (charts + page)
    # ==================================================================
    # ---- International (US) portfolio charts — same style, USD instead of KES ----
    def _portfolio_holding_tip(self, h):
        """Rich hover preview for one portfolio holding — cost, live value,
        gain, dividend, and the same transparent score breakdown used
        elsewhere on the dashboard. Escaped for a data-tip="" attribute."""
        if not h.get('data_available'):
            html = (f"<div class='tt-h'><span>{_tip_text(h['symbol'])}</span>"
                    f"<span style='color:#f87171'>no data today</span></div>"
                    f"<div class='tt-sub'>{h['quantity']:g} shares @ avg KES {h['avg_cost']:.2f} "
                    f"(cost KES {h['cost_basis']:,.0f})</div>"
                    "<div class='tt-note'>This stock had no live price in today's data pull — "
                    "showing your cost basis only. Try again after the next run.</div>")
            return html.replace("&", "&amp;").replace('"', "&quot;")

        gp = h.get('gain_pct')
        gc = '#4ade80' if (gp or 0) >= 0 else '#f87171'
        p = [
            f"<div class='tt-h'><span>{_tip_text(h['symbol'])}</span>"
            f"<span style='color:{gc}'>{gp:+.1f}%</span></div>",
            f"<div class='tt-sub'>{h['quantity']:g} shares · avg cost KES {h['avg_cost']:.2f} "
            f"· now KES {h['price']:.2f}</div>",
            f"<div class='tt-row'><span class='tt-k'>Cost basis</span><span>KES {h['cost_basis']:,.0f}</span></div>",
            f"<div class='tt-row'><span class='tt-k'>Market value</span><span>KES {h['market_value']:,.0f}</span></div>",
            f"<div class='tt-row'><span class='tt-k'>Gain / loss</span>"
            f"<span style='color:{gc}'>KES {h['gain']:+,.0f}</span></div>",
        ]
        if h.get('day_change_pct') is not None:
            dc = '#4ade80' if h['day_change_pct'] >= 0 else '#f87171'
            p.append(f"<div class='tt-row'><span class='tt-k'>Today</span>"
                     f"<span style='color:{dc}'>{h['day_change_pct']:+.2f}%</span></div>")
        if h.get('dps_fy'):
            p.append(f"<div class='tt-row'><span class='tt-k'>Est. annual dividend</span>"
                     f"<span>KES {(h.get('est_annual_dividend') or 0):,.0f}</span></div>")
        if h.get('score') is not None:
            oc = '#22c55e' if h['score'] >= 70 else '#f59e0b' if h['score'] >= 45 else '#ef4444'
            p.append(f"<div class='tt-row'><span class='tt-k'>Factor score</span>"
                     f"<span style='color:{oc};font-weight:800'>{h['score']}/100</span></div>")
            reasons = (h.get('score_detail') or {}).get('reasons') or {}
            allr = []
            for key in ('value', 'quality', 'momentum', 'dividend', 'liquidity'):
                allr += (reasons.get(key) or [])
            if allr:
                p.append(f"<div class='tt-note'>Why: {' · '.join(_tip_text(r) for r in allr[:5])}</div>")
        p.append(f"<div class='tt-note'>{_tip_text(h['tv_signal_label'])} — TradingView's technical rating, "
                 "the same one shown throughout this dashboard. Not personalized advice.</div>")
        return "".join(p).replace("&", "&amp;").replace('"', "&quot;")

    # ==================================================================
    # MY BONDS — private Treasury/Infrastructure bond holdings.
    # Lives on the SAME private 💼 My Portfolio page as the stock holdings
    # above (appended after them) — never its own page, never public.
    # ==================================================================
    @staticmethod
    def _bonds_explainer_facts(avail):
        """The holding-specific part of "📘 Understanding Your Bonds": the
        intro line plus the first six explainer cards, with every sentence
        about *your* bonds (how many are IFBs, which are taxed, which repay
        in one go, which the small-holder rule accelerates) worked out from
        the computed bonds in `avail`. Returns the opening of the
        explain-grid; the caller appends the general cards and closes it."""
        words = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
                 'eight', 'nine', 'ten', 'eleven', 'twelve']

        def num(n):
            return words[n] if 0 <= n < len(words) else str(n)

        def names(bs):
            items = [escape(b['issue']) for b in bs]
            return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]

        def kes(v):
            return f'KES {v:,.0f}'

        n = len(avail)
        ifbs = [b for b in avail if b.get('type') == 'IFB']
        taxed = [b for b in avail if not b.get('tax_free')]
        accelerated = [b for b in avail if b.get('small_holder_accelerated')]
        tranches = [b for b in avail if not b.get('small_holder_accelerated')
                    and len(b.get('redemption_structure') or []) > 1]
        bullets = [b for b in avail if not b.get('small_holder_accelerated')
                   and len(b.get('redemption_structure') or []) <= 1]

        intro = ('<p class="page-intro">A quick primer on the concepts below, written for exactly '
                 f'the {num(n)} bond{"" if n == 1 else "s"} you hold.</p>')

        if n and len(ifbs) == n:
            ifb_line = ('Your bond is an IFB.' if n == 1 else 'Both of your bonds are IFBs.' if n == 2
                        else f'All {num(n)} of your bonds are IFBs.')
        elif not ifbs:
            ifb_line = 'None of your bonds is an IFB.'
        else:
            ifb_line = (f'{num(len(ifbs)).capitalize()} of your {num(n)} bonds '
                        + ('is an IFB.' if len(ifbs) == 1 else 'are IFBs.'))

        if taxed:
            rates = ', '.join(f'{escape(b["issue"])} {b.get("withholding_pct", 0):g}%' for b in taxed)
            fxd_line = (f'Your {names(taxed)} '
                        + ('is a standard taxable Treasury bond. ' if len(taxed) == 1
                           else 'are standard taxable Treasury bonds. ')
                        + 'Withholding tax on Treasury bond interest is <strong>10% for tenors of 10 '
                        'years or more</strong> (15% for shorter tenors). The rate used here — '
                        f'{rates} — comes from each bond\'s CBK prospectus, linked on its card below.')
        else:
            fxd_line = ('None of your bonds is taxed. Should you buy one, withholding tax on Treasury '
                        'bond interest is <strong>10% for tenors of 10 years or more</strong> (15% for '
                        'shorter tenors).')

        amort_bits = []
        if bullets:
            each = '; '.join(f'{escape(b["issue"])}: full {kes(b["face_value"])} back only at '
                             f'maturity in {escape(str(b.get("legal_maturity_date", ""))[:4])}'
                             for b in bullets)
            amort_bits.append(
                f'Your {names(bullets)} '
                + ('is NOT amortized — it\'s a plain bullet bond (' if len(bullets) == 1
                   else 'are NOT amortized — plain bullet bonds (')
                + each + ').')
        if tranches:
            amort_bits.append(
                f'Your {names(tranches)} '
                + ('repays' if len(tranches) == 1 else 'repay')
                + ' principal in tranches — see the full schedule further down.')
        if accelerated:
            amort_bits.append('The small-holder rule (next card) changes this for '
                              f'your {names(accelerated)}.')

        if accelerated:
            faces = [b['face_value'] for b in accelerated]
            if len(faces) > 1 and len(set(faces)) == 1:
                amounts = f'{kes(faces[0])} each'
            else:
                amounts = ' and '.join(kes(v) for v in faces) if len(faces) <= 2 else \
                    ', '.join(kes(v) for v in faces[:-1]) + ' and ' + kes(faces[-1])
            from bonds_portfolio import SMALL_HOLDER_THRESHOLD
            margin = 'well under' if max(faces) <= SMALL_HOLDER_THRESHOLD / 2 else 'under'
            k = len(accelerated)
            small_title = '🎯 The small-holder rule — the single biggest finding here'
            small_body = (
                f'CBK\'s prospectus{"" if k == 1 else "es"} for your {names(accelerated)} '
                + ('states' if k == 1 else ('both state' if k == 2 else 'all state'))
                + ': <em>"Any amounts up to Kshs. 1.0 million per CSD account at amortization will be '
                f'redeemed in full."</em> Your holding{"" if k == 1 else "s in " + ("both" if k == 2 else "all")} '
                f'({amounts}) {"is" if k == 1 else "are"} {margin} that threshold — so instead of the '
                'printed multi-tranche schedule, CBK pays you <strong>100% of your principal at the FIRST '
                'amortization date</strong>, years earlier than the bond\'s advertised final maturity. This '
                'is reflected throughout this section as each bond\'s "Effective Redemption Date."')
        else:
            small_title = '🎯 The small-holder rule'
            small_body = (
                'Some CBK Infrastructure Bond prospectuses state: <em>"Any amounts up to Kshs. 1.0 '
                'million per CSD account at amortization will be redeemed in full."</em> Where that '
                'applies, CBK pays <strong>100% of your principal at the FIRST amortization date</strong> '
                'instead of in tranches. None of your current bonds qualifies, so each follows its '
                'printed schedule.')

        return (
            intro +
            '<div class="explain-grid">'
            '<div class="explain-card"><h4>🟢 Infrastructure Bonds (IFB) are tax-free</h4>'
            '<p>Under the Income Tax Act, interest from Kenyan Infrastructure Bonds is <strong>fully '
            f'exempt</strong> from withholding tax — every shilling of coupon is yours. {ifb_line}</p></div>'
            '<div class="explain-card"><h4>🔵 Fixed-coupon Bonds (FXD) are taxed</h4>'
            f'<p>{fxd_line}</p></div>'
            '<div class="explain-card"><h4>📅 Coupons are paid semi-annually</h4>'
            '<p>Every bond here pays interest twice a year, on fixed dates set at issuance. The payment '
            'amount is always <em>face value × coupon rate ÷ 2</em> — it doesn\'t fluctuate with market '
            'yields once you own the bond; only the coupon <em>rate</em> was set once, at issuance/auction.</p></div>'
            '<div class="explain-card"><h4>⏳ Accrued interest</h4>'
            '<p>Interest builds up daily between coupon dates. This dashboard uses CBK\'s own convention '
            '— Actual/365 on the annual coupon rate — reverse-verified against real "Accrued Interest (AI)" '
            'figures CBK itself publishes in its prospectuses.</p></div>'
            '<div class="explain-card"><h4>🧩 Amortization — principal paid back early</h4>'
            '<p>Unlike a typical bond that repays 100% of principal only at maturity, Kenyan '
            'Infrastructure Bonds are usually <strong>amortized</strong>: a chunk of your principal comes '
            'back on one or more dates <em>before</em> the final maturity date, printed right on the '
            'prospectus (e.g. "50% in 2027, 30% in 2029, 20% in 2030"). '
            + ' '.join(amort_bits) + '</p></div>'
            f'<div class="explain-card"><h4>{small_title}</h4><p>{small_body}</p></div>')

    # ==================================================================
    # MY INTERNATIONAL PORTFOLIO — private US-listed (USD) stock holdings.
    # Lives on the SAME private 💼 My Portfolio page as stocks/bonds above
    # — never its own page, never public. See international_portfolio.py.
    # ==================================================================
    def _intl_holding_tip(self, h):
        """Rich hover preview for one international holding — same shape as
        _portfolio_holding_tip, but USD figures + Wall Street analyst
        consensus (real, sourced third-party opinion) instead of the
        NSE-specific TradingView technical rating."""
        if not h.get('data_available'):
            html = (f"<div class='tt-h'><span>{_tip_text(h['symbol'])}</span>"
                    f"<span style='color:#f87171'>no data today</span></div>"
                    f"<div class='tt-sub'>{h['quantity']:g} shares @ avg ${h['avg_cost']:.2f} "
                    f"(cost ${h['cost_basis']:,.0f})</div>"
                    "<div class='tt-note'>This stock had no live price in today's data pull — "
                    "showing your cost basis only. Try again after the next run.</div>")
            return html.replace("&", "&amp;").replace('"', "&quot;")

        gp = h.get('gain_pct')
        gc = '#4ade80' if (gp or 0) >= 0 else '#f87171'
        p = [
            f"<div class='tt-h'><span>{_tip_text(h['symbol'])}</span>"
            f"<span style='color:{gc}'>{gp:+.1f}%</span></div>",
            f"<div class='tt-sub'>{h['quantity']:g} shares · avg cost ${h['avg_cost']:.2f} "
            f"· now ${h['price']:.2f}</div>",
            f"<div class='tt-row'><span class='tt-k'>Cost basis</span><span>${h['cost_basis']:,.0f}</span></div>",
            f"<div class='tt-row'><span class='tt-k'>Market value</span><span>${h['market_value']:,.0f}</span></div>",
            f"<div class='tt-row'><span class='tt-k'>Gain / loss</span>"
            f"<span style='color:{gc}'>{'-' if h['gain'] < 0 else '+'}${abs(h['gain']):,.0f}</span></div>",
        ]
        if h.get('day_change_pct') is not None:
            dc = '#4ade80' if h['day_change_pct'] >= 0 else '#f87171'
            p.append(f"<div class='tt-row'><span class='tt-k'>Today</span>"
                     f"<span style='color:{dc}'>{h['day_change_pct']:+.2f}%</span></div>")
        if h.get('target_mean_price'):
            up = h.get('target_upside_pct')
            uc = '#4ade80' if (up or 0) >= 0 else '#f87171'
            p.append(f"<div class='tt-row'><span class='tt-k'>Analyst target</span>"
                     f"<span style='color:{uc}'>${h['target_mean_price']:.2f} "
                     f"({up:+.0f}%)</span></div>")
        if h.get('score') is not None:
            oc = '#22c55e' if h['score'] >= 70 else '#f59e0b' if h['score'] >= 45 else '#ef4444'
            p.append(f"<div class='tt-row'><span class='tt-k'>Factor score</span>"
                     f"<span style='color:{oc};font-weight:800'>{h['score']}/100</span></div>")
        p.append(f"<div class='tt-note'>{_tip_text(h['recommendation_label'])} — Wall Street's real analyst "
                 f"consensus ({h.get('num_analysts') or 0} analysts), not personalized advice.</div>")
        return "".join(p).replace("&", "&amp;").replace('"', "&quot;")

    def _build_dashboard_pages(self, stocks, gainers, losers, sectors, breadth,
                               bullish, bearish, neutral, total,
                               data_date=None, alerts=None, usd_kes=None,
                               portfolio_summary=None, portfolio_history=None,
                               portfolio_news=None, portfolio_history_tracker=None,
                               bond_portfolio=None, intl_portfolio_summary=None,
                               intl_portfolio_history=None, intl_portfolio_news=None,
                               intl_portfolio_history_tracker=None, nse_results=None,
                               fundamentals_data=None, intl_results=None, intl_fundamentals=None,
                               watchlist_count=None):
        """
        Build the multi-page dashboard: a clean Overview plus grouped detail
        pages (Technicals, Fundamentals, Dividends, Sectors, Data Quality).
        Every piece of data from the old single page is preserved, just moved
        to a related page. Writes all pages and returns the index.html path.
        """
        subtitle = self.page_subtitle(total=total, data_date=data_date, usd_kes=usd_kes)

        # ---- OVERVIEW page (see page_overview.py): today for you, the market
        # pulse and breadth, top movers, sectors, and every stock.
        import datetime as _dt
        import page_market
        import page_overview
        import page_portfolio
        networth = page_portfolio.networth_summary(portfolio_summary, bond_portfolio,
                                                   intl_portfolio_summary, usd_kes)
        events = page_portfolio.coming_up_events(bond_portfolio, portfolio_summary, intl_portfolio_summary,
                                                 fundamentals_data, intl_fundamentals, _dt.date.today())
        overview_body = page_overview.build(
            self, stocks=stocks, gainers=gainers, losers=losers, sectors=sectors, breadth=breadth,
            bullish=bullish, bearish=bearish, neutral=neutral, total=total, nse_results=nse_results,
            networth=networth, events=events, watch_count=watchlist_count)

        # ---- The market pages (see page_market.py) ----
        # Next earnings counts days in Nairobi time; the dividend calendar in local time.
        try:
            from zoneinfo import ZoneInfo
            today_ke = datetime.now(ZoneInfo("Africa/Nairobi")).date()
        except Exception:
            today_ke = datetime.now().date()
        visuals_body = page_market.visuals(self, stocks)
        technicals_body = page_market.technicals(self, stocks)
        fundamentals_body = page_market.fundamentals(self, stocks)
        dividends_body = page_market.dividends(self, stocks, today=datetime.now().date())
        earnings_body = page_market.earnings(self, stocks, today=today_ke)
        sectors_body = page_market.sectors(self, sectors, stocks)
        quality_body = page_market.quality(self, stocks, alerts)

        foreign_body = page_market.foreign(self, self._load_foreign_flows())
        pulse_body = page_market.pulse(self, self._load_market_pulse(
            {s['symbol']: {'daily_change_pct': s.get('change')} for s in stocks}))
        bonds_body = page_market.bonds(self, self._load_treasury())

        # ---- MY PORTFOLIO page (private) — tabs: Summary · Kenyan stocks ·
        # Bonds · International · News (see page_portfolio.py). The "➕ Record
        # a purchase" form and "📝 Your entries" list are drawn into their two
        # panels by the dashboard app (manage.js); without the app, a short
        # note says how to get them.
        import page_portfolio

        def last_closes(results, summary, n=126):
            out = {}
            for h in (summary or {}).get('holdings', []):
                res = (results or {}).get(h['symbol']) or {}
                df = res.get('data')
                if df is not None and 'close' in getattr(df, 'columns', ()):
                    out[h['symbol']] = [float(v) for v in df['close'].tail(n)]
            return out

        portfolio_body = page_portfolio.build(
            self, portfolio_summary=portfolio_summary, bond_portfolio=bond_portfolio,
            intl_portfolio_summary=intl_portfolio_summary, usd_kes=usd_kes,
            portfolio_history=portfolio_history, portfolio_news=portfolio_news,
            portfolio_history_tracker=portfolio_history_tracker,
            intl_portfolio_history=intl_portfolio_history, intl_portfolio_news=intl_portfolio_news,
            intl_portfolio_history_tracker=intl_portfolio_history_tracker,
            fundamentals=fundamentals_data, intl_fundamentals=intl_fundamentals,
            nse_history=last_closes(nse_results, portfolio_summary),
            intl_history=last_closes(intl_results, intl_portfolio_summary))

        # ---- Assemble & write all pages ----
        pages = {
            'index.html': self._page_shell('NSE Dashboard — Overview', 'index.html', subtitle, overview_body, private=bool(networth)),
            'portfolio.html': self._page_shell('NSE — My Portfolio', 'portfolio.html', subtitle, portfolio_body),
            'visuals.html': self._page_shell('NSE — Visuals', 'visuals.html', subtitle, visuals_body),
            'technicals.html': self._page_shell('NSE — Technicals', 'technicals.html', subtitle, technicals_body),
            'fundamentals.html': self._page_shell('NSE — Fundamentals', 'fundamentals.html', subtitle,
                                                  fundamentals_body),
            'dividends.html': self._page_shell('NSE — Dividends', 'dividends.html', subtitle, dividends_body),
            'earnings.html': self._page_shell('NSE — Next Earnings', 'earnings.html', subtitle, earnings_body),
            'sectors.html': self._page_shell('NSE — Sectors', 'sectors.html', subtitle, sectors_body),
            'foreign.html': self._page_shell('NSE — Foreign Flows', 'foreign.html', subtitle, foreign_body),
            'pulse.html': self._page_shell('NSE — Market Pulse', 'pulse.html', subtitle, pulse_body),
            'bonds.html': self._page_shell('NSE — Government Bonds', 'bonds.html', subtitle, bonds_body),
            'quality.html': self._page_shell('NSE — Data Quality', 'quality.html', subtitle, quality_body),
        }
        for filename, html in pages.items():
            self.write_page(filename, html)
        logger.info(f"Dashboard saved: {len(pages)} pages — {', '.join(pages.keys())}")
        return os.path.join(self.output_dir, 'index.html')


    def _render(self, template_name, data):
        """Render a Jinja2 template, falling back to inline if file missing."""
        try:
            template = self.env.get_template(template_name)
            return template.render(**data)
        except Exception as e:
            logger.warning(f"Template {template_name} not found: {e}")
            return self._fallback_html(template_name, data)

    def _fallback_html(self, template_name, data):
        """Minimal fallback HTML when template files are missing."""
        return f"""<!DOCTYPE html><html><head><meta name="render-fallback" content="1"><title>{template_name}</title>
        <style>body{{font-family:sans-serif;margin:40px;}}
        table{{border-collapse:collapse;width:100%}}
        td,th{{border:1px solid #ddd;padding:8px;text-align:left}}
        .bullish{{color:green}}.bearish{{color:red}}</style></head>
        <body><h1>{template_name}</h1><p>Generated at {data.get('generated_at', 'N/A')}</p>
        <pre>{escape(json.dumps({k: str(v) for k, v in data.items() if k != 'charts'}, indent=2, default=str))}</pre>
        </body></html>"""

    def _save_report(self, prefix, html_content, report_type):
        """
        Save HTML content and optionally generate PDF.

        Returns:
            str or tuple of path(s).
        """
        html_path = os.path.join(
            self.output_dir, f"{prefix}_{self.timestamp}.html"
        )
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info(f"Saved HTML: {html_path}")

        if report_type == 'html':
            return html_path

        if report_type in ('pdf', 'both'):
            pdf_path = self._generate_pdf(html_content, prefix)
            if pdf_path:
                return (html_path, pdf_path) if report_type == 'both' else pdf_path
            return html_path if report_type == 'both' else None

        return html_path

    def _generate_pdf(self, html_content, prefix):
        """Generate PDF from HTML, gracefully handling failures."""
        if not WEASYPRINT_AVAILABLE:
            logger.warning(
                "PDF generation skipped — WeasyPrint not available. "
                "Run: brew install pango glib"
            )
            return None

        try:
            pdf_path = os.path.join(
                self.output_dir, f"{prefix}_{self.timestamp}.pdf"
            )
            # The dashboard-app stylesheet only exists when served by app.py — a
            # PDF never needs it (and WeasyPrint would warn it can't resolve it).
            html_content = html_content.replace('<link rel="stylesheet" href="/app-assets/manage.css">', '')
            HTML(string=html_content).write_pdf(pdf_path)
            logger.info(f"Saved PDF: {pdf_path}")
            return pdf_path
        except Exception as e:
            logger.error(f"PDF generation failed: {e}")
            return None


# ---- Test ----
if __name__ == "__main__":
    import numpy as np
    import pandas as pd
    from logger import setup_logging
    setup_logging()

    # Generate sample data
    dates = pd.date_range('2025-06-01', periods=60, freq='B')
    np.random.seed(42)
    close = np.random.randn(60).cumsum() + 100
    data = pd.DataFrame({
        'open': close + np.random.randn(60) * 0.5,
        'high': close + abs(np.random.randn(60)) * 2,
        'low': close - abs(np.random.randn(60)) * 2,
        'close': close,
        'volume': np.random.randint(50000, 500000, 60),
    }, index=dates)

    # Quick analysis
    from analysis_engine import AnalysisEngine
    engine = AnalysisEngine()
    result = engine.analyze_stock(data)

    # Generate report
    rg = ReportGenerator()
    path = rg.generate_stock_report('SCOM', result, report_type='html')
    print(f"\nReport: {path}")
    print("Open it in your browser to verify!")