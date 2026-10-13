"""
Sales — when you sell some or all of a holding.

Private, like the purchase files. Kenyan and international shares and bonds
go in portfolio/sales.csv, one row per sale; a fund's sales go in its own
log, portfolio/fund_transactions.csv (type "sell"), next to its top-ups.

    date        the day you sold
    kind        nse (Kenyan shares) · intl (international shares) · bond
    code        the ticker (EQTY, AAPL) or the bond issue (FXD1/2023/005)
    quantity    shares sold — or, for a bond, the face value sold, as you
                bought it (KES)
    sell_price  price per share (KES / USD) — or, for a bond, the clean
                price in % of face value (e.g. 101.25)
    fees        optional: brokerage and other charges, same currency
    note        optional

How a sale is counted — average cost, the "average cost" the dashboard shows
everywhere:

    gain on the sale = (quantity × sell price − fees) − quantity × average cost

Every purchase made before the sale shrinks by the same share, so the average
cost of what's left doesn't change. A purchase dated after a sale isn't
touched by it; a purchase without a date counts as bought before. A sale of
more than you held on its date is refused: the dashboard app and sell.py
check before saving, and a hand-typed row that doesn't add up is reported in
the log and left out, never guessed.

Bonds: the buyer pays the clean price plus the interest built up since the
last coupon (accrued interest), so a bond sale's result is clean price +
accrued interest + any instalments already repaid − fees − what you paid. On
a bond that repays in parts, the clean price applies to what's still owed.
The coupons the sold part paid you while you held it (after withholding
tax) are listed with the sale, separately. Selling part of a bond keeps the
rest on its schedule.
"""

import datetime as dt
import os

import portfolio_csv as pcsv
from logger import get_logger

logger = get_logger(__name__)

SALES_FILE = "sales.csv"
_F = pcsv.Field
SALE_FIELDS = (
    _F("date", ("sale_date", "date_sold", "sold_on", "trade_date"), "date", True),
    _F("kind", ("market", "asset", "asset_class"), "text", True),
    _F("code", ("symbol", "ticker", "issue", "bond"), "text", True),
    _F("quantity", ("qty", "shares", "face_value", "units"), "number", True),
    _F("sell_price", ("sale_price", "price", "price_pct", "sold_at"), "number", True),
    _F("fees", ("fee", "charges", "commission"), "number", False),
    pcsv._NOTE,
)
KINDS = {"nse": "nse", "kenya": "nse", "kenyan": "nse", "ke": "nse",
         "intl": "intl", "international": "intl", "us": "intl",
         "bond": "bond", "bonds": "bond", "fund": "fund", "funds": "fund"}
CURRENCY = {"nse": "KES", "intl": "USD", "bond": "KES", "fund": "USD"}
EPS = 1e-6                    # shares / units: anything smaller is rounding
FACE_EPS = 0.005              # bonds: half a cent of face value
NEW = 10 ** 9                 # the "line" of a sale being checked (sorts after same-day rows)


class SaleError(ValueError):
    """A sale that can't be recorded as it stands (plain-English message)."""


def money(v, currency):
    return f"KES {v:,.2f}" if currency == "KES" else f"${v:,.2f}"


def signed_pct(v):
    """+12.3% / −4.5% (a real minus sign, like the rest of the dashboard)."""
    return f"{'−' if v < 0 and round(v, 1) != 0 else '+'}{abs(v):.1f}%"


def _canon(kind, code):
    code = (code or "").strip()
    if kind == "bond":
        import bonds_portfolio as B
        return B._canonical_issue(code)
    return code.upper()


def _where(s):
    return "this sale" if s.get("line") in (None, NEW) else f"{SALES_FILE} line {s['line']}"


