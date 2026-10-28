"""Public dividend declarations checked against named issuer documents.

Payment, record/book-closure and ex-dividend dates are separate facts. Missing
fields mean unconfirmed, never zero. A proposed dividend is not an approval.
Annual DPS is included only when the same issuer document gives a full-year
total; one installment must never replace an annual portfolio estimate.

The reference is an auditable snapshot, not a claim of perpetual freshness.
DividendCalendar compares older snapshots with its persistent calendar cache.
Add/update declarations here after reading the cited source; no private data.
"""

import datetime as dt
import json
import os

from logger import get_logger

logger = get_logger(__name__)

# ----------------------------------------------------------------------------
# Verified reference data. A ticker NOT listed here simply falls back to the
# mystocks.co.ke calendar in dividend_calendar.py — nothing breaks.
# ----------------------------------------------------------------------------
def _cycle(amount, kind, payment, record, source, url, **extra):
    return {"amount": amount, "type": kind, "payment_date": payment,
            "book_closure": record, "source": source, "source_url": url,
            "verified_date": "2026-10-22", "confidence": "primary",
            "currency": "KES", "status": "declared", **extra}


DIVIDEND_REFERENCE = {
    "KPLC": [
        _cycle(1.20, "final dividend", None, None,
               "Kenya Power FY2025/26 results announcement, 18 September 2026",
               "https://newsroom.kplc.co.ke/articles/kenya-power-posts-kshs2499-billion-profit-after-tax-on-the-back-of-higher-electricity-revenue-and-reduction-in-finance-costs",
               status="recommended", annual_dps=1.50, annual_period="FY2025/26 (proposed total)",
               note="The issuer confirms the amount and annual total, but this announcement does not give payment or book-closure dates."),
        _cycle(0.80, "final dividend", "2026-01-30", "2025-12-02",
               "Kenya Power 2025 AGM shareholder questions, answers 34–37",
               "https://www.kplc.co.ke/storage/01KB2DNMGZW98TH2XTFFB51AZ8.pdf",
               status="declared"),
    ],
    "KCB": [
        _cycle(3.00, "interim dividend", "2026-11-10", "2026-09-02",
               "KCB Group dividend register", "https://kcbgroup.com/dividends"),
    ],
    "EQTY": [
        _cycle(5.75, "first and final dividend", "2026-06-30", "2026-05-22",
               "Equity Group 22nd AGM resolutions, 24 June 2026",
               "https://equitygroupholdings.com/nairobi-24th-june-2026-equity-group-holdings-plc-shareholders-have-approved-all-proposed-resolutions-tabled-at-the-22nd-annual-general-meeting-agm-including-dividend-payout-board-appointments-a/",
               annual_dps=5.75, annual_period="FY2025"),
    ],
    "COOP": [
        _cycle(1.50, "final dividend", "2026-06-05", "2026-04-30",
               "Co-operative Bank 2026 AGM notice, resolution 3",
               "https://www.co-opbank.co.ke/wp-content/uploads/2026/04/AGM-2026-notice-Nation_FA.pdf",
               status="recommended", annual_dps=2.50, annual_period="FY2025",
               note="AGM notice gives proposed terms; approval has not been independently checked here."),
    ],
    "ABSA": [
        _cycle(0.50, "interim dividend", "2026-10-15", "2026-09-18",
               "Absa Bank Kenya H1 2026 results, Delivering shareholder value",
               "https://www.absabank.co.ke/content/dam/kenya/absa/pdf/Investor-Relations/Financial-Results/unaudited-results-for-period-ended-30-June-2026.pdf"),
    ],
    "NCBA": [
        _cycle(3.75, "interim dividend", None, None,
               "NCBA Group H1 2026 results announcement, 5 August 2026",
               "https://ncbagroup.com/blog/newsroom-article/ncba-group-plc-profit-after-tax-rises-to-kes-12-4-billion-in-h1-2026-results/",
               note="Amount confirmed; payment and record dates not confirmed in this source."),
    ],
    "GOOG": [
        _cycle(0.22, "quarterly dividend", "2026-09-14", None,
               "Alphabet Q2 2026 Form 10-Q, Common Dividend Program",
               "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/goog-20260630.htm",
               currency="USD", record_date="2026-09-07"),
    ],
    "SCOM": [
        _cycle(1.15, "final dividend", "2026-09-04", "2026-08-04",
               "Safaricom FY2026 final dividend, per stockanalysis.com and Kenyan Wall Street, "
               "corroborated by africanfinancials.com",
               "https://stockanalysis.com/quote/nase/SCOM/dividend/",
               status="declared", confidence="secondary", ex_date="2026-08-05",
               annual_dps=2.00, annual_period="FY2026 (interim + final)"),
        _cycle(0.85, "interim dividend", "2026-03-31", "2026-02-25",
               "Safaricom FY2026 interim dividend, per stockanalysis.com and Kenyan Wall Street",
               "https://stockanalysis.com/quote/nase/SCOM/dividend/",
               status="declared", confidence="secondary", ex_date="2026-02-26"),
    ],
    "IMH": [
        _cycle(2.25, "final dividend", "2026-05-21", "2026-04-16",
               "I&M Group FY2025 final dividend, per stockanalysis.com, corroborated by Kenyan Wall Street",
               "https://stockanalysis.com/quote/nase/IMH/dividend/",
               status="declared", confidence="secondary", ex_date="2026-04-17"),
        _cycle(1.50, "interim dividend", "2026-01-14", "2025-12-15",
               "I&M Group FY2025 interim dividend, per stockanalysis.com, corroborated by Kenyan Wall Street",
               "https://stockanalysis.com/quote/nase/IMH/dividend/",
               status="declared", confidence="secondary", ex_date="2025-12-16"),
    ],
    "SBIC": [
        _cycle(1.64, "interim dividend", "2026-10-05", "2026-09-01",
               "Stanbic Holdings FY2026 interim dividend, per stockanalysis.com, corroborated by CDSC Kenya",
               "https://stockanalysis.com/quote/nase/SBIC/dividend/",
               status="declared", confidence="secondary", ex_date="2026-09-02"),
        _cycle(18.55, "final dividend", "2026-06-04", "2026-05-15",
               "Stanbic Holdings FY2025 final dividend, per stockanalysis.com, corroborated by Business "
               "Daily and Kenyan Wall Street",
               "https://stockanalysis.com/quote/nase/SBIC/dividend/",
               status="declared", confidence="secondary", ex_date="2026-05-18",
               annual_dps=22.35, annual_period="FY2025 (interim + final)"),
    ],
    "NSE": [
        _cycle(1.00, "first and final dividend", "2026-07-31", "2026-05-21",
               "Nairobi Securities Exchange Plc FY2025 first & final dividend, per stockanalysis.com",
               "https://stockanalysis.com/quote/nase/NSE/dividend/",
               status="declared", confidence="secondary", ex_date="2026-05-22",
               annual_dps=1.00, annual_period="FY2025"),
    ],
    # NOT included:
    # - HFCB (HF Group): researched 22 October 2026 — reported to pay no
    #   dividend; left out rather than invent a zero-amount cycle.
    # - BKG (BK Group / Bank of Kigali): cross-listed, dividend declared in
    #   RWF with Kenyan-shilling conversion mechanics this reference hasn't
    #   verified — the ORIGINAL dividend_calendar.py docstring already flagged
    #   this name's currency figures as unreliable. Needs dedicated research
    #   before adding, not a quick lookup.
    "KEGN": [
        _cycle(0.75, "first and final dividend", "2027-01-21", "2026-10-29",
               "CDSC Kenya corporate-actions registry ('First & Final 0.75 29-Oct-26 21-Jan-27'), "
               "corroborated by Business Daily's direct quote of the figures",
               "https://cdsckenya.com/corporate-actions/",
               status="declared", confidence="secondary",
               note="KES 4.94bn total payout, down from KES 0.90/share (KES 5.94bn) the prior year. "
                    "An ex-dividend date (reported elsewhere as 30-Oct-2026) was not independently confirmed here."),
    ],
    "NOK": [
        _cycle(0.046153, "quarterly ADR dividend", "2026-08-11", None,
               "Citi depositary: Nokia ADR final gross distribution",
               "https://depositaryreceipts.citi.com/adr/guides/pgm_dispadivd.aspx?cusip=654902204&pageid=15&subpageid=113&type=d",
               currency="USD", record_date="2026-07-28",
               note="Gross USD per ADR, before tax and depositary fees; Finnish ordinary-share payment dates differ."),
        _cycle(None, "quarterly ADR dividend", "2026-11-10", None,
               "Citi depositary: Nokia ADR preliminary distribution schedule",
               "https://depositaryreceipts.citi.com/adr/guides/pgm_dispadivd.aspx?cusip=654902204&pageid=15&subpageid=113&type=d",
               currency="USD", record_date="2026-10-27", status="preliminary",
               note="Preliminary ADR payment date; final USD amount is not confirmed. The Finnish ordinary-share timetable is different."),
    ],
}

