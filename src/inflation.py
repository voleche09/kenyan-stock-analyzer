"""
Inflation — are your investments growing faster than prices?

Two jobs:

  1. Kenya's inflation for the Market Pulse page: the latest rate, its
     history month by month and year by year (data from fetch_kenya() in
     inflation_data.py).
  2. "After inflation" for your portfolio: money you put in years ago is
     worth less today, because prices rose since. Every amount is moved to
     today's shillings with the consumer price index (CPI):

        today's shillings = amount × CPI(now) ÷ CPI(month it happened)

     Then, for the holdings whose dates are known:

        put in, in today's shillings    Σ purchases, each grown by inflation since
        got back, in today's shillings  value today + income received (each grown too)
        real gain                       got back − put in   (>0 = you can buy MORE than before)
        real yearly return              the yearly rate on those inflation-adjusted amounts

     A holding without a date is left out (and listed) — inflation since an
     unknown day can't be worked out, and a guess would be wrong.

Shillings only: US-dollar amounts are converted at the USD/KES rate of the
day they happened (purchases) or today's (value), so the shilling's moves
against the dollar are part of the result — what matters when the money is
spent in Kenya.
"""

import bisect
import datetime as dt

import funds_portfolio as fp


def month(d):
    d = fp._date(d)
    return dt.date(d.year, d.month, 1)


class CPI:
    """A monthly consumer price index: [(first day of month, index)], oldest first."""

    def __init__(self, points):
        self.points = sorted((month(d), float(v)) for d, v in points if v)
        self._months = [d for d, _v in self.points]

    def __bool__(self):
        return bool(self.points)

    @property
    def latest(self):
        return self.points[-1] if self.points else None

    def at(self, d):
        """The index for the month of `d` — or the latest published one, for a
        month that isn't out yet. None before the series starts."""
        m = month(d)
        i = bisect.bisect_right(self._months, m)
        if i == 0:
            return None
        return self.points[i - 1][1]

    def factor(self, then, now=None):
        """How many shillings today buy what 1 shilling bought then (≥ 1 when prices rose)."""
        a = self.at(then)
        b = self.at(now) if now else (self.latest[1] if self.latest else None)
        return (b / a) if (a and b) else None

    def change_pct(self, then, now=None):
        f = self.factor(then, now)
        return (f - 1) * 100.0 if f else None


def real_result(flows, value_now, cpi, today, fx=None, currency="KES", fx_now=None):
    """
    The after-inflation result of one holding (or a whole group).

    flows     [(date, amount)] — money in negative (purchases), money out
              positive (income, sales), in the holding's currency
    value_now what it's worth today, in the holding's currency
    fx        for USD: callable(date) -> KES per USD that day (None if unknown)
    fx_now    for USD: today's rate, for the value now (the same rate the rest
              of the page uses); falls back to fx(today)
    Returns None when an amount can't be put in shillings or a date is
    before the price index starts.
    """
    today = fp._date(today)
    flows = [(fp._date(d), a) for d, a in flows]
    rate_now = 1.0 if currency == "KES" else (fx_now or (fx(today) if fx else None))
    if rate_now is None:
        return None
    put_in = put_in_real = back = back_real = 0.0
    real_flows = []
    for d, amount in flows:
        rate = 1.0 if currency == "KES" else (fx(d) if fx else None)
        f = cpi.factor(d, today)
        if rate is None or f is None:
            return None
        kes = amount * rate
        if kes < 0:
            put_in += -kes
            put_in_real += -kes * f
        else:
            back += kes
            back_real += kes * f
        real_flows.append((fp._date(d), kes * f))
    value_kes = value_now * rate_now
    real_flows.append((today, value_kes))
    gain = value_kes + back - put_in
    real_gain = value_kes + back_real - put_in_real
    first = min((d for d, _a in flows), default=today)
    return {
        "put_in": put_in, "put_in_today": put_in_real, "inflation_cost": put_in_real - put_in,
        "income": back, "value": value_kes, "gain": gain, "gain_pct": gain / put_in * 100.0 if put_in else None,
        "real_gain": real_gain, "real_gain_pct": real_gain / put_in_real * 100.0 if put_in_real else None,
        "real_yearly": fp.xirr(real_flows) if (today - fp._date(first)).days >= 180 else None,
        "since": fp._date(first).isoformat(), "years": (today - fp._date(first)).days / 365.25,
    }


