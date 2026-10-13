#!/usr/bin/env python3
"""
Record a sale — some or all of a holding. Everything goes in your private
portfolio files (never committed to git): sales.csv for shares and bonds,
fund_transactions.csv for a fund.

  The easy way — it asks you step by step:
    python3 sell.py

  Or in one line ("all" sells everything you hold):
    python3 sell.py nse  SYMBOL SHARES|all PRICE      [--date YYYY-MM-DD] [--fees AMOUNT] [--note TEXT]
    python3 sell.py intl SYMBOL SHARES|all PRICE_USD  [--date …] [--fees …]
    python3 sell.py bond ISSUE  FACE_VALUE|all PRICE_PCT   (the clean price in % of face value, e.g. 101.25)
    python3 sell.py fund ISIN   UNITS|all [--amount CASH_RECEIVED]

  See what you hold and what you've sold:
    python3 sell.py list

Every sale is checked before it's saved (you can't sell more than you held
on that date) and shown back to you with its profit or loss — nothing is
saved until you say yes. Made a mistake? Remove the row from sales.csv (or
use "Your entries" in the dashboard app).
"""

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import venv_check
venv_check.require_project_packages("portfolio/sales.csv")

from config import Config
import sales

KIND_NAMES = {"nse": "Kenyan stock", "intl": "International stock", "bond": "Bond", "fund": "Fund"}


def _show_held(kind, folder, cache_dir):
    if kind == "fund":
        held = {k: v for k, v in sales.fund_positions(folder, cache_dir).items() if v["units"] > 0.0005}
        return [(isin, f'{p["name"]} — {p["units"]:,.3f} units', p) for isin, p in held.items()]
    held = sales.positions(folder, kind)
    out = []
    for code, p in sorted(held.items()):
        if kind == "bond":
            what = f"KES {p['quantity']:,.2f} face value"
        else:
            what = f"{p['quantity']:,.6g} shares · average cost {sales.money(p['avg_cost'], sales.CURRENCY[kind])}"
        out.append((code, f"{code} — {what}", p))
    return out


def _ask(prompt, default=None):
    try:
        got = input(f"{prompt}{f' [{default}]' if default not in (None, '') else ''}: ").strip()
    except EOFError:
        sys.exit("\nCancelled — nothing was saved.")
    return got or (default if default is not None else "")


def _check(a, folder, cache_dir):
    if a.kind == "fund":
        return sales.check_fund_sale(folder, a.code, a.quantity, a.date, amount=a.amount, note=a.note,
                                     cache_dir=cache_dir)
    return sales.check_sale(folder, a.kind, a.code, a.quantity, a.price, a.date, fees=a.fees, note=a.note)


def _save(a, folder, cache_dir, assume_yes):
    try:
        values, summary, warnings = _check(a, folder, cache_dir)
    except sales.SaleError as e:
        sys.exit(f"✗ {e}")
    print(f"\n🧾 {summary}")
    for w in warnings:
        print(f"⚠️  {w}")
    if not assume_yes and _ask("\nSave this sale? (y/n)", "n").lower() not in ("y", "yes"):
        sys.exit("Not saved.")
    dropped = sales.add_fund_sale(folder, values) if a.kind == "fund" else sales.add_sale(folder, values)
    if dropped:
        print(f"  note: your file has no column for {', '.join(dropped)} — those values weren't saved")
    where = "fund_transactions.csv" if a.kind == "fund" else sales.SALES_FILE
    print(f"✓ Saved to portfolio/{where}. Run ./run.sh (or press 🔄 Update in the app) to see it on My Portfolio.")


