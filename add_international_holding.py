#!/usr/bin/env python3
"""
Add an international (US-listed, USD) stock purchase to your private
portfolio (portfolio/international_holdings.json).

Prefer a spreadsheet? You don't need this script: put your holdings in
portfolio/international_holdings.csv instead (see portfolio/README.md). Once
that file exists it IS your portfolio, and this script will tell you to edit it.

Usage:
    python3 add_international_holding.py SYMBOL QUANTITY BUY_PRICE_USD [BUY_DATE] [--note "text"]

Examples:
    python3 add_international_holding.py AAPL 10 190.25
    python3 add_international_holding.py AAPL 10 190.25 2026-09-20
    python3 add_international_holding.py MSFT 5 410.00 --note "long-term hold"

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
venv_check.require_project_packages("portfolio/international_holdings.csv")

from config import Config
import international_portfolio as IP


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
    if not re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", symbol):
        die(f"'{symbol}' doesn't look like a valid US ticker (1-5 letters, "
            f"optionally a .X share-class suffix)")

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
        lots = IP.add_lot(config.portfolio_dir, symbol, quantity, buy_price, buy_date, note)
    except ValueError as e:      # international_holdings.csv is in use, or the .json is corrupt
        sys.exit(f"Error: {e}")

    print(f"✓ Added: {quantity:g} shares of {symbol} @ ${buy_price:g}"
          f"{f' on {buy_date}' if buy_date else ''}")

    agg = IP._aggregate_lots([l for l in lots if l["symbol"] == symbol])[symbol]
    print(f"  Your {symbol} position is now: {agg['quantity']:g} shares, "
          f"weighted avg cost ${agg['avg_cost']:.2f}, "
          f"total cost basis ${agg['cost_basis']:,.2f} "
          f"(across {len(agg['lots'])} lot{'s' if len(agg['lots']) != 1 else ''})")
    print(f"\nRun ./run.sh to see it reflected in the 🌍 My International Portfolio "
          f"section with today's live price and gain/loss.")


if __name__ == "__main__":
    main()