def combine(results):
    """Add several real_result()s together (the yearly rate isn't additive — dropped)."""
    results = [r for r in results if r]
    if not results:
        return None
    out = {k: sum(r[k] for r in results) for k in ("put_in", "put_in_today", "inflation_cost", "income", "value",
                                                   "gain", "real_gain")}
    out["gain_pct"] = out["gain"] / out["put_in"] * 100.0 if out["put_in"] else None
    out["real_gain_pct"] = out["real_gain"] / out["put_in_today"] * 100.0 if out["put_in_today"] else None
    out["since"] = min(r["since"] for r in results)
    out["years"] = max(r["years"] for r in results)
    return out


def needed_today(cpi, years, today=None, amount=100000.0):
    """Shillings needed today to buy what `amount` bought `years` ago (None
    when the index doesn't go back that far)."""
    today = fp._date(today or dt.date.today())
    then = dt.date(today.year - years, today.month, 1)
    f = cpi.factor(then, today)
    return amount * f if f else None


# ------------------------------------------------------------------ your portfolio, after inflation
def _fx_lookup(macro, usd_kes):
    """KES per USD on a date: that month's CBK average; the latest rate for
    months CBK hasn't published yet; None before 1993."""
    fxm = dict(macro.get("fx") or [])
    rate_now = (usd_kes or {}).get("rate")
    last = max(fxm) if fxm else None

    def fx(d):
        m = month(d)
        if m in fxm:
            return fxm[m]
        if last and m > last:
            return rate_now or fxm[last]
        return None
    return fx


def _monthly(start, end):
    """The same day of each month from `start` to `end` (inclusive of start)."""
    start, end = fp._date(start), fp._date(end)
    out, d = [], start
    while d <= end:
        out.append(d)
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        d = fp.scheduled_day(y, m, start.day)
    return out or [start]


