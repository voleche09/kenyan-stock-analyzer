# 🇰🇪 Kenyan Stock Analyzer — NSE Daily Dashboard

A fully automated daily stock analysis pipeline for the **Nairobi Securities Exchange (NSE)**. Fetches data from **TradingView** (100% accurate, no guesses), performs technical and fundamental analysis on all 57+ listed stocks, and generates an interactive HTML dashboard with individual stock reports.

## Features

- **All 57 NSE stocks** analyzed daily — not just a watchlist
- **Fundamental analysis** — P/E, PEG, ROE, ROIC, operating/net margins, debt ratios, revenue/EPS growth, market cap, and more
- **Technical analysis** — RSI, MACD, Bollinger Bands, SMA/EMA crossovers, Stochastic, ATR, OBV, support/resistance levels
- **6 charts per stock** — price+SMA+Bollinger, RSI, MACD, volume, stochastic, ATR
- **Plain-English explanations** — every metric explained in simple terms (e.g. "RSI above 70 = overbought, price may pull back")
- **Similar stocks** — peer comparison by sector, market cap, and valuation
- **Sector performance** — sector-by-sector breakdown with average returns
- **Market breadth** — advance/decline, % above SMA50, bullish MACD ratio
- **Excel export** — multi-sheet workbook with all data
- **Clean runs** — old reports and cache files are automatically removed each run

## Quick Start

### Prerequisites

- Python 3.10+
- macOS or Linux (Windows works but PDF generation requires extra setup)

### 1. Set up

```bash
cd kenyan_stock_analyzer
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure (optional)

```bash
cp .env.example .env
# Edit .env if you want email notifications or custom settings
```

### 3. Run

```bash
./run.sh
```

This generates:
- `reports/index.html` — **main dashboard** (open this in your browser)
- `reports/SCOM_report_*.html` — individual stock reports (57 files)
- `reports/nse_analysis_*.xlsx` — Excel export
- `reports/market_summary_*.html` — market summary

### 4. Open the dashboard

```bash
open reports/index.html
```

## Usage

### Daily run (recommended)

```bash
./run.sh
```

### Command-line options

```bash
python main.py                    # All stocks, 6-month data, HTML only
python main.py --detailed         # Generate individual stock reports (57 files)
python main.py --export-excel     # Also export Excel workbook
python main.py --report-type both # Generate HTML + PDF (requires WeasyPrint)
python main.py --period 1y        # Use 1 year of historical data
python main.py --force-refresh    # Skip cache, fetch fresh data
python main.py --watchlist-only   # Only analyze configured watchlist (10 stocks)
```

### Daily automated run (cron)

Add to your crontab (`crontab -e`):

```cron
# Run at 3:00 PM EAT (market close) on weekdays
0 12 * * 1-5 cd /path/to/kenyan_stock_analyzer && ./venv/bin/python main.py --detailed --export-excel >> logs/cron.log 2>&1
```

Nairobi is UTC+3, so 15:00 EAT = 12:00 UTC.

## Project Structure

```
kenyan_stock_analyzer/
├── main.py                     # Entry point — orchestrates the full pipeline
├── run.sh                      # One-command runner (venv + pipeline + open dashboard)
├── scheduler.py                # Optional scheduler for automated daily runs
├── requirements.txt            # Python dependencies
├── .env.example                # Example environment configuration
├── crontab.example             # Cron setup reference
├── README.md
│
├── src/
│   ├── data_acquisition.py     # Fetches OHLCV data from TradingView, NSE PDF, Yahoo
│   ├── analysis_engine.py      # Technical analysis (RSI, MACD, Bollinger, etc.)
│   ├── fundamental_analysis.py # Fundamental data from TradingView scanner
│   ├── report_generator.py     # HTML/PDF reports, Excel export, charts
│   ├── sector_analysis.py      # Sector-level aggregation
│   ├── config.py               # Centralized configuration from .env
│   ├── logger.py               # Logging setup
│   ├── utils.py                # Support/resistance detection, retry decorator
│   └── email_notifier.py       # Email report sending
│
├── templates/
│   ├── base.html               # Base HTML template with shared styles
│   ├── stock_report.html       # Individual stock report template
│   └── market_summary.html     # Market summary template
│
├── reports/                    # Generated reports (cleaned each run)
├── data/                       # Cached data files (cleaned each run)
└── logs/                       # Application logs
```

## Data Sources

All fundamental and technical data is sourced from **TradingView** via the `tvkit` library. Nothing is guessed or estimated.

| Data Type | Source | Method |
|-----------|--------|--------|
| OHLCV price history | TradingView | `tvkit` WebSocket API |
| Fundamental metrics | TradingView | Scanner API (Kenya market) |
| Daily price list (fallback) | NSE PDF | OCR via `pdf2image` + `pytesseract` |
| Historical data (fallback) | Yahoo Finance | `yfinance` |

## Metrics Included

### Technical Indicators
- RSI (14) with overbought/oversold signals
- MACD (12, 26, 9) with crossover detection
- Bollinger Bands (20, 2σ)
- SMA 20 & SMA 50 with golden cross/death cross detection
- EMA 12 & EMA 26
- Stochastic Oscillator (%K, %D)
- ATR (Average True Range)
- OBV (On-Balance Volume)
- Support & Resistance levels

### Fundamental Metrics (from TradingView)
- Market Cap, P/E (TTM), PEG, Price/Book, Price/Sales
- Enterprise Value, EV/Revenue, EV/EBITDA
- EPS (TTM), EPS Growth (YoY)
- Revenue (TTM), Revenue Growth (YoY)
- Net Income, Operating Income, EBITDA
- Gross Margin, Operating Margin, Net Margin, FCF Margin
- ROE, ROA, ROIC
- Debt/Equity, Current Ratio, Quick Ratio
- Total Assets, Total Debt, Net Debt, Total Equity
- Free Cash Flow, Operating Cash Flow, CapEx
- Dividend Yield, Payout Ratio, DPS
- Performance: 1W, 1M, 3M, 6M, YTD, 1Y, 5Y
- Analyst Recommendation (1=Strong Buy, 5=Strong Sell)
- Sector classification

## Requirements

```
tvkit>=1.0.0
yfinance>=0.2.0
pandas>=2.0
numpy>=1.24
matplotlib>=3.7
seaborn>=0.12
jinja2>=3.1
python-dotenv>=1.0
requests>=2.31
beautifulsoup4>=4.12
openpyxl>=3.1
certifi>=2023.0
pytesseract>=0.3
pdf2image>=1.16
weasyprint>=60.0    # Optional: for PDF output
schedule>=1.2       # Optional: for scheduler
```

### System dependencies (macOS)

```bash
# For PDF generation (WeasyPrint)
brew install pango glib

# For NSE PDF OCR (optical character recognition)
brew install tesseract poppler
```

## Troubleshooting

### WeasyPrint (PDF) not working on macOS
```bash
brew install pango glib
```

### Yahoo Finance timeout
Yahoo Finance can be unreliable for NSE stocks. The pipeline tries multiple sources:
1. TradingView (primary — most reliable)
2. NSE PDF (current-day prices via OCR)
3. Yahoo Finance (historical data, multiple ticker formats)

### Email not sending
- Gmail: Use an [App Password](https://myaccount.google.com/apppasswords), not your regular password
- Ensure `SMTP_HOST`, `SMTP_PORT`, `EMAIL_USER`, and `EMAIL_PASSWORD` are set in `.env`

## License

This project is for personal use. Use at your own risk. Not financial advice.