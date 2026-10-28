# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A daily stock analysis pipeline for the Nairobi Securities Exchange (NSE), plus a private "Finances Dashboard" (portfolio, bonds, mutual funds, watchlist) layered on top. It fetches/analyzes data, renders a static HTML dashboard into `reports/`, and optionally serves it via a small local Flask-less `http.server` app with click-to-add forms.

## Commands

```bash
# Setup
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env

# Run the full pipeline (writes reports/*.html)
./run.sh
python main.py                          # all stocks, 6mo data, HTML only
python main.py --detailed               # + individual stock reports (57 files)
python main.py --export-excel           # + Excel workbook
python main.py --report-type both       # HTML + PDF (needs WeasyPrint)
python main.py --watchlist-only         # only STOCK_SYMBOLS + NSE watchlist entries (fast)
python main.py --watchlist-page-only    # rebuild just reports/watchlist.html from today's cache
python main.py --force-refresh          # bypass the daily cache

# The click-to-add local dashboard app (reads/writes portfolio/*.csv)
python app.py                           # http://127.0.0.1:8765
python app.py --no-browser --port 8800 --no-auto-update

# CLI helpers for portfolio entry (same CSV code path as the app)
python add_holding.py / add_international_holding.py / add_bond.py
python sell.py nse EQTY 200 52.50 --fees 85   # record a sale; sell.py list to see holdings
python fund.py add ISIN UNITS AVG_COST ...    # mutual fund ledger

# Tests — unittest, NOT pytest (no pytest.ini/pyproject). Run per-file or let
# test.py discover everything matching test_*.py:
python test.py                          # discovers & runs all test_*.py + itself
python test_sales.py                    # one suite
python -m unittest test_sales.TestSellCLI.test_sell_all -v   # one test

# Daily summary email (separate lean pipeline, used by the GitHub Action)
python send_summary.py
python send_summary.py --ignore-calendar
```

No linter is configured in this repo — don't invent one.

## Architecture

**Entry points, three of them, sharing the same `src/` modules:**
- `main.py` — the full orchestrator. Runs in a strict sequence: fetch → analyze → price-validation/official-close → fundamentals → scoring/alerts → history snapshot → portfolio computations (NSE/bonds/international/funds) → report generation → index dashboard. Each stage is wrapped in its own `try/except` that logs and degrades gracefully — a failure in, say, fund pricing must never take down the rest of the dashboard. Follow that pattern when adding a new stage.
- `app.py` / `src/dashboard_app.py` — a dependency-free `http.server` app for local use only (binds `127.0.0.1`). Serves the already-generated `reports/` pages plus a small JSON API (`app_assets/manage.js` talks to it) for adding/removing watchlist entries, holdings, and sales. It never computes analysis itself — a change triggers `main.py` to re-run in the background (full run after a portfolio edit, `--watchlist-page-only` after a watchlist edit), building into a scratch dir and swapping it in atomically so a broken update never clobbers a working dashboard.
- `send_summary.py` — a separate, shorter pipeline for the daily email PDF, used by `.github/workflows/daily-summary.yml` on GitHub Actions (stateless runner, nothing persisted except one rolling backup artifact).

**`src/` is organized by concern, not by page:**
- Data layer: `data_acquisition.py` (NSE, via `DATA_SOURCES` fallback chain: tradingview → nse_pdf → yahoo_finance), `international_data.py` (Yahoo Finance for non-NSE watchlist/holdings), `fund_data.py` (fund manager sites + FT fallback), `treasury_securities.py` (CBK T-bill/bond data).
- Analysis layer: `analysis_engine.py` (technical indicators), `fundamental_analysis.py`, `sector_analysis.py`, `scoring.py` (the transparent 0–100 factor score + alert generation), `price_validation.py` (cross-checks TradingView vs an independent NSE close and can "anchor" displayed prices to the official close).
- Portfolio layer (each holding type is a parallel module, intentionally not unified into one class): `portfolio.py` (NSE), `bonds_portfolio.py`, `international_portfolio.py`, `funds_portfolio.py`, plus `sales.py` which is shared across all of them — it applies `portfolio/sales.csv` against loaded lots (average-cost basis) before any portfolio's summary is computed. `portfolio_csv.py` is the common CSV read/write/backup layer every entry point (CLI scripts and the dashboard app) goes through — never write to `portfolio/*.csv` any other way.
- Rendering layer: `report_generator.py` orchestrates Jinja rendering; `page_overview.py`, `page_stock.py`, `page_market.py`, `page_portfolio.py`, `page_funds.py` each build the template context for one page family. `templates/base.html` is the single shared layout (sidebar, light/dark mode) — **child templates must not define their own `<style>` in `extra_css`; see the Jinja nested-`<style>` gotcha, it silently drops the first CSS rule.** `ui_kit.py` / `ui_theme.py` hold shared component/HTML helpers; `svg_charts.py` renders the inline charts (no JS charting lib).
- `config.py` — a single `Config` class reading every tunable from `.env` at import time (indicator periods, feature flags like `ENABLE_PRICE_VALIDATION`/`ENABLE_SCORING`/`ENABLE_HISTORY`, directories, ports). Add new settings here rather than reading `os.getenv` elsewhere.

**Privacy is load-bearing, not incidental.** `portfolio/` holds the user's real financial data (NSE holdings, bonds, international stocks, funds, watchlist, sales) as CSVs (preferred) or legacy JSON. `.gitignore` blanket-excludes `portfolio/*.csv` and `portfolio/*.json` (only `*.example.*` and `README.md` are tracked) and `portfolio/backups/`. `reports/`, `data/`, `logs/` are also gitignored (regenerated, and may embed portfolio figures). Anything touching this directory must:
- never log or print real holdings/amounts to anywhere that could be committed;
- use `portfolio_csv.py`'s backup-then-write path, not ad hoc file writes;
- use fake data for tests/fixtures (see `tools/fake_bonds.py`, `tools/fake_funds.py`) — never real holdings, even as an example;
- double-check `git status`/diffs before any commit for an accidentally-tracked private file.

**Caching is date-keyed, not TTL-keyed.** `utils.enforce_daily_cache()` wipes `data/` and `reports/` the first time a new calendar day is seen (or on `--force-refresh`); within the same day, cached fetches are reused as-is. Modules that fetch external data generally accept a `force_refresh` flag and write into `config.cache_dir`.

**Docker is a secondary deployment path** (`Dockerfile`, `docker-compose.yml`, `docker_scheduled_run.py`, `supercronic` via `SCHEDULE_CRON`) for an always-on home-server dashboard, read-only (no dashboard app, edit the CSVs directly). It mirrors the bare-metal code paths exactly — no logic forks between the two.