def portfolio_real(ps, bp, ips, fs, macro, usd_kes, today=None, sales=None):
    """After inflation, part by part, for every holding whose dates are known
    — and every sale (`sales` from sales.apply(): what you paid, any income,
    and what the sale brought in). Returns {"parts": {key: {...}}, "total":
    {...}, ...} or None without a price index."""
    cpi = CPI((macro or {}).get("cpi") or [])
    if not cpi:
        return None
    today = fp._date(today or dt.date.today())
    fx = _fx_lookup(macro or {}, usd_kes)
    rate_now = (usd_kes or {}).get("rate")
    parts = {}

    def part(key, label):
        return parts.setdefault(key, {"label": label, "items": [], "undated": [], "too_early": [], "assumed": []})

    for key, label, summary, cur in (("stocks", "Kenyan shares", ps, "KES"), ("intl", "International shares", ips, "USD")):
        if not summary:
            continue
        p = part(key, label)
        for h in summary.get("holdings", []):
            if not h.get("data_available") or h.get("price") is None:
                continue
            for lot in h.get("lots") or []:
                desc = f'{h["symbol"]} · {lot["quantity"]:g} @ {lot["buy_price"]:g}'
                if not lot.get("buy_date"):
                    p["undated"].append(desc)
                    continue
                r = real_result([(lot["buy_date"], -lot["quantity"] * lot["buy_price"])], lot["quantity"] * h["price"],
                                cpi, today, fx=fx, currency=cur, fx_now=rate_now)
                (p["items"].append(dict(r, name=desc)) if r else p["too_early"].append(desc))
    if bp:
        p = part("bonds", "Bonds")
        for b in bp.get("bonds", []):
            if not b.get("data_available"):
                continue
            keep = 1 - (0 if b.get("tax_free") else (b.get("withholding_pct") or 0) / 100.0)
            flows = [(b["purchase_date"], -b["cost_basis"])]
            for cf in b.get("cashflows_past") or []:
                if fp._date(cf["date"]) >= fp._date(b["purchase_date"]) and cf.get("amount"):
                    flows.append((cf["date"], cf["amount"] * (keep if cf.get("type") == "coupon" else 1)))
            r = real_result(flows, b.get("indicative_value_par") or 0.0, cpi, today)
            if b.get("purchase_date_assumed"):
                p["assumed"].append(b["issue"])
            (p["items"].append(dict(r, name=b["issue"])) if r else p["too_early"].append(b["issue"]))
    if fs:
        p = part("funds", "Funds")
        for h in fs.get("funds", []):
            if not h.get("data_available"):
                continue
            if not h.get("invested_since"):
                p["undated"].append(h["name"])
                continue
            start_cost = h["statement_units"] * h["statement_avg_cost"]
            dates = (_monthly(h["invested_since"], h["as_of"]) if h.get("monthly_amount")
                     else [fp._date(h["invested_since"])])
            flows = [(d, -start_cost / len(dates)) for d in dates]
            for t in h.get("transactions") or []:
                amt = t.get("amount") or 0.0
                if t["type"] in ("topup", "buy"):
                    flows.append((t["date"], -amt))
                elif t["type"] in ("sell", "payout"):
                    flows.append((t["date"], amt))
            r = real_result(flows, h["market_value"], cpi, today, fx=fx, currency="USD", fx_now=rate_now)
            if h.get("monthly_amount"):
                p["assumed"].append(h["name"])
            (p["items"].append(dict(r, name=h["name"])) if r else p["too_early"].append(h["name"]))
    # what you sold: worth nothing now — the money came back to you on the sale day
    for kind, key, label in (("nse", "stocks", "Kenyan shares"), ("intl", "intl", "International shares"),
                             ("bond", "bonds", "Bonds")):
        for s in (sales or {}).get(kind) or []:
            p = part(key, label)
            desc = f'{s["code"]} · sold {s["quantity"]:g} on {s["date"]}'
            if s.get("undated"):
                p["undated"].append(desc)
                continue
            r = real_result(s["flows"], 0.0, cpi, today, fx=fx, currency=s["currency"], fx_now=rate_now)
            if kind == "bond" and s.get("purchase_date_assumed"):
                p["assumed"].append(s["code"])
            (p["items"].append(dict(r, name=desc)) if r else p["too_early"].append(desc))
    for p in parts.values():
        p["result"] = combine(p["items"])
    total = combine([p["result"] for p in parts.values()])
    return {"parts": parts, "total": total, "cpi_month": cpi.latest[0].isoformat(),
            "undated": sum(len(p["undated"]) for p in parts.values())}


def race(bp, fs, macro, tbill_pct=None):
    """This year's race against inflation — no purchase dates needed:
    [(what, rate %, note)] next to the latest 12-month inflation."""
    macro = macro or {}
    infl = (macro.get("latest") or {}).get("yoy")
    if infl is None:
        return None
    rows = []
    tb = tbill_pct if tbill_pct is not None else (macro.get("tbill_latest") or (None, None))[1]
    if tb is not None:
        rows.append(("91-day Treasury bill", tb, "the government's 3-month rate, before tax"))
    if bp and bp.get("totals"):
        t = bp["totals"]
        value = t.get("indicative_value_par") or 0.0
        if value and t.get("annual_after_tax_income"):
            rows.append(("Your bonds", t["annual_after_tax_income"] / value * 100.0,
                         "yearly interest after tax ÷ their value today"))
    fx_1y = macro.get("fx_1y_pct")
    if fs and fx_1y is not None:
        pairs = [(h["market_value"], h["returns"]["periods"].get("1Y")) for h in fs.get("funds", [])
                 if h.get("data_available") and h["returns"]["periods"].get("1Y") is not None]
        weight = sum(v for v, _r in pairs)
        if weight:
            usd = sum(v * r for v, r in pairs) / weight
            rows.append(("Your funds, last 12 months", ((1 + usd / 100) * (1 + fx_1y / 100) - 1) * 100.0,
                         f"{usd:+.1f}% in dollars; the dollar {'gained' if fx_1y >= 0 else 'lost'} "
                         f"{abs(fx_1y):.1f}% against the shilling"))
    return {"inflation": infl, "rows": [(w, r, n, r - infl) for w, r, n in rows]}