def load_sales(portfolio_dir):
    """[(line, sale)] from portfolio/sales.csv, oldest first ([] without one)."""
    path = os.path.join(portfolio_dir, SALES_FILE)
    if not os.path.exists(path):
        return []
    try:
        rows = pcsv.read_rows(path, SALE_FIELDS)
    except pcsv.CsvFormatError as e:
        logger.warning(f"Sales: {e}")
        return []
    out = []
    for line, r in rows:
        kind = KINDS.get((r["kind"] or "").strip().lower())
        if kind not in ("nse", "intl", "bond"):
            logger.warning(f"{SALES_FILE} line {line}: kind '{r['kind']}' not understood — use nse, intl or bond "
                           "(a fund's sales go in fund_transactions.csv) — skipping this row")
            continue
        if r["quantity"] <= 0 or r["sell_price"] <= 0 or (r.get("fees") or 0) < 0:
            logger.warning(f"{SALES_FILE} line {line}: quantity and sell_price must be more than 0 (fees 0 or "
                           "more) — skipping this row")
            continue
        out.append((line, {"line": line, "date": r["date"], "kind": kind, "code": _canon(kind, r["code"]),
                           "quantity": float(r["quantity"]), "sell_price": float(r["sell_price"]),
                           "fees": float(r.get("fees") or 0.0), "note": r.get("note") or ""}))
    out.sort(key=lambda x: (x[1]["date"], x[0]))
    return out


def _ordered(sales, kind):
    return sorted((s for s in sales if s["kind"] == kind), key=lambda s: (s["date"], s.get("line") or 0))


# ------------------------------------------------------------------ shares
def _bought_by(lot, day):
    return (lot.get("buy_date") or "0000-00-00") <= day


def apply_stock_sales(lots, sales, kind):
    """
    -> (remaining lots, realized sales, problems) for Kenyan ("nse") or
    international ("intl") shares.

    remaining   the purchases still held, each scaled down by the sales after
                it (the purchase files' shape, plus orig_quantity)
    realized    one dict per sale: date, code, quantity, sell_price, fees,
                proceeds, cost, gain, gain_pct, avg_cost, held_before,
                left_after, flows (for "after inflation"), undated
    problems    sales that didn't add up, in words (they are left out)
    """
    work = [dict(l, orig_quantity=l["quantity"]) for l in lots]
    realized, problems = [], []
    for s in _ordered(sales, kind):
        held = [l for l in work if l["symbol"] == s["code"] and _bought_by(l, s["date"]) and l["quantity"] > EPS]
        qty_held = sum(l["quantity"] for l in held)
        if qty_held + EPS < s["quantity"]:
            problems.append(f"{s['date']}: selling {s['quantity']:,.6g} {s['code']} is more than the "
                            f"{qty_held:,.6g} held then ({_where(s)}) — left out")
            continue
        cost_held = sum(l["quantity"] * l["buy_price"] for l in held)
        avg = cost_held / qty_held
        left = qty_held - s["quantity"]
        factor = left / qty_held if left > EPS else 0.0
        proceeds = s["quantity"] * s["sell_price"] - s["fees"]
        cost = avg * s["quantity"]
        flows, undated = [], False
        for l in held:
            part = l["quantity"] - l["quantity"] * factor
            if l.get("buy_date"):
                flows.append((l["buy_date"], -part * l["buy_price"]))
            else:
                undated = True
            l["quantity"] = round(l["quantity"] * factor, 6)
        flows.append((s["date"], proceeds))
        realized.append({"date": s["date"], "kind": kind, "code": s["code"], "quantity": s["quantity"],
                         "sell_price": s["sell_price"], "fees": s["fees"], "proceeds": proceeds, "cost": cost,
                         "gain": proceeds - cost, "gain_pct": (proceeds - cost) / cost * 100.0 if cost else None,
                         "total_gain": proceeds - cost, "avg_cost": avg, "held_before": qty_held,
                         "left_after": left if left > EPS else 0.0, "currency": CURRENCY[kind],
                         "flows": flows, "undated": undated, "line": s.get("line"), "note": s.get("note", "")})
    return [l for l in work if l["quantity"] > EPS], realized, problems


# ------------------------------------------------------------------ bonds
def _issued(code):
    import bonds_portfolio as B
    return (B.BOND_REFERENCE.get(code) or {}).get("value_date") or "0000-00-00"


