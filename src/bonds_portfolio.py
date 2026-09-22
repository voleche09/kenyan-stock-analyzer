"""
Personal BOND portfolio tracker — pure data + finance layer.

Reads the user's PRIVATE bond holdings (portfolio/bonds.json, gitignored —
never committed) and computes coupon schedules, accrued interest, cash-flow
projections, tax treatment and yield/ROI for Kenya Government Treasury and
Infrastructure Bonds.

WHY A SEPARATE MODULE FROM portfolio.py
    Bonds are a completely different instrument from equities: they have a
    fixed, contractual cash-flow schedule known in advance (coupon dates,
    coupon amounts, and — for Kenyan Infrastructure Bonds — a partial
    "amortization" repayment of principal before final maturity) rather than
    a fluctuating market price you look up. Almost everything useful about a
    bond can be computed from its terms with certainty; this module keeps
    that certain, contractual math separate from treasury_securities.py
    (which scrapes CBK's live, changing PRIMARY-MARKET offer data for the
    PUBLIC "Government Bonds" tab) and from portfolio.py (which handles the
    user's STOCK holdings).

DESIGN RULES (money is involved — no room for error, same discipline as
portfolio.py):
  - A bond's coupon rate, issue date, maturity date and redemption structure
    are FIXED FACTS at issuance. They are stored here as a small, cited
    reference table (BOND_REFERENCE) rather than re-scraped every run — they
    never change, and re-fetching a PDF on every daily run would be slow and
    fragile for data that cannot change. Every entry cites the exact CBK
    prospectus it was verified against.
  - An issue code the user enters that ISN'T in BOND_REFERENCE is never
    guessed at — it's surfaced as "unrecognized bond, add reference data"
    rather than shown with fabricated terms.
  - "Current market value" of a bond (what you could sell it for today) is
    NOT a fact — it depends on where market yields are trading right now,
    which this module cannot observe directly (Kenya's secondary bond
    market doesn't publish live retail quotes per ISIN the way NSE equities
    do). So this module deliberately does NOT invent one single "current
    value" number. Instead it shows:
      (a) the certain, contractual numbers (face value, accrued interest,
          coupon schedule, redemption schedule, YTM-at-par), and
      (b) a genuine price/yield SENSITIVITY table computed from the standard
          bond-pricing formula, so the user can look up today's real yield
          (e.g. from a recent CBK auction result) and read off an honest
          estimate — instead of trusting a single guessed figure.
  - Day-count convention: accrued interest uses Actual/365 on the ANNUAL
    coupon rate, matching CBK's own worked examples in its prospectuses
    (verified by reverse-engineering the "Accrued Interest (AI)" figures
    CBK itself publishes — see BOND_REFERENCE citations). The periodic
    coupon amount itself (face x coupon/2) is fixed and NOT day-count
    adjusted, also matching CBK's convention.
"""

import os
import json
import math
import datetime as dt

from logger import get_logger

logger = get_logger(__name__)

BONDS_FILE = "bonds.json"
BONDS_HISTORY_FILE = "bonds_history.json"

SMALL_HOLDER_THRESHOLD = 1_000_000  # KES — CBK's "redeemed in full" cutoff, per CDS account


