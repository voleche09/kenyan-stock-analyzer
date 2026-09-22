# My Portfolio — private, never committed

This folder holds **your real stock holdings**. It is deliberately kept out of
git: `portfolio/holdings.json` and `portfolio/history.json` are listed in
`.gitignore`, so they will never be committed, pushed, or visible to anyone
you share this repo with. Only this README and `holdings.example.json` are
tracked in git.

## Files

| File | Tracked in git? | What it is |
|---|---|---|
| `holdings.json` | **No — private** | Your real purchases (symbol, quantity, price paid). Created for you on first setup. |
| `history.json` | **No — private** | Auto-generated. One snapshot of your total portfolio value per day you run the tool. Powers the daily/weekly/monthly/yearly performance and the value-over-time chart. Never edit this by hand. |
| `holdings.example.json` | Yes | A template showing the format, with fake data. |

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

## Removing or correcting a holding

Open `holdings.json` and delete or edit the relevant lot object. If you've
sold a stock entirely, remove all of its lots. (This is a holdings tracker,
not a trade ledger — it doesn't currently record realized gains from sales;
it only shows what you currently hold.)

## Why nothing is committed to git

Your cost basis and holdings are your private financial information. This
project can be pushed to GitHub, shared, or open-sourced without ever
exposing what you own or what you paid for it — the `.gitignore` rule keeps
`holdings.json` and `history.json` on your machine only.

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
