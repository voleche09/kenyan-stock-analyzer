"""
Fund prices, payouts and facts, looked up by ISIN — for the mutual funds in
your portfolio and the peer funds they're compared with.

Public, read-only sources:

  FT markets data (any fund)   ISIN → FT's id → the daily price history in one
                               request; the fund's profile (category, launch,
                               charges, objective), top holdings, asset mix and
                               sector / region weights next to the category
                               average.
  The fund manager's own data  when funds.csv gives the fund's official page
                               (factsheet_url) or the manager is recognised:
                                 BlackRock  — the product page's daily NAV series
                                              and the payout table;
                                 Amundi     — the official share API by ISIN (NAV
                                              history, calendar-year returns,
                                              risk class, fund size, asset mix);
                                 Allianz GI — see _allianz().
  Google News RSS              recent headlines naming the fund.

Accuracy rules:
  - The manager's own prices are used when they're there and up to date;
    FT's otherwise. Both are compared date by date (cross_check) and any
    disagreement is reported, never averaged away.
  - FT's own return figures are left out on purpose: FT computes them in
    pounds sterling. Every return on the dashboard is computed here in the
    fund's currency from the published prices (+ payouts), the way fund
    managers do — checked against BlackRock's and Amundi's published figures.
  - Fund size and "price currency" from FT are in GBP; they're not used.

Everything is cached for the day in <cache_dir>/funds/ (wiped each new day
with the rest of the data cache).
"""

import datetime as dt
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

from logger import get_logger

logger = get_logger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36",
      "Accept-Language": "en-GB,en;q=0.9"}
TIMEOUT = 25
FT = "https://markets.ft.com/data"
AMUNDI_HOSTS = ("https://www.amundi.com.hk", "https://www.amundi.lu", "https://www.amundi.com.sg")
HISTORY_YEARS = 11
MISMATCH = 0.0051             # half a cent: FT rounds some funds' prices to 2 decimals


# ------------------------------------------------------------------ http
def _get(url, *, params=None, timeout=TIMEOUT):
    import requests
    try:
        r = requests.get(url, params=params, headers=UA, timeout=timeout)
        if r.status_code == 200:
            return r
        logger.info(f"Funds: {urlparse(url).netloc} answered {r.status_code}")
    except Exception as e:                  # network trouble = this source is skipped today
        logger.info(f"Funds: {urlparse(url).netloc} unavailable ({type(e).__name__})")
    return None