# ----------------------------------------------------------------------------
# Reference data — FIXED, contractual facts, verified against CBK prospectuses.
# Nothing here changes day to day; each bond cites the exact document it was
# checked against so the numbers can be re-verified at any time.
# ----------------------------------------------------------------------------
BOND_REFERENCE = {
    "IFB1/2023/6.5": {
        "type": "IFB",
        "type_label": "Infrastructure Bond",
        "tenor_label": "6.5-year",
        "coupon_pct": 17.9327,
        "tax_free": True,
        "withholding_pct": 0.0,
        "value_date": "2023-11-13",
        "maturity_date": "2030-05-06",
        "coupon_dates": [
            "2024-05-13", "2024-11-11", "2025-05-12", "2025-11-10",
            "2026-05-11", "2026-11-09", "2027-05-10", "2027-11-08",
            "2028-05-08", "2028-11-06", "2029-05-07", "2029-11-05",
            "2030-05-06",
        ],
        "redemption_structure": [
            {"date": "2027-05-10", "pct_of_original_face": 50.0},
            {"date": "2029-05-07", "pct_of_original_face": 30.0},
            {"date": "2030-05-06", "pct_of_original_face": 20.0},
        ],
        "small_holder_rule_confirmed": False,  # not restated in the Aug-2024 reopening prospectus
        "source": "CBK prospectus, re-opened 6.5 & 17yr IFBs, dated 19-Aug-2024",
        "source_url": "https://www.centralbank.go.ke/uploads/treasury_bonds_prospectuses/"
                      "718912010_AUG%202024%20IFB1-2023-6.5%20AND%20IFB1-2023-017%20"
                      "DATED%2019-AUG%202024.pdf",
    },
    "IFB/2024/8.5": {  # normalized alias — CBK's real code is IFB1/2024/8.5
        "type": "IFB",
        "type_label": "Infrastructure Bond",
        "tenor_label": "8.5-year",
        "coupon_pct": 18.4607,
        "tax_free": True,
        "withholding_pct": 0.0,
        "value_date": "2024-02-19",
        "maturity_date": "2032-08-09",
        "coupon_dates": [
            "2024-08-19", "2025-02-17", "2025-08-18", "2026-02-16",
            "2026-08-17", "2027-02-15", "2027-08-16", "2028-02-14",
            "2028-08-14", "2029-02-12", "2029-08-13", "2030-02-11",
            "2030-08-12", "2031-02-10", "2031-08-11", "2032-02-09",
            "2032-08-09",
        ],
        "redemption_structure": [
            {"date": "2027-02-15", "pct_of_original_face": 20.0},
            {"date": "2030-02-11", "pct_of_original_face": 30.0},
            {"date": "2032-08-09", "pct_of_original_face": 50.0},
        ],
        "small_holder_rule_confirmed": True,
        "source": "CBK original prospectus IFB1/2024/8.5, dated 19-02-2024",
        "source_url": "https://www.centralbank.go.ke/uploads/treasury_bonds_prospectuses/"
                      "2087959385_February%202024%20IFB1-2024-8.5%20%20DATED%2019-02-2024.pdf",
    },
    "IFB1/2023/17": {
        "type": "IFB",
        "type_label": "Infrastructure Bond",
        "tenor_label": "17-year",
        "coupon_pct": 14.3990,
        "tax_free": True,
        "withholding_pct": 0.0,
        "value_date": "2023-03-13",
        "maturity_date": "2040-02-20",
        "coupon_dates": [
            "2023-09-11", "2024-03-11", "2024-09-09", "2025-03-10", "2025-09-08",
            "2026-03-09", "2026-09-07", "2027-03-08", "2027-09-06", "2028-03-06",
            "2028-09-04", "2029-03-05", "2029-09-03", "2030-03-04", "2030-09-02",
            "2031-03-03", "2031-09-01", "2032-03-01", "2032-08-30", "2033-02-28",
            "2033-08-29", "2034-02-27", "2034-08-28", "2035-02-26", "2035-08-27",
            "2036-02-25", "2036-08-25", "2037-02-23", "2037-08-24", "2038-02-22",
            "2038-08-23", "2039-02-21", "2039-08-22", "2040-02-20",
        ],
        "redemption_structure": [
            {"date": "2033-02-28", "pct_of_original_face": 50.0},
            {"date": "2040-02-20", "pct_of_original_face": 50.0},
        ],
        "small_holder_rule_confirmed": True,
        "source": "CBK original prospectus IFB1/2023/17, dated 13-03-2023",
        "source_url": "https://www.centralbank.go.ke/uploads/treasury_bonds_prospectuses/"
                      "344731597_MARCH%202023%20IFB1-2023-17%20%20DATED%2013-03-2023.pdf",
    },
    "FXD1/2022/025": {
        "type": "FXD",
        "type_label": "Fixed-coupon Treasury Bond",
        "tenor_label": "25-year",
        "coupon_pct": 14.1880,
        "tax_free": False,
        "withholding_pct": 10.0,  # tenor >= 10 years -> 10% WHT (confirmed in CBK pricing table)
        "value_date": "2022-09-23",
        "maturity_date": "2047-09-23",
        "coupon_dates": None,  # bullet bond, generated (Mar 23 / Sep 23 each year) — see _coupon_dates()
        "redemption_structure": [
            {"date": "2047-09-23", "pct_of_original_face": 100.0},
        ],
        "small_holder_rule_confirmed": False,  # N/A — FXD bonds are not amortized
        "source": "CBK prospectus, re-opened 20 & 25yr FXDs, dated 27-Jul-2026",
        "source_url": "https://www.centralbank.go.ke/uploads/treasury_bonds_prospectuses/"
                      "1604163180_JULY%202026%20FXD1-2019-020%20AND%20FXD1-2022-025%20DATED"
                      "%20%2027-07-2026.pdf",
    },
}

