# 🇰🇪 Kenyan Stock Analyzer — NSE Daily Dashboard

A fully automated daily stock analysis pipeline for the **Nairobi Securities Exchange (NSE)**. Displays the **NSE official closing price** for each stock (settled after market close), cross-checked against **TradingView**, and uses TradingView for fundamentals and price history. Performs technical and fundamental analysis on all 57+ listed stocks and generates an interactive HTML dashboard with individual stock reports.

> **Accuracy note:** prices shown are the official NSE close from an independent NSE data service, cross-checked against TradingView; where the two disagree (usually thinly-traded stocks) the price is flagged ❗ so you know to confirm it. No free feed is guaranteed accurate to the shilling intraday — for the most reliable numbers, run **after market close (15:00 EAT)**.

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

There are **two ways to run this project** — pick whichever fits your setup. Both produce the identical dashboard from the identical code; nothing about the analysis differs between them.

| | **Bare metal** (below) | **Docker** ([jump to section](#-running-with-docker)) |
|---|---|---|
| Best for | Running it yourself, on demand, on your own machine | An always-on dashboard on a home server/NAS, refreshed on a schedule, reachable from anywhere on your Tailscale network |
| Needs | Python 3.10+ | Docker Engine + Compose |
| Scheduling | Your own cron entry (example below) | Built in (`supercronic`, inside the container) |
| Serves a live web page? | No — you open the generated HTML file yourself | Yes — nginx serves it continuously on a port you choose |

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

## Daily Summary Email (automated, cloud-based)

Get a **1-page PDF summary of key metrics emailed to you every weekday at ~10 pm EAT** — automatically, on GitHub's servers, whether or not your computer is on.

The summary is a subset of the dashboard (the dashboard itself is unchanged) and includes:
- Market pulse — bullish/bearish counts, breadth, USD/KES
- **Data quality** — how many prices were independently verified vs flagged as mismatched/stale
- Your watchlist — TV signal (Buy/Sell), price, change, yield, score
- Top gainers/losers
- **Upcoming ex-dividends** (next 30 days)
- Key alerts (strong Buy/Sell, oversold, high yield, etc.)

### How it works

- `send_summary.py` runs a lean pipeline (data → analysis → fundamentals → price validation → scoring), builds the PDF via `ReportGenerator.generate_summary`, and emails it.
- `.github/workflows/daily-summary.yml` runs `send_summary.py` on **GitHub Actions** at `19:00 UTC` (22:00 EAT), Monday–Friday, plus a manual "Run workflow" button.
- **Skips non-trading days.** Weekends are excluded by the schedule, and `send_summary.py` also checks the **Kenyan public-holiday calendar** (Christmas, Boxing Day, Jamhuri Day, Madaraka Day, Mashujaa Day, Labour Day, New Year, Easter, Eid, etc. via the `holidays` library) — on those days it sends nothing. Manual runs pass `--ignore-calendar`, so you can still test on any day.
- **Files never accumulate on GitHub.** Each run happens on a fresh, throwaway machine and starts by clearing any leftovers. The report is committed nowhere (`reports/`, `data/`, `*.pdf`, `*.xlsx` are git-ignored). The only thing kept is a **single backup artifact** (the latest PDF) — and the workflow **deletes the previous one just before generating the new one**, so at most one ever exists. That backup lets you re-download the report if an email ever fails.

### Non-trading days (no email sent)

The NSE does not trade on weekends or Kenyan public holidays, so there is no new data to report on those days. `send_summary.py` checks the date **in Nairobi time** and sends nothing when the market is closed:

- **Weekends** — Saturday & Sunday (scheduled runs are already limited to Mon–Fri, and the code double-checks).
- **Kenyan public holidays** — resolved automatically via the [`holidays`](https://pypi.org/project/holidays/) library (`holidays.Kenya`):

  | Holiday | Date |
  |---------|------|
  | New Year's Day | 1 January |
  | Good Friday | moves each year (Easter) |
  | Easter Monday | moves each year (Easter) |
  | Labour Day | 1 May |
  | Madaraka Day | 1 June |
  | Idd-ul-Fitr (Eid al-Fitr) | moves each year (Islamic calendar) |
  | Idd-ul-Adha (Eid al-Adha) | moves each year (Islamic calendar) |
  | Utamaduni / Mazingira Day | 10 October |
  | Mashujaa Day | 20 October |
  | Jamhuri Day | 12 December |
  | Christmas Day | 25 December |
  | Boxing Day | 26 December |

**Works for every year, automatically.** The calendar is computed for the current year at run time — nothing is hard-coded to a single year, and the moving holidays (Easter, Eid) are recalculated correctly each year. Occasional one-off public holidays declared by the government are also picked up as the `holidays` library is updated.

**Manual runs always send.** The "Run workflow" button passes `--ignore-calendar`, so you can test on any day (weekend or holiday). Only the automatic scheduled runs respect the calendar.

**Fails open.** If the holiday check is ever unavailable, the report still runs rather than silently going missing.

### Setup (one time)

1. Create a Gmail **App Password** at <https://myaccount.google.com/apppasswords> (requires 2-Step Verification). Do **not** use your normal password.
2. In your GitHub repo, add three **Actions secrets** — *Settings → Secrets and variables → Actions → New repository secret*:

   | Secret name | What it holds |
   |-------------|----------------|
   | `EMAIL_USER` | the Gmail address that sends the report |
   | `EMAIL_PASSWORD` | the 16-character Gmail **app password** |
   | `EMAIL_RECIPIENTS` | comma-separated recipient address(es) |

   > 🔒 **Keep these in GitHub Actions secrets only.** Never commit real credentials to the code, the README, or a tracked `.env`. (`.env` is git-ignored.)

### Run it manually

- **Web:** repo → **Actions** → *Daily NSE Summary Email* → **Run workflow** → branch `main` → **Run**.
- **CLI:** `gh workflow run "Daily NSE Summary Email"` then `gh run watch`.

### Run / test locally

Put `EMAIL_USER`, `EMAIL_PASSWORD`, and `EMAIL_RECIPIENTS` in your **local `.env`** (git-ignored), then:

```bash
ENABLE_EMAIL_NOTIFICATIONS=true python send_summary.py --force-refresh
```

With email disabled it just builds `reports/nse_summary_*.pdf` without sending.

> **Notes:** GitHub's scheduler can start a few minutes late — harmless for a daily digest. Running at 10 pm means the market is closed, so prices reflect the settled daily close. Check your spam folder for the first email and mark it "not spam".

## 🐳 Running with Docker

An alternative to the bare-metal setup above — a self-contained deployment for a home server/NAS/home lab: an **always-on dashboard**, refreshed **automatically every weekday afternoon**, reachable from your phone or laptop over **Tailscale**, that survives reboots and doesn't depend on any one machine being switched on. This is a second **installation method**, not a replacement — the bare-metal steps above keep working exactly as documented, and both can even run side by side (they don't share any state except your `portfolio/` folder, if you point them at the same one).

### Does this actually solve "Docker doesn't run as a cron"?

You're right that it doesn't, and it's worth being precise about what's actually happening, since getting this wrong is how "it's running" becomes "it silently stopped running three weeks ago":

`docker run` / `docker compose up` starts a container and keeps it alive for as long as its **main process** keeps running — it is not itself a scheduler. What makes this work is that the container's main process **is** a scheduler: [**supercronic**](https://github.com/aptible/supercronic), a small, cron-compatible job runner built specifically to be a container's PID 1 (plain cron wants to run as root, needs a syslog daemon to show you its logs, and has known zombie-process issues when used as PID 1 in a container — supercronic avoids all three: it runs as the same non-root user as everything else, logs straight to stdout so `docker compose logs` shows every run, and handles `docker stop`'s SIGTERM correctly). The container never exits between runs; supercronic just sits there, checking the clock, exactly like a real cron daemon on a normal Linux box — because, functionally, it is one.

Reading `docker/entrypoint.sh` end to end will show you the whole mechanism in about 20 lines — there's no magic beyond "a small program whose entire job is to wait for the right minute and then run a command."

**An alternative worth knowing about, if you'd rather not run anything long-lived:** your home-lab host's *own* cron or a `systemd` timer firing `docker compose run --rm generator python3 docker_scheduled_run.py` on a schedule, with the container exiting between runs. That's a perfectly valid, arguably simpler pattern for a pure batch job. It's not what's set up here, because it doesn't give you an *always-browsable* dashboard on its own — you'd still need something serving `reports/` continuously (which brings you back to needing a long-running container anyway), and you asked specifically for the application to be *running*, reachable, at 4pm. The two-service setup below (an always-on `web` container + a scheduled `generator` container) is built for exactly that.

### What you get

- **`generator`** — your app + supercronic. Runs the full pipeline (`main.py --report-type both --export-excel --detailed` — the same invocation `run.sh` uses) once a day, plus once immediately when the container first starts (so you're not staring at an empty page for up to 24 hours — see `RUN_ON_STARTUP` below).
- **`web`** — plain `nginx`, serving whatever `generator` most recently wrote. This is the part that's *continuously running* and reachable — refreshing your browser at any time of day always shows the latest generated dashboard, not a live-updating page.
- Both share your **exact same folder layout** as the bare-metal install (`data/`, `reports/`, `logs/`, `portfolio/`) via bind mounts — nothing is hidden inside a Docker-managed volume you'd need special commands to inspect. `ls reports/` on your host shows you exactly what's being served, same as it always has.

### Prerequisites

- [Docker Engine](https://docs.docker.com/engine/install/) 20.10+ and the **Compose plugin** (`docker compose version` should print something — if you only have the old standalone `docker-compose` binary, the commands below are the same, just with a hyphen: `docker-compose up -d`).
- Works on **amd64 and arm64** (Raspberry Pi 4/5, most NAS boxes, Apple Silicon) — the image is built for both automatically; `docker compose build` picks the right one for your host.
- Outbound internet access from the host (the app fetches from TradingView, CBK, Google News, and a few others — same sources as the bare-metal install, nothing Docker-specific). No inbound access is needed except whatever you choose to expose for browsing the dashboard.

### Quick start

```bash
cd kenyan_stock_analyzer
cp .env.example .env
# Edit .env if you want email notifications too — everything else has a
# sensible default and Docker will run fine even if you skip this step.

docker compose up -d --build
```

That's it — `generator` starts, immediately kicks off a first pipeline run in the background (a few minutes; it's fetching real data for 57+ stocks), and `web` starts serving straight away (showing a friendly "not ready yet" page until that first run finishes).

```bash
# Watch the first run happen:
docker compose logs -f generator

# Once it's done, open:
open http://localhost:8080          # macOS
xdg-open http://localhost:8080      # Linux
```

From another device on your **Tailscale** network, replace `localhost` with your host's Tailscale hostname or IP — see [Accessing it over Tailscale](#accessing-it-over-tailscale) below.

### Configuration

Everything from `.env.example` works exactly as it does bare-metal (`ENABLE_EMAIL_NOTIFICATIONS`, `EMAIL_USER`, etc. — see the [Daily Summary Email](#daily-summary-email-automated-cloud-based) section above for what those do). A few settings are **Docker-specific**:

| Variable | Default | What it does |
|---|---|---|
| `SCHEDULE_CRON` | `0 16 * * 1-5` | When the dashboard refreshes — standard 5-field crontab syntax, interpreted in `Africa/Nairobi` time. The default is 16:00 EAT, weekdays — an hour after the NSE's 15:00 close, once official closing prices have settled (the same reasoning `.env.example`'s `ENABLE_OFFICIAL_CLOSE` comment already gives for the bare-metal path). |
| `RUN_ON_STARTUP` | `true` | Also run once immediately when the container starts, so a fresh deployment isn't empty until the next scheduled slot. Set to `false` if you restart the container often and don't want an extra fetch triggered each time. |
| `PUID` / `PGID` | `1000` / `1000` | The user ID the container writes files as, so it can actually write into the bind-mounted folders below. Run `id -u` and `id -g` on your Docker host — if you get anything other than 1000, set these in `.env` (see [Permissions](#permissions-a-real-gotcha) below). |

To change the schedule, edit `.env` and restart: `docker compose up -d` (no rebuild needed — this is read at container *start*, not baked into the image).

### Volumes, persistence, and privacy

| Host folder | Container path | What's in it | Persists across `docker compose down`? |
|---|---|---|---|
| `./data` | `/app/data` | Cached API responses, daily history log | Yes (until you delete it) |
| `./reports` | `/app/reports` (rw in `generator`, ro in `web`) | The generated dashboard — what you actually browse | Yes |
| `./logs` | `/app/logs` | `analyzer.log` — same file the bare-metal install writes | Yes |
| `./portfolio` | `/app/portfolio` | **Your real stock and bond holdings, if you've set them up** | Yes — **and it must** |

That last row is the one to actually pay attention to. `portfolio/holdings.json` and `portfolio/bonds.json` hold real money figures — what you own, what you paid. Three separate things keep them safe here:

1. **`.dockerignore`** excludes them from the Docker *build context*, so they can never end up baked into an image layer — not even accidentally. An image is a distributable artifact; a bind-mounted folder is not. This project treats that distinction as a hard boundary, not a technicality.
2. **`.gitignore`** already excludes them from git (unchanged from the bare-metal setup) — Docker doesn't touch this at all, it's the same protection either way.
3. They're a **bind mount to your own host filesystem**, not a Docker-managed named volume — `docker compose down -v` (which deletes named volumes) does **not** touch bind-mounted host folders. `docker volume rm`, `docker system prune`, and friends are all no-ops against them. If you truly want them gone, you delete `./portfolio` on the host yourself, same as any other file.

**Back these up like you would any other financial record** — they live on your home-lab host's disk, under your control, exactly as intended.

### Does the built image contain any of your data? Verify it yourself

```bash
docker run --rm kenyan-stock-analyzer:latest sh -c "ls -la /app/portfolio"
```

Should show only whatever was in the *build context* (nothing, on a fresh checkout, or just `holdings.example.json`/`README.md`/`bonds.example.json` if you've kept the repo's tracked files) — never `holdings.json`, `bonds.json`, or either `*_history.json`, even if those exist right next to the Dockerfile on your host. If you ever see them in that `ls` output, stop and open an issue — that would mean `.dockerignore` isn't being honored by your Docker version, which shouldn't happen but is worth knowing how to check for yourself rather than taking on faith.

### Permissions (a real gotcha)

The container runs as a non-root user (UID 1000 by default) for security — it should not run as root, and doesn't. That user needs to be able to *write* into your bind-mounted `./data`, `./reports`, `./logs`, `./portfolio` folders, which are owned by whatever user created them **on the host**.

- If your host user's UID is 1000 (the default first-user UID on most single-user Debian/Ubuntu/Raspberry Pi OS installs — check with `id -u`), this just works, no action needed.
- If it's something else, set `PUID`/`PGID` in `.env` to match (`id -u` / `id -g` on the host), then `docker compose up -d` to apply it.
- If you already ran it once before setting these correctly, fix existing ownership with: `sudo chown -R $(id -u):$(id -g) data reports logs portfolio` on the host, then restart.

Symptom if this is wrong: `PermissionError` in `docker compose logs generator`, and `reports/` staying empty forever even though the container looks "up".

### Accessing it over Tailscale

**The simple way (recommended for almost everyone):** install [Tailscale](https://tailscale.com/download) on the home-lab host itself, the same as you would for any other self-hosted service on that machine. `docker-compose.yml` already publishes the dashboard on port `8080` (`ports: - "8080:80"`) — once Tailscale is running on the host, that port is automatically reachable from any other device on your tailnet at `http://<tailscale-hostname-or-100.x.y.z>:8080`, with zero Docker-specific configuration. This is genuinely all there is to it — Tailscale doesn't need to know or care that the service on the other end happens to be in a container.

To confirm the hostname/IP to use, run `tailscale status` on the host, or check the [Tailscale admin console](https://login.tailscale.com/admin/machines).

If you'd rather **not** expose port 8080 on your regular LAN at all (only over the tailnet), bind it to the host's Tailscale interface specifically instead of all interfaces — change the `ports:` line in `docker-compose.yml` to:
```yaml
ports:
  - "100.x.y.z:8080:80"   # your host's own Tailscale IP, from `tailscale ip -4`
```

<details>
<summary><strong>Advanced/optional: give the container its own Tailscale identity (sidecar)</strong></summary>

If you specifically want the `web` container to have its *own* tailnet machine identity (its own MagicDNS name, its own ACLs, independent of the host) rather than piggybacking on the host's Tailscale, add the official [`tailscale/tailscale`](https://hub.docker.com/r/tailscale/tailscale) image as a third service and have `web` share its network namespace. This is real added complexity (an auth key to manage, `NET_ADMIN` capability, a dedicated Tailscale volume) that most home-lab setups don't need — the host-level approach above already gives you private, encrypted, zero-config access. Only reach for this if you have a specific reason to (e.g. you want per-container Tailscale ACLs). The [Tailscale + Docker Compose guide](https://tailscale.com/kb/1282/docker) covers the sidecar pattern in full if you decide you need it.

</details>

### Common operations

```bash
# Live logs — watch the schedule fire, or a run in progress
docker compose logs -f generator
docker compose logs -f web

# Trigger a run right now, without waiting for the schedule
docker compose exec generator python3 docker_scheduled_run.py --ignore-calendar

# Check the configured schedule and confirm the container's timezone
docker compose exec generator sh -c 'cat /app/.crontab; date'

# Add a stock or bond purchase (identical to bare-metal — same script, same file)
docker compose exec generator python3 add_holding.py SCOM 500 34.50
docker compose exec generator python3 add_bond.py IFB1/2023/6.5 150000

# Restart (e.g. after editing .env)
docker compose up -d

# Stop everything (your data in ./data, ./reports, ./logs, ./portfolio is untouched)
docker compose down

# Update to newer code: pull/checkout the new version, then
docker compose up -d --build

# Full health check
docker compose ps
```

### Docker troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `reports/` stays empty, dashboard shows "not ready yet" forever | First run still in progress (can take several minutes), or it errored | `docker compose logs generator` — look for a Python traceback near the top |
| `PermissionError` in the generator's logs | Bind-mounted folder ownership doesn't match the container's user | See [Permissions](#permissions-a-real-gotcha) above |
| PDF generation fails inside the container | Shouldn't happen — WeasyPrint's system libraries are installed by the Dockerfile — but if it does, `docker compose exec generator python3 -c "import weasyprint"` will show the real error | Open an issue with that error's exact text |
| Container restarts in a loop | Check `docker compose ps` for the reported status, then `docker compose logs generator` for the crash | Usually a missing/invalid `.env` value — compare against `.env.example` |
| Schedule doesn't seem to be firing | Wrong `SCHEDULE_CRON` syntax, or the container's clock/timezone is off | `docker compose exec generator date` should show Nairobi time; re-check `SCHEDULE_CRON`'s 5-field syntax (minute hour day month weekday) |
| Can't reach `:8080` from another device | Tailscale isn't running on the host, or a host firewall is blocking the port even on the tailnet interface | `tailscale status` on the host; confirm `docker compose ps` shows `web` as `Up` and `healthy` |

### How this relates to the other automation in this README

Three independent things, each solving a different problem — you can use any subset of them:

1. **This Docker setup** — an always-on, home-lab-hosted **dashboard** you browse, refreshed on your own schedule, on hardware you control.
2. **[Daily Summary Email](#daily-summary-email-automated-cloud-based)** — a 1-page PDF **emailed** to you nightly, run on GitHub's servers, independent of any machine of yours being on at all. Nothing to do with Docker — it's a separate GitHub Actions workflow.
3. **Bare-metal `./run.sh`** — run it yourself, on demand, whenever you're at your machine.

Nothing about setting up Docker changes or requires touching the GitHub Actions email workflow, and vice versa.

## Project Structure

```
kenyan_stock_analyzer/
├── main.py                     # Entry point — orchestrates the full pipeline
├── send_summary.py             # Builds the 1-page PDF summary and emails it (daily job)
├── docker_scheduled_run.py     # Docker's scheduled job: market-closed check + full pipeline
├── run.sh                      # One-command runner, bare metal (venv + pipeline + open dashboard)
├── scheduler.py                # Optional scheduler for automated daily runs
├── .github/workflows/          # GitHub Actions — daily-summary.yml (scheduled email)
├── Dockerfile                  # Multi-stage image: Python app + supercronic scheduler
├── docker-compose.yml          # generator (scheduled pipeline) + web (nginx) services
├── docker/
│   ├── entrypoint.sh           # Renders the crontab from SCHEDULE_CRON, execs supercronic
│   └── nginx.conf              # Static file server config for the "web" service
├── .dockerignore                # Keeps venv/, .git/ AND your private portfolio/ files out of the image
├── requirements.txt            # Python dependencies
├── .env.example                # Example environment configuration
├── crontab.example             # Cron setup reference (bare-metal)
├── README.md
│
├── src/
│   ├── data_acquisition.py     # Fetches OHLCV data from TradingView, NSE PDF, Yahoo
│   ├── analysis_engine.py      # Technical analysis (RSI, MACD, Bollinger, etc.)
│   ├── fundamental_analysis.py # Fundamental data from TradingView scanner
│   ├── price_validation.py     # Cross-checks prices vs an independent source + freshness
│   ├── market_context.py       # USD/KES rate + per-sector median valuation
│   ├── scoring.py              # Transparent 0-100 factor score + per-stock alerts
│   ├── history_tracker.py      # Appends a daily snapshot for later accuracy review
│   ├── report_generator.py     # HTML/PDF reports, Excel export, charts, summary PDF
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

See [`requirements.txt`](requirements.txt) for the authoritative, version-pinned list (Python packages only — the system libraries below are separate, OS-level installs `pip` can't provide). Summarized:

```
tvkit, yfinance, pandas, numpy, python-dotenv         # core data
matplotlib, seaborn                                    # charts
jinja2, weasyprint, openpyxl                            # HTML/PDF/Excel reports
beautifulsoup4, requests, pdfplumber                    # scraping / PDF text
pytesseract, pdf2image                                  # NSE PDF OCR fallback
rich                                                     # console logging
schedule, holidays                                       # scheduling + Kenyan public-holiday calendar
scipy                                                    # scientific/statistics helpers
```

### System dependencies

Two Python packages above need a matching **system-level** (non-pip) library to actually work:

| Python package | Needs this installed on your OS | What it's for |
|---|---|---|
| `weasyprint` | Pango, Cairo, GDK-Pixbuf | Renders the PDF reports |
| `pytesseract` + `pdf2image` | Tesseract OCR, Poppler | Reads the NSE's daily PDF price list (fallback data source only — TradingView is primary) |

**macOS:**
```bash
brew install pango glib      # WeasyPrint (PDF generation)
brew install tesseract poppler   # NSE PDF OCR fallback
```

**Debian/Ubuntu (and what the Docker image installs automatically — see below):**
```bash
sudo apt-get install -y \
  libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 libffi-dev libjpeg-dev \
  tesseract-ocr poppler-utils
```

Running via **Docker** installs all of this automatically inside the image — see below.

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