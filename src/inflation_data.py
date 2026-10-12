"""
Kenya's inflation and the market rates around it — public data, fetched once
a day and cached in <cache_dir>/macro/.

  CBK inflation CSV        12-month inflation, every month since 2005, and the
                           12-month average (Central Bank of Kenya, from KNBS)
  IMF consumer price index the index itself, monthly since Feb 2009 (base Feb
                           2019 = 100, same as KNBS): exact "prices rose X%
                           between two months" — and a check on CBK's figures
  KNBS monthly release     what's getting dearer: the 13 spending groups
                           (weight, 1-month and 12-month change), core and
                           non-core inflation, everyday retail prices (the
                           PDF, read with curl + pdftotext — knbs.or.ke's
                           certificate chain is incomplete, so Python's own
                           HTTPS refuses it)
  CBK rates CSV            the 91-day Treasury bill rate and the Central Bank
                           Rate, month by month
  CBK exchange-rate CSV    USD/KES, the monthly average since 1993
  FRED                     US consumer prices, for context

Accuracy rules: CBK's file has a few typos, so every month since 2021 is
compared with the 12-month change of the IMF index and differences over
0.05 points are reported (not silently fixed). Everything fails safe: a
source that doesn't answer is simply missing, and the page says so.
"""

import calendar
import csv
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote, urljoin

from logger import get_logger

logger = get_logger(__name__)

CBK = "https://www.centralbank.go.ke"
CBK_INFLATION_PAGE = f"{CBK}/statistics/inflation-rates/"
CBK_FX_PAGE = f"{CBK}/statistics/exchange-rates/"
CBK_RATES_PAGE = f"{CBK}/statistics/interest-rates/"
IMF_CPI = "https://api.imf.org/external/sdmx/2.1/data/IMF.STA,CPI/KEN.CPI._T.IX.M"
KNBS_MEDIA = "https://www.knbs.or.ke/wp-json/wp/v2/media"
FRED_CPI = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
TARGET = (2.5, 5.0, 7.5)               # CBK's band: 5% ± 2.5%
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9


def _get(url, *, headers=None, timeout=30):
    import requests
    try:
        r = requests.get(url, headers=UA if headers is None else headers, timeout=timeout)
        if r.status_code == 200:
            return r
        logger.info(f"Inflation data: {url[:60]} answered {r.status_code}")
    except Exception as e:                     # a source that doesn't answer is skipped today
        logger.info(f"Inflation data: {url[:60]} unavailable ({type(e).__name__})")
    return None