# Common typos/aliases -> canonical BOND_REFERENCE key
_ALIASES = {
    "FDX1/2022/025": "FXD1/2022/025",
    "IFB1/2024/8.5": "IFB/2024/8.5",
}


def _canonical_issue(issue):
    issue = (issue or "").strip().upper()
    issue = _ALIASES.get(issue, issue)
    return issue


# ----------------------------------------------------------------------------
# Loading + saving (the private lots file — same pattern as portfolio.py)
# ----------------------------------------------------------------------------
def _bonds_path(portfolio_dir):
    return os.path.join(portfolio_dir, BONDS_FILE)


def load_bonds(portfolio_dir):
    """
    Return the list of bond lots: [{issue, face_value, purchase_price_pct,
    purchase_date, note}]. [] if the file doesn't exist yet or can't be
    parsed — never raises.
    """
    path = _bonds_path(portfolio_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        raw = data.get("bonds", []) if isinstance(data, dict) else data
        out = []
        for b in raw:
            try:
                issue = _canonical_issue(b["issue"])
                face = float(b["face_value"])
                if not issue or face <= 0:
                    continue
                out.append({
                    "issue": issue,
                    "face_value": face,
                    "purchase_price_pct": float(b["purchase_price_pct"]) if b.get("purchase_price_pct") else 100.0,
                    "purchase_date": b.get("purchase_date") or None,
                    "note": b.get("note") or "",
                })
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"Bonds: skipping malformed lot {b!r}: {e}")
        return out
    except Exception as e:
        logger.warning(f"Bonds: could not read {path}: {e}")
        return []


def add_bond(portfolio_dir, issue, face_value, purchase_price_pct=100.0,
            purchase_date=None, note=""):
    """Append one bond holding to bonds.json (creating the file if needed)."""
    path = _bonds_path(portfolio_dir)
    os.makedirs(portfolio_dir, exist_ok=True)
    data = {"bonds": []}
    if os.path.exists(path):
        try:
            with open(path) as f:
                existing = json.load(f)
            if isinstance(existing, dict) and "bonds" in existing:
                data = existing
        except Exception as e:
            raise ValueError(f"Existing {path} is not valid JSON — fix or remove it first: {e}")

    data.setdefault("bonds", []).append({
        "issue": _canonical_issue(issue),
        "face_value": float(face_value),
        "purchase_price_pct": float(purchase_price_pct),
        "purchase_date": purchase_date,
        "note": note or "",
    })
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return data["bonds"]


# ----------------------------------------------------------------------------
# Date / schedule helpers
# ----------------------------------------------------------------------------
def _d(s):
    return dt.date.fromisoformat(s)


def _add_months(d, months):
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                       31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return dt.date(y, m, day)


def _coupon_dates(ref):
    """The bond's full semi-annual coupon date list, as date objects.
    Uses the confirmed CBK list where we have one; otherwise generates one
    at 6-month intervals from the value date (accurate for a plain bullet
    bond like FXD1/2022/025, whose real dates simply weren't in the source
    document we pulled — CBK confirms these fall on the value-date's day of
    year, adjusted for weekends/holidays in practice)."""
    if ref.get("coupon_dates"):
        return [_d(s) for s in ref["coupon_dates"]]
    value_date = _d(ref["value_date"])
    maturity = _d(ref["maturity_date"])
    dates = []
    n = 1
    while True:
        cd = _add_months(value_date, 6 * n)
        if cd >= maturity:
            dates.append(maturity)
            break
        dates.append(cd)
        n += 1
    return dates


