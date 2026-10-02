# My Portfolio — private, never committed

This folder holds **your real stock, bond and international-stock
holdings**. It is deliberately kept out of git: every real file in here
(`*.csv` other than the `*.example.csv` templates, plus `holdings.json`,
`history.json`, `bonds.json`, `bonds_history.json`,
`international_holdings.json` and `international_history.json`) is listed in
`.gitignore` — and in `.dockerignore`, for the Docker install — so it will
never be committed, pushed, baked into a Docker image, or visible to anyone
you share this repo with, including in commit history, pull requests, or
merge diffs. Only this README and the `*.example.*` templates are tracked.

## Quick start — enter your holdings in a spreadsheet (CSV)

No Python commands, no virtual environment to remember: your holdings live in
plain `.csv` files you edit in Excel, Numbers, Google Sheets, or any text
editor.

**1. Copy the template for each asset class you hold** (from the project folder):

```bash
cp portfolio/holdings.example.csv               portfolio/holdings.csv                # NSE stocks (KES)
cp portfolio/international_holdings.example.csv portfolio/international_holdings.csv  # US-listed stocks (USD)
cp portfolio/bonds.example.csv                  portfolio/bonds.csv                   # Kenya Treasury / Infrastructure bonds (KES)
```

You only need the files for what you actually own — skip the rest.

**2. Open each file in your spreadsheet app, delete the example rows, and add
yours — one row per purchase.** Keep the header row (first line). Column
order doesn't matter.

