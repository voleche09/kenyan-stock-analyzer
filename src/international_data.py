"""
Data acquisition for international (US-listed, USD) stocks — via Yahoo Finance
(yfinance), already a dependency in this project (used as an NSE fallback
source in data_acquisition.py). Unlike NSE stocks, US tickers have no
suffix/exchange quirks for yfinance — a bare symbol (GOOG, INTC, UBER, ...)
is all that's needed.

Mirrors data_acquisition.py + fundamental_analysis.py's role for NSE stocks:
this module only FETCHES and normalizes; international_portfolio.py and
international_stock report generation do the computing/rendering.

Every function here is fail-safe: on any error it logs a warning and returns
None/[]/{} rather than raising, so one bad ticker or a network hiccup never
crashes the pipeline — same contract as the rest of this codebase.

Percentage-scale conversions below were verified against LIVE yfinance data
for GOOG, INTC, DRAM (an ETF) and KEEL before being hardcoded here (not
assumed from docs, which are inconsistent/stale across yfinance versions):
  - returnOnEquity, profitMargins, payoutRatio are FRACTIONS (0.487 = 48.7%)
    -> multiply by 100.
  - debtToEquity is ALREADY on a x100 scale (48.997 means a D/E of 0.49x)
    -> divide by 100 to get a plain ratio, matching scoring.py's formula.
  - dividendYield is ALREADY a plain percent number (0.26 means 0.26%,
    confirmed against GOOG's real dividendRate/price) -> used as-is.
"""

import os
import hashlib
import datetime as dt

import pandas as pd

from logger import get_logger

logger = get_logger(__name__)

CACHE_PREFIX = "intl_"


# ----------------------------------------------------------------------------
# Historical OHLCV — same DataFrame shape as data_acquisition.py, so
# AnalysisEngine.analyze_stock() and every chart builder work unchanged.
# ----------------------------------------------------------------------------
def fetch_history(symbol, period="6mo", interval="1d", cache_dir="data", force_refresh=False):
    """Return an OHLCV DataFrame (lowercase open/high/low/close/volume
    columns) for a US ticker, or None on failure. Cached as Parquet in
    cache_dir with an intl_ prefix so it's swept by the existing
    utils.enforce_daily_cache() new-day wipe with no changes needed there."""
    if not force_refresh:
        cached = _load_from_cache(symbol, cache_dir)
        if cached is not None:
            logger.debug(f"Intl cache hit for {symbol}")
            return cached

    try:
        import yfinance as yf
        data = yf.download(symbol, period=period, interval=interval,
                           progress=False, timeout=10)
        if data is None or data.empty:
            logger.warning(f"  No Yahoo Finance data for {symbol}")
            return None

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        col_map = {'Open': 'open', 'High': 'high', 'Low': 'low',
                  'Close': 'close', 'Volume': 'volume'}
        data = data.rename(columns=col_map)
        required = ['open', 'high', 'low', 'close', 'volume']
        available = [c for c in required if c in data.columns]
        if len(available) < 4:
            logger.warning(f"  Missing OHLCV columns for {symbol}: {data.columns.tolist()}")
            return None
        data = data[available]
        data.index.name = 'Date'

        _save_to_cache(symbol, data, cache_dir)
        logger.info(f"  {symbol} from Yahoo Finance: {len(data)} rows, "
                    f"close={data['close'].iloc[-1]:.2f}")
        return data
    except Exception as e:
        logger.warning(f"  Yahoo Finance history error for {symbol}: {e}")
        return None