def _num(s):
    s = (s or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _rows(text):
    return list(csv.reader(io.StringIO(text.lstrip("﻿"))))


def _link(page_url, pattern):
    """The CSV a CBK page links to (its file name changes with each update)."""
    r = _get(page_url)
    if r is None:
        return None
    m = re.search(r'href="([^"]*' + pattern + r'[^"]*\.csv)"', r.text, re.I)
    return urljoin(CBK, quote(m.group(1), safe="/:%")) if m else None


# ------------------------------------------------------------------ sources
def kenya_headline():
    """[(first of month, 12-month inflation %, 12-month average %)] since 2005."""
    url = _link(CBK_INFLATION_PAGE, r"uploads/inflation_rates/")
    r = _get(url) if url else None
    if r is None:
        return []
    out, year = [], None
    for row in _rows(r.text):
        if len(row) < 4:
            continue
        if row[0].strip().isdigit():
            year = int(row[0])
        mon = MONTHS.get(row[1].strip().lower())
        yoy = _num(row[2])
        if year and mon and yoy is not None:
            out.append((dt.date(year, mon, 1), yoy, _num(row[3])))
    return sorted(out)


def kenya_cpi():
    """[(first of month, CPI index)] — IMF, base Feb 2019 = 100."""
    r = _get(IMF_CPI + "?detail=dataonly",
             headers=dict(UA, Accept="application/vnd.sdmx.data+csv;version=1.0.0"), timeout=40)
    if r is None:
        return []
    rows = _rows(r.text)
    if not rows:
        return []
    head = rows[0]
    try:
        ti, vi = head.index("TIME_PERIOD"), head.index("OBS_VALUE")
    except ValueError:
        return []
    out = []
    for row in rows[1:]:
        m = re.fullmatch(r"(\d{4})-M(\d{2})", row[ti] if len(row) > ti else "")
        v = _num(row[vi]) if len(row) > vi else None
        if m and v:
            out.append((dt.date(int(m.group(1)), int(m.group(2)), 1), v))
    return sorted(out)


def cbk_rates():
    """[(first of month, 91-day T-bill %, Central Bank Rate %)] — CBK monthly."""
    url = _link(CBK_RATES_PAGE, r"Central[^\"]*Bank[^\"]*Rates")
    r = _get(url) if url else None
    if r is None:
        return []
    rows = _rows(r.text)
    head = next((i for i, row in enumerate(rows) if row and row[0].strip().upper() == "YEAR"), None)
    if head is None:
        return []
    cols = [c.strip().lower() for c in rows[head]]
    try:
        tb = next(i for i, c in enumerate(cols) if c.startswith("91"))
        cbr = next(i for i, c in enumerate(cols) if "central bank rate" in c)
    except StopIteration:
        return []
    out, year = [], None
    for row in rows[head + 1:]:
        if len(row) <= max(tb, cbr):
            continue
        if row[0].strip().isdigit():
            year = int(row[0])
        mon = MONTHS.get(row[1].strip().lower()[:3]) if row[1].strip() else None
        if year and mon:
            t, c = _num(row[tb]), _num(row[cbr])
            if t is not None or c is not None:
                out.append((dt.date(year, mon, 1), t, c))
    return sorted(out)


def usd_kes_monthly():
    """[(first of month, KES per USD, monthly average)] since 1993."""
    url = _link(CBK_FX_PAGE, r"Monthly[^\"]*period average")
    r = _get(url) if url else None
    if r is None:
        return []
    rows = _rows(r.text)
    head = next((i for i, row in enumerate(rows) if len(row) > 2 and row[0].strip().lower() == "year"), None)
    if head is None:
        return []
    usd = next((i for i, c in enumerate(rows[head]) if "united states" in c.lower()), None)
    if usd is None:
        return []
    out = []
    for row in rows[head + 1:]:
        if len(row) > usd and row[0].strip().isdigit() and row[1].strip().isdigit():
            v = _num(row[usd])
            if v:
                out.append((dt.date(int(row[0]), int(row[1]), 1), v))
    return sorted(out)


def us_cpi():
    """[(first of month, US CPI)] — FRED (its server stalls on a browser user agent)."""
    r = _get(FRED_CPI, headers={}, timeout=40)
    if r is None:
        return []
    out = []
    for row in _rows(r.text)[1:]:
        if len(row) >= 2 and _num(row[1]):
            out.append((dt.date.fromisoformat(row[0][:10]).replace(day=1), _num(row[1])))
    return sorted(out)


def _curl(url, dest=None, timeout=60):
    if not shutil.which("curl"):
        return None
    cmd = ["curl", "-sSL", "--max-time", str(timeout), "-A", UA["User-Agent"], url]
    if dest:
        cmd += ["-o", dest]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return True if dest else res.stdout


_DIVISION = re.compile(r"^\s{0,3}([A-Z][A-Za-z ,&\-]+?)\s{2,}(\d{1,2}\.\d{2,4})\s+(-?\d+\.\d)\s+(-?\d+\.\d)\s*$")
_CORE = re.compile(r"^\s*([A-Z][a-z]{2})-(\d{2})\s+([\d.]+)\s+(-?[\d.]+)\s+([\d.]+)\s+(-?[\d.]+)\s*$")
_PRICE = re.compile(r"^\s{0,3}([A-Z][A-Za-z0-9 ()/\-,']+?)\s{2,}([\d,]+)\s+([A-Za-z]+)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})"
                    r"\s+([\d,]+\.\d{2})\s+(-?\d+\.\d)\s+(-?\d+\.\d)\s*$")


def parse_knbs(text):
    """The parts of a KNBS monthly CPI release the dashboard uses (from pdftotext -layout)."""
    out = {"divisions": [], "core": [], "prices": []}
    m = re.search(r"\((\w+) (\d{4})\s*/\s*\w+ \d{4}\)", text)
    if m and MONTHS.get(m.group(1).lower()):
        out["month"] = dt.date(int(m.group(2)), MONTHS[m.group(1).lower()], 1).isoformat()
    section = None
    for line in text.splitlines():
        if line.lstrip().startswith("Table 1"):
            section = "div"
        elif line.lstrip().startswith("Table 3"):
            section = "price"
        elif line.lstrip().startswith("Table 4"):
            section = "core"
        elif re.match(r"\s*(Table [25-9]|Figure)", line):
            section = None
        if section == "div":
            d = _DIVISION.match(line)
            if d:
                out["divisions"].append({"name": d.group(1).strip(), "weight": float(d.group(2)),
                                         "month_pct": float(d.group(3)), "year_pct": float(d.group(4))})
        elif section == "core":
            c = _CORE.match(line)
            if c and MONTHS.get(c.group(1).lower()):
                out["core"].append({"month": dt.date(2000 + int(c.group(2)), MONTHS[c.group(1).lower()], 1).isoformat(),
                                    "core_pct": float(c.group(4)), "noncore_pct": float(c.group(6))})
        elif section == "price":
            p = _PRICE.match(line)
            if p:
                out["prices"].append({"name": p.group(1).strip(), "qty": p.group(2), "unit": p.group(3),
                                      "year_ago": _num(p.group(4)), "last_month": _num(p.group(5)),
                                      "now": _num(p.group(6)), "month_pct": float(p.group(7)),
                                      "year_pct": float(p.group(8))})
    return out


def knbs_release():
    """The latest KNBS monthly CPI release, parsed — or None."""
    if not shutil.which("pdftotext"):
        return None
    raw = _curl(f"{KNBS_MEDIA}?search=Consumer-Price-Indices-and-Inflation-Rates&per_page=20"
                "&orderby=date&order=desc&_fields=date,source_url")
    try:
        items = json.loads(raw or b"[]")
    except ValueError:
        return None
    pdfs = [i["source_url"] for i in items if str(i.get("source_url", "")).lower().endswith(".pdf")
            and "inflation-rates" in i["source_url"].lower()]
    if not pdfs:
        return None
    with tempfile.TemporaryDirectory() as d:
        pdf = os.path.join(d, "cpi.pdf")
        if not _curl(pdfs[0], pdf):
            return None
        try:
            text = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, timeout=60).stdout.decode(
                "utf-8", "replace")
        except (OSError, subprocess.TimeoutExpired):
            return None
    out = parse_knbs(text)
    out["url"] = pdfs[0]
    return out if (out["divisions"] or out["core"]) else None