def _effective_redemption_schedule(ref, face_value):
    """
    The redemption structure that ACTUALLY applies to this holding: if CBK's
    "amounts up to KES 1,000,000 per CDS account are redeemed in full at
    amortization" rule is confirmed for this bond and this holding is under
    that threshold, the bond pays out 100% at its FIRST scheduled
    amortization date instead of the printed pro-rata tranches. Returns
    [{date, pct_of_original_face}], always summing to 100.
    """
    structure = ref["redemption_structure"]
    if (ref.get("small_holder_rule_confirmed") and face_value <= SMALL_HOLDER_THRESHOLD
            and len(structure) > 1):
        first = structure[0]
        return [{"date": first["date"], "pct_of_original_face": 100.0,
                "small_holder_acceleration": True}]
    return structure


# ----------------------------------------------------------------------------
# Core per-bond computation
# ----------------------------------------------------------------------------
def compute_bond(lot, as_of=None):
    """
    Full picture for one bond holding: cash-flow schedule, accrued interest,
    next payment, tax treatment, yields. Returns a dict, or a minimal dict
    with data_available=False if the issue isn't in BOND_REFERENCE.
    """
    as_of = as_of or dt.date.today()
    if isinstance(as_of, str):
        as_of = _d(as_of)

    issue = lot["issue"]
    face = lot["face_value"]
    ref = BOND_REFERENCE.get(issue)
    if ref is None:
        return {
            "issue": issue, "face_value": face, "data_available": False,
            "note": lot.get("note", ""),
        }

    coupon_pct = ref["coupon_pct"]
    value_date = _d(ref["value_date"])
    maturity = _d(ref["maturity_date"])
    coupon_dates = _coupon_dates(ref)
    schedule = _effective_redemption_schedule(ref, face)
    small_holder_accelerated = any(s.get("small_holder_acceleration") for s in schedule)
    effective_redemption_date = _d(schedule[-1]["date"]) if len(schedule) == 1 else maturity
    # ^ if accelerated to a single full payout, that IS the bond's effective
    # "maturity" for this holder; otherwise the last printed tranche is the
    # legal final maturity date (already true, kept explicit for clarity).

    purchase_date = _d(lot["purchase_date"]) if lot.get("purchase_date") else value_date
    price_pct = lot.get("purchase_price_pct", 100.0)
    cost_basis = face * price_pct / 100.0

    # ---- Build the full principal-outstanding timeline (for coupon sizing) ----
    # principal outstanding just BEFORE each coupon date, as % of original face
    redemption_by_date = {_d(r["date"]): r["pct_of_original_face"] for r in schedule}
    outstanding_pct = 100.0
    cashflows = []  # {date, type, pct_of_face, amount, is_future}
    for cd in coupon_dates:
        if cd > effective_redemption_date:
            break
        coupon_amount = face * (coupon_pct / 100.0) / 2.0 * (outstanding_pct / 100.0)
        cashflows.append({
            "date": cd.isoformat(), "type": "coupon",
            "amount": round(coupon_amount, 2),
        })
        if cd in redemption_by_date:
            principal_amount = face * redemption_by_date[cd] / 100.0
            cashflows.append({
                "date": cd.isoformat(), "type": "principal",
                "amount": round(principal_amount, 2),
            })
            outstanding_pct -= redemption_by_date[cd]

    for cf in cashflows:
        cf["is_future"] = _d(cf["date"]) > as_of
        cf["is_today"] = _d(cf["date"]) == as_of

    future_cf = [cf for cf in cashflows if cf["is_future"] or cf["is_today"]]
    past_cf = [cf for cf in cashflows if not cf["is_future"] and not cf["is_today"]]

    # Since-purchase cashflows (for "income received since you bought it")
    since_purchase_cf = [cf for cf in past_cf if _d(cf["date"]) >= purchase_date]

    next_coupon = next((cf for cf in future_cf if cf["type"] == "coupon"), None)
    next_principal = next((cf for cf in future_cf if cf["type"] == "principal"), None)
    next_event = future_cf[0] if future_cf else None

    # ---- Accrued interest since last coupon (Actual/365 on the annual rate) ----
    prior_coupons = [cf for cf in cashflows if cf["type"] == "coupon" and _d(cf["date"]) <= as_of]
    last_coupon_date = _d(prior_coupons[-1]["date"]) if prior_coupons else value_date
    days_accrued = (as_of - last_coupon_date).days
    accrued_interest = round(face * (coupon_pct / 100.0) / 365.0 * max(days_accrued, 0), 2)

    is_matured = as_of >= effective_redemption_date

    # ---- Totals ----
    total_future_coupons = sum(cf["amount"] for cf in future_cf if cf["type"] == "coupon")
    total_future_principal = sum(cf["amount"] for cf in future_cf if cf["type"] == "principal")
    total_since_purchase_coupons = sum(cf["amount"] for cf in since_purchase_cf if cf["type"] == "coupon")
    total_lifetime_coupons_from_purchase = total_since_purchase_coupons + total_future_coupons

    withholding_pct = ref["withholding_pct"]
    after_tax_future_coupons = total_future_coupons * (1 - withholding_pct / 100.0)
    after_tax_annual_income = face * (coupon_pct / 100.0) * (1 - withholding_pct / 100.0)

    years_from_purchase_to_redemption = (effective_redemption_date - purchase_date).days / 365.0
    total_return_pct = (total_lifetime_coupons_from_purchase / cost_basis * 100.0) if cost_basis else None
    annualized_running_yield_pct = coupon_pct * (1 - withholding_pct / 100.0)  # after-tax running yield

    return {
        "issue": issue,
        "type": ref["type"],
        "type_label": ref["type_label"],
        "tenor_label": ref["tenor_label"],
        "data_available": True,
        "face_value": face,
        "cost_basis": round(cost_basis, 2),
        "purchase_price_pct": price_pct,
        "purchase_date": purchase_date.isoformat(),
        "coupon_pct": coupon_pct,
        "tax_free": ref["tax_free"],
        "withholding_pct": withholding_pct,
        "value_date": ref["value_date"],
        "legal_maturity_date": ref["maturity_date"],
        "effective_redemption_date": effective_redemption_date.isoformat(),
        "small_holder_accelerated": small_holder_accelerated,
        "small_holder_rule_confirmed": ref.get("small_holder_rule_confirmed", False),
        "redemption_structure": schedule,
        "is_matured": is_matured,
        "days_accrued": days_accrued,
        "accrued_interest": accrued_interest,
        "outstanding_face_today": round(_outstanding_face_today(face, schedule, as_of), 2),
        "indicative_value_par": round(_outstanding_face_today(face, schedule, as_of) + accrued_interest, 2),
        "next_event": next_event,
        "next_coupon": next_coupon,
        "next_principal": next_principal,
        "cashflows_past": past_cf,
        "cashflows_future": future_cf,
        "cashflows_all": cashflows,
        "total_future_coupons": round(total_future_coupons, 2),
        "total_future_principal": round(total_future_principal, 2),
        "after_tax_future_coupons": round(after_tax_future_coupons, 2),
        "after_tax_annual_income": round(after_tax_annual_income, 2),
        "annual_pretax_income": round(face * coupon_pct / 100.0, 2),
        "total_since_purchase_coupons": round(total_since_purchase_coupons, 2),
        "total_lifetime_coupons_from_purchase": round(total_lifetime_coupons_from_purchase, 2),
        "years_from_purchase_to_redemption": round(years_from_purchase_to_redemption, 2),
        "total_return_pct": round(total_return_pct, 2) if total_return_pct is not None else None,
        "running_yield_pretax_pct": coupon_pct,
        "running_yield_after_tax_pct": round(annualized_running_yield_pct, 4),
        "source": ref["source"],
        "source_url": ref["source_url"],
        "note": lot.get("note", ""),
    }