# Common typos/aliases -> canonical DIVIDEND_REFERENCE key
_ALIASES = {"GOOGL": "GOOG"}


def _canonical(ticker):
    ticker = (ticker or "").strip().upper()
    return _ALIASES.get(ticker, ticker)


def _parse_date(cycle, key):
    try:
        return dt.date.fromisoformat(cycle[key])
    except (KeyError, TypeError, ValueError):
        return None


def current_cycle(ticker, today=None):
    """The relevant dividend cycle for `ticker`, or None if it isn't in the
    reference table. Picks the soonest cycle whose payment hasn't happened
    yet; if every cycle has already been paid, picks the most recently paid
    one."""
    cycles = DIVIDEND_REFERENCE.get(_canonical(ticker))
    if not cycles:
        return None
    today = today or dt.date.today()
    upcoming = [c for c in cycles if (_parse_date(c, "payment_date") or dt.date.max) >= today]
    if upcoming:
        return min(upcoming, key=lambda c: _parse_date(c, "payment_date") or dt.date.max)
    return max(cycles, key=lambda c: _parse_date(c, "payment_date") or dt.date.min)


def load_extra_reference(path):
    """Add reference cycles from a JSON file (same shape as
    DIVIDEND_REFERENCE) — how the sandbox uses made-up dividends. Normal runs
    don't: it happens only when DIVIDEND_REFERENCE_EXTRA names such a file.
    Returns how many tickers were added."""
    try:
        with open(path, encoding="utf-8") as f:
            extra = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"DIVIDEND_REFERENCE_EXTRA: couldn't read {path}: {e}")
        return 0
    added = {_canonical(k): v for k, v in extra.items() if isinstance(v, list)}
    DIVIDEND_REFERENCE.update(added)
    return len(added)


if os.environ.get("DIVIDEND_REFERENCE_EXTRA"):
    load_extra_reference(os.environ["DIVIDEND_REFERENCE_EXTRA"])
