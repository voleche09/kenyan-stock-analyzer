#!/usr/bin/env python3
"""
Your mutual funds, from the terminal — everything goes in your private
portfolio/funds.csv and portfolio/fund_transactions.csv (never committed to
git; you can also edit both in a spreadsheet — see portfolio/README.md).

  Add a fund you hold (copy the figures from your bank statement):
    python3 fund.py add ISIN UNITS AVG_COST --as-of YYYY-MM-DD --nav PRICE --name "Fund name"
                        [--payouts reinvest|cash|none] [--monthly AMOUNT --day DAY] [--fee PCT]
                        [--peers ISIN;ISIN] [--url FUND_PAGE_URL]

  Record a one-off purchase or sale (leave --units out and the dashboard
  works them out from that day's price after the fund's sales charge):
    python3 fund.py buy ISIN AMOUNT [--units UNITS] [--date YYYY-MM-DD]
    python3 fund.py sell ISIN UNITS [--amount CASH] [--date YYYY-MM-DD]

  Your statement arrived — copy its units into an automatic top-up / payout:
    python3 fund.py confirm ISIN REF UNITS        (REF as listed, e.g. topup:2026-03)

  When you first invested (approximate is fine) — for "after inflation":
    python3 fund.py since ISIN YYYY-MM-DD

  See what's recorded:
    python3 fund.py list

Monthly top-ups and payouts are added by the dashboard itself, once each.
"""

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import venv_check
venv_check.require_project_packages("portfolio/funds.csv")

from config import Config
import funds_portfolio as fp
import portfolio_csv as pcsv


def _date(s):
    try:
        return dt.date.fromisoformat(s).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{s}' is not a date like 2026-03-31")


def _positive(s):
    try:
        v = float(str(s).replace(",", ""))
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{s}' is not a number")
    if v <= 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return v


def _isin(s):
    s = s.strip().upper()
    if not fp.valid_isin(s):
        raise argparse.ArgumentTypeError(f"'{s}' is not a valid ISIN (12 characters, as on your statement)")
    return s


def _append(path, fields, row):
    if not os.path.exists(path):
        pcsv.create_csv(path, fields)
    dropped = pcsv.append_row(path, fields, row)
    if dropped:
        print(f"  note: your file has no column for {', '.join(dropped)} — those values weren't saved")


def cmd_add(a, folder):
    if any(f["isin"] == a.isin for f in fp.load_funds(folder)):
        sys.exit(f"{a.isin} is already in funds.csv — record a later purchase with: fund.py buy {a.isin} AMOUNT")
    if a.monthly and not a.day:
        sys.exit("--monthly needs --day (the day of the month the bank invests it, 1–31)")
    print("Checking the fund with its price source…")
    import fund_data
    found = fund_data.ft_lookup(a.isin)
    if found:
        navs = fund_data.ft_history(found["xid"], years=1)
        same = dict(navs).get(dt.date.fromisoformat(a.as_of))
        print(f"  found: {found['name']}" + (f" — latest price {navs[-1][1]} on {navs[-1][0]}" if navs else ""))
        if same is not None:
            ok = abs(same - a.nav) <= 0.0051
            print(f"  your statement's price {a.nav} on {a.as_of}: " + ("matches ✓" if ok else
                  f"the source says {same} ✗ — check you copied the right fund / share class"))
    else:
        print("  not found on FT — it's saved anyway; add --url with the fund's page on its manager's site")
    _append(os.path.join(folder, fp.FUNDS_FILE), fp.FUND_FIELDS, {
        "isin": a.isin, "name": a.name, "units": a.units, "avg_cost": a.avg_cost, "as_of": a.as_of,
        "statement_nav": a.nav, "currency": "USD", "payouts": a.payouts, "monthly_amount": a.monthly,
        "monthly_day": a.day, "fee_pct": a.fee, "peers": a.peers, "factsheet_url": a.url})
    print(f"✓ Added {a.name} ({a.units:g} units) to {fp.FUNDS_FILE}. Run ./run.sh to see it in My Portfolio → Funds.")


def cmd_buy(a, folder, sell=False):
    if not any(f["isin"] == a.isin for f in fp.load_funds(folder)):
        sys.exit(f"{a.isin} isn't in funds.csv yet — add it first with: fund.py add …")
    row = {"date": a.date, "isin": a.isin, "type": "sell" if sell else "buy",
           "amount": a.amount, "units": a.units, "status": "confirmed" if a.units else "pending",
           "note": "added with fund.py"}
    if not sell:
        row["fee_pct"] = next(f["fee_pct"] for f in fp.load_funds(folder) if f["isin"] == a.isin)
    _append(os.path.join(folder, fp.TXN_FILE), fp.TXN_FIELDS, row)
    print(f"✓ Recorded a {'sale' if sell else 'purchase'} on {a.date}"
          + ("" if a.units else " — its units will be worked out once that day's price is published"))


