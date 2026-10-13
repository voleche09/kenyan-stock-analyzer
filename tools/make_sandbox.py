#!/usr/bin/env python3
"""
Create a throw-away sandbox with a made-up portfolio, for testing the
dashboard without touching your real files.

Everything in it is placeholder data — tickers nobody needs to own, made-up
bonds (tools/fake_bonds.py), made-up mutual funds (tools/fake_funds.py) and round
example amounts — so pages built from it are safe to screenshot or share. Point
the pipeline at it with environment variables (BOND_REFERENCE_EXTRA makes the
made-up bonds known; FUND_DATA_FILE supplies the made-up funds' prices):

  python tools/make_sandbox.py /tmp/nse-sandbox
  PORTFOLIO_DIR=/tmp/nse-sandbox/portfolio REPORT_DIRECTORY=/tmp/nse-sandbox/reports \\
  CACHE_DIR=/tmp/nse-sandbox/data LOG_FILE=/tmp/nse-sandbox/logs/analyzer.log \\
  BOND_REFERENCE_EXTRA=/tmp/nse-sandbox/bond_reference.json \\
  FUND_DATA_FILE=/tmp/nse-sandbox/fund_market.json \\
      ./venv/bin/python3 main.py --no-email --detailed

The holdings deliberately cover the awkward cases too: two purchases of one
stock, a ticker with no market data, a bond issue with no reference terms,
an international stock quoted in pence, and a watchlist note with HTML in it.
And some sales: part of a stock, all of one (at a loss), part of an
international stock, part of a bond and some units of a fund.
"""

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_bonds                      # noqa: E402
import fake_funds                      # noqa: E402

HOLDINGS = """symbol,quantity,buy_price,buy_date,note
EQTY,200,45.10,2025-11-03,
EABL,50,160.00,2026-02-10,
ABSA,300,15.20,2025-06-01,first lot
ABSA,100,21.50,2026-08-20,top-up
BKG,400,32.00,2026-03-15,
CARB,150,18.00,2026-04-01,
ZZZZ,10,5.00,2026-01-05,ticker with no market data
"""

BONDS = """issue,face_value,purchase_price_pct,purchase_date,note
IFB9/2021/7,250000,100,2024-01-10,example amortizing IFB
IFB9/2020/15,80000,99.5,2024-05-20,example small-holder IFB
FXD9/2020/020,400000,101.2,2023-02-01,
FXD9/2099/001,50000,100,2025-01-01,issue with no reference terms
"""

INTERNATIONAL = """symbol,quantity,buy_price,buy_date,note
AAPL,10,190.25,2025-01-15,
MSFT,5,410.00,2025-03-01,
NVDA,1.5,120.00,2025-06-10,
"""

WATCHLIST = """symbol,market,buy_below,sell_above,note,added,added_price
EQTY,NSE,40,120,"Waiting for the dividend, then decide",2026-09-01,
EABL,NSE,,,,2026-09-15,
ABSA,,30,,blank market -> auto NSE,,
CARB,NSE,15,25,,2026-08-01,17.5
AAPL,INTL,300,400,<script>alert(1)</script> & co,2026-09-01,
MSFT,INTL,,500,already held,2026-07-01,
VOD.L,INTL,60,90,quoted in pence,2026-09-10,
RELIANCE.NS,INTL,,,Indian listing,2026-09-20,
"""


def _sales(today):
    """sales.csv, dated relative to today so every sale comes after its purchase."""
    ago = lambda days: (today - dt.timedelta(days=days)).isoformat()     # noqa: E731
    return ("date,kind,code,quantity,sell_price,fees,note\n"
            f"{ago(20)},nse,EQTY,50,52.00,30,example partial sale\n"
            f"{ago(35)},nse,BKG,400,29.50,45,example: sold all of it - at a loss\n"
            f"{ago(50)},intl,NVDA,0.5,180.00,,\n"
            f"{ago(60)},bond,FXD9/2020/020,100000,102.5,,example: part of a bond\n")


def _fund_sales(today):
    """A fund sale (units only — the dashboard prices it at that day's price)."""
    return ("date,isin,type,amount,units,price,fee_pct,status,ref,note\n"
            f"{(today - dt.timedelta(days=20)).isoformat()},{fake_funds.FUNDS[1]['isin']},sell,,10,,,pending,,"
            "example fund sale\n")


def _cost(csv_text, symbols):
    """What the holdings in csv_text cost (the symbols given only) — the
    history's cost basis, so look-backs compare like with like."""
    total = 0.0
    for line in csv_text.strip().splitlines()[1:]:
        sym, qty, price = line.split(",")[:3]
        if sym in symbols:
            total += float(qty) * float(price)
    return round(total, 2)


def _history(per_symbol_today, cost, days=75, step=5, drift=0.0015):
    """Synthetic snapshots going back `days` days, so look-back returns
    (1W / 1M / 1Y) have something to compare against. `cost` is the
    holdings' cost basis — the same on every day (no purchases in between)."""
    today = dt.date.today()
    rows = []
    for back in range(days, -1, -step):
        factor = (1 - drift) ** back
        values = {s: round(v * factor, 2) for s, v in per_symbol_today.items()}
        mv = round(sum(values.values()), 2)
        rows.append({"date": (today - dt.timedelta(days=back)).isoformat(),
                     "cost_basis": cost, "market_value": mv,
                     "gain_pct": round((mv - cost) / cost * 100, 2), "per_symbol": values})
    return rows


def main(target):
    portfolio = os.path.join(target, "portfolio")
    for sub in ("portfolio", "reports", "data", "logs"):
        os.makedirs(os.path.join(target, sub), exist_ok=True)
    files = {"holdings.csv": HOLDINGS, "bonds.csv": BONDS,
             "international_holdings.csv": INTERNATIONAL, "watchlist.csv": WATCHLIST}
    for name, text in files.items():
        with open(os.path.join(portfolio, name), "w", encoding="utf-8") as f:
            f.write(text)
    nse = {"EQTY": 9800.0, "EABL": 9100.0, "ABSA": 7600.0, "BKG": 14000.0, "CARB": 3200.0}
    intl = {"AAPL": 2500.0, "MSFT": 2400.0, "NVDA": 280.0}
    hist = {"history.json": _history(nse, _cost(HOLDINGS, nse)),
            "international_history.json": _history(intl, _cost(INTERNATIONAL, intl))}
    for name, rows in hist.items():
        with open(os.path.join(portfolio, name), "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
    with open(os.path.join(portfolio, "funds.csv"), "w", encoding="utf-8") as f:
        f.write(fake_funds.funds_csv())
    with open(os.path.join(portfolio, "sales.csv"), "w", encoding="utf-8") as f:
        f.write(_sales(dt.date.today()))
    with open(os.path.join(portfolio, "fund_transactions.csv"), "w", encoding="utf-8") as f:
        f.write(_fund_sales(dt.date.today()))
    ref = fake_bonds.write_reference(os.path.join(target, "bond_reference.json"))
    funds = fake_funds.write_market(os.path.join(target, "fund_market.json"))
    print(f"Sandbox ready: {os.path.abspath(target)}")
    print(f"Set BOND_REFERENCE_EXTRA={os.path.abspath(ref)} and FUND_DATA_FILE={os.path.abspath(funds)} "
          "when running the pipeline on it.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
