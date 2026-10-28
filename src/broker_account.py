"""
Your international brokerage account as a whole — the money you put in and
how it has grown, not just the shares you hold today.

Private, like every portfolio file: portfolio/international_account.csv
(never committed to git, kept out of Docker images).

    date     the day of the deposit / withdrawal, or the date of the cash balance
    type     deposit · withdrawal · cash
    amount   US dollars
    note     optional

  deposit / withdrawal   money you sent to the broker or took out. One row
                         per transfer — or one row with the total so far.
  cash                   uninvested cash in the account on that date (copy it
                         from the broker). The latest-dated row counts.

Why it matters: the cost of the shares you hold today is not the money you
put in. Sell something at a profit, reinvest it, and the new shares' cost
includes that profit — so "gain vs cost" only shows the paper gain on what's
held. Against your deposits, the account's growth is the whole story:

    account value = shares you hold (today's prices) + cash
    growth        = account value − (deposits − withdrawals)

and the part of the growth that isn't the paper gain on today's shares came
from what you sold, dividends, interest and fees.
"""

import os

import portfolio_csv as pcsv
from logger import get_logger

logger = get_logger(__name__)

ACCOUNT_FILE = "international_account.csv"
_F = pcsv.Field
ACCOUNT_FIELDS = (
    _F("date", ("as_of", "value_date"), "date", True),
    _F("type", ("kind", "transaction"), "text", True),
    _F("amount", ("amount_usd", "usd", "value"), "number", True),
    pcsv._NOTE,
)
TYPES = {"deposit": "deposit", "deposits": "deposit", "transfer in": "deposit", "top-up": "deposit",
         "withdrawal": "withdrawal", "withdrawals": "withdrawal", "transfer out": "withdrawal",
         "cash": "cash", "cash balance": "cash", "balance": "cash"}


def load_account(portfolio_dir):
    """{deposits, withdrawals, cash, cash_as_of, first_date, rows} from the
    private file — None when there's no such file (nothing changes then)."""
    path = os.path.join(portfolio_dir, ACCOUNT_FILE)
    if not os.path.exists(path):
        return None
    try:
        rows = pcsv.read_rows(path, ACCOUNT_FIELDS)
    except pcsv.CsvFormatError as e:
        logger.warning(f"International account: {e}")
        return None
    out = {"deposits": 0.0, "withdrawals": 0.0, "cash": None, "cash_as_of": None, "first_date": None, "rows": 0}
    best_cash = None
    for line, r in rows:
        kind = TYPES.get((r["type"] or "").strip().lower())
        if kind is None:
            logger.warning(f"{ACCOUNT_FILE} line {line}: type '{r['type']}' not understood — use deposit, withdrawal "
                           "or cash — skipping this row")
            continue
        amount = r["amount"]
        if amount < 0 or (kind != "cash" and amount == 0):
            logger.warning(f"{ACCOUNT_FILE} line {line}: the amount must be more than 0 — skipping this row")
            continue
        out["rows"] += 1
        if kind == "cash":
            if best_cash is None or (r["date"], line) >= best_cash[:2]:
                best_cash = (r["date"], line, amount)
            continue
        out["deposits" if kind == "deposit" else "withdrawals"] += amount
        out["first_date"] = min(filter(None, (out["first_date"], r["date"])))
    if best_cash:
        out["cash_as_of"], out["cash"] = best_cash[0], best_cash[2]
    return out


def summarise(account, ips, realized_usd=0.0):
    """The account picture for My Portfolio (all USD), or None without an
    account file. `ips` is the international portfolio summary;
    realized_usd the profit or loss of the international sales recorded in
    sales.csv. complete=False when a holding had no price today (its value
    is then missing from the account value, so growth isn't shown as fact)."""
    if not account:
        return None
    t = (ips or {}).get("totals") or {}
    holdings_value = t.get("market_value") or 0.0
    unrealized = t.get("gain") or 0.0
    cash = account["cash"] or 0.0
    net = account["deposits"] - account["withdrawals"]
    value = holdings_value + cash
    growth = value - net
    complete = not (ips or {}).get("missing_symbols")
    other = growth - unrealized
    return {
        "deposits": account["deposits"], "withdrawals": account["withdrawals"], "net_deposited": net,
        "first_date": account["first_date"], "cash": account["cash"], "cash_as_of": account["cash_as_of"],
        "holdings_value": holdings_value, "holdings_cost": t.get("cost_basis") or 0.0, "value": value,
        "growth": growth, "growth_pct": growth / net * 100.0 if net > 0 else None,
        "unrealized": unrealized, "realized_recorded": realized_usd,
        "other": other, "other_unrecorded": other - realized_usd, "complete": complete,
    }
