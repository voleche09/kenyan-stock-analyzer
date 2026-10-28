"""Issuer-first dividends with persistent, explicitly attributed fallback data.

A dividend payment is NOT annual DPS. Book closure is NOT an ex-date.
Verified declarations are retained; the fallback calendar is checked weekly
for new declarations or changed terms, with conflicts surfaced for review.
"""
import datetime as dt
import re

import dividend_reference
from dividend_cache import cached_public_data
from logger import get_logger

logger = get_logger(__name__)
CALENDAR_URL = "https://live.mystocks.co.ke/m/calendar"
SOURCE = "mystocks.co.ke"
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


class DividendCalendar:
    def __init__(self, cache_dir="data", force_refresh=False, refresh_days=7):
        self.cache_dir = cache_dir
        self.force_refresh = force_refresh
        self.refresh_days = refresh_days
        self._data = None
        self.cache_meta = {}

    def fetch(self):
        if self._data is not None:
            return self._data

        def load():
            from utils import http_get
            response = http_get(CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"})
            if response is None or response.status_code != 200:
                raise ValueError("Dividend calendar unavailable")
            rows = self._parse(response.text)
            if not rows:
                raise ValueError("Dividend calendar layout unrecognised")
            return rows

        data, self.cache_meta = cached_public_data(
            self.cache_dir, CALENDAR_URL, load, refresh_days=self.refresh_days,
            force_refresh=self.force_refresh)
        self._data = data if isinstance(data, dict) else {}
        return self._data

    @staticmethod
    def _parse(html):
        # Dates are headings shared by several rows. Preserve them instead of
        # attaching the date only to the first company under each heading.
        from bs4 import BeautifulSoup
        text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())
        dates = list(re.finditer(r"\b([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{4})\b", text))
        pattern = re.compile(
            r"\b([A-Z]{2,5})\s+[A-Z0-9][^:]{2,100}?:\s*"
            r"(Payment of|Book closure|Announced an?|Trading ex-dividend|Ex-dividend)"
            r"[^0-9]{0,15}KES\s+([\d.]+)\s*"
            r"((?:(?:first|and|final|interim|special|quarterly)\s+)*dividend)")
        events = {}
        for match in pattern.finditer(text):
            preceding = [d for d in dates if d.end() <= match.start()]
            if not preceding:
                continue
            mon, day, year = preceding[-1].groups()
            try:
                date = dt.date(int(year), _MONTHS[mon], int(day)).isoformat()
                symbol, verb, amount, kind = match.groups()
                amount = float(amount)
            except (KeyError, TypeError, ValueError):
                continue
            events.setdefault(symbol, []).append((date, verb, amount, kind))
        result = {}
        fields = {"Payment of": "payment_date", "Book closure": "book_closure",
                  "Trading ex-dividend": "ex_date", "Ex-dividend": "ex_date"}
        for symbol, rows in events.items():
            cycles = []
            for date, verb, amount, kind in sorted(rows):
                field = fields.get(verb, "announcement_date")
                # Same amount can recur in different years, or for interim and
                # final. Never merge those into a fictitious dividend cycle.
                candidates = [c for c in cycles if c["amount"] == amount and c["type"] == kind
                              and not c.get(field) and 0 <= (dt.date.fromisoformat(date)
                              - dt.date.fromisoformat(c["_first"])).days <= 180]
                cycle = candidates[-1] if candidates else None
                if cycle is None:
                    cycle = {"amount": amount, "type": kind, "_first": date,
                             "currency": "KES", "source": SOURCE, "source_url": CALENDAR_URL}
                    cycles.append(cycle)
                cycle[field] = date
            upcoming = [c for c in cycles if c.get("payment_date", "") >= dt.date.today().isoformat()]
            chosen = (min(upcoming, key=lambda c: c["payment_date"]) if upcoming
                      else max(cycles, key=lambda c: c.get("payment_date") or c["_first"]))
            result[symbol] = {k: v for k, v in chosen.items() if not k.startswith("_")}
        return result

    def validate(self, symbol, tv_dps, tolerance_pct=15.0):
        ref = dividend_reference.current_cycle(symbol)
        if ref:
            result = {**ref, "declaration_status": ref.get("status", "declared"),
                      "status": "company-verified" if ref.get("confidence") == "primary" else "secondary",
                      "note": f"Source: {ref['source']}. " + ref.get("note", "")}
            if ref.get("confidence") == "secondary":
                result["note"] += " The company's own circular wasn't read directly."
            try:
                age = (dt.date.today() - dt.date.fromisoformat(ref["verified_date"])).days
            except (KeyError, TypeError, ValueError):
                age = 0
            # Reuse persisted issuer updates even when the bundled seed is
            # recent. The seed starts the cache without another network pull.
            if symbol.upper() == "KCB":
                from dividend_sources import refresh_kcb
                cycles, meta = refresh_kcb(self.cache_dir, force_refresh=self.force_refresh,
                                           refresh_days=self.refresh_days, seed=ref)
                if cycles:
                    today = dt.date.today().isoformat()
                    upcoming = [c for c in cycles if c["payment_date"] >= today]
                    chosen = (min(upcoming, key=lambda c: c["payment_date"]) if upcoming
                              else max(cycles, key=lambda c: c["payment_date"]))
                    return {**chosen, "status": "company-verified", "declaration_status": "declared",
                            "verified_date": meta.get("checked"), "stale": meta.get("stale", False),
                            "note": "Issuer dividend register; final and special payments on the same date are combined."}
            if age >= self.refresh_days or self.force_refresh:
                # An old issuer reference must not hide a newer announcement.
                row = self.fetch().get(symbol.upper())
                result["stale"] = self.cache_meta.get("stale", False)
                result["checked_date"] = self.cache_meta.get("checked")
                result["review_required"] = True
                result["note"] += " Issuer snapshot needs review for newer announcements."
                if row and row.get("payment_date") and ref.get("payment_date"):
                    if row["payment_date"] > ref["payment_date"]:
                        return {**row, "status": "secondary", "review_required": True,
                                "note": "Newer calendar entry; issuer confirmation pending.",
                                "checked_date": self.cache_meta.get("checked"),
                                "stale": self.cache_meta.get("stale", False)}
                    if row["payment_date"] == ref["payment_date"] and row["amount"] != ref["amount"]:
                        result["note"] += " Calendar conflicts with issuer amount; retaining issuer terms."
            return result
        row = self.fetch().get(symbol.upper())
        if not row:
            return {"amount": None, "status": "unavailable",
                    "note": "No confirmed declaration available; this does not establish that the company pays no dividend."}
        # TradingView DPS is annual, and is not comparable to one installment.
        return {**row, "status": "secondary", "note": "Calendar entry; not issuer-verified.",
                "checked_date": self.cache_meta.get("checked"),
                "stale": self.cache_meta.get("stale", False)}