def cmd_confirm(a, folder):
    path = os.path.join(folder, fp.TXN_FILE)
    for line, t in fp.load_transactions(folder):
        if t["isin"] == a.isin and t["ref"] == a.ref:
            pcsv.update_row(path, fp.TXN_FIELDS, line, expect={"ref": a.ref},
                            changes={"units": a.units, "status": "confirmed"})
            print(f"✓ {a.ref}: {a.units:g} units, confirmed — the dashboard won't change it again")
            return
    sys.exit(f"No row {a.ref} for {a.isin} — see the refs with: fund.py list")


def cmd_since(a, folder):
    """Set a fund's invested_since date — adding the column to funds.csv if
    it doesn't have one yet (a safety copy goes to portfolio/backups/ first)."""
    import csv
    path = os.path.join(folder, fp.FUNDS_FILE)
    fund = next((f for f in fp.load_funds(folder) if f["isin"] == a.isin), None)
    if not fund:
        sys.exit(f"{a.isin} isn't in funds.csv")
    if a.date > fund["as_of"]:
        sys.exit(f"{a.date} is after the statement date {fund['as_of']} — the first investment comes before it")
    for line, r in pcsv.read_rows(path, fp.FUND_FIELDS):
        if (r["isin"] or "").strip().upper() == a.isin:
            _new, dropped = pcsv.update_row(path, fp.FUND_FIELDS, line, expect={"isin": r["isin"]},
                                            changes={"invested_since": a.date})
            if not dropped:
                print(f"✓ {fund['name']}: invested since {a.date}")
                return
            break
    # no invested_since column yet: add it, keeping every other cell as it was
    pcsv.backup_file(path)
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    rows[0].append("invested_since")
    for row in rows[1:]:
        row.append(a.date if row and row[0].strip().upper() == a.isin else "")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)
    os.replace(tmp, path)
    print(f"✓ {fund['name']}: invested since {a.date} (added an invested_since column to funds.csv)")


def cmd_list(folder):
    funds = fp.load_funds(folder)
    if not funds:
        print("No funds yet — add one with: fund.py add …")
        return
    for f in funds:
        plan = f" · {f['monthly_amount']:g} on day {f['monthly_day']}" if f["monthly_amount"] else ""
        print(f"{f['isin']}  {f['name']}  ·  {f['units']:g} units on {f['as_of']}  ·  payouts: "
              f"{f['payouts'] or '?'}{plan}  ·  fee {f['fee_pct']:g}%")
    rows = fp.load_transactions(folder)
    if rows:
        print("\nTransactions since your statements:")
        for _l, t in rows:
            print(f"  {t['date']}  {t['isin']}  {t['type']:<8} {t.get('amount') or '':>9}  "
                  f"units {t.get('units') if t.get('units') is not None else '—':<9}  {t['status']:<9}  {t['ref']}")


def main():
    p = argparse.ArgumentParser(description="Your mutual funds (private files in portfolio/)",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add", help="add a fund you hold, from your statement")
    s.add_argument("isin", type=_isin)
    s.add_argument("units", type=_positive)
    s.add_argument("avg_cost", type=_positive)
    s.add_argument("--as-of", required=True, type=_date, help="the NAV date on the statement")
    s.add_argument("--nav", required=True, type=_positive, help="the price per unit the statement shows")
    s.add_argument("--name", required=True)
    s.add_argument("--payouts", choices=("reinvest", "cash", "none"), default="none")
    s.add_argument("--monthly", type=_positive)
    s.add_argument("--day", type=int, choices=range(1, 32), metavar="1-31")
    s.add_argument("--fee", type=float, default=0.0, help="the bank's sales charge in %%")
    s.add_argument("--peers", default="", help="competitor ISINs separated by ;")
    s.add_argument("--url", default="", help="the fund's page on its manager's website")
    for name, sell in (("buy", False), ("sell", True)):
        s = sub.add_parser(name, help=f"record a one-off {'sale' if sell else 'purchase'}")
        s.add_argument("isin", type=_isin)
        if sell:
            s.add_argument("units", type=_positive)
            s.add_argument("--amount", type=_positive)
        else:
            s.add_argument("amount", type=_positive)
            s.add_argument("--units", type=_positive)
        s.add_argument("--date", type=_date, default=dt.date.today().isoformat())
    s = sub.add_parser("confirm", help="copy your statement's units into an automatic row")
    s.add_argument("isin", type=_isin)
    s.add_argument("ref")
    s.add_argument("units", type=_positive)
    s = sub.add_parser("since", help="when you first invested in a fund (for 'after inflation')")
    s.add_argument("isin", type=_isin)
    s.add_argument("date", type=_date)
    sub.add_parser("list", help="show what's recorded")
    a = p.parse_args()
    folder = Config().portfolio_dir
    os.makedirs(folder, exist_ok=True)
    if a.cmd == "add":
        cmd_add(a, folder)
    elif a.cmd in ("buy", "sell"):
        cmd_buy(a, folder, sell=a.cmd == "sell")
    elif a.cmd == "confirm":
        cmd_confirm(a, folder)
    elif a.cmd == "since":
        cmd_since(a, folder)
    else:
        cmd_list(folder)


if __name__ == "__main__":
    main()
