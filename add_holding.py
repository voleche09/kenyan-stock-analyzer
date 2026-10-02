#!/usr/bin/env python3
"""
Add a stock purchase to your private portfolio (portfolio/holdings.json).

Prefer a spreadsheet? Keep your holdings in portfolio/holdings.csv instead
(see portfolio/README.md). This script works with either: it adds the new
purchase as a row at the end of holdings.csv if that file exists, otherwise
to holdings.json.

Usage:
    python3 add_holding.py SYMBOL QUANTITY BUY_PRICE [BUY_DATE] [--note "text"]

Examples:
    python3 add_holding.py SCOM 500 34.50
    python3 add_holding.py SCOM 500 34.50 2026-09-20
    python3 add_holding.py KCB 200 92.00 --note "top-up after Q2 results"

Run this every time you buy more of a stock — even one you already hold.
Each purchase is added as its own lot; the dashboard automatically combines
all your lots for a symbol into one row (total shares + a correctly
weighted average cost). Nothing here is ever committed to git.
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

import venv_check
venv_check.require_project_packages("portfolio/holdings.csv")

from config import Config
import portfolio as P
import portfolio_csv


def die(msg):
    print(f"Error: {msg}\n")
    print(__doc__)
    sys.exit(1)


def main():
    argv = sys.argv[1:]
    note = ""
    if "--note" in argv:
        i = argv.index("--note")
        if i + 1 >= len(argv):
            die("--note needs a value")
        note = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    if len(argv) not in (3, 4):
        die(f"expected 3-4 positional args, got {len(argv)}")

    symbol = argv[0].strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,8}", symbol):
        die(f"'{symbol}' doesn't look like a valid NSE ticker (letters/digits only)")

    try:
        quantity = float(argv[1])
        if quantity <= 0:
            raise ValueError
    except ValueError:
        die(f"quantity must be a positive number, got '{argv[1]}'")

    try:
        buy_price = float(argv[2])
        if buy_price <= 0:
            raise ValueError
    except ValueError:
        die(f"buy_price must be a positive number, got '{argv[2]}'")

    buy_date = None
    if len(argv) == 4:
        buy_date = argv[3]
        try:
            datetime.strptime(buy_date, "%Y-%m-%d")
        except ValueError:
            die(f"buy_date must be YYYY-MM-DD, got '{buy_date}'")

    config = Config()
    try:
        lots = P.add_lot(config.portfolio_dir, symbol, quantity, buy_price, buy_date, note)
    except ValueError as e:      # the holdings file is unreadable or malformed
        sys.exit(f"Error: {e}")

    print(f"✓ Added: {quantity:g} shares of {symbol} @ KES {buy_price:g}"
          f"{f' on {buy_date}' if buy_date else ''}")

    # Show the updated aggregate position for this symbol (best-effort — no
    # network call, just arithmetic over what's now in your holdings file).
    agg = P._aggregate_lots([l for l in lots if l["symbol"] == symbol])[symbol]
    print(f"  Your {symbol} position is now: {agg['quantity']:g} shares, "
          f"weighted avg cost KES {agg['avg_cost']:.2f}, "
          f"total cost basis KES {agg['cost_basis']:,.2f} "
          f"(across {len(agg['lots'])} lot{'s' if len(agg['lots']) != 1 else ''})")
    print(f"  {portfolio_csv.saved_to_message(config.portfolio_dir, P.HOLDINGS_CSV_FILE, P.HOLDINGS_FILE)}")
    print(f"\nRun ./run.sh to see it reflected in the 💼 My Portfolio tab "
          f"with today's live price and gain/loss.")


if __name__ == "__main__":
    main()