def apply_declaration(fund, result):
    """Apply event fields without corrupting annual analytics or currency units."""
    today = dt.date.today().isoformat()
    fields = {"amount": "amount", "type": "type", "currency": "currency",
              "book_closure": "book_closure", "record_date": "record_date",
              "payment_date": "payment_date", "source": "source", "source_url": "source_url",
              "verified_date": "verified_date", "checked_date": "checked_date",
              "note": "note", "declaration_status": "declaration_status",
              "review_required": "review_required", "stale": "cache_stale"}
    for key, dest in fields.items():
        fund["dividend_" + dest] = result.get(key)
    payment = result.get("payment_date")
    fund["dividend_payment_upcoming"] = bool(payment and payment >= today)
    fund["dividend_ex_date"] = result.get("ex_date")
    fund["dividend_ex_date_is_upcoming"] = bool(result.get("ex_date") and result["ex_date"] >= today)
    book_closure = result.get("book_closure")
    fund["dividend_book_closure_upcoming"] = bool(book_closure and book_closure >= today)
    # The actionable NSE date for "needs your attention" style alerts: a true
    # ex-dividend date when one is known, else the book closure (most issuer
    # declarations and the mystocks calendar give book closure, not ex-date).
    fund["dividend_next_date"] = result.get("ex_date") or book_closure
    fund["dividend_next_date_upcoming"] = (
        fund["dividend_ex_date_is_upcoming"] if result.get("ex_date") else fund["dividend_book_closure_upcoming"]
    )
    # Only an explicitly sourced annual total can replace annual DPS. Never
    # derive a payout ratio by mixing one installment with a year's earnings.
    if result.get("annual_dps") is not None and result.get("currency", "KES") == "KES":
        fund["dps_fy"] = result["annual_dps"]
        close = fund.get("close")
        fund["dividend_yield"] = round(result["annual_dps"] / close * 100, 2) if close and close > 0 else None
        fund["dividend_yield_basis"] = result.get("annual_period", "Issuer annual total")
    else:
        fund["dividend_yield_basis"] = "Annual figure from market-data provider; not this installment"


def apply_dividend_calendar(fundamentals_data, cache_dir="data", logger=None,
                            force_refresh=False, refresh_days=7):
    counts = {"company_verified": 0, "verified": 0, "corrected": 0, "unverified": 0, "none": 0}
    dc = DividendCalendar(cache_dir, force_refresh, refresh_days)
    for symbol, fund in (fundamentals_data or {}).items():
        if not fund:
            continue
        try:
            result = dc.validate(symbol, fund.get("dps_fy"))
            apply_declaration(fund, result)
            status = {"company-verified": "company_verified", "secondary": "verified"}.get(result["status"], "unverified")
            fund["dividend_status"] = status
            counts[status] += 1
        except Exception:
            if logger:
                logger.warning("Dividend enrichment unavailable; preserving market-data fields")
    return counts
