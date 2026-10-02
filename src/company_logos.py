"""
Company logos for the ticker tables — small circular favicons next to each
stock symbol.

Source: Google's public favicon service
(https://www.google.com/s2/favicons?domain=X&sz=64). Tried Clearbit's logo
API first (the more commonly-cited option) — it's dead, DNS doesn't even
resolve. Google's service was verified live against real companies before
being wired in here (Intel, Safaricom, Equity Bank, Co-op Bank, Absa Kenya,
Standard Chartered, I&M Bank, KenGen, the NSE — all returned correct,
recognizable logos).

Logos are fetched ONCE and cached to disk as raw bytes (data/logos/), then
embedded as base64 data URIs in the generated HTML — never a live
<img src="https://..."> at view time. Two reasons, both matching existing
conventions in this codebase: (1) every other page here is fully
self-contained and offline-viewable (charts are base64 PNGs, the favicon is
an inline SVG data URI) — a live external image would be the only thing on
the page requiring a network call to view; (2) it would leak exactly which
stocks/companies someone is looking at to a third party on every single
page load, which cuts against how privacy-conscious this project already
is about the user's holdings.

NSE stocks have no "company website" field anywhere in this codebase's data
sources, so their domains are a small, HAND-VERIFIED mapping below — never
guessed blind. A wrong guess doesn't just fail gracefully, it can resolve to
someone else's real, unrelated logo: the first guess for KPLC
(kplc.co.ke) resolves to an unrelated betting site, not Kenya Power. Verify
any addition at https://www.google.com/s2/favicons?domain=X&sz=64 in a
browser and confirm it's actually the right company before adding it.
International stocks don't need manual mapping — international_data.py's
fetch_fundamentals() already captures yfinance's 'website' field, and the
domain is extracted from that automatically.

A symbol with no resolvable domain (most NSE stocks, for now) gets a
graceful fallback: a small colored circle with its first letters, never a
broken image or a guessed logo.
"""

import os
import re
import hashlib
import base64
from urllib.parse import urlparse

from logger import get_logger

logger = get_logger(__name__)

LOGO_CACHE_SUBDIR = "logos"

# ---------------------------------------------------------------------------
# NSE ticker -> domain. Hand-verified only (see module docstring) — an
# unmapped symbol falls back to the colored-initial badge, which is always
# safer than a guessed domain.
# ---------------------------------------------------------------------------
NSE_LOGO_DOMAINS = {
    "SCOM": "safaricom.co.ke",
    "EQTY": "equity.co.ke",
    "EABL": "eabl.com",
    "COOP": "co-opbank.co.ke",
    "ABSA": "absabank.co.ke",
    "SCBK": "sc.com",
    "IMH": "imbankgroup.com",
    "KEGN": "kengen.co.ke",
    "NSE": "nse.co.ke",
}

# Palette for the fallback badge — deterministic per symbol (same symbol
# always gets the same color), chosen for reasonable contrast with white text.
_FALLBACK_COLORS = [
    "#2563eb", "#16a34a", "#dc2626", "#9333ea", "#0891b2",
    "#c2410c", "#4338ca", "#0d9488", "#be185d", "#65a30d",
]


def resolve_domain(symbol, website=None):
    """Return a bare domain (no protocol/www/path) for a symbol, or None if
    unresolvable. `website` (from yfinance, international stocks) takes
    priority when given; otherwise falls back to the NSE mapping."""
    if website:
        try:
            parsed = urlparse(website if "//" in website else f"//{website}")
            domain = (parsed.netloc or parsed.path).strip().lower()
            domain = re.sub(r"^www\.", "", domain).split("/")[0]
            if domain:
                return domain
        except Exception as e:
            logger.debug(f"Could not parse website '{website}': {e}")
    return NSE_LOGO_DOMAINS.get(symbol)


def fetch_logo_base64(domain, cache_dir="data", size=64):
    """
    Return a base64-encoded PNG for `domain`'s favicon, or None on failure.
    Cached to disk indefinitely (logos don't change day to day, unlike
    prices) — never raises, matches every other fetcher in this codebase.
    """
    if not domain:
        return None
    logo_dir = os.path.join(cache_dir, LOGO_CACHE_SUBDIR)
    safe_name = re.sub(r"[^a-z0-9.-]", "_", domain.lower())
    path = os.path.join(logo_dir, f"{safe_name}.png")

    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = f.read()
            if data:
                return base64.b64encode(data).decode("ascii")
        except OSError as e:
            logger.debug(f"Logo cache read failed for {domain}: {e}")

    try:
        import requests
        url = f"https://www.google.com/s2/favicons?domain={domain}&sz={size}"
        r = requests.get(url, timeout=(3, 6))
        if r.status_code != 200 or not r.content:
            return None
        os.makedirs(logo_dir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(r.content)
        return base64.b64encode(r.content).decode("ascii")
    except Exception as e:
        logger.debug(f"Logo fetch failed for {domain}: {e}")
        return None


def fallback_badge_html(symbol, css_class="ticker-logo-fallback"):
    """A small colored-initial circle — used whenever no domain is
    resolvable or the fetch failed. Never a broken image, never a guess."""
    initials = (symbol or "?")[:2].upper()
    h = int(hashlib.md5((symbol or "").encode()).hexdigest(), 16)
    color = _FALLBACK_COLORS[h % len(_FALLBACK_COLORS)]
    return f'<span class="{css_class}" style="background:{color}">{initials}</span>'
