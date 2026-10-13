"""
🏦 Mutual funds — units you hold in funds bought through your bank, valued
every run at each fund's latest published price per unit (its NAV).

Private, like every holdings file (portfolio/*.csv and *.json are gitignored
AND dockerignored):

  portfolio/funds.csv              one row per fund, copied from your bank
                                   statement — units, average unit cost and
                                   the NAV date they're true at — plus how the
                                   fund is run for you: payouts reinvested or
                                   paid out, a monthly top-up, the sales charge.
  portfolio/fund_transactions.csv  everything after that statement: monthly
                                   top-ups, reinvested and cash payouts, and any
                                   purchase or sale you add yourself.

sync_transactions() writes the automatic rows, each exactly ONCE: every one
carries a unique `ref` (topup:2026-03, payout:2026-03-31), so running twice
on the same day, or catching up after a month the computer was off, never adds
a second copy. Units it works out itself are `estimated` (the amount after the
sales charge ÷ the fund's price for the dealing day); copy the exact units
from your bank statement into the row, mark it `confirmed`, and it is never
changed again.

Money rules:
  - "Invested" is the cash you paid: the statement's units × average cost,
    plus every top-up and purchase in full (the sales charge included —
    it's money that left your account). Reinvested payouts add units but no
    new money: they're return, not investment.
  - "Gain" = value today − invested (what a bank statement calls unrealised
    gain). Payouts paid into your account are counted separately, as income
    received, and are part of the total return.
  - A top-up whose dealing price isn't published yet counts at its cash value
    after the sales charge until it is.
  - A fund with no price this run is listed and left out of the totals,
    never guessed. Returns are total returns (price + payouts, payouts
    reinvested on the ex-date) from the published history; when a paying
    fund's payout history is missing they're labelled price-only.
"""

import bisect
import calendar
import datetime as dt
import math
import os
import re

import portfolio_csv as pcsv
from logger import get_logger

logger = get_logger(__name__)

FUNDS_FILE = "funds.csv"
TXN_FILE = "fund_transactions.csv"

_F = pcsv.Field
FUND_FIELDS = (
    _F("isin", ("fund_isin",), "text", True),
    _F("name", ("fund", "fund_name"), "text", True),
    _F("units", ("current_units", "available_units", "quantity"), "number", True),
    _F("avg_cost", ("avg_unit_cost", "average_unit_cost", "average_cost", "unit_cost"), "number", True),
    _F("as_of", ("nav_date", "statement_date"), "date", True),
    # The price per unit your statement shows for that date — only used to
    # check that the price source is quoting exactly your share class.
    _F("statement_nav", ("nav_bid", "bank_nav", "statement_price"), "number", False),
    _F("currency", ("ccy", "fund_currency"), "text", False),
    _F("payouts", ("distributions", "dividends", "income"), "text", False),
    _F("monthly_amount", ("monthly", "monthly_topup", "topup_amount"), "number", False),
    _F("monthly_day", ("topup_day",), "number", False),
    _F("fee_pct", ("sales_charge", "sales_charge_pct", "fee"), "number", False),
    _F("peers", ("competitors",), "text", False),
    # The fund's page on its manager's website — lets the dashboard read the
    # manager's own prices and payouts, where the dashboard can read them.
    _F("factsheet_url", ("official_url", "fund_url", "url"), "text", False),
    # When you first put money in (approximate is fine) — for "after
    # inflation". With a monthly top-up, the statement's cost is taken as
    # paid in evenly month by month from this date; without, all on it.
    _F("invested_since", ("first_invested", "start_date", "since"), "date", False),
    pcsv._NOTE,
)

TXN_FIELDS = (
    _F("date", ("trade_date", "dealing_date"), "date", True),
    _F("isin", ("fund_isin",), "text", True),
    _F("type", ("kind", "transaction"), "text", True),
    _F("amount", ("cash", "amount_usd"), "number", False),
    _F("units", ("quantity",), "number", False),
    _F("price", ("unit_price", "nav_used"), "number", False),
    _F("fee_pct", ("sales_charge", "sales_charge_pct", "fee"), "number", False),
    _F("status", (), "text", False),
    _F("ref", ("reference",), "text", False),
    pcsv._NOTE,
)

PAYOUTS = {"reinvest": "reinvest", "reinvested": "reinvest", "reinvt": "reinvest", "reinvestment": "reinvest",
           "cash": "cash", "paid": "cash", "payout": "cash", "pay": "cash", "account": "cash",
           "none": "none", "acc": "none", "accumulating": "none", "accumulation": "none", "no": "none"}
TYPES = {"topup": "topup", "top-up": "topup", "top_up": "topup", "monthly": "topup",
         "buy": "buy", "purchase": "buy", "subscription": "buy",
         "sell": "sell", "redemption": "sell", "redeem": "sell",
         "reinvest": "reinvest", "reinvested": "reinvest",
         "payout": "payout", "dividend": "payout", "distribution": "payout"}
STATUSES = ("pending", "estimated", "confirmed")
UNIT_DP = 3                    # banks report units to 3 decimals
SOLD_OUT = 0.0005              # fewer units than this left = the whole fund was sold