def _outstanding_face_today(face, schedule, as_of):
    outstanding_pct = 100.0
    for r in schedule:
        if _d(r["date"]) <= as_of:
            outstanding_pct -= r["pct_of_original_face"]
    return face * max(outstanding_pct, 0.0) / 100.0


# ----------------------------------------------------------------------------
# Price / yield sensitivity — genuine bond-pricing math, no fabricated numbers
# ----------------------------------------------------------------------------
def price_at_yield(face, remaining_events, market_yield_pct):
    """
    Clean price (per KES 100 face) using the standard bond-pricing
    convention: each remaining cash flow is discounted as a WHOLE
    semi-annual period from today (period 1 = the next coupon, period 2 =
    the one after, ...) rather than by its exact number of calendar days.
    This is deliberately the same convention CBK's own published
    YIELD / CLEAN PRICE tables use in every prospectus — it's what makes
    "clean price = par when yield = coupon" hold true regardless of where
    you are inside the current coupon period. The stub (the days already
    elapsed in the current period) is handled separately as accrued
    interest, then added to get the dirty/invoice price — see
    bond_price_sensitivity. `remaining_events` is an ordered list of the
    total cash flow (coupon, or coupon+principal) due on each remaining
    payment date.
    """
    if not remaining_events:
        return None
    y = market_yield_pct / 100.0 / 2.0  # semi-annual discount rate
    pv = sum(cf / (1 + y) ** i for i, cf in enumerate(remaining_events, start=1))
    return round(pv / face * 100.0, 4) if face else None


