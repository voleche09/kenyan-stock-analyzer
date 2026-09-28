#!/usr/bin/env python3
"""
Add an international (US-listed, USD) stock purchase to your private
portfolio (portfolio/international_holdings.json).

Usage:
    python3 add_international_holding.py SYMBOL QUANTITY BUY_PRICE_USD [BUY_DATE] [--note "text"]

Examples:
    python3 add_international_holding.py GOOG 7 264.06
    python3 add_international_holding.py GOOG 7 264.06 2026-09-20
    python3 add_international_holding.py UBER 15 74.88 --note "long-term hold"

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
    lots = IP.add_lot(config.portfolio_dir, symbol, quantity, buy_price, buy_date, note)

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