# ------------------------------------------------------------------ small helpers
def valid_isin(code):
    """2 letters + 9 letters/digits + 1 check digit, and the check digit adds up."""
    code = (code or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", code):
        return False
    digits = "".join(str(int(c, 36)) for c in code[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return (10 - total % 10) % 10 == int(code[-1])


def isin_check_digit(body):
    """The check digit for the first 11 characters of an ISIN (used by the
    tests and the sandbox to make well-formed made-up codes)."""
    for c in "0123456789":
        if valid_isin(body + c):
            return c
    raise ValueError(f"no check digit fits {body!r}")


def _date(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def scheduled_day(year, month, day):
    """The `day`th of a month — its last day when the month is shorter (31 → 30 Apr)."""
    return dt.date(year, month, min(int(day), calendar.monthrange(year, month)[1]))


def topup_dates(day, after, through):
    """Every scheduled top-up date strictly after `after`, up to and including `through`."""
    after, through = _date(after), _date(through)
    out, y, m = [], after.year, after.month
    while True:
        d = scheduled_day(y, m, day)
        if d > through:
            return out
        if d > after:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def next_topup(day, today):
    """The first scheduled date after today."""
    return topup_dates(day, today, _date(today) + dt.timedelta(days=62))[0]


def nav_on_or_after(navs, d):
    """(date, nav) of the first published price dated on or after d — the
    price a purchase placed on d is dealt at — or None if not out yet."""
    i = bisect.bisect_left(navs, (_date(d), -math.inf))
    return navs[i] if i < len(navs) else None


def nav_on_or_before(navs, d):
    i = bisect.bisect_right(navs, (_date(d), math.inf))
    return navs[i - 1] if i else None


# ------------------------------------------------------------------ loading
def load_funds(portfolio_dir):
    """[fund dict] from portfolio/funds.csv ([] when there isn't one)."""
    path = os.path.join(portfolio_dir, FUNDS_FILE)
    if not os.path.exists(path):
        return []
    try:
        rows = pcsv.read_rows(path, FUND_FIELDS)
    except pcsv.CsvFormatError as e:
        logger.warning(f"Funds: {e}")
        return []
    funds, seen = [], set()
    for line, r in rows:
        isin = (r["isin"] or "").strip().upper()
        if not valid_isin(isin):
            logger.warning(f"{FUNDS_FILE} line {line}: '{r['isin']}' is not a valid ISIN (12 characters, "
                           "e.g. as printed on your statement or the fund's factsheet) — skipping this row")
            continue
        if isin in seen:
            logger.warning(f"{FUNDS_FILE} line {line}: {isin} is listed twice — only the first row is used. "
                           "Record later purchases in fund_transactions.csv instead.")
            continue
        if r["units"] is None or r["units"] < 0 or r["avg_cost"] is None or r["avg_cost"] < 0:
            logger.warning(f"{FUNDS_FILE} line {line}: units and avg_cost must be 0 or more — skipping this row")
            continue
        payouts = PAYOUTS.get((r.get("payouts") or "").strip().lower()) if r.get("payouts") else None
        if r.get("payouts") and not payouts:
            logger.warning(f"{FUNDS_FILE} line {line}: payouts '{r['payouts']}' not understood — use reinvest, "
                           "cash or none")
        day = r.get("monthly_day")
        amount = r.get("monthly_amount")
        if amount is not None and amount <= 0:
            amount = None
        if amount and (day is None or not 1 <= day <= 31 or not float(day).is_integer()):
            logger.warning(f"{FUNDS_FILE} line {line}: monthly_amount needs a monthly_day from 1 to 31 — "
                           "no automatic top-ups for this fund")
            amount = None
        fee = r.get("fee_pct")
        if fee is not None and not 0 <= fee < 20:
            logger.warning(f"{FUNDS_FILE} line {line}: fee_pct {fee} looks wrong (a percentage, e.g. 1.5) — using 0")
            fee = None
        peers = [p.strip().upper() for p in re.split(r"[;\s,]+", r.get("peers") or "") if p.strip()]
        seen.add(isin)
        funds.append({
            "isin": isin, "name": r["name"].strip(), "units": float(r["units"]), "avg_cost": float(r["avg_cost"]),
            "as_of": r["as_of"], "statement_nav": r.get("statement_nav"),
            "currency": (r.get("currency") or "USD").strip().upper(), "payouts": payouts,
            "monthly_amount": amount, "monthly_day": int(day) if amount else None,
            "fee_pct": float(fee or 0.0), "peers": [p for p in peers if valid_isin(p) and p != isin],
            "factsheet_url": (r.get("factsheet_url") or "").strip()
            if (r.get("factsheet_url") or "").strip().lower().startswith("https://") else None,
            "invested_since": r.get("invested_since") if (r.get("invested_since") or "9999") <= r["as_of"] else None,
            "note": r.get("note") or "",
        })
        if r.get("invested_since") and r["invested_since"] > r["as_of"]:
            logger.warning(f"{FUNDS_FILE} line {line}: invested_since is after the statement date — ignored")
    return funds


def load_transactions(portfolio_dir):
    """[(line_no, txn dict)] from portfolio/fund_transactions.csv, oldest first."""
    path = os.path.join(portfolio_dir, TXN_FILE)
    if not os.path.exists(path):
        return []
    try:
        rows = pcsv.read_rows(path, TXN_FIELDS)
    except pcsv.CsvFormatError as e:
        logger.warning(f"Funds: {e}")
        return []
    out = []
    for line, r in rows:
        kind = TYPES.get((r["type"] or "").strip().lower())
        if not kind:
            logger.warning(f"{TXN_FILE} line {line}: type '{r['type']}' not understood — use topup, buy, sell, "
                           "reinvest or payout — skipping this row")
            continue
        status = (r.get("status") or "").strip().lower()
        if status not in STATUSES:
            # A row you typed in yourself: with units it's what your bank says; without, it needs pricing.
            status = "confirmed" if r.get("units") is not None else "pending"
        out.append((line, dict(r, isin=r["isin"].strip().upper(), type=kind, status=status,
                               ref=(r.get("ref") or "").strip())))
    out.sort(key=lambda x: (x[1]["date"], x[0]))
    return out


# ------------------------------------------------------------------ the automatic rows
def _units_for(amount, fee_pct, nav):
    return round(amount * (1 - (fee_pct or 0) / 100.0) / nav, UNIT_DP)


def _units_held_before(fund, txns, day):
    """Units held at the end of the day BEFORE `day` (entitled to a payout
    going ex on `day`), or None when an earlier row isn't priced yet."""
    units = fund["units"]
    for t in txns:
        if _date(t["date"]) >= day:
            break
        if _date(t["date"]) <= _date(fund["as_of"]):      # already in the statement's units
            continue
        if t["type"] in ("topup", "buy", "reinvest"):
            if t.get("units") is None:
                return None
            units += t["units"]
        elif t["type"] == "sell" and t.get("units") is not None:
            units -= t["units"]
    return units


def _sold_out_since(fund, txns):
    """The date you sold the whole fund, if you hold none now and haven't
    bought since — no automatic top-ups after it. None otherwise."""
    units, out = fund["units"], None
    for t in sorted(txns, key=lambda t: _date(t["date"])):
        if _date(t["date"]) <= _date(fund["as_of"]):
            continue
        if t["type"] in ("topup", "buy", "reinvest"):
            units += t.get("units") or 0.0
            if t["type"] != "reinvest":
                out = None
        elif t["type"] == "sell" and t.get("units"):
            units -= t["units"]
            if units <= SOLD_OUT:
                out = _date(t["date"])
    return out


def sync_transactions(portfolio_dir, funds, market, today=None):
    """Add the automatic rows that are due and price the pending ones —
    each automatic row once (see the module docstring). Returns the list of
    changes, in words, for the log."""
    today = _date(today or dt.date.today())
    path = os.path.join(portfolio_dir, TXN_FILE)
    changes, backed_up = [], False

    def backup():
        nonlocal backed_up
        if not backed_up and os.path.exists(path):
            pcsv.backup_file(path)
        backed_up = True

    # 1. price rows that were waiting for their dealing day's price
    for line, t in load_transactions(portfolio_dir):
        if t["type"] == "sell" and t.get("amount") is None and t.get("units"):
            # a sale typed as units only: the cash is units × the dealing day's price
            hit = nav_on_or_after((market.get(t["isin"]) or {}).get("navs") or [], t["date"])
            if not hit:
                continue
            cash = round(t["units"] * hit[1], 2)
            try:
                backup()
                pcsv.update_row(path, TXN_FIELDS, line, expect={"date": t["date"], "units": t["units"]},
                                changes={"amount": cash, "price": hit[1], "status": "estimated"}, backup=False)
                changes.append(f"priced the {t['date']} sale for {t['isin']}: {t['units']} units at {hit[1]} = "
                               f"{cash} ({hit[0].isoformat()} price)")
            except (pcsv.RowChangedError, pcsv.CsvFormatError, OSError) as e:
                logger.warning(f"Funds: couldn't update {TXN_FILE} line {line} ({e}) — will try again next run")
            continue
        if t["status"] != "pending" or t["type"] not in ("topup", "buy", "reinvest") or t.get("amount") is None:
            continue
        navs = (market.get(t["isin"]) or {}).get("navs") or []
        hit = nav_on_or_after(navs, t["date"])
        if not hit:
            continue
        fee = t.get("fee_pct") if t["type"] != "reinvest" else 0.0
        units = _units_for(t["amount"], fee, hit[1])
        try:
            backup()
            pcsv.update_row(path, TXN_FIELDS, line, expect={"date": t["date"], "ref": t.get("ref") or None},
                            changes={"units": units, "price": hit[1], "status": "estimated"}, backup=False)
            changes.append(f"priced {t['ref'] or t['type']} for {t['isin']}: {units} units at {hit[1]} "
                           f"({hit[0].isoformat()} price)")
        except (pcsv.RowChangedError, pcsv.CsvFormatError, OSError) as e:
            logger.warning(f"Funds: couldn't update {TXN_FILE} line {line} ({e}) — will try again next run")

    def append(values):
        backup()
        if not os.path.exists(path):
            pcsv.create_csv(path, TXN_FIELDS)
        pcsv.append_row(path, TXN_FIELDS, values)

    # 2. top-ups that are due (none after you sold the whole fund, until you buy again)
    have = {(t["isin"], t["ref"]) for _l, t in load_transactions(portfolio_dir) if t["ref"]}
    for f in funds:
        if not f["monthly_amount"]:
            continue
        navs = (market.get(f["isin"]) or {}).get("navs") or []
        closed = _sold_out_since(f, [t for _l, t in load_transactions(portfolio_dir) if t["isin"] == f["isin"]])
        for d in topup_dates(f["monthly_day"], f["as_of"], today):
            ref = f"topup:{d:%Y-%m}"
            if (f["isin"], ref) in have or (closed and d > closed):
                continue
            hit = nav_on_or_after(navs, d)
            row = {"date": d.isoformat(), "isin": f["isin"], "type": "topup", "amount": f["monthly_amount"],
                   "fee_pct": f["fee_pct"], "status": "pending", "ref": ref,
                   "note": "automatic top-up — check the units on your statement"}
            if hit:
                row.update(units=_units_for(f["monthly_amount"], f["fee_pct"], hit[1]), price=hit[1],
                           status="estimated")
            try:
                append(row)
                have.add((f["isin"], ref))
                changes.append(f"recorded the {d:%d %b %Y} top-up for {f['isin']}"
                               + (f" ({row['units']} units)" if hit else " (priced once that day's NAV is out)"))
            except (pcsv.CsvFormatError, OSError) as e:
                logger.warning(f"Funds: couldn't add the {ref} top-up for {f['isin']} ({e})")

    # 3. payouts that went ex after the statement date
    for f in funds:
        if f["payouts"] not in ("reinvest", "cash"):
            continue
        m = market.get(f["isin"]) or {}
        navs = m.get("navs") or []
        txns = [t for _l, t in load_transactions(portfolio_dir) if t["isin"] == f["isin"]]
        for dist in sorted(m.get("distributions") or [], key=lambda x: x["ex_date"]):
            ex = _date(dist["ex_date"])
            if not (_date(f["as_of"]) < ex <= today):
                continue
            ref = f"payout:{ex.isoformat()}"
            held = _units_held_before(f, txns, ex)
            if held is None:          # an earlier top-up isn't priced yet — the entitled units aren't known
                continue
            sold_out = held <= SOLD_OUT                  # sold before it went ex — nothing to pay
            if sold_out and (f["isin"], ref) not in have:
                continue
            cash = 0.0 if sold_out else round(held * dist["amount"], 2)
            hit = nav_on_or_after(navs, ex)
            provisional = " — amount provisional (last published payout), corrected automatically" \
                if dist.get("provisional") else ""
            what = ("none held on the ex-date (sold before it), so nothing was paid" if sold_out else
                    f"{dist['amount']} per unit × {held} units"
                    + (", reinvested" if f["payouts"] == "reinvest" else
                       (f", paid {_date(dist['pay_date']).isoformat()}" if dist.get("pay_date") else "")) + provisional)
            if (f["isin"], ref) in have:
                # An automatic row whose amount is out of date — the real payout is now published, or a sale
                # recorded since changed the units you held on the ex-date: fix it (never a confirmed row).
                for line, t in load_transactions(portfolio_dir):
                    if (t["isin"], t["ref"]) != (f["isin"], ref) or t["status"] not in ("estimated", "pending"):
                        continue
                    if abs((t.get("amount") or 0) - cash) < 0.005:
                        continue                         # already right
                    if dist.get("provisional") and "provisional" not in (t.get("note") or "") and not sold_out:
                        continue                         # never swap a published amount for a provisional one
                    upd = {"amount": cash, "note": what}
                    if t["type"] == "reinvest" and (hit or sold_out):
                        upd.update(units=0.0 if sold_out else _units_for(cash, 0.0, hit[1]),
                                   price=hit[1] if hit else None, status="estimated")
                    try:
                        backup()
                        pcsv.update_row(path, TXN_FIELDS, line, expect={"ref": ref}, changes=upd, backup=False)
                        changes.append(f"corrected the {ex:%d %b %Y} payout for {f['isin']} "
                                       + ("— nothing was held by then" if sold_out else f"to {cash}"))
                        txns = [t for _l, t in load_transactions(portfolio_dir) if t["isin"] == f["isin"]]
                    except (pcsv.RowChangedError, pcsv.CsvFormatError, OSError) as e:
                        logger.warning(f"Funds: couldn't update {TXN_FILE} line {line} ({e})")
                continue
            row = {"date": ex.isoformat(), "isin": f["isin"], "amount": cash, "status": "estimated", "ref": ref,
                   "note": what}
            if f["payouts"] == "cash":
                row["type"] = "payout"
            else:
                row["type"] = "reinvest"
                if hit:
                    row.update(units=_units_for(cash, 0.0, hit[1]), price=hit[1])
                else:
                    row["status"] = "pending"
            try:
                append(row)
                have.add((f["isin"], ref))
                txns = [t for _l, t in load_transactions(portfolio_dir) if t["isin"] == f["isin"]]
                changes.append(f"recorded the {ex:%d %b %Y} payout for {f['isin']} "
                               f"({'reinvested' if f['payouts'] == 'reinvest' else 'paid to your account'})")
            except (pcsv.CsvFormatError, OSError) as e:
                logger.warning(f"Funds: couldn't add the {ref} payout for {f['isin']} ({e})")
    return changes


# ------------------------------------------------------------------ returns maths (also used for peers)
def total_return_index(navs, distributions=()):
    """[(date, index)] — growth of 1 with every payout reinvested at the
    ex-date price. Without payouts it's the plain price index."""
    by_ex = {}
    for d in distributions or ():
        ex = _date(d["ex_date"])
        by_ex[ex] = by_ex.get(ex, 0.0) + float(d["amount"])
    out, idx = [], 1.0
    for i, (d, nav) in enumerate(navs):
        if i:
            prev = navs[i - 1][1]
            payout = sum(v for ex, v in by_ex.items() if navs[i - 1][0] < ex <= d)
            idx *= (nav + payout) / prev
        out.append((d, idx))
    return out


def _ratio(index, start, end_value):
    p = nav_on_or_before(index, start)
    return end_value / p[1] if p and p[1] else None


def returns_from_index(index, today=None):
    """Period returns (%), annualised past one year; calendar-year returns;
    risk figures. Missing history → None for that period."""
    if len(index) < 2:
        return {"periods": {}, "calendar": {}, "risk": {}}
    end_d, end_v = index[-1]
    today = _date(today or end_d)
    first_d = index[0][0]
    periods = {}
    # From the same calendar date 1 week / N months / N years earlier (the
    # fund managers' convention); periods over a year are annualised.
    spans = [("1D", None, 0), ("1W", "w", 1), ("1M", "m", 1), ("3M", "m", 3), ("6M", "m", 6), ("YTD", "ytd", 0),
             ("1Y", "m", 12), ("3Y", "m", 36), ("5Y", "m", 60), ("10Y", "m", 120)]
    for key, unit, n in spans:
        if unit is None:
            periods[key] = (end_v / index[-2][1] - 1) * 100.0
            continue
        if unit == "ytd":
            start = dt.date(end_d.year - 1, 12, 31)
        elif unit == "w":
            start = end_d - dt.timedelta(days=7)
        else:
            y, m = divmod(end_d.month - 1 - n, 12)
            start = scheduled_day(end_d.year + y, m + 1, end_d.day)
        if start < first_d - dt.timedelta(days=5):
            periods[key] = None
            continue
        r = _ratio(index, start, end_v)
        if r is None:
            periods[key] = None
        elif unit == "m" and n > 12:
            periods[key] = (r ** (12 / n) - 1) * 100.0                 # a year's worth, on average
        else:
            periods[key] = (r - 1) * 100.0
    if first_d <= end_d - dt.timedelta(days=30):
        yrs = (end_d - first_d).days / 365.25
        periods["since_start"] = ((end_v / index[0][1]) ** (1 / yrs) - 1) * 100.0 if yrs >= 1 else \
            (end_v / index[0][1] - 1) * 100.0

    # calendar years: a full year needs the last price of the year before
    year_end = {}
    for d, v in index:
        year_end[d.year] = (d, v)
    calendar_ret = {}
    years = sorted(year_end)
    for y in years:
        prev = year_end.get(y - 1)
        if prev:
            calendar_ret[y] = (year_end[y][1] / prev[1] - 1) * 100.0
    # risk over the last year: daily swings, worst fall from a peak, best / worst month
    risk = {}
    last_year = [(d, v) for d, v in index if d > end_d - dt.timedelta(days=365)]
    if len(last_year) > 60:
        rets = [math.log(last_year[i][1] / last_year[i - 1][1]) for i in range(1, len(last_year))]
        mean = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        per_year = len(rets) / ((last_year[-1][0] - last_year[0][0]).days / 365.25 or 1)
        risk["volatility_1y"] = sd * math.sqrt(per_year) * 100.0
    for key, days in (("max_drawdown_1y", 365), ("max_drawdown_3y", 365 * 3)):
        window = [v for d, v in index if d > end_d - dt.timedelta(days=days)]
        if len(window) > 20 and index[0][0] <= end_d - dt.timedelta(days=days - 10):
            peak, worst = window[0], 0.0
            for v in window:
                peak = max(peak, v)
                worst = min(worst, v / peak - 1)
            risk[key] = worst * 100.0
    # The regulators' risk-and-reward class (SRRI, 1–7): how much weekly
    # returns swung over the last 5 years (≥ 2 years needed), in fixed bands.
    week_end = {}
    for d, v in index:
        if d > end_d - dt.timedelta(days=365 * 5 + 7):
            week_end[d.isocalendar()[:2]] = (d, v)
    weeks = [week_end[k] for k in sorted(week_end)]
    if len(weeks) >= 104:
        wr = [math.log(weeks[i][1] / weeks[i - 1][1]) for i in range(1, len(weeks))]
        mean = sum(wr) / len(wr)
        vol = math.sqrt(sum((r - mean) ** 2 for r in wr) / (len(wr) - 1)) * math.sqrt(52) * 100.0
        risk["volatility_weekly"] = vol
        risk["srri_years"] = round((weeks[-1][0] - weeks[0][0]).days / 365.25, 1)
        risk["srri"] = next(i for i, top in enumerate((0.5, 2, 5, 10, 15, 25, math.inf), 1) if vol < top)
    month_end = {}
    for d, v in index:
        month_end[(d.year, d.month)] = v
    keys = sorted(month_end)
    monthly = [(k, (month_end[k] / month_end[keys[i - 1]] - 1) * 100.0) for i, k in enumerate(keys) if i
               and keys[i - 1] == ((k[0], k[1] - 1) if k[1] > 1 else (k[0] - 1, 12))]
    recent = [m for m in monthly if dt.date(m[0][0], m[0][1], 1) > end_d - dt.timedelta(days=365 * 3)]
    if recent:
        risk["best_month"] = max(recent, key=lambda m: m[1])
        risk["worst_month"] = min(recent, key=lambda m: m[1])
        risk["months_up_pct"] = sum(1 for _k, r in recent if r > 0) / len(recent) * 100.0
    return {"periods": periods, "calendar": calendar_ret, "risk": risk, "first_date": first_d, "last_date": end_d}


def xirr(flows):
    """Annual rate r with Σ amount / (1+r)^(years) = 0 for [(date, amount)]
    (money in negative, money out positive) — your own money-weighted return."""
    flows = sorted((_date(d), float(a)) for d, a in flows if a)
    if len(flows) < 2 or not (any(a < 0 for _d, a in flows) and any(a > 0 for _d, a in flows)):
        return None
    d0 = flows[0][0]

    def npv(r):
        return sum(a / (1 + r) ** ((d - d0).days / 365.25) for d, a in flows)
    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7:
            break
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return mid * 100.0


# ------------------------------------------------------------------ one fund, today
def _position(fund, txns, navs, dists):
    """Units / money figures and the day-by-day profit series for one fund.
    `txns` are this fund's rows dated after the statement date, oldest first."""
    by_ex = {}
    for d in dists:
        ex = _date(d["ex_date"])
        by_ex[ex] = by_ex.get(ex, 0.0) + float(d["amount"])

    def dealing(d):
        hit = nav_on_or_after(navs, d)
        return hit[0] if hit else d

    units, invested, realized = fund["units"], fund["units"] * fund["avg_cost"], 0.0
    pending, payouts, reinvested, topups, sold = [], 0.0, 0.0, 0.0, 0.0
    deals = []                       # (dealing date, units change, cash paid in, is a reinvested payout)
    flows = []                       # your own cash in / out, for the money-weighted return
    sales = []                       # one dict per sale, for "Your sales"
    put_in_steps = []                # (date, money in at cost after that day's rows)
    for t in txns:
        amt, u, kind = t.get("amount") or 0.0, t.get("units"), t["type"]
        d = _date(t["date"])
        if kind == "sell" and u and t.get("amount") is None:
            # not priced yet: the dealing day's price when published, else the latest one (an estimate)
            hit = nav_on_or_after(navs, d) or nav_on_or_before(navs, d)
            amt = u * hit[1] if hit else 0.0
        if kind in ("topup", "buy"):
            invested += amt
            if kind == "topup":
                topups += amt
            if amt:
                flows.append((d, -amt))
            if u is None:
                pending.append((d, amt * (1 - (t.get("fee_pct") or 0) / 100.0), amt))
            else:
                units += u
                deals.append((dealing(d), u, amt, False))
        elif kind == "reinvest":
            reinvested += amt
            if u is not None:
                units += u
                deals.append((dealing(d), u, 0.0, True))
        elif kind == "payout":
            payouts += amt
            if amt:
                flows.append((d, amt))
        elif kind == "sell" and u:
            waiting = sum(p[2] for p in pending)     # purchases not priced yet have money in but no units
            avg = (invested - waiting) / units if units > SOLD_OUT else 0.0
            invested -= avg * u
            units -= u
            if units <= SOLD_OUT:                    # sold it all: no rounding crumbs left behind
                units, invested = 0.0, waiting
            sold += amt
            realized += amt - avg * u
            deals.append((dealing(d), -u, -amt, False))
            if amt:
                flows.append((d, amt))
            sales.append({"date": d.isoformat(), "units": u, "proceeds": amt, "cost": avg * u,
                          "gain": amt - avg * u, "gain_pct": (amt - avg * u) / (avg * u) * 100.0 if avg * u else None,
                          "avg_cost": avg, "price": t.get("price") or amt / u, "left_after": units,
                          "estimated": t.get("amount") is None or t.get("status") != "confirmed",
                          "fees": 0.0})
        put_in_steps.append((d, invested))

    # Profit day by day = the units held coming into the day × (today's price
    # + any payout going ex today − the previous price), plus what a purchase
    # or sale made or lost on its own dealing day (e.g. the sales charge). A
    # reinvested payout is already in the "+ payout" part; its units count
    # from the next day. Before the statement date the statement's units are
    # assumed — the history before it isn't known.
    daily = []
    for i in range(1, len(navs)):
        d, nav = navs[i]
        prev_d, prev = navs[i - 1]
        held = fund["units"] + sum(x[1] for x in deals if x[0] < d)
        payout = sum(v for ex, v in by_ex.items() if prev_d < ex <= d)
        pl = held * (nav + payout - prev)
        pl += sum(x[1] * nav - x[2] for x in deals if x[0] == d and not x[3])
        daily.append((d, pl))

    # Value and money put in, day by day since the statement date.
    # "Put in" is the cost of what's still held, so a sale lowers it by the
    # average cost of the units sold (the same figure the gain is measured from).
    as_of = _date(fund["as_of"])
    start_cost = fund["units"] * fund["avg_cost"]
    series = []
    for d, nav in navs:
        if d < as_of:
            continue
        held = fund["units"] + sum(x[1] for x in deals if x[0] <= d)
        cash = sum(p[1] for p in pending if p[0] <= d)
        put_in = next((v for sd, v in reversed(put_in_steps) if sd <= d), start_cost)
        series.append((d, max(held, 0.0) * nav + cash, put_in))
    return {"units": units, "invested": invested, "realized": realized, "pending": pending, "payouts": payouts,
            "reinvested": reinvested, "topups": topups, "sold": sold, "daily": daily, "flows": flows,
            "series": series, "sales": sales}


def _sum_since(daily, start):
    vals = [v for d, v in daily if d > start]
    return sum(vals) if vals else None


def _group(daily, key, n):
    groups = {}
    for d, v in daily:
        k = key(d)
        groups[k] = groups.get(k, 0.0) + v
    return sorted(groups.items())[-n:]


def compute_fund_portfolio(funds, txns, market, usd_kes=None, today=None):
    """The funds summary for the page — same shape as the other portfolio
    summaries (totals + one dict per holding). None when no funds are set up."""
    if not funds:
        return None
    today = _date(today or dt.date.today())
    rate = (usd_kes or {}).get("rate")
    out, missing, warnings = [], [], []
    for f in funds:
        m = market.get(f["isin"]) or {}
        navs = sorted((_date(d), float(v)) for d, v in (m.get("navs") or []) if v)
        dists = [d for d in (m.get("distributions") or []) if d.get("amount")]
        rows = [t[1] if isinstance(t, tuple) else t for t in (txns or [])]
        ft = [t for t in rows if t["isin"] == f["isin"] and _date(t["date"]) > _date(f["as_of"])]
        early = [t for t in rows if t["isin"] == f["isin"] and _date(t["date"]) <= _date(f["as_of"])]
        if early:
            warnings.append(f"{f['name']}: {len(early)} transaction(s) dated on or before the statement date "
                            f"{f['as_of']} were ignored — your statement's units already include them")
        h = {k: f.get(k) for k in ("isin", "name", "currency", "payouts", "monthly_amount", "monthly_day", "fee_pct",
                                   "as_of", "statement_nav", "peers", "factsheet_url", "invested_since", "note")}
        h.update(statement_units=f["units"], statement_avg_cost=f["avg_cost"], transactions=ft,
                 source=m.get("source"), source_url=m.get("source_url"), facts=m.get("facts") or {},
                 cross_check=m.get("cross_check"), distributions=dists, navs=navs, news=m.get("news") or [])
        if f["currency"] != "USD":
            warnings.append(f"{f['name']}: priced in {f['currency']} — only USD funds are added to your totals")
        if not navs or f["currency"] != "USD":
            missing.append(f["name"])
            h.update(data_available=False)
            out.append(h)
            continue
        pos = _position(f, ft, navs, dists)
        nav_d, nav = navs[-1]
        prev = navs[-2] if len(navs) > 1 else None
        pending_net = sum(p[1] for p in pos["pending"])
        value = pos["units"] * nav + pending_net
        gain = value - pos["invested"]
        day_pl = pos["daily"][-1][1] if pos["daily"] else None
        # payouts still to be paid for this fund in the past 12 months, per unit
        last12 = [d for d in dists if _date(d["ex_date"]) > nav_d - dt.timedelta(days=365)]
        per_unit_12m = sum(float(d["amount"]) for d in last12)
        paying = f["payouts"] in ("cash", "reinvest") or bool(dists)
        index = total_return_index(navs, dists)
        ret = returns_from_index(index, today)
        price_only = paying and not dists
        check = None
        if f.get("statement_nav"):
            same_day = next((v for d, v in navs if d == _date(f["as_of"])), None)
            if same_day is None:
                check = {"ok": None, "statement_nav": f["statement_nav"], "date": f["as_of"]}
            else:
                diff = (same_day - f["statement_nav"]) / f["statement_nav"] * 100.0
                check = {"ok": abs(diff) <= 0.05, "statement_nav": f["statement_nav"], "source_nav": same_day,
                         "date": f["as_of"], "diff_pct": diff}
                if not check["ok"]:
                    warnings.append(f"{f['name']}: the price source says {same_day:g} on {f['as_of']} but your "
                                    f"statement says {f['statement_nav']:g} — check it's the same share class")
        stale_days = (today - nav_d).days
        if stale_days > 6:
            warnings.append(f"{f['name']}: the latest price is from {nav_d.isoformat()} ({stale_days} days old)")
        # Your own yearly return since the statement: the statement value counts
        # as money put in on that day, then every top-up in and payout out.
        start = f.get("statement_nav") or (nav_on_or_before(navs, f["as_of"]) or (None, f["avg_cost"]))[1]
        flows = [(_date(f["as_of"]), -f["units"] * start)] + pos["flows"] + [(nav_d, value)]
        tracked_days = (nav_d - _date(f["as_of"])).days
        sold_out = pos["units"] <= SOLD_OUT and not pos["pending"]
        nxt = next_topup(f["monthly_day"], today) if f["monthly_amount"] and not sold_out else None
        last_ex = max((_date(d["ex_date"]) for d in dists), default=None)
        # A monthly payer's next ex-date ≈ the same point of next month (an estimate, labelled as one).
        next_ex = None
        if last_ex and len(last12) >= 10:
            ny, nm = (last_ex.year + 1, 1) if last_ex.month == 12 else (last_ex.year, last_ex.month + 1)
            next_ex = scheduled_day(ny, nm, 31 if last_ex.day >= 28 else last_ex.day)
            while next_ex <= today:
                ny, nm = (next_ex.year + 1, 1) if next_ex.month == 12 else (next_ex.year, next_ex.month + 1)
                next_ex = scheduled_day(ny, nm, 31 if last_ex.day >= 28 else last_ex.day)
        h.update(
            data_available=True, nav=nav, nav_date=nav_d.isoformat(),
            nav_prev=prev[1] if prev else None, nav_prev_date=prev[0].isoformat() if prev else None,
            units=round(pos["units"], 6), invested=pos["invested"],
            avg_cost=pos["invested"] / pos["units"] if pos["units"] else None,
            market_value=value, pending_value=pending_net, pending=pos["pending"],
            gain=gain, gain_pct=gain / pos["invested"] * 100.0 if pos["invested"] else None,
            payouts_received=pos["payouts"], reinvested=pos["reinvested"], realized=pos["realized"],
            sales=pos["sales"], sold_out=sold_out,
            total_return_value=gain + pos["payouts"] + pos["realized"],
            day_change_value=day_pl,
            day_change_pct=(day_pl / (value - day_pl) * 100.0) if day_pl is not None and value - day_pl else None,
            nav_change_pct=((nav / prev[1] - 1) * 100.0) if prev else None,
            pl_periods={k: _sum_since(pos["daily"], s) for k, s in (
                ("1W", nav_d - dt.timedelta(days=7)), ("1M", nav_d - dt.timedelta(days=30)),
                ("3M", nav_d - dt.timedelta(days=91)), ("YTD", dt.date(nav_d.year - 1, 12, 31)),
                ("1Y", nav_d - dt.timedelta(days=365)))},
            daily_pl=pos["daily"][-30:], value_series=pos["series"],
            weekly_pl=_group(pos["daily"], lambda d: d - dt.timedelta(days=d.weekday()), 13),
            monthly_pl=_group(pos["daily"], lambda d: (d.year, d.month), 12),
            returns=ret, total_return_index=index, price_only=price_only,
            dist_per_unit_12m=per_unit_12m if dists else None,
            distribution_yield=(per_unit_12m / nav * 100.0) if dists and nav else None,
            est_annual_income=(per_unit_12m * pos["units"]) if dists else None,
            last_payout=max(dists, key=lambda d: _date(d["ex_date"])) if dists else None,
            next_payout_est=next_ex.isoformat() if next_ex else None,
            payouts_per_year=len(last12),
            next_topup=nxt.isoformat() if nxt else None,
            next_topup_units_est=_units_for(f["monthly_amount"], f["fee_pct"], nav) if nxt else None,
            your_return_annual=xirr(flows) if tracked_days >= 90 else None, tracked_days=tracked_days,
            check=check, stale_days=stale_days,
        )
        for k, s in (("market_value", value), ("invested", pos["invested"]), ("gain", gain),
                     ("day_change_value", day_pl), ("payouts_received", pos["payouts"])):
            h[k + "_kes"] = s * rate if (rate and s is not None) else None
        out.append(h)

    avail = [h for h in out if h.get("data_available")]
    mv = sum(h["market_value"] for h in avail)
    inv = sum(h["invested"] for h in avail)
    day = [h["day_change_value"] for h in avail if h.get("day_change_value") is not None]
    day_v = sum(day) if day else None
    income = [h["est_annual_income"] for h in avail if h.get("est_annual_income") is not None]
    totals = {
        "market_value": mv, "invested": inv, "cost_basis": inv, "gain": mv - inv,
        "gain_pct": (mv - inv) / inv * 100.0 if inv else None,
        "day_change_value": day_v, "day_change_pct": (day_v / (mv - day_v) * 100.0) if day_v and mv - day_v else
        (0.0 if day_v == 0 else None),
        "payouts_received": sum(h["payouts_received"] for h in avail),
        "realized": sum(h["realized"] for h in avail),
        "est_annual_income": sum(income) if income else None,
        "monthly_topups": sum(h["monthly_amount"] or 0.0 for h in out if not h.get("sold_out")),
        "n_funds": len(out), "n_available": len(avail), "fx_available": bool(rate),
    }
    for k in ("market_value", "invested", "gain", "day_change_value", "payouts_received", "est_annual_income"):
        v = totals[k]
        totals[k + "_kes"] = v * rate if (rate and v is not None) else None
    for p in ("1W", "1M", "3M", "YTD", "1Y"):
        vals = [h["pl_periods"][p] for h in avail if h["pl_periods"].get(p) is not None]
        totals.setdefault("pl_periods", {})[p] = sum(vals) if vals else None
    for key in ("daily_pl", "weekly_pl", "monthly_pl"):
        merged = {}
        for h in avail:
            for k, v in h[key]:
                merged[k] = merged.get(k, 0.0) + v
        totals[key] = sorted(merged.items())
    # all funds' value since the latest statement date, each carried forward over the others' holidays
    start = max((_date(h["as_of"]) for h in avail), default=None)
    dates = sorted({d for h in avail for d, _v, _p in h["value_series"] if d >= start}) if start else []
    combined = []
    for d in dates:
        v = p = 0.0
        for h in avail:
            last = [x for x in h["value_series"] if x[0] <= d]
            if last:
                v += last[-1][1]
                p += last[-1][2]
        combined.append((d, v, p))
    totals["value_series"] = combined
    return {"as_of": today.isoformat(), "funds": out, "holdings": out, "totals": totals, "missing": missing,
            "missing_symbols": missing, "warnings": warnings, "fx_rate": rate,
            "fx_as_of": (usd_kes or {}).get("updated", "")}