def apply_bond_sales(lots, sales):
    """
    -> (remaining lots, realized sales, problems) for bonds. A sale's
    quantity is face value as bought (KES) and its sell_price the clean
    price in % of face. gain = clean + accrued + principal repaid − fees −
    cost; coupons_received (after tax, while you held it) is reported
    beside it, not in it.
    """
    import bonds_portfolio as B
    work = [dict(l, orig_face_value=l["face_value"]) for l in lots]
    realized, problems = [], []
    for s in _ordered(sales, "bond"):
        ref = B.BOND_REFERENCE.get(s["code"])
        issued = _issued(s["code"])
        held = [l for l in work if B._canonical_issue(l["issue"]) == s["code"] and l["face_value"] > FACE_EPS
                and (l.get("purchase_date") or issued) <= s["date"]]
        face_held = sum(l["face_value"] for l in held)
        if face_held + FACE_EPS < s["quantity"]:
            problems.append(f"{s['date']}: selling KES {s['quantity']:,.2f} face value of {s['code']} is more than "
                            f"the KES {face_held:,.2f} held then ({_where(s)}) — left out")
            continue
        sale_day = dt.date.fromisoformat(s["date"])
        bonds = [B.compute_bond(l, as_of=sale_day) for l in held] if ref else []
        if any(b.get("is_matured") for b in bonds):
            problems.append(f"{s['date']}: {s['code']} had already been repaid in full by then "
                            f"({_where(s)}) — left out")
            continue
        share = min(s["quantity"] / face_held, 1.0)
        clean = accrued = coupons = principal = cost = 0.0
        flows, assumed = [], False
        for i, l in enumerate(held):
            part = l["face_value"] * share
            c = part * (l.get("purchase_price_pct") or 100.0) / 100.0
            cost += c
            flows.append((l.get("purchase_date") or issued, -c))
            assumed = assumed or not l.get("purchase_date")
            if bonds and bonds[i].get("data_available"):
                b = bonds[i]          # the whole lot — CBK's small-holder rule follows what you held
                keep = 1 - (0 if b.get("tax_free") else (b.get("withholding_pct") or 0) / 100.0)
                out_frac = b["outstanding_face_today"] / l["face_value"]
                for cf in b["cashflows_all"]:
                    if b["purchase_date"] <= cf["date"] <= s["date"]:
                        amt = cf["amount"] * share * (keep if cf["type"] == "coupon" else 1)
                        flows.append((cf["date"], amt))
                        if cf["type"] == "coupon":
                            coupons += amt
                        else:
                            principal += amt
                accrued += b["accrued_interest"] * out_frac * share
                clean += part * out_frac * s["sell_price"] / 100.0
            else:
                clean += part * s["sell_price"] / 100.0
            l["face_value"] = round(l["face_value"] - part, 2)
        proceeds = clean + accrued - s["fees"]
        gain = proceeds + principal - cost
        flows.append((s["date"], proceeds))
        realized.append({"date": s["date"], "kind": "bond", "code": s["code"], "quantity": s["quantity"],
                         "sell_price": s["sell_price"], "fees": s["fees"], "clean": clean, "accrued": accrued,
                         "proceeds": proceeds, "cost": cost, "coupons_received": coupons,
                         "principal_received": principal, "gain": gain,
                         "gain_pct": gain / cost * 100.0 if cost else None,
                         "price_gain": clean + principal - s["fees"] - cost,
                         "total_gain": gain, "held_before": face_held,
                         "left_after": max(face_held - s["quantity"], 0.0), "currency": "KES",
                         "reference_missing": not ref, "purchase_date_assumed": assumed,
                         "flows": flows, "undated": False, "line": s.get("line"), "note": s.get("note", "")})
    return [l for l in work if l["face_value"] > FACE_EPS], realized, problems


# ------------------------------------------------------------------ one call for everyone who loads holdings
def _run(kind, lots, sales):
    if kind == "bond":
        return apply_bond_sales(lots, sales)
    return apply_stock_sales(lots, sales, kind)


def apply(portfolio_dir, kind, lots, sales=None):
    """(the lots still held after your sales, the sales) — what main.py, the
    email summary, the watchlist and the app use. Sales that don't add up are
    logged and left out."""
    try:
        if sales is None:
            sales = [s for _l, s in load_sales(portfolio_dir)]
        remaining, realized, problems = _run(kind, lots, sales)
    except Exception as e:                       # noqa: BLE001 — never takes the holdings down with it
        logger.error(f"Sales: couldn't apply your {kind} sales ({type(e).__name__}: {e}) — showing holdings "
                     "as bought, before any sale")
        return lots, []
    for p in problems:
        logger.warning(f"Sales: {p}")
    return remaining, realized