def _post_json(url, body, *, timeout=TIMEOUT):
    import requests
    try:
        r = requests.post(url, json=body, headers=UA, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        logger.info(f"Funds: {urlparse(url).netloc} answered {r.status_code}")
    except Exception as e:
        logger.info(f"Funds: {urlparse(url).netloc} unavailable ({type(e).__name__})")
    return None


def _num(text):
    s = re.sub(r"[^\d.\-−]", "", (text or "").replace("−", "-"))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _text(node):
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node is not None else ""


# ------------------------------------------------------------------ FT
def ft_lookup(isin, currency="USD"):
    """{xid, symbol, name} for this exact ISIN and currency, or None."""
    r = _get(f"{FT}/searchapi/searchsecurities", params={"query": isin})
    if r is None:
        return None
    try:
        found = r.json()["data"]["security"]
    except (ValueError, KeyError, TypeError):
        return None
    exact = [s for s in found if str(s.get("symbol", "")).upper() == f"{isin}:{currency}".upper()]
    same = exact or [s for s in found if str(s.get("symbol", "")).upper().startswith(isin.upper() + ":")]
    if not same:
        return None
    s = same[0]
    return {"xid": str(s["xid"]), "symbol": s["symbol"], "name": html.unescape(s.get("name") or "")}


def ft_history(xid, years=HISTORY_YEARS, today=None):
    """[(date, price)] oldest first — every published price in one request."""
    today = today or dt.date.today()
    start = today - dt.timedelta(days=int(365.25 * years))
    r = _get(f"{FT}/equities/ajax/get-historical-prices",
             params={"startDate": f"{start:%Y/%m/%d}", "endDate": f"{today:%Y/%m/%d}", "symbol": xid}, timeout=40)
    if r is None:
        return []
    try:
        body = r.json().get("html") or ""
    except ValueError:
        return []
    out = {}
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 5:
            continue
        label = re.findall(r">([A-Z][a-z]+day, [A-Z][a-z]+ \d{1,2}, \d{4})<", cells[0])
        price = _num(re.sub(r"<[^>]+>", "", cells[4]))
        if not label or not price:
            continue
        out[dt.datetime.strptime(label[0], "%A, %B %d, %Y").date()] = price
    return sorted(out.items())


def _ft_table_rows(table):
    return [[_text(c) for c in tr.select("td,th")] for tr in table.select("tr")]


def ft_profile(symbol, *, holdings=True):
    """Category, launch, charges, objective — and with holdings=True the top
    holdings, asset mix, sector and region weights (with category average)."""
    from bs4 import BeautifulSoup
    out = {"ft_url": f"{FT}/funds/tearsheet/summary?s={quote(symbol)}"}
    r = _get(f"{FT}/funds/tearsheet/summary", params={"s": symbol})
    if r is None:
        return out
    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.select_one('[data-module-name="OverviewQuoteApp"] h1')
    if title:
        out["name"] = _text(title)
    prof = {}
    for tr in soup.select('[data-module-name="ProfileAndInvestmentApp"] tr'):
        th, td = tr.select_one("th"), tr.select_one("td")
        if th and td:
            prof[_text(th)] = _text(td)
    out["profile"] = prof
    for key, label in (("category", "Morningstar category"), ("income_treatment", "Income treatment"),
                       ("launch_date", "Launch date"), ("domicile", "Domicile"), ("fund_type", "Fund type"),
                       ("pricing_frequency", "Pricing frequency"), ("managers", "Manager & start date")):
        v = prof.get(label)
        if v and v != "--":
            out[key] = v
    for key, label in (("ongoing_charge_pct", "Ongoing charge"), ("initial_charge_pct", "Initial charge"),
                       ("max_annual_charge_pct", "Max annual charge"), ("exit_charge_pct", "Exit charge")):
        v = _num(prof.get(label))
        if v is not None:
            out[key] = v
    obj = soup.select_one('[data-module-name="ObjectiveApp"]')
    if obj:
        out["objective"] = re.sub(r"^Objective\s*", "", _text(obj))
    if not holdings:
        return out

    r = _get(f"{FT}/funds/tearsheet/holdings", params={"s": symbol})
    if r is None:
        return out
    soup = BeautifulSoup(r.text, "html.parser")
    asof = re.search(r"As of ([A-Z][a-z]{2} \d{1,2} \d{4})", r.text)
    out["holdings_as_of"] = (dt.datetime.strptime(asof.group(1), "%b %d %Y").date().isoformat() if asof else None)
    mix = []
    for row in _ft_table_rows(soup.select_one('[data-module-name="AssetAllocationApp"] table') or soup.new_tag("x"))[1:]:
        if len(row) >= 2 and _num(row[1]) is not None:
            mix.append((row[0], _num(row[1])))
    out["asset_mix"] = _plain_asset_mix(mix)
    tables = soup.select('[data-module-name="WeightingsApp"] table')
    if tables:
        out["sectors"] = [(r[0], _num(r[1]), _num(r[2]) if len(r) > 2 else None)
                          for r in _ft_table_rows(tables[0]) if len(r) >= 2 and _num(r[1]) is not None]
    if len(tables) > 1:
        regions, current = [], None
        for tr in tables[1].select("tr"):
            cells = [_text(c) for c in tr.select("td,th")]
            if len(cells) < 2 or _num(cells[1]) is None:
                continue
            top = any("row--colored" in c for c in (tr.get("class") or []))
            item = {"name": cells[0], "weight": _num(cells[1]),
                    "category": _num(cells[2]) if len(cells) > 2 else None, "parts": []}
            if top or current is None:
                current = item
                regions.append(item)
            else:
                current["parts"].append(item)
        out["regions"] = regions
    top = soup.select('[data-module-name="TopHoldingsApp"] table')
    if len(top) > 1:
        rows = []
        for r_ in _ft_table_rows(top[-1]):
            if len(r_) >= 3 and _num(r_[2]) is not None:
                name = re.sub(r"\s+[A-Z0-9.]+:[A-Z]{2,4}(:[A-Z]{3})?$", "", r_[0])   # drop "CSPX:LSE:USD"
                rows.append({"name": name, "weight": _num(r_[2]), "change_1y": _num(r_[1])})
        out["top_holdings"] = rows
    return out


def _plain_asset_mix(rows):
    """FT's UK-investor labels → plain ones ("Non-UK stock" + "UK stock" → Stocks)."""
    names = {"non-uk stock": "Stocks", "uk stock": "Stocks", "non-uk bond": "Bonds", "uk bond": "Bonds",
             "cash": "Cash", "other": "Other", "property": "Property", "convertible": "Convertibles"}
    out = {}
    for label, v in rows:
        k = names.get(label.lower(), label)
        out[k] = out.get(k, 0.0) + v
    return sorted(out.items(), key=lambda x: -abs(x[1]))


# ------------------------------------------------------------------ fund managers' own data
def _blackrock(url):
    """Daily NAVs and payouts from a BlackRock product page URL
    (https://www.blackrock.com/<site>/…/products/<id>/<name>)."""
    m = re.match(r"(https://www\.blackrock\.com/.+?/products/\d+/[^/?#]+)", url or "")
    if not m:
        return None
    base = m.group(1)
    out = {"source": "BlackRock", "source_url": base}
    r = _get(base, timeout=40)
    if r is not None:
        block = re.search(r"var navData\s*=\s*\[(.*?)\];", r.text, re.S)
        pts = re.findall(r"Date\.UTC\((\d+),(\d+),(\d+)\),y:Number\(\(([\d.]+)\)", block.group(1)) if block else []
        # JavaScript months are 0-based
        out["navs"] = sorted((dt.date(int(y), int(mo) + 1, int(d)), float(v)) for y, mo, d, v in pts)
    # The official risk class (SRI 1–7) is printed on BlackRock's Luxembourg page for the same product.
    pid_slug = re.search(r"/products/(\d+/[^/?#]+)", base).group(1)
    lu = _get(f"https://www.blackrock.com/lu/individual/products/{pid_slug}", timeout=40)
    if lu is not None:
        sri = re.findall(r'class="[^"]*level-(\d)[^"]*selected', lu.text)
        if len(set(sri)) == 1:
            out["facts"] = {"risk_class": int(sri[0])}
    r = _get(base + "/1480664165279.ajax", params={"tab": "distributions", "fileType": "json", "subtab": "table"})
    if r is not None:
        try:
            rows = json.loads(r.content.decode("utf-8-sig"))["table"]["aaData"]
            dists = []
            for row in rows:
                ex = dt.datetime.strptime(str(row[0]["raw"]), "%Y%m%d").date()
                dists.append({"ex_date": ex, "amount": float(row[1]["raw"]), "pay_date": None})
            out["distributions"] = sorted(dists, key=lambda d: d["ex_date"])
        except (ValueError, KeyError, TypeError, IndexError) as e:
            logger.info(f"Funds: BlackRock payout table not readable ({type(e).__name__})")
    return out


def _amundi(isin):
    """The official Amundi share API — works by ISIN for any Amundi fund."""
    fields = ["lastNav", "navHistory", "calendarPerformances", "class", "comp.distribution", "launchDate"]
    for host in AMUNDI_HOSTS:
        j = _post_json(f"{host}/product-services/fdr/share/v3/isin/{isin}/history", {"fields": fields})
        if not j:
            continue
        item = j[0] if isinstance(j, list) and j else j
        hist = item.get("navHistory") or []
        if not hist:
            continue
        out = {"source": "Amundi", "source_url": host,
               "navs": sorted((dt.date.fromisoformat(h["date"][:10]), float(h["value"])) for h in hist
                              if h.get("value"))}
        last = item.get("lastNav") or {}
        facts = {}
        if last.get("compartmentAssets"):
            facts["fund_size"] = float(last["compartmentAssets"])
        if last.get("assets"):
            facts["class_size"] = float(last["assets"])
        if last.get("subscriptionPrice") and last.get("value"):
            facts["buy_price_incl_charge"] = float(last["subscriptionPrice"])
            facts["entry_charge_pct"] = round((float(last["subscriptionPrice"]) / float(last["value"]) - 1) * 100, 2)
        cls = item.get("class") or {}
        if cls.get("sri"):
            facts["risk_class"] = int(cls["sri"])
        for key, field in (("management_fee_pct", "fiManagementFees"), ("admin_fee_pct", "fdmAdminFees"),
                           ("underlying_funds_fee_pct", "fiIndirectManagementFees"),
                           ("max_initial_charge_pct", "maxDirectSubscriptionFee"),
                           ("switch_fee_pct", "maxArbitrageFee")):
            if cls.get(field) is not None:
                facts[key] = float(cls[field])
        if item.get("launchDate"):
            facts["launch_date"] = item["launchDate"][:10]
        cal = {}
        for c in item.get("calendarPerformances") or []:
            if (c.get("currency") or {}).get("iso3Code") == "USD" and c.get("calendarPerf") is not None:
                cal[int(c["exercice"]["label"])] = float(c["calendarPerf"])
        if cal:
            facts["official_calendar_returns"] = cal
        for d in ((item.get("comp") or {}).get("distribution") or [])[:1]:
            for t in d.get("byTypes") or []:
                if t.get("type") == "repartition_classe_actif":
                    facts["asset_mix"] = [(s["name"], float(s["y"])) for s in t["data"]["series"]]
                    facts["asset_mix_as_of"] = d.get("productionDate")
        out["facts"] = facts
        out["distributions"] = []             # accumulating classes pay nothing; distributing ones: not in this API
        return out
    return None


_AGI_SG = "https://sg.allianzgi.com/en-SG/api/funddata"
_AGI_LU = "https://regulatory.allianzgi.com/en-GB/api/funddata"
_AGI_IDS = {          # the sites' own data-component ids (the same for every fund)
    "prices_sg": "sg_retail_financial_advisor/361c3268-a408-4d56-b9a0-456f554316b7",
    "prices_lu": "lu_regulatory_endclients/aab8dd70-0a03-4c7e-bd94-c0ae051b8b75",
    "dividends_sg": "sg_retail_financial_advisor/f6d611e8-7d34-4ec4-aad3-0104d6452faa",
    "calendar_sg": "sg_retail_financial_advisor/c4b784f5-d148-4b5b-9e68-67ac22515a60",
}


def _dmy(s):
    return dt.datetime.strptime(str(s).strip(), "%d/%m/%Y").date()


def _allianz(isin, url=None):
    """Allianz Global Investors' own data, from the fund page named in
    funds.csv (…allianzgi.com/…/<fund-name>): the full daily NAV history,
    every payout (ex-date, pay date, amount) and the payout calendar.

    The published payout history runs a month or two behind; the calendar
    doesn't. A payout date that has passed but isn't in the history yet gets
    the last published amount, marked `provisional` — replaced automatically
    once the real one is published."""
    seo = urlparse(url or "").path.rstrip("/").split("/")[-1] if url else ""
    if not re.fullmatch(r"[a-z0-9-]+", seo or ""):
        return None
    out = {"source": "Allianz Global Investors", "source_url": url, "facts": {}}
    navs = {}
    r = _get(f"{_AGI_SG}/prices/{seo}/{_AGI_IDS['prices_sg']}")
    for row in (r.json() if r is not None else []) or []:
        v = _num(row.get("nav"))
        if v:
            navs[_dmy(row["date"])] = v
    r = _get(f"{_AGI_LU}/prices/{seo}/{_AGI_IDS['prices_lu']}")
    lu = (r.json() if r is not None else []) or []
    for row in lu:
        v = _num(row.get("redemptionPrice"))
        if v:
            navs.setdefault(_dmy(row["date"]), v)          # newer days the SG list doesn't have yet
    out["navs"] = sorted(navs.items())
    if lu:
        last = max(lu, key=lambda x: _dmy(x["date"]))
        for key, field in (("fund_size", "fundAssetsFund"), ("class_size", "shareClassAssets")):
            v = _num(last.get(field))
            if v:
                out["facts"][key] = v
        sell, buy = _num(last.get("redemptionPrice")), _num(last.get("offeringPrice"))
        if sell and buy:
            out["facts"]["entry_charge_pct"] = round((buy / sell - 1) * 100, 2)
    dists = {}
    r = _get(f"{_AGI_SG}/dividends/{seo}/{_AGI_IDS['dividends_sg']}")
    for row in (r.json() if r is not None else []) or []:
        amount = _num(row.get("DistReinvestPerUnit"))
        if amount and row.get("ExDividendDate"):
            ex = _dmy(row["ExDividendDate"])
            dists[ex] = {"ex_date": ex, "amount": amount,
                         "pay_date": _dmy(row["PayableDate"]) if row.get("PayableDate") else None}
    r = _get(f"{_AGI_SG}/fundCalendar/{_AGI_IDS['calendar_sg']}", params={"seoName": seo})
    upcoming = []
    try:
        events = r.json().get("CalendarData") or [] if r is not None else []
    except ValueError:
        events = []
    pays = {}
    for e in events:
        when = dt.datetime.fromtimestamp(int(e["Timestamp"]) / 1000, dt.timezone.utc).date()
        text = " ".join(e.get("Event") or [])
        if "Ex-Dividend Date" in text:
            upcoming.append(when)
        if "Payable Date" in text:
            pays[when] = True
    if dists:
        last_amount = dists[max(dists)]["amount"]
        for ex in sorted(upcoming):
            if ex > max(dists) and ex <= dt.date.today():
                pay = min((p for p in pays if p >= ex), default=None)
                dists[ex] = {"ex_date": ex, "amount": last_amount, "pay_date": pay, "provisional": True}
        nxt = [ex for ex in upcoming if ex > dt.date.today()]
        if nxt:
            out["facts"]["next_ex_date"] = min(nxt).isoformat()
            pay = min((p for p in pays if p >= min(nxt)), default=None)
            if pay:
                out["facts"]["next_pay_date"] = pay.isoformat()
    out["distributions"] = [dists[k] for k in sorted(dists)]
    if "regulatory.allianzgi.com" in (url or ""):
        page = _get(url)
        sri = re.findall(r"c-indicator__copy--active[^>]*>\s*(\d)\s*<", page.text) if page is not None else []
        if len(set(sri)) == 1:
            out["facts"]["risk_class"] = int(sri[0])
    return out


def _official(isin, url, ft_name):
    host = (urlparse(url).netloc if url else "").lower()
    name = (ft_name or "").lower()
    if "blackrock" in host:
        return _blackrock(url)
    if "amundi" in host or "amundi" in name:
        return _amundi(isin)
    if "allianz" in host or "allianz" in name:
        return _allianz(isin, url)
    return None


# ------------------------------------------------------------------ news
_NOISE = re.compile(r"\b(stock price|quote|chart|historical prices|nav price)\b", re.I)


def news_queries(ft_name, house=None):
    """'Example Global Funds - World Bond Opportunities Fund A2' →
    ['"World Bond Opportunities" Example', '"World Bond" Example'] — the
    fund's own name first, then a looser one for funds with little coverage."""
    name = ft_name or ""
    core = name.split(" - ")[-1]
    core = re.sub(r"\b(Fund|Funds)\b.*$", "", core).strip() or core
    core = re.sub(r"\s+(A|AU|AM|AT|C|D|MD|Acc|Dis|Inc|USD|H2|[A-Z]\d+)(\s|$).*$", "", core).strip()
    house = house or (name.split()[0] if name else "")
    tail = f" {house}" if house and house.lower() not in core.lower() else ""
    out = [f'"{core}"{tail}'] if core else []
    words = core.split()
    if len(words) > 2:
        out.append(f'"{" ".join(words[:2])}"{tail}')
    return out


def fund_news(query, *, days=180, limit=8):
    r = _get("https://news.google.com/rss/search", params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    if r is None:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    out, seen = [], set()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip()
        when = item.findtext("pubDate") or ""
        try:
            published = dt.datetime.strptime(when, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            published = None
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        key = title.lower()
        if not title or key in seen or _NOISE.search(title) or (published and published < cutoff):
            continue
        if not link.lower().startswith(("http://", "https://")):
            continue
        seen.add(key)
        out.append({"title": title, "url": link, "source": source,
                    "published_utc": published.strftime("%d %b %Y") if published else ""})
    return out[:limit]


# ------------------------------------------------------------------ one fund
def _cross_check(primary, other):
    a, b = dict(primary or []), dict(other or [])
    common = sorted(set(a) & set(b))
    bad = [(d, a[d], b[d]) for d in common if abs(a[d] - b[d]) > MISMATCH]
    return {"compared": len(common), "mismatches": len(bad), "examples": [(d.isoformat(), x, y) for d, x, y in bad[:3]],
            "primary_last": max(a).isoformat() if a else None, "other_last": max(b).isoformat() if b else None}


def fetch_fund(isin, *, official_url=None, currency="USD", details=True, today=None):
    """Everything known about one fund today (see the module docstring)."""
    today = today or dt.date.today()
    out = {"isin": isin, "currency": currency, "navs": [], "distributions": [], "facts": {}}
    ft = ft_lookup(isin, currency)
    ft_navs = ft_history(ft["xid"], today=today) if ft else []
    if ft:
        out["facts"].update(ft_profile(ft["symbol"], holdings=details))
        out["name"] = out["facts"].get("name") or ft["name"]
    official = _official(isin, official_url, (ft or {}).get("name")) if details or official_url else None
    off_navs = (official or {}).get("navs") or []
    # A share class's prices start at its launch — some sites fill the years
    # before with a sister class's prices, which would distort every return.
    try:
        launch = dt.datetime.strptime(out["facts"].get("launch_date", ""), "%d %b %Y").date()
    except ValueError:
        launch = None
    if launch:
        off_navs = [p for p in off_navs if p[0] >= launch]
        ft_navs = [p for p in ft_navs if p[0] >= launch]
    # The manager's own prices win (FT rounds some funds to 2 decimals and
    # sometimes shows a price a day before the manager confirms it); FT is
    # used only when the manager's site is missing or more than 4 days behind.
    if off_navs and (not ft_navs or (ft_navs[-1][0] - off_navs[-1][0]).days <= 4):
        out.update(navs=off_navs, source=official["source"], source_url=official.get("source_url"))
        if ft_navs:
            out["cross_check"] = dict(_cross_check(off_navs, ft_navs), other="FT")
            if ft_navs[-1][0] > off_navs[-1][0]:
                out["cross_check"]["newer_elsewhere"] = (ft_navs[-1][0].isoformat(), ft_navs[-1][1])
    elif ft_navs:
        out.update(navs=ft_navs, source="FT", source_url=out["facts"].get("ft_url"))
        if off_navs:
            out["cross_check"] = dict(_cross_check(ft_navs, off_navs), other=official["source"])
            out["stale_official"] = off_navs[-1][0].isoformat()
    if official:
        out["distributions"] = official.get("distributions") or []
        out["distributions_source"] = official["source"] if official.get("distributions") else None
        for k, v in (official.get("facts") or {}).items():
            out["facts"].setdefault(k, v) if k != "asset_mix" else out["facts"].__setitem__("asset_mix_official", v)
    if details:
        out["news"] = []
        for q in news_queries(out.get("name") or "", (official or {}).get("source")):
            out["news_query"], out["news"] = q, fund_news(q)
            if out["news"]:
                break
    return out


# ------------------------------------------------------------------ cache + everything at once
def _to_json(d):
    def conv(o):
        if isinstance(o, (dt.date, dt.datetime)):
            return o.isoformat()
        raise TypeError(type(o).__name__)
    return json.dumps(d, default=conv)


def _from_json(d):
    d["navs"] = [(dt.date.fromisoformat(a), b) for a, b in d.get("navs") or []]
    for x in d.get("distributions") or []:
        x["ex_date"] = dt.date.fromisoformat(x["ex_date"])
    return d


def fetch_all(funds, cache_dir, *, today=None, workers=6, force=False):
    """{isin: data} for every fund you hold (with details) and every peer named
    in funds.csv (prices + profile only), cached for the day."""
    today = today or dt.date.today()
    # Sandbox runs use made-up funds (tools/fake_funds.py) — prices from a file, no network.
    if os.environ.get("FUND_DATA_FILE"):
        with open(os.environ["FUND_DATA_FILE"], encoding="utf-8") as fh:
            made_up = {k: _from_json(v) for k, v in json.load(fh).items()}
        logger.info(f"Funds: using the made-up fund data in {os.environ['FUND_DATA_FILE']}")
        return made_up
    folder = os.path.join(cache_dir, "funds")
    os.makedirs(folder, exist_ok=True)
    jobs = {}
    for f in funds:
        jobs[f["isin"]] = dict(official_url=f.get("factsheet_url"), currency=f.get("currency") or "USD",
                               details=True)
    for f in funds:
        for p in f.get("peers") or []:
            jobs.setdefault(p, dict(official_url=None, currency=f.get("currency") or "USD", details=False))

    def one(isin):
        path = os.path.join(folder, f"{isin}.json")
        try:
            with open(path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if not force and cached.get("fetched") == today.isoformat() and cached.get("navs"):
                return isin, _from_json(cached)
        except (OSError, ValueError):
            pass
        data = fetch_fund(isin, today=today, **jobs[isin])
        if data.get("navs"):
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(_to_json(dict(data, fetched=today.isoformat())))
            except OSError:
                pass
        return isin, data

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(one, list(jobs)))