# ------------------------------------------------------------------ everything, checked
def _yoy_from_index(cpi):
    by = dict(cpi)
    return {d: (v / by[dt.date(d.year - 1, d.month, 1)] - 1) * 100.0
            for d, v in cpi if dt.date(d.year - 1, d.month, 1) in by}


def summarise(headline, cpi, rates, fx, us, knbs, today=None):
    """The figures the pages show, from the raw series."""
    today = today or dt.date.today()
    out = {"headline": headline, "cpi": cpi, "rates": rates, "fx": fx, "us_cpi": us, "knbs": knbs, "target": TARGET}
    if headline:
        d, yoy, avg = headline[-1]
        prev = headline[-2][1] if len(headline) > 1 else None
        out["latest"] = {"month": d.isoformat(), "yoy": yoy, "prev_yoy": prev, "annual_avg": avg,
                         "in_band": TARGET[0] <= yoy <= TARGET[2]}
    if cpi:
        by = dict(cpi)
        last_d, last_v = cpi[-1]
        prev_m = (last_d - dt.timedelta(days=1)).replace(day=1)
        if prev_m in by and out.get("latest"):
            out["latest"]["month_pct"] = (last_v / by[prev_m] - 1) * 100.0
        # cross-check CBK's 12-month figures against the index, since 2021 (one base, no rebasing)
        imf_yoy = _yoy_from_index(cpi)
        diffs = [(d.isoformat(), y, round(imf_yoy[d], 2)) for d, y, _a in headline
                 if d >= dt.date(2021, 1, 1) and d in imf_yoy and abs(imf_yoy[d] - y) > 0.05]
        compared = sum(1 for d, _y, _a in headline if d >= dt.date(2021, 1, 1) and d in imf_yoy)
        out["cross_check"] = {"compared": compared, "differences": diffs}
        # calendar-year inflation: the year's average price level vs the year before's
        years = {}
        for d, v in cpi:
            years.setdefault(d.year, []).append(v)
        yearly = {}
        for y in sorted(years):
            if y - 1 in years and len(years[y - 1]) == 12:
                n = len(years[y])
                yearly[y] = {"avg_pct": (sum(years[y]) / n) / (sum(years[y - 1][:n]) / n) * 100.0 - 100.0
                             if n == 12 else None,
                             "dec_pct": (years[y][-1] / years[y - 1][-1] - 1) * 100.0 if n == 12 else None,
                             "ytd_pct": (years[y][-1] / years[y - 1][-1] - 1) * 100.0, "months": n}
        out["yearly_index"] = yearly
    # calendar years before the index starts: CBK's 12-month average, as published for December
    out["yearly_cbk"] = {d.year: a for d, _y, a in headline if d.month == 12 and a is not None}
    if rates:
        tb = [(d, t) for d, t, _c in rates if t is not None]
        cb = [(d, c) for d, _t, c in rates if c is not None]
        out["tbill_latest"] = tb[-1] if tb else None
        out["cbr_latest"] = cb[-1] if cb else None
    if fx:
        last_d, last_v = fx[-1]
        year_ago = dict(fx).get(dt.date(last_d.year - 1, last_d.month, 1))
        out["fx_latest"] = (last_d, last_v)
        out["fx_1y_pct"] = (last_v / year_ago - 1) * 100.0 if year_ago else None
    if us:
        by = dict(us)
        last_d, last_v = us[-1]
        ago = by.get(dt.date(last_d.year - 1, last_d.month, 1))
        out["us_latest"] = {"month": last_d.isoformat(), "yoy": (last_v / ago - 1) * 100.0 if ago else None}
    return out