def new_problems(kind, lots, sales, lots_after=None, sales_after=None):
    """Sales that would stop adding up after a change (a purchase removed, a
    sale added) — the ones that add up today but not after it."""
    before = set(_run(kind, lots, sales)[2])
    after = _run(kind, lots if lots_after is None else lots_after, sales if sales_after is None else sales_after)[2]
    return [p for p in after if p not in before]


# ------------------------------------------------------------------ what you hold (for the app and sell.py)
def lots_for(portfolio_dir, kind):
    if kind == "nse":
        import portfolio as m
        return m.load_holdings(portfolio_dir)
    if kind == "intl":
        import international_portfolio as m
        return m.load_holdings(portfolio_dir)
    if kind == "bond":
        import bonds_portfolio as m
        return m.load_bonds(portfolio_dir)
    raise SaleError("Choose what you sold: a Kenyan stock, an international stock, a bond or a fund.")


def _code_of(kind, lot):
    return _canon("bond", lot["issue"]) if kind == "bond" else lot["symbol"]


def _size(kind, lot):
    return lot["face_value"] if kind == "bond" else lot["quantity"]


def _unit_cost(kind, lot):
    return (lot.get("purchase_price_pct") or 100.0) / 100.0 if kind == "bond" else lot["buy_price"]


def _bought_on_or_before(kind, lot, day):
    if kind == "bond":
        return (lot.get("purchase_date") or _issued(_code_of(kind, lot))) <= day
    return _bought_by(lot, day)


def positions(portfolio_dir, kind, on=None):
    """What you hold now (or at the end of day `on`), after your sales:
    {code: {"quantity", "cost", "avg_cost", "lots"}} — for bonds quantity
    is face value and avg_cost the average price in % of face."""
    lots = lots_for(portfolio_dir, kind)
    sales = [s for _l, s in load_sales(portfolio_dir)]
    if on:
        lots = [l for l in lots if _bought_on_or_before(kind, l, on)]
        sales = [s for s in sales if s["date"] <= on]
    out = {}
    for l in _run(kind, lots, sales)[0]:
        p = out.setdefault(_code_of(kind, l), {"quantity": 0.0, "cost": 0.0, "lots": 0})
        p["quantity"] += _size(kind, l)
        p["cost"] += _size(kind, l) * _unit_cost(kind, l)
        p["lots"] += 1
    for p in out.values():
        p["quantity"] = round(p["quantity"], 2 if kind == "bond" else 6)
        p["avg_cost"] = (p["cost"] / p["quantity"] * (100.0 if kind == "bond" else 1.0)) if p["quantity"] else None
    return out


def _number(raw, what):
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    try:
        return pcsv.parse_number(str(raw))
    except (TypeError, ValueError):
        raise SaleError(f"{what} '{raw}' isn't a number.")


def _day(raw, today):
    if raw in (None, ""):
        return today
    try:
        day = dt.date.fromisoformat(pcsv.parse_date(str(raw)))
    except ValueError as e:
        raise SaleError(f"The sale date: {e}")
    if day > today:
        raise SaleError(f"The sale date ({day.isoformat()}) is in the future — please check it.")
    return day


def _is_all(raw):
    return isinstance(raw, str) and raw.strip().lower() in ("all", "everything", "whole")