def bond_price_sensitivity(bond):
    """
    [{yield_pct, clean_price_per_100, dirty_price_per_100, value_for_holding,
      is_coupon_yield}] for a spread of yields around the bond's own coupon
    — lets the user look up today's real market yield (e.g. from the latest
    CBK auction result for a similar tenor) and read off an honest
    indicative value, instead of trusting one invented "current price".
    A payment due TODAY is treated as already yours (shown separately as
    "Next Payment"), not as part of the remaining bond's priced value —
    otherwise a bond that happens to pay on the valuation date would look
    artificially more valuable than one that paid yesterday.
    """
    if not bond.get("data_available") or bond.get("is_matured"):
        return []
    coupon_pct = bond["coupon_pct"]
    face = bond["face_value"]
    future = [cf for cf in bond["cashflows_future"] if cf["is_future"]]
    if not future:
        return []
    dates = sorted({_d(cf["date"]) for cf in future})
    amounts_by_date = {}
    for cf in future:
        d = _d(cf["date"])
        amounts_by_date[d] = amounts_by_date.get(d, 0.0) + cf["amount"]
    remaining_events = [amounts_by_date[d] for d in dates]

    center = round(coupon_pct)
    spread = [center - 2, center - 1, center - 0.5, coupon_pct, center + 0.5, center + 1, center + 2]
    spread = sorted(set(round(s, 4) for s in spread))
    accrued_per_100 = bond["accrued_interest"] / face * 100.0 if face else 0
    out = []
    for y in spread:
        clean = price_at_yield(face, remaining_events, y)
        if clean is None:
            continue
        dirty = round(clean + accrued_per_100, 4)
        out.append({
            "yield_pct": y,
            "clean_price_per_100": clean,
            "dirty_price_per_100": dirty,
            "value_for_holding": round(dirty / 100.0 * face, 2),
            "is_coupon_yield": abs(y - coupon_pct) < 1e-6,
        })
    return out


