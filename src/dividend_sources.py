"""Conservative automatic adapters for issuer-published dividend tables.

Unknown layouts raise rather than replace a verified declaration with a guess.
Other issuers use the documented reference snapshot and require review when
new terms appear in the calendar. Add an adapter only with parser fixtures.
"""
import datetime as dt
import re

from dividend_cache import cached_public_data

KCB_URL = "https://kcbgroup.com/dividends"


def parse_kcb(html):
    from bs4 import BeautifulSoup
    cycles = {}
    for row in BeautifulSoup(html, "html.parser").select("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cells) < 4:
            continue
        kind, amount, record, payment = cells[-4:]
        if kind.lower() not in ("interim", "final", "special"):
            continue
        match = re.fullmatch(r"Ksh\.?\s*([0-9]+(?:\.[0-9]+)?)", amount, re.I)
        if not match:
            continue
        try:
            record = dt.datetime.strptime(record, "%d %B %Y").date().isoformat()
            payment = dt.datetime.strptime(payment, "%d %B %Y").date().isoformat()
        except ValueError:
            continue
        key = (record, payment)
        cycle = cycles.setdefault(key, {"amount": 0, "type": "", "currency": "KES",
                                       "book_closure": record, "payment_date": payment,
                                       "status": "declared", "confidence": "primary",
                                       "source": "KCB Group dividend register", "source_url": KCB_URL})
        cycle["amount"] = round(cycle["amount"] + float(match[1]), 6)
        cycle["type"] = (cycle["type"] + " + " if cycle["type"] else "") + kind.lower()
    if not cycles:
        raise ValueError("KCB dividend table not recognised")
    return list(cycles.values())


def refresh_kcb(cache_dir, *, force_refresh=False, refresh_days=7, seed=None):
    def load():
        from utils import http_get
        response = http_get(KCB_URL, headers={"User-Agent": "Mozilla/5.0"})
        if response is None or response.status_code != 200:
            raise ValueError("KCB source unavailable")
        return parse_kcb(response.text)

    return cached_public_data(cache_dir, KCB_URL, load, refresh_days=refresh_days,
                              force_refresh=force_refresh, seed=[seed] if seed else None,
                              seed_date=seed.get("verified_date") if seed else None)