def check_sale(portfolio_dir, kind, code, quantity, sell_price, sale_date=None, fees=None, note="",
               today=None, today_price=None):
    """
    Check one sale of shares or a bond before it's saved. quantity may be "all".
    -> (values to save, summary in words, [warnings]); raises SaleError with a
    plain-English reason when it can't be recorded.
    """
    today = today or dt.date.today()
    kind = KINDS.get(str(kind or "").strip().lower())
    if kind not in ("nse", "intl", "bond"):
        raise SaleError("Choose what you sold: a Kenyan stock, an international stock or a bond.")
    code = _canon(kind, code)
    if not code:
        raise SaleError("Which holding did you sell? Pick it from the list.")
    day = _day(sale_date, today)
    cur = CURRENCY[kind]
    unit = "face value" if kind == "bond" else "shares"
    tiny = FACE_EPS if kind == "bond" else EPS
    held = (positions(portfolio_dir, kind, on=day.isoformat()).get(code) or {}).get("quantity", 0.0)
    if held <= tiny:
        raise SaleError(f"You didn't hold any {code} on {day:%d %b %Y} — check the holding and the date "
                        "(a purchase without a date counts as bought before any sale).")
    if _is_all(quantity):
        qty = held
    else:
        if quantity in (None, ""):
            raise SaleError(f"Enter how much you sold ({unit}), or choose Sell all.")
        qty = _number(quantity, "The face value" if kind == "bond" else "The number of shares")
        if qty <= 0:
            raise SaleError(f"Enter how much you sold ({unit}), or choose Sell all.")
        if qty > held + tiny:
            amount = f"KES {held:,.2f} face value" if kind == "bond" else f"{held:,.6g} shares"
            raise SaleError(f"On {day:%d %b %Y} you held {amount} of {code}, so you can't sell more than that. "
                            "Choose Sell all to sell everything.")
        qty = min(qty, held)
    if sell_price in (None, ""):
        raise SaleError("Enter the price you sold at" + (" — the clean price in % of face value, e.g. 101.25."
                                                        if kind == "bond" else " (per share)."))
    price = _number(sell_price, "The price")
    if price <= 0:
        raise SaleError("The sale price must be more than zero.")
    fee = 0.0 if fees in (None, "") else _number(fees, "The fees")
    if fee < 0:
        raise SaleError("Fees can't be negative.")
    value = {"date": day.isoformat(), "kind": kind, "code": code,
             "quantity": round(qty, 2 if kind == "bond" else 6), "sell_price": price, "fees": fee,
             "note": " ".join((note or "").split())[:200]}

    lots = lots_for(portfolio_dir, kind)
    sales = [s for _l, s in load_sales(portfolio_dir)]
    mine = dict(value, line=NEW)
    broken = new_problems(kind, lots, sales, sales_after=sales + [mine])
    if broken:
        raise SaleError("That would leave a later sale bigger than what's held: " + broken[0])
    sale = next(r for r in _run(kind, lots, sales + [mine])[1] if r["line"] == NEW)
    if kind == "bond":
        import bonds_portfolio as B
        ref = B.BOND_REFERENCE.get(code)
        if ref and day.isoformat() >= ref.get("maturity_date", "9999"):
            raise SaleError(f"{code} matured on {ref['maturity_date']} — it was repaid, not sold.")

    warnings = []
    if kind == "bond":
        if not 50 <= price <= 150:
            warnings.append(f"{price:g}% of face value is an unusual price — it's the clean price in %, e.g. "
                            "101.25, not an amount in shillings.")
        if sale["reference_missing"]:
            warnings.append(f"{code}'s coupon schedule isn't in the dashboard, so the interest it paid you "
                            "and the accrued interest aren't counted.")
        what = f"KES {qty:,.2f} face value of {code} at {price:g}%"
        if sale["principal_received"]:
            what += (f" (KES {sale['principal_received']:,.2f} of it was already repaid, so the price "
                     f"applies to the KES {qty - sale['principal_received']:,.2f} still outstanding)")
        what += f" = {money(sale['clean'], cur)} + accrued interest {money(sale['accrued'], cur)}"
        sold_for = sale["clean"]
    else:
        if today_price and (price > today_price * 1.5 or price < today_price / 1.5):
            warnings.append(f"You entered {money(price, cur)} but {code} trades at {money(today_price, cur)} "
                            "today. Fine if you sold a while ago — otherwise check it's the price per share, "
                            "not the total.")
        if kind == "nse" and not float(qty).is_integer():
            warnings.append("NSE shares are normally sold in whole numbers — check the number of shares.")
        what = f"{qty:,.6g} {code} at {money(price, cur)} each = {money(qty * price, cur)}"
        sold_for = qty * price
    if fee and fee > sold_for * 0.1:
        warnings.append(f"Fees of {money(fee, cur)} are more than a tenth of the sale — please check them.")
    undated = [l for l in lots if _code_of(kind, l) == code
               and not (l.get("purchase_date") if kind == "bond" else l.get("buy_date"))]
    if undated and kind != "bond":
        warnings.append(f"{len(undated)} of your {code} purchases {'has' if len(undated) == 1 else 'have'} no date, so "
                        "they're counted as bought before this sale. If you bought any of them after it, add its "
                        "buy_date first — otherwise the profit is worked out from the wrong average cost.")
    left = held - qty
    gain = sale["gain"]
    still = f"KES {left:,.2f} face value" if kind == "bond" else f"{left:,.6g} shares"
    summary = (f"Sell {what}" + (f", less fees {money(fee, cur)}" if fee else "") + f", on {day:%d %b %Y}. "
               f"{'Profit' if gain >= 0 else 'Loss'} vs what you paid: "
               f"{'+' if gain >= 0 else '−'}{money(abs(gain), cur)}"
               + (f" ({signed_pct(sale['gain_pct'])})" if sale.get("gain_pct") is not None else "")
               + (f" (accrued interest included); it also paid you {money(sale['coupons_received'], cur)} in "
                  "coupons while you held it" if kind == "bond" and sale["coupons_received"] else "")
               + (f". You'll still hold {still}." if left > tiny else ". That's all of it."))
    return value, summary, warnings


