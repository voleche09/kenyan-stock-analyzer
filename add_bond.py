#!/usr/bin/env python3
"""
Add a Treasury/Infrastructure bond holding to your private portfolio
(portfolio/bonds.json).

Usage:
    python3 add_bond.py ISSUE FACE_VALUE [PURCHASE_PRICE_PCT] [PURCHASE_DATE] [--note "text"]

Examples:
    python3 add_bond.py IFB1/2023/6.5 150000
    python3 add_bond.py FXD1/2022/025 250000 100.0 2022-09-23
    python3 add_bond.py IFB1/2024/8.5 50000 --note "bought via CBK DhowCSD"

ISSUE must be a bond this dashboard has verified reference data for — see
BOND_REFERENCE in src/bonds_portfolio.py, each entry cited against its exact
official CBK prospectus. An issue not in that table will still be saved, but
will show up flagged as "unrecognized" on the dashboard rather than with
guessed terms — add reference data for it first if you want full figures.

PURCHASE_PRICE_PCT is optional — the clean price you paid per KES 100 of
face value (defaults to 100.0, i.e. par, which is an assumption, not a
fact — correct it if you know what you actually paid). PURCHASE_DATE is
optional (YYYY-MM-DD) — defaults to the bond's original issuance date.

Nothing here is ever committed to git.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

from config import Config
import bonds_portfolio as B


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

    if len(argv) not in (2, 3, 4):
        die(f"expected 2-4 positional args, got {len(argv)}")

    issue = B._canonical_issue(argv[0])
    if not issue:
        die("issue code cannot be empty")

    try:
        face_value = float(argv[1])
        if face_value <= 0:
            raise ValueError
    except ValueError:
        die(f"face_value must be a positive number, got '{argv[1]}'")

    price_pct = 100.0
    if len(argv) >= 3:
        try:
            price_pct = float(argv[2])
            if price_pct <= 0:
                raise ValueError
        except ValueError:
            die(f"purchase_price_pct must be a positive number, got '{argv[2]}'")

    purchase_date = None
    if len(argv) == 4:
        purchase_date = argv[3]
        try:
            datetime.strptime(purchase_date, "%Y-%m-%d")
        except ValueError:
            die(f"purchase_date must be YYYY-MM-DD, got '{purchase_date}'")

    config = Config()
    bonds = B.add_bond(config.portfolio_dir, issue, face_value, price_pct, purchase_date, note)

    print(f"✓ Added: KES {face_value:,.0f} face value of {issue}"
          f"{f' @ {price_pct:g}% of face' if price_pct != 100.0 else ' (at par)'}"
          f"{f' on {purchase_date}' if purchase_date else ''}")

    if issue in B.BOND_REFERENCE:
        b = B.compute_bond({"issue": issue, "face_value": face_value,
                            "purchase_price_pct": price_pct, "purchase_date": purchase_date,
                            "note": note})
        ne = b.get("next_event")
        if ne:
            print(f"  Next payment: KES {ne['amount']:,.2f} ({ne['type']}) on {ne['date']}")
        tax_label = "tax-free" if b['tax_free'] else f"{b['withholding_pct']:.0f}% withholding tax"
        accel_note = " (accelerated — see 🏦 My Bonds for why)" if b['small_holder_accelerated'] else ""
        print(f"  Coupon {b['coupon_pct']:.4f}% · {tax_label} · "
              f"effective redemption {b['effective_redemption_date']}{accel_note}")
    else:
        print(f"  ⚠ '{issue}' has no verified reference data yet — it will show as "
              f"'unrecognized' on the dashboard. Add it to BOND_REFERENCE in "
              f"src/bonds_portfolio.py (cite the CBK prospectus) for full figures.")

    print(f"\nRun ./run.sh to see it reflected in the 🏦 My Bonds section of the "
          f"💼 My Portfolio tab.")


if __name__ == "__main__":
    main()
