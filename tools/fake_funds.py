"""
Made-up mutual funds for the tests and the sandbox, so nothing in this public
repo uses anyone's real funds as examples. Invented names, invented ISINs
(well-formed, with a correct check digit, under the "XS9" prefix nobody
uses for these) and prices from a seeded random walk — the same every run.

They cover each kind of fund the dashboard handles:

  Sample Global Bond Income Fund   pays out monthly (reinvested in the sandbox)
  Sample US Equity Growth Fund     accumulating — no payouts
  Sample Europe Dividend Fund      pays out quarterly (paid as cash)

  FUNDS                 the funds, with what the sandbox holds of each
  PEERS                 two made-up competitors per fund
  market(today)         {isin: data} shaped like fund_data.fetch_fund()'s result
  funds_csv(today)      a portfolio/funds.csv for the sandbox
  write_market(path)    the market data as JSON, for FUND_DATA_FILE (sandbox runs)
"""

import datetime as dt
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import funds_portfolio as fp            # noqa: E402


def _isin(n):
    body = f"XS9{n:08d}"
    return body + fp.isin_check_digit(body)


FUNDS = [
    {"isin": _isin(1), "name": "Sample Global Bond Income Fund A (USD) Dist", "category": "Global Bond",
     "nav0": 10.0, "drift": 0.035, "vol": 0.05, "payout_yield": 0.055, "every": 1, "seed": 11,
     "units": 400.0, "avg_cost": 9.80, "payouts": "reinvest", "monthly": 50, "day": 15, "fee": 2.0},
    {"isin": _isin(2), "name": "Sample US Equity Growth Fund A (USD) Acc", "category": "US Large-Cap Blend Equity",
     "nav0": 100.0, "drift": 0.10, "vol": 0.15, "payout_yield": None, "every": None, "seed": 12,
     "units": 30.0, "avg_cost": 150.00, "payouts": "none", "monthly": None, "day": None, "fee": 2.0},
    {"isin": _isin(3), "name": "Sample Europe Dividend Fund A (USD) Dist", "category": "Europe Equity Income",
     "nav0": 20.0, "drift": 0.06, "vol": 0.13, "payout_yield": 0.04, "every": 3, "seed": 13,
     "units": 100.0, "avg_cost": 21.00, "payouts": "cash", "monthly": 25, "day": 31, "fee": 2.0},
]
PEERS = {f["isin"]: [_isin(10 + 2 * i), _isin(11 + 2 * i)] for i, f in enumerate(FUNDS)}


def _walk(seed, nav0, drift, vol, payout_yield, every, start, today):
    """Business-day prices from `start`, and the payouts (the price drops by
    each payout on its ex-date — the last business day of a payout month)."""
    rng = random.Random(seed)
    navs, dists, nav, d = [], [], nav0, start
    daily_sd, daily_mu = vol / math.sqrt(252), drift / 252
    while d <= today:
        if d.weekday() < 5:
            nav *= math.exp(rng.gauss(daily_mu, daily_sd))
            nxt = d + dt.timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += dt.timedelta(days=1)
            month_end = nxt.month != d.month
            if payout_yield and month_end and d.month % every == 0:
                amount = round(nav * payout_yield * every / 12, 4)
                nav -= amount
                dists.append({"ex_date": d, "amount": amount, "pay_date": d + dt.timedelta(days=3)})
            navs.append((d, round(nav, 4)))
        d += dt.timedelta(days=1)
    return navs, dists


def _facts(f, start):
    return {
        "name": f["name"], "category": f["category"], "launch_date": start.strftime("%d %b %Y"),
        "income_treatment": "Income" if f.get("payout_yield") else "Accumulation", "domicile": "Luxembourg",
        "fund_type": "SICAV", "pricing_frequency": "Daily", "ongoing_charge_pct": 1.4, "initial_charge_pct": 5.0,
        "risk_class": 2 if "Bond" in f["name"] else 4, "fund_size": 1.5e9,
        "objective": "A made-up fund for the dashboard's tests and sandbox — not a real investment.",
        "asset_mix": [("Bonds", 92.0), ("Cash", 8.0)] if "Bond" in f["name"] else [("Stocks", 97.0), ("Cash", 3.0)],
        "top_holdings": [{"name": f"Example Holding {i}", "weight": round(6.0 - i * 0.4, 2), "change_1y": None}
                         for i in range(1, 11)],
        "sectors": [("Technology", 22.0, 20.0), ("Financial Services", 15.0, 16.0), ("Healthcare", 12.0, 11.0)],
        "regions": [{"name": "Americas", "weight": 60.0, "category": 55.0,
                     "parts": [{"name": "United States", "weight": 58.0, "category": None, "parts": []}]},
                    {"name": "Greater Europe", "weight": 30.0, "category": 33.0, "parts": []}],
        "holdings_as_of": None, "ft_url": None,
    }


def market(today=None):
    """{isin: data} for the made-up funds and their peers — the shape
    fund_data.fetch_fund() returns, so everything downstream is exercised."""
    today = today or dt.date.today()
    start = dt.date(2016, 1, 4)
    out = {}
    for f in FUNDS:
        navs, dists = _walk(f["seed"], f["nav0"], f["drift"], f["vol"], f["payout_yield"], f["every"], start, today)
        out[f["isin"]] = {"isin": f["isin"], "name": f["name"], "currency": "USD", "navs": navs,
                          "distributions": dists, "facts": _facts(f, start), "source": "Example Asset Management",
                          "source_url": None, "news": [],
                          "cross_check": {"compared": len(navs), "mismatches": 0, "examples": [], "other": "FT"}}
        for j, p in enumerate(PEERS[f["isin"]]):
            pn, _pd = _walk(f["seed"] * 10 + j, f["nav0"], f["drift"] + (0.01 if j else -0.01), f["vol"],
                            None, None, start, today)
            pf = dict(_facts(f, start), name=f"Sample Competitor {j + 1} ({f['category']}) Acc",
                      income_treatment="Accumulation", ongoing_charge_pct=1.2 + 0.3 * j)
            out[p] = {"isin": p, "name": pf["name"], "currency": "USD", "navs": pn, "distributions": [],
                      "facts": pf, "source": "FT"}
    return out


def funds_csv(today=None):
    """portfolio/funds.csv for the sandbox: statements dated 45 days ago, so a
    top-up and some payouts fall after them and land in fund_transactions.csv."""
    today = today or dt.date.today()
    as_of = today - dt.timedelta(days=45)
    m = market(today)
    lines = ["isin,name,units,avg_cost,as_of,statement_nav,currency,payouts,monthly_amount,monthly_day,fee_pct,"
             "peers,factsheet_url,note"]
    for f in FUNDS:
        day, nav = fp.nav_on_or_before(m[f["isin"]]["navs"], as_of)      # a statement shows a business day's price
        lines.append(",".join(str(x) for x in (
            f["isin"], f["name"], f["units"], f["avg_cost"], day.isoformat(), nav, "USD", f["payouts"],
            f["monthly"] or "", f["day"] or "", f["fee"], ";".join(PEERS[f["isin"]]), "", "made-up example")))
    return "\n".join(lines) + "\n"


def write_market(path, today=None):
    """The made-up market data as JSON — point FUND_DATA_FILE at it for a sandbox run."""
    import fund_data
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(fund_data._to_json(market(today)))
    return path
