# My Portfolio — private, never committed

This folder holds **your real stock and bond holdings**. It is deliberately
kept out of git: `holdings.json`, `history.json`, `bonds.json` and
`bonds_history.json` are all listed in `.gitignore`, so they will never be
committed, pushed, or visible to anyone you share this repo with — including
in commit history, pull requests, or merge diffs. Only this README and the
two `*.example.json` templates are tracked in git.

## Files

| File | Tracked in git? | What it is |
|---|---|---|
| `holdings.json` | **No — private** | Your real stock purchases (symbol, quantity, price paid). |
| `history.json` | **No — private** | Auto-generated stock portfolio value snapshots. Never edit by hand. |
| `holdings.example.json` | Yes | A template showing the stock format, with fake data. |
| `bonds.json` | **No — private** | Your real Treasury/Infrastructure bond holdings (issue code, face value, what you paid). See **Bonds** below. |
| `bonds_history.json` | **No — private** | Reserved for future use (bond values don't need daily snapshots the way stock prices do — a bond's cash flows are already fully known in advance). |
| `bonds.example.json` | Yes | A template showing the bond format, with fake data. |

## Stocks

## How to add a stock you just bought

**Easiest — one command, from the project root:**

```bash
python3 add_holding.py SCOM 500 34.50
```

That adds a new lot: 500 shares of SCOM bought at KES 34.50/share, dated
today. Add a purchase date if you want it recorded exactly:

```bash
python3 add_holding.py SCOM 500 34.50 2026-09-20
```

Run it again any time you buy more of a stock you already hold (even the
same symbol) — it does **not** overwrite anything, it adds a new lot. The
dashboard automatically combines all your lots for a symbol into one row
(total shares + a correctly weighted average cost).

**Or edit the file directly.** Open `portfolio/holdings.json` and add a new
object to the `holdings` array:

```json
{"symbol": "SCOM", "quantity": 500, "buy_price": 34.50, "buy_date": "2026-09-20", "note": "optional note"}
```

- `symbol` — the NSE ticker (must match a symbol the dashboard already
  tracks, e.g. SCOM, KCB, EQTY).
- `quantity` — number of shares.
- `buy_price` — price you paid per share, in KES (your cost — not today's
  market price; the dashboard fetches today's price live every time it runs).
- `buy_date` — `"YYYY-MM-DD"`, or `null` if you don't know/remember it.
- `note` — anything you want, purely for your own reference.

## Removing or correcting a stock holding

Open `holdings.json` and delete or edit the relevant lot object. If you've
sold a stock entirely, remove all of its lots. (This is a holdings tracker,
not a trade ledger — it doesn't currently record realized gains from sales;
it only shows what you currently hold.)

## Bonds

Open `portfolio/bonds.json` (create it by copying `bonds.example.json` if it
doesn't exist yet) and add one object per bond holding to the `bonds` array:

```json
{"issue": "IFB1/2023/6.5", "face_value": 150000, "purchase_price_pct": 100.0, "purchase_date": "2023-11-13", "note": "optional note"}
```

- `issue` — the CBK issue code, e.g. `IFB1/2023/6.5`, `FXD1/2022/025`. Must
  be one of the bonds this dashboard has verified reference data for (see
  `BOND_REFERENCE` in `src/bonds_portfolio.py` — the coupon rate, full coupon
  schedule, maturity/redemption structure and tax treatment for each, each
  one cited against the exact official CBK prospectus it was checked
  against). An issue code not in that table shows up flagged as
  "unrecognized" instead of guessed.
- `face_value` — the KES face (nominal) value you hold — what you'll be
  repaid at redemption, and what your coupon is calculated on. Not what you
  paid, unless you bought at exactly par.
- `purchase_price_pct` — optional, the clean price you paid per KES 100 of
  face value (e.g. `101.33` if you paid a small premium at a tap sale).
  Defaults to `100.0` (par) if omitted — this is an assumption, not a fact,
  so correct it if you know what you actually paid; it only affects your
  cost basis and total-return %, never the coupon or redemption amounts.
- `purchase_date` — `"YYYY-MM-DD"`, or `null`/omitted to assume you bought
  at original issuance.
- `note` — anything you want, purely for your own reference.

Unlike stocks, a bond's entire future cash-flow schedule (every coupon date
and amount, and when principal comes back) is fixed and knowable in advance
— so there's no live price to fetch and no `add_holding.py`-style script;
just add the fact of what you hold and the dashboard computes everything
else (accrued interest, next payment, full cash-flow calendar, after-tax
income, running yield) deterministically.

## Why nothing is committed to git

Your cost basis and holdings — stocks and bonds — are your private financial
information. This project can be pushed to GitHub, shared, or open-sourced
without ever exposing what you own or what you paid for it — the
`.gitignore` rules keep `holdings.json`, `history.json`, `bonds.json` and
`bonds_history.json` on your machine only, and out of every commit, PR and
merge from day one.

If you use the GitHub Actions daily-email workflow and want your portfolio
included in *that* automated email too (not just when you run the tool
locally), that requires deliberately storing your holdings as an encrypted
GitHub Actions secret — ask before setting that up, since it moves your data
off your machine (Actions secrets are encrypted, but the fully "committed to
nothing, stored nowhere else" boundary changes at that point).

## What the dashboard computes from this

Everything else — current price, market value, gain/loss, sector, dividend
yield, company ROE, TradingView signal, factor score, news — is **fetched
fresh from the same verified sources used everywhere else in this dashboard**
every time you run it. Nothing about performance is stored or guessed; only
your quantity and cost basis are yours to maintain.