**3. Save it back as CSV** under the same name in this folder (see
[Saving from your spreadsheet app](#saving-from-your-spreadsheet-app)).

**4. Run the dashboard** (`./run.sh`) and look for lines like these in the
output — they confirm each file was read and how many rows it contained:

```
INFO     Portfolio: loaded 9 lot(s) from holdings.csv
INFO     Intl portfolio: loaded 6 lot(s) from international_holdings.csv
INFO     Bonds: loaded 4 holding(s) from bonds.csv
```

If the count is lower than the number of rows you entered, a `WARNING` just
above it names the file, the line number, and what was wrong with that row.

That's the whole workflow. To record a new purchase later: add a row, save,
re-run.

## The three files

| File (you create it) | Tracked in git? | What it holds | Money columns are in |
|---|---|---|---|
| `holdings.csv` | **No — private** | NSE stock purchases | KES |
| `international_holdings.csv` | **No — private** | US-listed stock purchases | **USD** |
| `bonds.csv` | **No — private** | Kenya Treasury / Infrastructure bond purchases | KES |
| `holdings.example.csv`, `international_holdings.example.csv`, `bonds.example.csv` | Yes | Fake-data templates to copy from | — |

Each is a separate file because each asset class has its own currency and its
own meaning for "price" — so a US stock priced in dollars can never be mixed
up with an NSE stock priced in shillings.

(`history.json` and `international_history.json` are daily value snapshots the
dashboard writes for itself — never edit them by hand. `bonds_history.json` is
reserved and currently unused: a bond's cash flows are fully known in advance,
so it needs no daily snapshots.)

## Column reference

### Stocks — `holdings.csv` (KES) and `international_holdings.csv` (USD)

| Column | Required? | What to put |
|---|---|---|
| `symbol` | **Yes** | The ticker. NSE: e.g. `SCOM`, `KCB`, `EQTY` (a symbol the dashboard already tracks). US: any valid Yahoo Finance ticker, e.g. `AAPL`, `MSFT`. Case doesn't matter. |
| `quantity` | **Yes** | Number of shares bought in this purchase. Fractional shares are fine (`1.2`). |
| `buy_price` | **Yes** | The price you **paid per share** — in KES for `holdings.csv`, **USD** for `international_holdings.csv`. Your cost, not today's price (today's price is fetched live every run). |
| `buy_date` | No | When you bought it. Leave blank if you don't know. See [Dates](#dates). |
| `note` | No | Anything you like, just for your own reference. |

Example (`international_holdings.csv`):

```csv
symbol,quantity,buy_price,buy_date,note
AAPL,10,190.25,2026-03-14,first purchase
AAPL,4,205.10,2026-08-01,topped up
NVDA,1.5,120.00,2026-05-02,fractional shares are fine
```

**One row per purchase.** Buying more of a stock you already hold? Add
another row with the same symbol — the dashboard combines all rows for a
symbol into a single position with a correctly weighted average cost.

### Bonds — `bonds.csv`

| Column | Required? | What to put |
|---|---|---|
| `issue` | **Yes** | The CBK issue code, e.g. `IFB1/2023/6.5`, `FXD1/2022/025`. Must be a bond this dashboard has verified reference data for (see `BOND_REFERENCE` in `src/bonds_portfolio.py` — coupon, full schedule, redemption structure and tax treatment, each cited against its official CBK prospectus). An unknown code is shown flagged as "unrecognized" rather than guessed at. |
| `face_value` | **Yes** | The KES **face (nominal) value** you hold — what you'll be repaid and what your coupon is calculated on. Not what you paid, unless you bought at exactly par. |
| `purchase_price_pct` | No | The clean price you paid **as a percentage of face value** — e.g. `101.33` for a small premium. **Not** an amount in KES. Blank means `100` (par) — an assumption, not a fact, so fill it in if you know it; it only affects your cost basis and total-return %, never the coupon or redemption amounts. |
| `purchase_date` | No | Blank assumes you bought at the original issue. See [Dates](#dates). |
| `note` | No | Anything you like. |

```csv
issue,face_value,purchase_price_pct,purchase_date,note
IFB1/2023/6.5,150000,101.33,2023-12-11,tap sale
FXD1/2022/025,250000,100,,
```

A bond's whole future cash-flow schedule (every coupon date and amount, and
when principal comes back) is fixed and knowable in advance, so all you enter
is what you hold; accrued interest, next payment, the cash-flow calendar,
after-tax income and running yield are all computed for you.

## What the reader accepts (and what it deliberately refuses)

It is forgiving about things that are harmless, and strict about anything that
could silently produce a wrong money figure.

**Forgiving**
- **Header spelling.** Case, spaces and `(units)` are ignored, and common
  alternatives work: `Ticker`, `Qty`/`Shares`, `Avg Price (USD)`,
  `Purchase Date`, `Notes`, … So a broker export with `Instrument`,
  `Position`, `Avg Price` columns loads as-is. (Full alias list:
  `STOCK_LOT_FIELDS` / `BOND_FIELDS` in `src/portfolio_csv.py`.)
- **Extra columns are ignored** — `Market Value`, `Unrealized P&L` and the
  like are never read, on purpose: only what you *paid* is stored, and
  everything else is recomputed fresh. The console tells you which columns it
  ignored.
- **Number formats:** `1234.5`, `1,234.50`, `$1,234.50`, `KES 1,234.50`.
- **Blank rows** and the empty trailing rows spreadsheets add are skipped
  silently. An empty file (or just a header) is simply an empty portfolio.
- **Excel's "CSV UTF-8"** (which starts with a hidden byte-order mark) and
  Windows/Mac line endings.

**Strict — these produce a warning that names the file and line number**
- A **bare `Price` column is *not* accepted as what you paid.** In a broker
  export "Price" is usually *today's* price, which would make every
  gain/loss zero. Name the column `buy_price` (or `Avg Price`, `Avg Cost`,
  `Price Paid`, …).
- **Decimal commas are rejected** (`224,3`) rather than read as 2243. Use a
  dot: `190.25`.
- **A bad or blank required value** (`symbol`, `quantity`, `buy_price` /
  `issue`, `face_value`), or a quantity/price that isn't above zero, **skips
  that one row** with a warning — the rest of your portfolio still loads.
- **A bad optional value** (e.g. an unreadable date) is dropped with a
  warning and the row is kept.
- **Semicolon- or tab-separated files** are refused with instructions —
  re-save as comma-separated.
- **A missing required column** (e.g. no `buy_price`) refuses the whole file,
  listing the header names it will accept and the ones it found.

Warnings show in the console during `./run.sh` and are also written to
`logs/analyzer.log`.

### Dates

`buy_date` / `purchase_date` are optional, but when present they should be
unambiguous. **`YYYY-MM-DD`** (e.g. `2026-09-20`) is always safe. Also
understood: `20 Sep 2026`, `Sep 20, 2026`, `20-Sep-2026`, `2026/09/20`, and
numeric day/month dates **only when they can't be misread** — `20/09/2026`
(20 can't be a month) and `09/20/2026` both work, and so does `05/05/2026`.

A date like `05/06/2026` could be 5 June or 6 May, so it is **not guessed**:
that date is ignored (with a warning), the row is kept, and the dashboard
treats the date as unknown. Spreadsheet apps love to reformat dates as you
type them; the cure is to set the date column's format to **Text / Plain
text** *before* you type dates in, then use `YYYY-MM-DD`.

## Saving from your spreadsheet app

Save with these exact file names, in this `portfolio/` folder, as
**comma-separated** CSV:

- **Excel:** File → Save As → **CSV UTF-8 (Comma delimited) (\*.csv)**.
- **Numbers:** File → Export To → **CSV…** (text encoding UTF-8).
- **Google Sheets:** File → Download → **Comma Separated Values (.csv)**. This
  downloads only the current sheet, so keep one asset class per sheet, then
  move the downloaded file here and rename it (e.g. to `holdings.csv`).

If your Excel uses `;` between columns (common in some regional settings) you
will get a clear "looks semicolon-separated" message — re-save as the CSV
UTF-8 *comma* delimited option above.

## Correcting or removing a holding

Edit the row, or delete it. If you've sold a stock entirely, delete all of its
rows. (This is a holdings tracker, not a trade ledger — it shows what you
currently hold and doesn't record realized gains from sales.)

## Already using the old JSON files?

They keep working. The rule is one line:

> **If the `.csv` exists, it is your portfolio. Otherwise the `.json` is.**

Never both merged — merging two files would silently double-count anything
that appears in both. If both exist, the dashboard uses the CSV and prints a
warning saying the JSON is being ignored.

**To switch:** create the CSV from the template and copy each JSON lot across
— the JSON keys are exactly the CSV column names (`symbol`, `quantity`,
`buy_price`, `buy_date`, `note`; for bonds `issue`, `face_value`,
`purchase_price_pct`, `purchase_date`, `note`). Run `./run.sh`, check your
totals match what you had, then delete or rename the old `.json` file so there
is no doubt which one counts.

### The terminal helper scripts (JSON users only)

`add_holding.py`, `add_international_holding.py` and `add_bond.py` append to
the JSON files. They need the project's Python packages, so run them with the
project's virtual environment from the project folder:

```bash
./venv/bin/python3 add_holding.py SCOM 500 34.50 2026-09-20
./venv/bin/python3 add_international_holding.py AAPL 10 190.25 2026-03-14
./venv/bin/python3 add_bond.py IFB1/2023/6.5 150000
```

(or `source venv/bin/activate` first). Running them with the system `python3`
gives `ModuleNotFoundError: No module named 'dotenv'` — the script will now
print the exact command to use instead. Once a CSV exists for that asset
class the scripts refuse to run and tell you to edit the CSV, because writing
to a JSON file that is being ignored would lose your entry.

## Tracking someone else's portfolio

Anyone who clones this project uses the same templates and gets their own
private `portfolio/` folder — nothing in it ever leaves their machine.

To track a second portfolio (say, a family member's) from the *same* checkout,
point a run at its own portfolio folder **and** its own report folder. Use
locations **outside** the project folder, so there is no chance of them being
committed:

```bash
PORTFOLIO_DIR=/Users/you/Documents/alice-portfolio \
REPORT_DIRECTORY=/Users/you/Documents/alice-reports \
./venv/bin/python main.py --report-type html --detailed
```

Put that person's `holdings.csv` / `international_holdings.csv` / `bonds.csv`
in the portfolio folder (copy the templates from here), then open
`alice-reports/index.html`. Each portfolio folder keeps its own history files.
Leave out `REPORT_DIRECTORY` and the run would overwrite your own dashboard in
`reports/`. (`./run.sh` accepts the same two variables, but it always opens
*your* `reports/index.html` when it finishes.)

## Why nothing is committed to git

Your cost basis and holdings — stocks, bonds and international stocks — are
your private financial information. This project can be pushed to GitHub,
shared, or open-sourced without ever exposing what you own or what you paid
for it. The `.gitignore` / `.dockerignore` rules cover every `.csv` in this
folder except the `*.example.csv` templates (a catch-all on purpose: even a
raw broker export you drop in here is private by default), plus every real
`.json` file listed at the top. They stay on your machine and out of every
commit, PR, merge and Docker image from day one.

Back these files up like any other financial record — they are the only copy
of your cost basis.

If you use the GitHub Actions daily-email workflow and want your portfolio
included in *that* automated email too (not just when you run the tool
locally), that requires deliberately storing your holdings as an encrypted
GitHub Actions secret — ask before setting that up, since it moves your data
off your machine (Actions secrets are encrypted, but the fully "committed to
nothing, stored nowhere else" boundary changes at that point).

## What the dashboard computes from this

Everything else — current price, market value, gain/loss, sector, dividend
yield, company ROE, TradingView signal / analyst consensus, factor score,
news, the combined net worth in KES and USD — is **fetched fresh from the same
verified sources used everywhere else in this dashboard** every time you run
it. Nothing about performance is stored or guessed; only your quantity and
cost basis are yours to maintain. For international stocks the combined
"Net Worth" converts your USD total to KES using the live USD/KES rate (never
a stale or invented one) — if that rate isn't available on a given run, they
are shown in USD only, with a note, rather than guessed.
