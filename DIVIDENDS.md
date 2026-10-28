# Dividend accuracy and refresh behaviour

Public sources reviewed on 22 October 2026. No holdings, quantities, costs or
portfolio reports are stored in the reference data or sent to these sources.

## What the dashboard shows

The stock card shows Type, Next Dividend Date (scheduled cash payment),
Dividend Amount per share, annual Dividend Yield and Payout Ratio. Record/book
closure and ex-dividend dates are separate. A past scheduled payment is history,
not the next payment. A missing announcement is **not evidence of zero dividends**.
Proposed and preliminary terms are labelled; unknown fields remain unconfirmed.

A single interim/final payment never overwrites annual DPS, annual income
estimates or annual yield. An explicit issuer annual total can replace annual
DPS and calculate yield against the current price. That total may itself be
proposed, as labelled. Payout ratio remains the market provider's annual/TTM
metric; this work does not claim it was verified by the dividend announcement.

## Checked issuer/depositary references

Exact source URLs and verification dates are in `src/dividend_reference.py`.

| Issuer | Verified information | Limits |
| --- | --- | --- |
| Kenya Power | Proposed final KES 1.20; proposed annual total KES 1.50 | September issuer press release does not provide payment or book-closure dates; December 31 is not asserted as confirmed. |
| KCB Group | Interim KES 3.00; book closure September 2; payment November 10, 2026 | Its structured dividend register is also refreshed automatically. |
| Equity Group | First/final KES 5.75; record May 22; payment June 30, 2026 | Historical scheduled payment; next date not projected. |
| Co-operative Bank | Final KES 1.50; annual total KES 2.50; record April 30; payment June 5, 2026 | AGM notice proposes the terms; approval not independently checked here. |
| Absa Kenya | Interim KES 0.50; record September 18; payment October 15, 2026 | Historical scheduled payment. |
| NCBA | Interim KES 3.75 | Dates not confirmed by the press release read. |
| Safaricom | Final KES 1.15, ex-div Aug 5, payment Sep 4, 2026 (most recent of two FY2026 payouts; interim KES 0.85 also on file) | Via stockanalysis.com's dividend table, corroborated by Kenyan Wall Street/africanfinancials — not Safaricom's own circular. |
| I&M Group | Final KES 2.25, ex-div Apr 17, payment May 21, 2026 (interim KES 1.50 also on file) | Via stockanalysis.com, corroborated by Kenyan Wall Street. |
| Stanbic Holdings | Interim KES 1.64, ex-div Sep 2, payment Oct 5, 2026 (final KES 18.55 also on file) | Via stockanalysis.com, corroborated by CDSC Kenya, Business Daily and Kenyan Wall Street. |
| Nairobi Securities Exchange Plc | First & final KES 1.00, ex-div May 22, payment Jul 31, 2026 | Via stockanalysis.com. |
| KenGen | First & final KES 0.75, KES 4.94bn total (down from KES 0.90/share, KES 5.94bn, the prior year); book closure October 29; payment January 21, 2027 | Confirmed via CDSC Kenya's corporate-actions registry and corroborated by Business Daily, not KenGen's own circular. A reported ex-dividend date of October 30, 2026 was not independently confirmed and is not shown. |
| Alphabet, classes A/C | Quarterly USD 0.22; record September 7; payment September 14, 2026 | Issuer SEC filing; next quarterly date not projected. |
| Nokia ADR | Last final gross USD 0.046153, US payment August 11, 2026; preliminary next US date November 10 | Citi depositary schedule. Next USD amount left unconfirmed; ordinary shares in Finland have different payment dates. |

Checked but deliberately NOT seeded: HFCB (HF Group) — researched 22 October
2026 and reported to pay no dividend, so no cycle is recorded rather than a
fabricated zero. BKG (BK Group / Bank of Kigali) — cross-listed, dividend
declared in RWF; the KES conversion mechanics for NSE-Kenya shareholders
haven't been independently verified (this name's currency figures were
already flagged as unreliable before this reference table existed).

Other NSE symbols use the secondary myStocks calendar, clearly attributed.
Other international symbols retain market-provider annual metrics and cached
Yahoo dividend event history. Yahoo history dates are ex-dividend dates, not
cash payment dates. This is not comprehensive issuer verification of all stocks.

## Persistence and changes

- Reviewed issuer declarations are saved in the reference module and reused.
- The KCB issuer table, fallback NSE calendar and international dividend history
  are persisted under the existing ignored `data/dividends/` directory. Daily
  quote resets leave this directory intact.
- Normal checks reuse the cache for seven days. NSE/KCB check frequency is
  configurable using `DIVIDEND_REFRESH_DAYS` (default 7). `--force-refresh`
  bypasses those caches in the main pipeline.
- A check is required to discover changes: this is periodic polling, not a
  real-time subscription. Changed data replaces the saved value atomically;
  an unchanged check only advances its checked date. Fetch failure retains
  saved data and backs off for a day.
- KCB's structured issuer table updates automatically, including payment-date
  changes and combined final/special payments. Unknown table layouts fail safe.
- Other issuer snapshots are curated. After seven days the NSE path compares
  with the cached calendar and flags review; a newer calendar event is labelled
  secondary, not issuer-verified. International snapshots flag review after
  seven days. **New company notices, approval decisions, cancellations and
  revisions for these issuers still require review and a reference update.**
- To update a reviewed snapshot, edit the public cycle with the exact source URL,
  amount/currency, separately confirmed dates, approval status and verification
  date. Do not estimate payment dates from last year's pattern.

## Validation

`venv/bin/python test.py` runs the complete suite, including synthetic portfolio
builds, the local HTTP app, cache persistence/refresh/failure tests and dividend
semantics. No real portfolio needs to be rebuilt to run these tests.