def _encode(o):
    if isinstance(o, dt.date):
        return {"__d": o.isoformat()}
    raise TypeError(type(o).__name__)


def _decode(o):
    return dt.date.fromisoformat(o["__d"]) if set(o) == {"__d"} else o


def fetch_all(cache_dir, *, today=None, force=False):
    """Everything above, cached for the day. Never raises."""
    today = today or dt.date.today()
    folder = os.path.join(cache_dir, "macro")
    path = os.path.join(folder, "inflation.json")
    try:
        if not force:
            with open(path, encoding="utf-8") as fh:
                cached = json.load(fh, object_hook=_decode)
            if cached.get("fetched") == today.isoformat():
                return summarise(*(_tuples(cached[k]) for k in ("headline", "cpi", "rates", "fx", "us")),
                                 cached.get("knbs"), today)
    except (OSError, ValueError, KeyError):
        pass
    raw = {}
    for key, fn in (("headline", kenya_headline), ("cpi", kenya_cpi), ("rates", cbk_rates),
                    ("fx", usd_kes_monthly), ("us", us_cpi)):
        try:
            raw[key] = fn()
        except Exception as e:                 # noqa: BLE001 — one broken source never stops the rest
            logger.warning(f"Inflation data: {key} skipped ({type(e).__name__}: {e})")
            raw[key] = []
    try:
        raw["knbs"] = knbs_release()
    except Exception as e:                     # noqa: BLE001
        logger.info(f"Inflation data: KNBS release skipped ({type(e).__name__})")
        raw["knbs"] = None
    missing = [k for k in ("headline", "cpi", "rates", "fx") if not raw[k]]
    if missing:
        logger.warning(f"Inflation data: no {', '.join(missing)} this run")
    if raw["headline"] or raw["cpi"]:
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(dict(raw, fetched=today.isoformat()), fh, default=_encode)
        except OSError:
            pass
    return summarise(raw["headline"], raw["cpi"], raw["rates"], raw["fx"], raw["us"], raw["knbs"], today)


def _tuples(rows):
    return [tuple(r) for r in rows or []]