def has_sales(portfolio_dir, kind, code):
    """Whether any recorded sale is of this holding (for "add a date" warnings on purchases)."""
    code = _canon(kind, code)
    return any(s["kind"] == kind and s["code"] == code for _l, s in load_sales(portfolio_dir))


def add_sale(portfolio_dir, values):
    """Append a checked sale to portfolio/sales.csv (creating the file if
    needed). Returns the fields the file had no column for."""
    path = os.path.join(portfolio_dir, SALES_FILE)
    if not os.path.exists(path):
        pcsv.create_csv(path, SALE_FIELDS)
    row = {k: values.get(k) for k in ("date", "kind", "code", "quantity", "sell_price", "fees", "note")}
    if not row["fees"]:
        row["fees"] = None
    return pcsv.append_row(path, SALE_FIELDS, row)


# ------------------------------------------------------------------ the page's list
KIND_LABEL = {"nse": "Kenyan shares", "intl": "International shares", "bond": "Bonds", "fund": "Funds"}


def summarise(sales, funds_summary=None, macro=None, usd_kes=None):
    """
    Every sale, newest first, for My Portfolio — shares and bonds from
    sales.apply(), funds from funds_summary — each also in shillings: a
    US-dollar sale at the Central Bank's average rate for its month (today's
    rate for a month not published yet). None when nothing was sold.
    """
    import inflation
    fx = inflation._fx_lookup(macro or {}, usd_kes)
    rate_now = (usd_kes or {}).get("rate")
    items = []
    for kind in ("nse", "intl", "bond"):
        for r in (sales or {}).get(kind) or []:
            items.append(dict(r, name=r["code"]))
    for h in (funds_summary or {}).get("funds", []):
        for s in h.get("sales") or []:
            items.append(dict(s, kind="fund", code=h["isin"], name=h["name"], quantity=s["units"],
                              sell_price=s["price"], currency=h.get("currency") or "USD", total_gain=s["gain"],
                              report_file=h.get("report_file")))
    if not items:
        return None
    for it in items:
        if it["currency"] == "KES":
            rate = 1.0
        else:
            rate = fx(it["date"]) or rate_now
        it["rate"] = rate
        for k in ("proceeds", "cost", "gain", "total_gain"):
            it[k + "_kes"] = it[k] * rate if rate else None
    items.sort(key=lambda x: (x["date"], x.get("line") or 0), reverse=True)
    totals, by_kind = {"count": len(items)}, {}
    for k in ("proceeds_kes", "gain_kes", "total_gain_kes"):
        vals = [it[k] for it in items if it[k] is not None]
        totals[k] = sum(vals) if vals else None
    totals["bond_interest_kes"] = sum(it.get("coupons_received") or 0.0 for it in items if it["kind"] == "bond")
    totals["unconverted"] = sum(1 for it in items if it["gain_kes"] is None)
    for it in items:
        b = by_kind.setdefault(it["kind"], {"count": 0, "gain": 0.0, "total_gain": 0.0, "proceeds": 0.0,
                                            "currency": it["currency"], "label": KIND_LABEL[it["kind"]]})
        b["count"] += 1
        for k in ("gain", "total_gain", "proceeds"):
            b[k] += it[k]
    year = max(it["date"] for it in items)[:4]
    ytd = [it["total_gain_kes"] for it in items if it["date"][:4] == year and it["total_gain_kes"] is not None]
    totals.update(year=year, year_total_gain_kes=sum(ytd) if ytd else None,
                  wins=sum(1 for it in items if it["total_gain"] > 0),
                  losses=sum(1 for it in items if it["total_gain"] < 0))
    return {"items": items, "totals": totals, "by_kind": by_kind}


