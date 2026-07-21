"""
Price validation module.

Cross-checks the prices shown on the dashboard (sourced from TradingView)
against an INDEPENDENT second source — afx.kwayisi.org, which publishes the
Nairobi Securities Exchange board — and assesses data freshness.

The goal is accuracy: rather than trusting a single feed, every stock's price
is compared against a second source and flagged when they disagree, and each
price is tagged with when the stock last actually traded (thinly-traded NSE
counters may not trade for days, making a "last price" misleading).

Everything here fails safe: if the second source is unreachable, validation
degrades to "unverified" and the rest of the pipeline is unaffected.
"""

import os
import re
import json
import requests
from datetime import datetime

import pandas as pd

from logger import get_logger

logger = get_logger(__name__)

AFX_URL = "https://afx.kwayisi.org/nse/"
REFERENCE_SOURCE = "afx.kwayisi.org"


class PriceValidator:
    """Validate dashboard prices against an independent source + freshness."""

    def __init__(self, cache_dir="data", disagree_threshold_pct=1.0):
        """
        Args:
            cache_dir: Directory for the daily reference-price cache.
            disagree_threshold_pct: Percent difference above which the two
                sources are considered to disagree.
        """
        self.cache_dir = cache_dir
        self.disagree_threshold_pct = disagree_threshold_pct
        os.makedirs(self.cache_dir, exist_ok=True)
        self._reference = None  # {ticker: {'price': float, 'volume': int}}
        self._clean_old_cache()

    def _clean_old_cache(self):
        today = datetime.now().strftime("%Y%m%d")
        for fname in os.listdir(self.cache_dir):
            if fname.startswith("reference_prices_") and today not in fname:
                try:
                    os.remove(os.path.join(self.cache_dir, fname))
                except OSError:
                    pass

    # ---- Reference price acquisition ----

    def _cache_path(self):
        today = datetime.now().strftime("%Y%m%d")
        return os.path.join(self.cache_dir, f"reference_prices_{today}.json")

    def fetch_reference_prices(self, force_refresh=False):
        """
        Fetch the independent NSE price board. Cached per day.

        Returns:
            dict: {ticker: {'price': float, 'volume': int}}, or {} on failure.
        """
        if self._reference is not None:
            return self._reference

        # Try disk cache first
        path = self._cache_path()
        if not force_refresh and os.path.exists(path):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(path))
                if mtime.date() == datetime.now().date():
                    with open(path) as f:
                        self._reference = json.load(f)
                    logger.info(
                        f"Reference prices from cache "
                        f"({len(self._reference)} stocks)"
                    )
                    return self._reference
            except Exception as e:
                logger.debug(f"Reference cache read error: {e}")

        # Fetch fresh
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(AFX_URL, headers=headers, timeout=20)
            resp.raise_for_status()
            self._reference = self._parse_afx(resp.text)
            logger.info(
                f"Reference prices from {REFERENCE_SOURCE}: "
                f"{len(self._reference)} stocks"
            )
            if self._reference:
                try:
                    with open(path, "w") as f:
                        json.dump(self._reference, f)
                except Exception as e:
                    logger.debug(f"Reference cache write error: {e}")
        except Exception as e:
            logger.warning(f"Reference price source unavailable: {e}")
            self._reference = {}

        return self._reference

    @staticmethod
    def _parse_afx(html):
        """
        Parse the afx.kwayisi.org NSE board. Its rows use unclosed tags:
            <tr><td><a>TICKER</a><td><a>Name</a><td>VOLUME<td>PRICE<td ...>CHG
        """
        strip = lambda s: re.sub(r"<[^>]+>", "", s).replace("&amp;", "&").strip()
        out = {}
        for row in html.split("<tr>"):
            if "/nse/" not in row:
                continue
            segs = row.split("<td")
            if len(segs) < 5:
                continue
            cells = [
                strip(s.split(">", 1)[1]) if ">" in s else "" for s in segs[1:]
            ]
            ticker = cells[0]
            if not re.fullmatch(r"[A-Z0-9]{2,6}", ticker):
                continue
            try:
                volume = int(cells[2].replace(",", ""))
                price = float(cells[3].replace(",", ""))
            except (ValueError, IndexError):
                continue
            out[ticker] = {"price": price, "volume": volume}
        return out

    # ---- Validation ----

    def validate(self, symbol, dashboard_price, history_df=None):
        """
        Validate one stock's price and assess freshness.

        Args:
            symbol: Ticker symbol.
            dashboard_price: The price the dashboard shows (TradingView).
            history_df: Optional OHLCV DataFrame to derive last-traded date.

        Returns:
            dict with keys:
                reference_price, reference_source, pct_diff, agree,
                last_traded_date, days_stale, is_stale, status, note
        """
        ref = self.fetch_reference_prices()
        ref_row = ref.get(symbol.upper()) if ref else None
        reference_price = ref_row["price"] if ref_row else None

        pct_diff = None
        agree = None
        if reference_price and dashboard_price and reference_price > 0:
            pct_diff = (dashboard_price - reference_price) / reference_price * 100
            agree = abs(pct_diff) <= self.disagree_threshold_pct

        # Freshness from history (last bar with real volume)
        last_traded_date = None
        days_stale = None
        if history_df is not None and not history_df.empty:
            try:
                traded = history_df
                if "volume" in history_df.columns:
                    traded = history_df[history_df["volume"] > 0]
                if not traded.empty:
                    last_idx = traded.index[-1]
                    last_traded_date = (
                        last_idx.strftime("%Y-%m-%d")
                        if hasattr(last_idx, "strftime")
                        else str(last_idx)
                    )
                    if hasattr(last_idx, "date"):
                        days_stale = (datetime.now().date() - last_idx.date()).days
            except Exception as e:
                logger.debug(f"Freshness calc error for {symbol}: {e}")

        is_stale = bool(days_stale and days_stale > 1)

        # Status precedence: unverified -> mismatch -> stale -> ok
        if reference_price is None:
            status = "unverified"
            note = "No independent source to compare against"
        elif agree is False:
            status = "mismatch"
            note = (
                f"Differs from {REFERENCE_SOURCE} by {pct_diff:+.1f}% "
                f"({reference_price:g} vs {dashboard_price:g})"
            )
        elif is_stale:
            status = "stale"
            note = f"Last traded {days_stale} days ago ({last_traded_date})"
        else:
            status = "ok"
            note = f"Matches {REFERENCE_SOURCE} within {self.disagree_threshold_pct:g}%"

        return {
            "reference_price": reference_price,
            "reference_source": REFERENCE_SOURCE,
            "pct_diff": round(pct_diff, 2) if pct_diff is not None else None,
            "agree": agree,
            "last_traded_date": last_traded_date,
            "days_stale": days_stale,
            "is_stale": is_stale,
            "status": status,
            "note": note,
        }


# ---- Test ----
if __name__ == "__main__":
    from logger import setup_logging
    setup_logging()
    logger = get_logger(__name__)

    pv = PriceValidator()
    ref = pv.fetch_reference_prices()
    print(f"Reference prices: {len(ref)} stocks")
    for sym, price in [("SCOM", 35.0), ("KCB", 81.0), ("HAFR", 1.18)]:
        print(f"\n{sym} @ {price}:")
        print(" ", pv.validate(sym, price))