# ----------------------------------------------------------------------------
# Portfolio-level aggregation
# ----------------------------------------------------------------------------
def compute_bond_portfolio(lots, as_of=None):
    """
    Aggregate view across every bond holding. Returns None if there are no
    lots. Bonds with an unrecognized issue code are included with
    data_available=False and excluded from totals (surfaced, not hidden).
    """
    if not lots:
        return None
    as_of = as_of or dt.date.today()
    if isinstance(as_of, str):
        as_of = _d(as_of)

    bonds = [compute_bond(lot, as_of=as_of) for lot in lots]
    avail = [b for b in bonds if b["data_available"]]
    missing = [b["issue"] for b in bonds if not b["data_available"]]

    total_face = sum(b["face_value"] for b in avail)
    total_cost = sum(b["cost_basis"] for b in avail)
    total_outstanding_face = sum(b["outstanding_face_today"] for b in avail)
    total_accrued = sum(b["accrued_interest"] for b in avail)
    total_indicative_value = round(total_outstanding_face + total_accrued, 2)
    total_annual_pretax_income = sum(b["annual_pretax_income"] for b in avail if not b["is_matured"])
    total_annual_after_tax_income = sum(b["after_tax_annual_income"] for b in avail if not b["is_matured"])
    total_future_coupons = sum(b["total_future_coupons"] for b in avail)
    total_future_principal = sum(b["total_future_principal"] for b in avail)
    total_after_tax_future_coupons = sum(b["after_tax_future_coupons"] for b in avail)

    blended_running_yield_pretax = (
        sum(b["face_value"] * b["coupon_pct"] for b in avail if not b["is_matured"])
        / sum(b["face_value"] for b in avail if not b["is_matured"]) * 1.0
        if any(not b["is_matured"] for b in avail) else None
    )
    blended_running_yield_after_tax = (
        total_annual_after_tax_income / sum(b["face_value"] for b in avail if not b["is_matured"]) * 100.0
        if any(not b["is_matured"] for b in avail) else None
    )

    # ---- combined cash-flow calendar (all bonds, future only) ----
    calendar = {}
    for b in avail:
        for cf in b["cashflows_future"]:
            key = cf["date"]
            row = calendar.setdefault(key, {"date": key, "coupon": 0.0, "principal": 0.0, "bonds": []})
            row[cf["type"]] += cf["amount"]
            row["bonds"].append(b["issue"])
    calendar_rows = sorted(calendar.values(), key=lambda r: r["date"])
    for r in calendar_rows:
        r["total"] = round(r["coupon"] + r["principal"], 2)
        r["bonds"] = sorted(set(r["bonds"]))

    # ---- next 12 months upcoming payments (for a focused "what's coming" list) ----
    one_year_out = as_of + dt.timedelta(days=366)
    upcoming = [r for r in calendar_rows if _d(r["date"]) <= one_year_out]

    # ---- allocation by issue (for chart) ----
    allocation = {b["issue"]: b["face_value"] for b in avail}

    # ---- annual interest-income projection (by calendar year, for a bar chart) ----
    by_year = {}
    for b in avail:
        for cf in b["cashflows_future"]:
            if cf["type"] != "coupon":
                continue
            yr = cf["date"][:4]
            by_year[yr] = by_year.get(yr, 0.0) + cf["amount"]
    income_by_year = [{"year": y, "amount": round(v, 2)} for y, v in sorted(by_year.items())]

    return {
        "as_of": as_of.isoformat(),
        "bonds": bonds,
        "missing_issues": missing,
        "totals": {
            "n_bonds": len(bonds),
            "n_available": len(avail),
            "face_value": round(total_face, 2),
            "cost_basis": round(total_cost, 2),
            "outstanding_face_today": round(total_outstanding_face, 2),
            "accrued_interest": round(total_accrued, 2),
            "indicative_value_par": total_indicative_value,
            "annual_pretax_income": round(total_annual_pretax_income, 2),
            "annual_after_tax_income": round(total_annual_after_tax_income, 2),
            "total_future_coupons": round(total_future_coupons, 2),
            "total_future_principal": round(total_future_principal, 2),
            "total_after_tax_future_coupons": round(total_after_tax_future_coupons, 2),
            "blended_running_yield_pretax_pct": round(blended_running_yield_pretax, 2) if blended_running_yield_pretax is not None else None,
            "blended_running_yield_after_tax_pct": round(blended_running_yield_after_tax, 2) if blended_running_yield_after_tax is not None else None,
        },
        "allocation": allocation,
        "calendar": calendar_rows,
        "upcoming_12m": upcoming,
        "income_by_year": income_by_year,
    }


# ---- Smoke test ----
if __name__ == "__main__":
    from logger import setup_logging
    setup_logging()
    lots = load_bonds("../portfolio")
    print(f"{len(lots)} bond lot(s) loaded")
    p = compute_bond_portfolio(lots)
    if p:
        print(json.dumps(p["totals"], indent=2))
        for b in p["bonds"]:
            if b["data_available"]:
                print(f"\n{b['issue']} — next event: {b['next_event']}")