# ------------------------------------------------------------------ funds (their own log: fund_transactions.csv)
def _fund_units(fund, txns, until=None):
    """(units, money in at average cost, [dates of rows not priced yet]) at the end of day `until`."""
    import funds_portfolio as fp
    units, invested, pending = fund["units"], fund["units"] * fund["avg_cost"], []
    for t in txns:
        if until and fp._date(t["date"]) > until:
            break
        u = t.get("units")
        if t["type"] in ("topup", "buy", "reinvest"):
            if u is None:
                pending.append(fp._date(t["date"]).isoformat())
                continue
            units += u
            if t["type"] != "reinvest":
                invested += t.get("amount") or 0.0
        elif t["type"] == "sell" and u:
            avg = invested / units if units > EPS else 0.0
            invested -= avg * u
            units -= u
            if units <= 0.0005:
                units = invested = 0.0
    return units, invested, pending


def fund_positions(portfolio_dir, cache_dir=None):
    """{isin: {"name", "units", "pending", "avg_cost", "nav", "nav_date", ...}} — each fund's units now."""
    import funds_portfolio as fp
    out = {}
    txns = [t for _l, t in fp.load_transactions(portfolio_dir)]
    for f in fp.load_funds(portfolio_dir):
        mine = [t for t in txns if t["isin"] == f["isin"] and fp._date(t["date"]) > fp._date(f["as_of"])]
        units, invested, pending = _fund_units(f, mine)
        nav = None
        if cache_dir:
            import fund_data
            navs = sorted((fund_data.cached(f["isin"], cache_dir) or {}).get("navs") or [])
            nav = navs[-1] if navs else None
        out[f["isin"]] = {"name": f["name"], "units": round(units, 3), "pending": pending,
                          "avg_cost": invested / units if units > EPS and not pending else None,
                          "nav": nav[1] if nav else None, "nav_date": nav[0].isoformat() if nav else None,
                          "currency": f.get("currency") or "USD", "as_of": f["as_of"]}
    return out