# ----------------------------------------------------------------------------
# Fundamentals — shaped to be directly usable by scoring.score_stock() (which
# is currency-agnostic already), plus extra display-only fields.
# ----------------------------------------------------------------------------
def fetch_fundamentals(symbol):
    """
    Return a fundamentals dict for a US ticker, or {} on failure. Keys
    matching scoring.py's expectations (pe_ratio, price_to_book, peg_ratio,
    roe, net_margin, debt_to_equity, current_ratio, dividend_yield,
    dividend_payout_ratio, value_traded) let score_stock() work unchanged.
    Extra keys are for display only (name, sector, analyst consensus, etc).
    An ETF (e.g. DRAM) legitimately has most fundamental fields as None —
    that's real, not a bug; the dashboard shows it as "no data" rather than
    guessing, same as everywhere else in this codebase.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        if not info or len(info) < 3:
            logger.warning(f"  No Yahoo Finance fundamentals for {symbol}")
            return {}

        price = info.get('currentPrice') or info.get('regularMarketPrice')
        roe = info.get('returnOnEquity')
        net_margin = info.get('profitMargins')
        payout = info.get('payoutRatio')
        de = info.get('debtToEquity')
        avg_vol = info.get('averageVolume')

        return {
            # ---- display ----
            'name': info.get('longName') or info.get('shortName') or symbol,
            'quote_type': info.get('quoteType'),  # 'EQUITY' or 'ETF'
            'sector': info.get('sector'),
            'industry': info.get('industry'),
            'currency': info.get('currency') or 'USD',
            'market_cap': info.get('marketCap'),
            'price': price,
            'beta': info.get('beta'),
            'week52_low': info.get('fiftyTwoWeekLow'),
            'week52_high': info.get('fiftyTwoWeekHigh'),
            'average_volume': avg_vol,
            # ---- analyst consensus — real, sourced, not fabricated sentiment ----
            'target_mean_price': info.get('targetMeanPrice'),
            'target_high_price': info.get('targetHighPrice'),
            'target_low_price': info.get('targetLowPrice'),
            'recommendation_key': info.get('recommendationKey'),
            'num_analysts': info.get('numberOfAnalystOpinions'),
            # ---- scoring.py-compatible keys (verified unit conversions — see module docstring) ----
            'pe_ratio': info.get('trailingPE'),
            'forward_pe': info.get('forwardPE'),
            'peg_ratio': info.get('pegRatio'),
            'price_to_book': info.get('priceToBook'),
            'roe': roe * 100 if roe is not None else None,
            'net_margin': net_margin * 100 if net_margin is not None else None,
            'debt_to_equity': de / 100 if de is not None else None,
            'current_ratio': info.get('currentRatio'),
            'dividend_yield': info.get('dividendYield'),  # already percent-scale, verified live
            'dividend_rate': info.get('dividendRate'),
            'dividend_payout_ratio': payout * 100 if payout is not None else None,
            'value_traded': (avg_vol * price) if (avg_vol and price) else None,
        }
    except Exception as e:
        logger.warning(f"  Yahoo Finance fundamentals error for {symbol}: {e}")
        return {}


def fetch_dividend_history(symbol, max_items=12):
    """Return [{date, amount}], most recent last (matches yfinance's own
    order), or [] if the ticker has no dividend history."""
    try:
        import yfinance as yf
        div = yf.Ticker(symbol).dividends
        if div is None or div.empty:
            return []
        div = div.tail(max_items)
        return [{'date': idx.strftime('%Y-%m-%d'), 'amount': round(float(v), 4)}
                for idx, v in div.items()]
    except Exception as e:
        logger.debug(f"  Dividend history unavailable for {symbol}: {e}")
        return []


def fetch_earnings_calendar(symbol):
    """Return the next-earnings + consensus-estimate dict yfinance provides
    (real keys like 'Earnings Date', 'Earnings Average', 'Revenue Average'),
    or {} if unavailable. Dates are converted to ISO strings; anything not a
    plain date/number is dropped rather than passed through unpredictably."""
    try:
        import yfinance as yf
        cal = yf.Ticker(symbol).calendar
        if not cal or not isinstance(cal, dict):
            return {}
        out = {}
        for k, v in cal.items():
            if isinstance(v, (dt.date, dt.datetime)):
                out[k] = v.isoformat()
            elif isinstance(v, list):
                out[k] = [d.isoformat() if isinstance(d, (dt.date, dt.datetime)) else d for d in v]
            elif isinstance(v, (int, float, str)) or v is None:
                out[k] = v
        return out
    except Exception as e:
        logger.debug(f"  Earnings calendar unavailable for {symbol}: {e}")
        return {}


# ----------------------------------------------------------------------------
# News — yfinance's own .news (real headlines) PLUS the same Google News RSS
# pattern already proven in portfolio.py::fetch_portfolio_news, merged and
# de-duped. Deliberately NOT sentiment-tagged — same discipline as the rest
# of this codebase (see portfolio.py's module docstring).
# ----------------------------------------------------------------------------
def _from_yfinance_news(symbol, max_items=6):
    try:
        import yfinance as yf
        raw = yf.Ticker(symbol).news or []
    except Exception as e:
        logger.debug(f"  yfinance news unavailable for {symbol}: {e}")
        return []

    out = []
    for item in raw:
        content = item.get('content') or {}
        if not content:
            continue
        title = content.get('title')
        url = (content.get('canonicalUrl') or {}).get('url') or (content.get('clickThroughUrl') or {}).get('url')
        if not title or not url:
            continue
        provider = (content.get('provider') or {}).get('displayName', '')
        pub_date = content.get('pubDate', '')
        out.append({
            'symbol': symbol, 'title': title, 'url': url,
            'source': provider, 'published_utc': pub_date,
            'is_story': content.get('contentType') == 'STORY',
        })
    # Prefer real articles ('STORY') over videos/other content types.
    out.sort(key=lambda x: x['is_story'], reverse=True)
    for o in out:
        del o['is_story']
    return out[:max_items]


def fetch_news(symbol, company_name=None, max_items_per_symbol=6, max_age_days=7):
    """Return [{symbol, title, url, source, published_utc}], newest-first
    where a date is available, deduped by URL. Combines yfinance's .news
    with the same Google News RSS fallback used for NSE holdings, so a
    ticker yfinance has thin news for still gets real coverage."""
    combined = _from_yfinance_news(symbol, max_items_per_symbol)
    # NOTE: portfolio.fetch_portfolio_news() is NOT reused here — it builds
    # its query from SYMBOL_NAMES / "<SYM> NSE Kenya", which is wrong for a
    # US ticker. _from_google_news_rss() below is the same parsing approach,
    # queried on the real company name instead.
    rss_items = _from_google_news_rss(symbol, company_name, max_items_per_symbol, max_age_days)

    seen = {n['url'] for n in combined}
    for n in rss_items:
        if n['url'] not in seen:
            combined.append(n)
            seen.add(n['url'])
    return combined[: max_items_per_symbol * 2]


def _from_google_news_rss(symbol, company_name, max_items, max_age_days):
    """Google News RSS search on the company's real name (or symbol as a
    fallback) — same parsing approach as portfolio.py::fetch_portfolio_news,
    duplicated narrowly here because that function's query-building is
    NSE-specific (SYMBOL_NAMES / "<SYM> NSE Kenya")."""
    try:
        import re
        import requests
        import datetime as _dt
        from html import unescape
        from utils import http_get
    except Exception:
        return []

    _UA = {"User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )}
    query = company_name or symbol
    cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=max_age_days)
    try:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
        r = http_get(url, headers=_UA)
        if r is None or r.status_code != 200:
            return []
        items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
        out = []
        for raw in items:
            title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", raw, re.S)
            link_m = re.search(r"<link>(.*?)</link>", raw, re.S)
            date_m = re.search(r"<pubDate>(.*?)</pubDate>", raw, re.S)
            src_m = re.search(r"<source[^>]*>(.*?)</source>", raw, re.S)
            if not (title_m and link_m and date_m):
                continue
            published_utc = date_m.group(1).strip()
            try:
                pub_dt = _dt.datetime.strptime(published_utc[:25], "%a, %d %b %Y %H:%M:%S")
            except ValueError:
                continue
            if pub_dt < cutoff:
                continue
            title = unescape((title_m.group(1) or title_m.group(2) or "").strip())
            title = re.sub(r"\s*-\s*[^-]+$", "", title).strip()
            out.append({
                'symbol': symbol, 'title': title,
                'url': link_m.group(1).strip(),
                'source': unescape(src_m.group(1).strip()) if src_m else "",
                'published_utc': published_utc, '_pub_dt': pub_dt,
            })
        out.sort(key=lambda x: x['_pub_dt'], reverse=True)
        for o in out:
            del o['_pub_dt']
        return out[:max_items]
    except Exception as e:
        logger.debug(f"  Google News RSS failed for {symbol}: {e}")
        return []


# ----------------------------------------------------------------------------
# Caching — Parquet, day-scoped, prefixed so it lives alongside NSE cache
# files in the same data/ dir without any naming collision.
# ----------------------------------------------------------------------------
def _cache_key(symbol):
    today = dt.datetime.now().strftime('%Y%m%d')
    h = hashlib.md5(symbol.encode()).hexdigest()[:8]
    return f"{CACHE_PREFIX}{symbol}_{today}_{h}"


def _save_to_cache(symbol, data, cache_dir):
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, f"{_cache_key(symbol)}.parquet")
        data.to_parquet(path)
    except Exception as e:
        logger.debug(f"  Intl cache write error: {e}")


def _load_from_cache(symbol, cache_dir):
    try:
        if not os.path.isdir(cache_dir):
            return None
        prefix = f"{CACHE_PREFIX}{symbol}_{dt.datetime.now().strftime('%Y%m%d')}_"
        for fname in os.listdir(cache_dir):
            if fname.startswith(prefix) and fname.endswith('.parquet'):
                path = os.path.join(cache_dir, fname)
                mtime = dt.datetime.fromtimestamp(os.path.getmtime(path))
                if mtime.date() == dt.datetime.now().date():
                    return pd.read_parquet(path)
    except Exception as e:
        logger.debug(f"  Intl cache read error: {e}")
    return None


# ---- Smoke test ----
if __name__ == "__main__":
    from logger import setup_logging
    setup_logging()
    for sym in ['GOOG', 'INTC', 'DRAM']:
        print(f"\n=== {sym} ===")
        hist = fetch_history(sym, period='1mo')
        print(f"history: {len(hist) if hist is not None else 0} rows")
        fund = fetch_fundamentals(sym)
        print(f"fundamentals: name={fund.get('name')!r} price={fund.get('price')} "
              f"pe={fund.get('pe_ratio')} roe={fund.get('roe')}")
        divs = fetch_dividend_history(sym)
        print(f"dividends: {len(divs)} payments")
        news = fetch_news(sym, fund.get('name'))
        print(f"news: {len(news)} headlines")
        if news:
            print(f"  first: {news[0]['title'][:70]}")