def interactive(folder, cache_dir):
    print("Record a sale — press Enter to accept the suggestion in [brackets], Ctrl+C to cancel.\n")
    kinds = list(KIND_NAMES)
    for i, k in enumerate(kinds, 1):
        print(f"  {i}. {KIND_NAMES[k]}")
    choice = _ask("What did you sell? (1-4)")
    if choice not in {str(i) for i in range(1, 5)}:
        sys.exit("Please choose 1, 2, 3 or 4.")
    kind = kinds[int(choice) - 1]
    held = _show_held(kind, folder, cache_dir)
    if not held:
        sys.exit(f"You don't hold any {KIND_NAMES[kind].lower()}s right now.")
    print()
    for i, (_code, label, _p) in enumerate(held, 1):
        print(f"  {i}. {label}")
    pick = _ask(f"Which one? (1-{len(held)})", "1" if len(held) == 1 else None)
    if not pick.isdigit() or not 1 <= int(pick) <= len(held):
        sys.exit("That's not one of the numbers above.")
    code, _label, p = held[int(pick) - 1]
    a = argparse.Namespace(kind=kind, code=code, price=None, fees=None, amount=None, note="")
    unit = {"bond": "face value (KES)", "fund": "units", "nse": "shares", "intl": "shares"}[kind]
    a.quantity = _ask(f"How much did you sell — {unit}, or 'all'", "all")
    if kind == "fund":
        a.amount = _ask("Cash you received in US$ (Enter = work it out from the fund's price)", "") or None
    elif kind == "bond":
        a.price = _ask("Price sold at, in % of face value (100 = par)", "100")
    else:
        a.price = _ask(f"Price per share you sold at ({sales.CURRENCY[kind]})")
    a.date = _ask("Date you sold (YYYY-MM-DD)", dt.date.today().isoformat())
    if kind != "fund":
        a.fees = _ask(f"Fees ({sales.CURRENCY[kind]}, Enter = none)", "") or None
    a.note = _ask("Note (optional)", "")
    _save(a, folder, cache_dir, assume_yes=False)


def cmd_list(folder, cache_dir):
    for kind in KIND_NAMES:
        rows = _show_held(kind, folder, cache_dir)
        print(f"\n{KIND_NAMES[kind]}s you hold:" + ("" if rows else " none"))
        for _c, label, _p in rows:
            print(f"  {label}")
    done = sales.load_sales(folder)
    print("\nSales recorded in sales.csv:" + ("" if done else " none"))
    for line, s in done:
        cur = sales.CURRENCY[s["kind"]]
        price = f"{s['sell_price']:g}%" if s["kind"] == "bond" else sales.money(s["sell_price"], cur)
        print(f"  line {line}: {s['date']}  {s['kind']:<4} {s['code']:<14} {s['quantity']:>12,.6g}  at {price}"
              + (f"  fees {sales.money(s['fees'], cur)}" if s["fees"] else ""))


def main():
    folder, cache_dir = Config().portfolio_dir, Config().cache_dir
    if len(sys.argv) == 1:
        os.makedirs(folder, exist_ok=True)
        try:
            interactive(folder, cache_dir)
        except KeyboardInterrupt:
            sys.exit("\nCancelled — nothing was saved.")
        return
    p = argparse.ArgumentParser(description="Record a sale (private files in portfolio/)",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="kind", required=True)
    for kind in ("nse", "intl", "bond"):
        s = sub.add_parser(kind, help=f"sell a {KIND_NAMES[kind].lower()}")
        s.add_argument("code", help="the ticker, or the bond issue")
        s.add_argument("quantity", help="how many shares (face value for a bond) — or 'all'")
        s.add_argument("price", help="price per share — for a bond, the clean price in %% of face value")
        s.add_argument("--date", default=None, help="the day you sold (default: today)")
        s.add_argument("--fees", default=None, help="brokerage and other charges")
        s.add_argument("--note", default="")
        s.add_argument("--yes", action="store_true", help="save without asking")
    s = sub.add_parser("fund", help="sell units of a fund")
    s.add_argument("code", help="the fund's ISIN")
    s.add_argument("quantity", help="how many units — or 'all'")
    s.add_argument("--amount", default=None, help="the cash you received (default: units × that day's price)")
    s.add_argument("--date", default=None)
    s.add_argument("--note", default="")
    s.add_argument("--yes", action="store_true")
    sub.add_parser("list", help="what you hold and what you've sold")
    a = p.parse_args()
    if a.kind == "list":
        cmd_list(folder, cache_dir)
        return
    a.price = getattr(a, "price", None)
    a.fees = getattr(a, "fees", None)
    a.amount = getattr(a, "amount", None)
    os.makedirs(folder, exist_ok=True)
    _save(a, folder, cache_dir, assume_yes=a.yes)


if __name__ == "__main__":
    main()
