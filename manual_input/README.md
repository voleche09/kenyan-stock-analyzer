# Manual data inputs

Data that is not reliably available from any free automated source lives here.
It is intentionally **not** in `data/` so it survives the daily cache reset,
and it is **not** gitignored so your edits can be committed and versioned.

## `foreign_flows.json` — NSE weekly foreign-investor activity

The NSE publishes foreign-investor participation figures in its **Weekly Market
Statistics** bulletin. There is no free, structured, per-stock daily feed for
this data, so we enter it manually once a week. See [NSE Market Statistics](https://www.nse.co.ke/market-statistics/).

### How to update (5 minutes each week)

1. Open <https://www.nse.co.ke/market-statistics/> and download the latest
   **Weekly Market Statistics** PDF (usually published Monday/Tuesday for
   the week just ended).
2. Open `manual_input/foreign_flows.json`.
3. **Prepend** a new object at the top of the `weeks:` list (newest first).
   Copy this as a template:

   ```json
   {
     "week_ending": "YYYY-MM-DD",
     "source_label": "NSE Weekly Market Statistics",
     "source_url": "<direct URL of the PDF you just downloaded>",
     "aggregate": {
       "foreign_participation_pct": 0.0,
       "foreign_buys_kes": 0,
       "foreign_sells_kes": 0,
       "net_foreign_flow_kes": 0
     },
     "top_foreign_buys": [
       {"symbol": "SCOM", "value_kes": 0}
     ],
     "top_foreign_sells": [
       {"symbol": "EABL", "value_kes": 0}
     ]
   }
   ```

4. Fill in every number **exactly** as it appears in the bulletin. Do not
   estimate. If a figure is not in the bulletin, leave it out — the page
   handles missing fields gracefully.
5. `net_foreign_flow_kes = foreign_buys_kes − foreign_sells_kes`
   (positive = net inflow, negative = net outflow).
6. Save the file. The next `./run.sh` will render the new week.

### Rules (to keep the page trustworthy)

- **Every figure must be traceable to the NSE bulletin.** Put its URL in
  `source_url` so a reviewer can verify.
- **Never invent per-stock breakdowns.** If the bulletin lists top-5 buys
  and top-3 sells, list exactly what it prints. Do not fill gaps.
- **Historical rows are permanent.** Add new weeks; do not edit old ones
  unless you find a genuine transcription error, in which case note it in a
  commit message.
- **Commit your updates** so they are versioned and shared across runs.

### Empty state

If `weeks:` is empty (or the file is missing/malformed), the Foreign Flows
tab shows a friendly empty-state message. The rest of the dashboard is
unaffected.