def check_fund_sale(portfolio_dir, isin, units, sale_date=None, amount=None, note="", today=None,
                    cache_dir=None):
    """
    Check one fund sale before it's saved. units may be "all"; amount (the
    cash your bank paid) is optional — without it the dashboard works it out
    from the dealing day's price. -> (row for fund_transactions.csv, summary, [warnings]).
    """
    import funds_portfolio as fp
    today = today or dt.date.today()
    isin = (isin or "").strip().upper()
    fund = next((f for f in fp.load_funds(portfolio_dir) if f["isin"] == isin), None)
    if not fund:
        raise SaleError("Pick the fund you sold from the list.")
    day = _day(sale_date, today)
    as_of = fp._date(fund["as_of"])
    if day <= as_of:
        raise SaleError(f"Your statement for this fund is dated {as_of:%d %b %Y}, and its units already "
                        "include anything sold up to then — enter a sale after that date, or update the "
                        "statement figures in funds.csv.")
    txns = [t for _l, t in fp.load_transactions(portfolio_dir) if t["isin"] == isin and fp._date(t["date"]) > as_of]
    held, invested, pending = _fund_units(fund, txns, until=day)
    if pending:
        raise SaleError(f"A purchase or top-up on {pending[0]} isn't priced yet, so the exact units you held "
                        "aren't known. Run an update once that day's price is out (usually the next working "
                        "day), or confirm its units from your bank's statement first.")
    held = round(held, 3)
    if held <= 0.0005:
        raise SaleError(f"You didn't hold any units of this fund on {day:%d %b %Y}.")
    if _is_all(units):
        qty = held
    else:
        if units in (None, ""):
            raise SaleError("Enter the number of units you sold, or choose Sell all.")
        qty = round(_number(units, "The number of units"), 3)
        if qty <= 0:
            raise SaleError("Enter the number of units you sold, or choose Sell all.")
        if qty > held + 0.0005:
            raise SaleError(f"On {day:%d %b %Y} you held {held:,.3f} units, so you can't sell more than that. "
                            "Choose Sell all to sell everything.")
    cash = None if amount in (None, "") else _number(amount, "The amount")
    if cash is not None and cash <= 0:
        raise SaleError("The amount you received must be more than zero (or leave it blank).")
    # every later sale must still fit
    left_now = _fund_units(fund, txns)[0] - qty
    if left_now < -0.0005:
        raise SaleError(f"A later sale already uses these units — together they'd sell {-left_now:,.3f} more "
                        "units than you hold. Check the sales in Your entries.")

    import fund_data
    m = (fund_data.cached(isin, cache_dir) if cache_dir else None) or {}
    navs = sorted(m.get("navs") or [])
    hit = fp.nav_on_or_after(navs, day) or fp.nav_on_or_before(navs, day)
    cur = fund.get("currency") or "USD"
    warnings = []
    est = qty * hit[1] if hit else None
    if cash is not None and est and abs(cash - est) > est * 0.1:
        warnings.append(f"{money(cash, cur)} is more than 10% away from {qty:,.3f} units × the "
                        f"{hit[0]:%d %b} price {money(hit[1], cur)} = {money(est, cur)} — check the units "
                        "and the amount.")
    avg = invested / held if held else 0.0
    got = cash if cash is not None else est
    row = {"date": day.isoformat(), "isin": isin, "type": "sell", "units": qty, "amount": cash,
           "price": round(cash / qty, 6) if cash is not None else None,
           "status": "confirmed" if cash is not None else "pending",
           "note": " ".join((note or "").split())[:200] or "sale recorded in the dashboard"}
    summary = f"Sell {qty:,.3f} units of {fund['name']} on {day:%d %b %Y}"
    if cash is not None:
        summary += f" for {money(cash, cur)}"
    elif hit:
        summary += (f" — about {money(est, cur)} at the {hit[0]:%d %b} price of {money(hit[1], cur)} "
                    "(the dashboard uses the dealing day's price once it's published)")
    if got is not None:
        g = got - avg * qty
        summary += (f". {'Profit' if g >= 0 else 'Loss'} vs what you paid: {'+' if g >= 0 else '−'}"
                    f"{money(abs(g), cur)}" + (f" ({signed_pct(g / (avg * qty) * 100)})" if avg * qty else ""))
    left = held - qty
    summary += f". You'll still hold {left:,.3f} units." if left > 0.0005 else ". That's all of it."
    if left <= 0.0005:
        later = [t for t in txns if t["type"] == "topup" and fp._date(t["date"]) > day]
        if later:
            warnings.append("There's an automatic top-up recorded after this date "
                            f"({later[0]['ref'] or later[0]['date']}) — if your monthly plan stopped, remove it "
                            "in Your entries.")
        if fund.get("monthly_amount"):
            warnings.append("Selling everything stops the automatic monthly top-ups in the dashboard. If your "
                            "bank still invests every month, record the next one as a purchase and they resume.")
    return row, summary, warnings


def add_fund_sale(portfolio_dir, row):
    """Append a checked fund sale to portfolio/fund_transactions.csv."""
    import funds_portfolio as fp
    path = os.path.join(portfolio_dir, fp.TXN_FILE)
    if not os.path.exists(path):
        pcsv.create_csv(path, fp.TXN_FIELDS)
    return pcsv.append_row(path, fp.TXN_FIELDS, row)
