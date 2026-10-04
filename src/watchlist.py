"""
⭐ Watchlist — stocks you're keeping an eye on, Kenyan (NSE) and international
(anything Yahoo Finance lists: US, London, Johannesburg, ...).

Stored privately in portfolio/watchlist.csv — gitignored and dockerignored,
exactly like the holdings files (the portfolio/*.csv catch-all), because what
you're thinking of buying is as private as what you own.

    symbol,market,buy_below,sell_above,note,added,added_price
    EQTY,NSE,40,60,Waiting for the dividend,2026-10-01,45.10
    AAPL,INTL,,,,2026-10-01,

Two halves:

  1. Storage — load / add / remove / update rows, through portfolio_csv (the
     same forgiving-but-strict parsing as the holdings CSVs, and the same
     safe single-row edits with a backup copy first).

  2. Decision support — pure functions (no network, no files) that turn one
     stock's technical analysis + fundamentals into a plain-English signal
     checklist, your target status, "since you added it" performance and
     today's attention items. Unit-tested directly.

The checklist reports what each indicator says and WHY, and tallies which way
they lean. It deliberately never says "buy" or "sell" — that decision is
yours; this is information, not financial advice.
"""

import datetime as dt
import math
import os
import re
import urllib.parse

import portfolio_csv
from portfolio_csv import Field
from logger import get_logger

logger = get_logger(__name__)

WATCHLIST_CSV_FILE = "watchlist.csv"

MARKET_NSE = "NSE"
MARKET_INTL = "INTL"

WATCHLIST_FIELDS = (
    Field("symbol", ("ticker", "stock", "code", "instrument", "security"), "text", True),
    Field("market", ("exchange", "listing", "listed_on"), "text", False),
    Field("buy_below", ("buy_target", "target_buy", "buy_at", "buy_if_below", "buy_under",
                        "entry", "entry_price"), "number", False),
    Field("sell_above", ("sell_target", "target_sell", "sell_at", "sell_if_above", "sell_over",
                         "exit", "exit_price", "take_profit"), "number", False),
    Field("note", ("notes", "comment", "comments", "remarks", "why"), "text", False),
    Field("added", ("date_added", "added_on", "watching_since", "since", "date"), "date", False),
    Field("added_price", ("price_when_added", "price_added", "start_price"), "number", False),
)

_NSE_MARKET_WORDS = {"NSE", "KE", "KENYA", "NAIROBI", "NSEKE", "NSE KENYA", "KES"}
_INTL_MARKET_WORDS = {"INTL", "INTERNATIONAL", "FOREIGN", "GLOBAL", "WORLD", "OTHER", "US", "USA",
                      "UNITED STATES", "YAHOO", "NASDAQ", "NYSE", "NYSEARCA", "AMEX", "LSE",
                      "LONDON", "UK", "JSE", "JOHANNESBURG", "TSX", "XETRA", "EURONEXT"}

_NSE_SYMBOL = re.compile(r"^[A-Z0-9]{1,10}$")
_INTL_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
MAX_NOTE_LENGTH = 300


class AlreadyWatching(ValueError):
    """That stock is already on the watchlist."""


class NotOnWatchlist(ValueError):
    """That stock isn't on the watchlist (any more)."""


# ----------------------------------------------------------------------------
# Small validators shared by the CSV loader and the dashboard app
# ----------------------------------------------------------------------------
def clean_symbol(raw):
    return (raw or "").strip().upper()


def normalize_market(raw):
    """'NSE'/'Kenya'/... -> 'NSE'; 'US'/'International'/'NASDAQ'/... -> 'INTL';
    blank -> None (decide automatically). Unrecognised words also return None
    (with a warning) rather than a guess."""
    word = re.sub(r"\s+", " ", (raw or "").strip().upper())
    if not word:
        return None
    if word in _NSE_MARKET_WORDS or "NAIROBI" in word or "KENYA" in word:
        return MARKET_NSE
    if word in _INTL_MARKET_WORDS:
        return MARKET_INTL
    logger.warning(f"watchlist: market '{raw}' not recognised — use NSE or INTL; deciding automatically")
    return None


def resolve_market(symbol, market, nse_symbols=None):
    """An explicit market wins; otherwise NSE if it's an NSE ticker, else international."""
    m = normalize_market(market)
    if m:
        return m
    known = set(nse_symbols or ())
    if not known:
        # No live NSE list to hand: fall back to the names we know offline.
        from portfolio import SYMBOL_NAMES
        known = set(SYMBOL_NAMES)
    return MARKET_NSE if symbol in known else MARKET_INTL


def validate_symbol(symbol, market):
    """Raise ValueError with a plain-English reason if `symbol` can't be a ticker on `market`."""
    if not symbol:
        raise ValueError("Please enter a ticker symbol (e.g. EQTY or AAPL).")
    if market == MARKET_NSE:
        if not _NSE_SYMBOL.match(symbol):
            raise ValueError(f"'{symbol}' isn't a valid NSE ticker — NSE tickers are short codes "
                             f"of letters and digits, like EQTY or EABL.")
    else:
        if symbol.startswith("^") or "=" in symbol:
            raise ValueError(f"'{symbol}' is a market index or futures contract, not a stock — "
                             f"search for a company or ETF instead.")
        if not _INTL_SYMBOL.match(symbol):
            raise ValueError(f"'{symbol}' doesn't look like a ticker — use letters/digits, plus . or - "
                             f"for other exchanges (e.g. AAPL, BRK-B, VOD.L).")


def parse_target(raw, label):
    """'' / None -> None; anything else must be a positive number (same parsing
    rules as the CSV files: '1,234.50' ok, '224,3' refused)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    value = raw if isinstance(raw, (int, float)) else portfolio_csv.parse_number(str(raw))
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a price above zero.")
    return value


def validate_targets(buy_below, sell_above):
    b = parse_target(buy_below, "Buy-below price")
    s = parse_target(sell_above, "Sell-above price")
    if b is not None and s is not None and b >= s:
        raise ValueError(f"Your buy-below price ({b:g}) should be lower than your sell-above "
                         f"price ({s:g}) — otherwise both would trigger at once.")
    return b, s


def clean_note(note):
    note = re.sub(r"[\x00-\x1f\x7f]+", " ", note or "").strip()
    note = re.sub(r"\s{2,}", " ", note)
    if len(note) > MAX_NOTE_LENGTH:
        raise ValueError(f"That note is too long — please keep it under {MAX_NOTE_LENGTH} characters.")
    return note


# ----------------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------------
def watchlist_path(portfolio_dir):
    return os.path.join(portfolio_dir, WATCHLIST_CSV_FILE)


def _load_rows(portfolio_dir, nse_symbols=None):
    """Every usable row (duplicates included), as entry dicts with their CSV line."""
    path = watchlist_path(portfolio_dir)
    if not os.path.exists(path):
        return []
    rows = []
    for line_no, rec in portfolio_csv.read_rows(path, WATCHLIST_FIELDS):
        symbol = clean_symbol(rec.get("symbol"))
        market = resolve_market(symbol, rec.get("market"), nse_symbols)
        try:
            validate_symbol(symbol, market)
        except ValueError as e:
            logger.warning(f"{WATCHLIST_CSV_FILE} line {line_no}: {e} — skipping this row")
            continue
        buy, sell = rec.get("buy_below"), rec.get("sell_above")
        if buy is not None and buy <= 0:
            logger.warning(f"{WATCHLIST_CSV_FILE} line {line_no}: buy_below must be above zero — ignoring it")
            buy = None
        if sell is not None and sell <= 0:
            logger.warning(f"{WATCHLIST_CSV_FILE} line {line_no}: sell_above must be above zero — ignoring it")
            sell = None
        if buy is not None and sell is not None and buy >= sell:
            logger.warning(f"{WATCHLIST_CSV_FILE} line {line_no}: buy_below ({buy:g}) is not below "
                           f"sell_above ({sell:g}) — check that row")
        rows.append({
            "symbol": symbol, "market": market,
            "buy_below": buy, "sell_above": sell,
            "note": (rec.get("note") or "").strip(),
            "added": rec.get("added"),
            "added_price": rec.get("added_price") if (rec.get("added_price") or 0) > 0 else None,
            "line": line_no,
        })
    return rows


def load_watchlist(portfolio_dir, nse_symbols=None):
    """The watchlist in file order, one entry per (market, symbol) — if a stock
    is listed twice, the first row counts and the duplicate is reported."""
    seen, out = set(), []
    for row in _load_rows(portfolio_dir, nse_symbols):
        key = (row["market"], row["symbol"])
        if key in seen:
            logger.warning(f"{WATCHLIST_CSV_FILE} line {row['line']}: {row['symbol']} is listed more "
                           f"than once — using the first row")
            continue
        seen.add(key)
        out.append(row)
    return out


def add_to_watchlist(portfolio_dir, symbol, market=None, buy_below=None, sell_above=None,
                     note="", added=None, added_price=None, nse_symbols=None):
    """Add one stock. Creates watchlist.csv (with its header row) on first use.
    Raises AlreadyWatching / ValueError (plain-English) instead of writing a bad row.
    Returns (entry, dropped_fields)."""
    symbol = clean_symbol(symbol)
    market = resolve_market(symbol, market, nse_symbols)
    validate_symbol(symbol, market)
    buy, sell = validate_targets(buy_below, sell_above)
    note = clean_note(note)
    added = added or dt.date.today().isoformat()
    added_price = parse_target(added_price, "Price when added") if added_price not in (None, "") else None

    for row in load_watchlist(portfolio_dir, nse_symbols):
        if row["symbol"] == symbol and row["market"] == market:
            raise AlreadyWatching(f"{symbol} is already on your watchlist.")

    path = watchlist_path(portfolio_dir)
    if not os.path.exists(path):
        try:
            portfolio_csv.create_csv(path, WATCHLIST_FIELDS)
        except portfolio_csv.CsvFormatError:
            pass        # created a moment ago by someone else — fine, append to it
    values = {"symbol": symbol, "market": market, "buy_below": buy, "sell_above": sell,
              "note": note, "added": added, "added_price": added_price}
    dropped = portfolio_csv.append_row(path, WATCHLIST_FIELDS, values)
    if dropped:
        logger.warning(f"{WATCHLIST_CSV_FILE} has no column for {', '.join(dropped)}, so that "
                       f"value was not saved — add the column to its header row to keep it.")
    return dict(values), dropped


def remove_from_watchlist(portfolio_dir, symbol, market, nse_symbols=None):
    """Remove a stock (every row for it, if it was listed twice). A backup copy of
    the file is taken first. Raises NotOnWatchlist if it isn't there."""
    symbol = clean_symbol(symbol)
    market = normalize_market(market) or resolve_market(symbol, None, nse_symbols)
    rows = [r for r in _load_rows(portfolio_dir, nse_symbols)
            if r["symbol"] == symbol and r["market"] == market]
    if not rows:
        raise NotOnWatchlist(f"{symbol} isn't on your watchlist (any more).")
    path = watchlist_path(portfolio_dir)
    # Bottom-up, so the line numbers of the rows still to remove stay valid.
    for i, row in enumerate(sorted(rows, key=lambda r: r["line"], reverse=True)):
        portfolio_csv.remove_row(path, WATCHLIST_FIELDS, row["line"],
                                 expect={"symbol": symbol}, backup=(i == 0))
    first = min(rows, key=lambda r: r["line"])
    return {k: first[k] for k in ("symbol", "market", "buy_below", "sell_above", "note",
                                   "added", "added_price")}


_KEEP = object()


def update_watchlist_entry(portfolio_dir, symbol, market, buy_below=_KEEP, sell_above=_KEEP,
                           note=_KEEP, nse_symbols=None):
    """Change a stock's targets and/or note in place (None or '' clears a value).
    Returns (updated_entry, dropped_fields)."""
    symbol = clean_symbol(symbol)
    market = normalize_market(market) or resolve_market(symbol, None, nse_symbols)
    row = next((r for r in load_watchlist(portfolio_dir, nse_symbols)
                if r["symbol"] == symbol and r["market"] == market), None)
    if row is None:
        raise NotOnWatchlist(f"{symbol} isn't on your watchlist (any more).")
    new_buy = row["buy_below"] if buy_below is _KEEP else buy_below
    new_sell = row["sell_above"] if sell_above is _KEEP else sell_above
    new_buy, new_sell = validate_targets(new_buy, new_sell)
    changes = {"buy_below": new_buy, "sell_above": new_sell}
    if note is not _KEEP:
        changes["note"] = clean_note(note)
    values, dropped = portfolio_csv.update_row(watchlist_path(portfolio_dir), WATCHLIST_FIELDS,
                                               row["line"], {"symbol": symbol}, changes)
    entry = dict(row)
    entry.update({"buy_below": values.get("buy_below"), "sell_above": values.get("sell_above"),
                  "note": (values.get("note") or "").strip()})
    return entry, dropped


# ----------------------------------------------------------------------------
# Formatting (shared by the page + app) — prices are shown in the stock's own
# currency. Yahoo quotes London/Johannesburg/Tel Aviv prices in the MINOR unit
# (GBp = pence, ZAc = cents, ILA = agorot) but market caps in the major one.
# ----------------------------------------------------------------------------
_MINOR_UNITS = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0),
                "ZAC": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}


def _decimals(value):
    return 2 if abs(value) >= 0.1 else 4


def fmt_money(value, currency):
    """$1,234.56 | KES 1,234.56 | 126.80 GBp ; '—' if missing."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    d = _decimals(value)
    if currency == "USD":
        return f"${value:,.{d}f}" if value >= 0 else f"-${-value:,.{d}f}"
    if currency in (None, "", "KES"):
        return f"KES {value:,.{d}f}"
    return f"{value:,.{d}f} {currency}"


def fmt_big(value, currency):
    """Market-cap style: $3.21T, KES 1.40T, 29.4B GBP."""
    if not value:
        return "—"
    major = _MINOR_UNITS.get(currency, (currency, 1.0))[0]
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= div:
            num = f"{value / div:,.2f}{unit}"
            break
    else:
        num = f"{value:,.0f}"
    if major == "USD":
        return f"${num}"
    if major in (None, "", "KES"):
        return f"KES {num}"
    return f"{num} {major}"


def fmt_pct(value, signed=True, decimals=1):
    if value is None:
        return "—"
    return f"{value:+.{decimals}f}%" if signed else f"{value:.{decimals}f}%"


def external_link(symbol, market):
    if market == MARKET_NSE:
        return f"https://www.tradingview.com/symbols/NSEKE-{symbol}/", "TradingView"
    return f"https://finance.yahoo.com/quote/{urllib.parse.quote(symbol)}", "Yahoo Finance"


# ----------------------------------------------------------------------------
# Decision support — pure functions
# ----------------------------------------------------------------------------
def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _iso_date(v):
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def extract_facts(market, analysis_result, fund):
    """One common shape for NSE (TradingView) and international (Yahoo) data."""
    ar = analysis_result or {}
    latest = ar.get("latest") or {}
    signals = ar.get("signals") or {}
    fund = fund or {}
    nse = market == MARKET_NSE

    price = _num(latest.get("close"))
    if price is None:
        price = _num(fund.get("close") if nse else fund.get("price"))
    change = _num(ar.get("daily_change_pct"))
    if change is None and nse:
        change = _num(fund.get("change_pct"))

    currency = "KES" if nse else (fund.get("currency") or "USD")
    if nse:
        hi, lo = _num(fund.get("price_52w_high")), _num(fund.get("price_52w_low"))
        ex_div = fund.get("dividend_ex_date")
        earnings = fund.get("earnings_next_date")
    else:
        hi, lo = _num(fund.get("week52_high")), _num(fund.get("week52_low"))
        ex_div = fund.get("ex_dividend_date")
        earnings = fund.get("next_earnings_date")

    return {
        "market": market, "price": price, "change_pct": change, "currency": currency,
        "name": fund.get("name"), "sector": fund.get("sector"), "industry": fund.get("industry"),
        "website": None if nse else fund.get("website"),
        "exchange": "NSE Kenya" if nse else (fund.get("exchange_name") or ""),
        "quote_type": fund.get("quote_type"),
        "week52_high": hi, "week52_low": lo,
        "rsi": _num(latest.get("rsi")), "sma20": _num(latest.get("sma_20")),
        "sma50": _num(latest.get("sma_50")),
        "ma_signal": signals.get("ma_crossover"), "macd_signal": signals.get("macd"),
        "pe": _num(fund.get("pe_ratio")), "forward_pe": _num(fund.get("forward_pe")),
        "peg": _num(fund.get("peg_ratio")), "price_to_book": _num(fund.get("price_to_book")),
        "eps": _num(fund.get("eps_ttm")), "roe": _num(fund.get("roe")),
        "net_margin": _num(fund.get("net_margin")), "debt_to_equity": _num(fund.get("debt_to_equity")),
        "beta": _num(fund.get("beta")), "market_cap": _num(fund.get("market_cap")),
        "dividend_yield": _num(fund.get("dividend_yield")),
        "payout": _num(fund.get("dividend_payout_ratio")),
        "ex_dividend_date": _iso_date(ex_div), "earnings_date": _iso_date(earnings),
        "rec_key": (fund.get("recommendation_key") or "").lower() or None,
        "num_analysts": fund.get("num_analysts"), "target_mean": _num(fund.get("target_mean_price")),
        "analyst_mark": _num(fund.get("recommendation")) if nse else None,
        "tech_rating": _num(fund.get("tech_rating")) if nse else None,
        "value_traded": _num(fund.get("value_traded")),
        "volume": _num(latest.get("volume")), "avg_volume": _num(latest.get("volume_sma_20")),
        "price_source": latest.get("price_source"),
    }


def _item(key, label, lean, text, in_tally=True):
    return {"key": key, "label": label, "lean": lean, "text": text, "in_tally": in_tally}


_REC_LABELS = {"strong_buy": "Strong Buy", "buy": "Buy", "hold": "Hold", "underperform": "Underperform",
               "sell": "Sell", "strong_sell": "Strong Sell"}


def signal_checklist(facts, sector_medians=None, score=None, buy_below=None, sell_above=None):
    """The plain-English checklist. Each item leans 'buy', 'sell', 'neutral' or
    'na' (no data) and says why. Items with in_tally=False (52-week position,
    your own targets) are shown but don't count towards the tally."""
    f, cur = facts, facts["currency"]
    p = f["price"]
    money = lambda v: fmt_money(v, cur)  # noqa: E731
    items = []

    # 1. Trend — price vs its 50-day average
    if p is not None and f["sma50"]:
        gap = (p - f["sma50"]) / f["sma50"] * 100
        if p >= f["sma50"]:
            items.append(_item("trend", "Trend", "buy",
                               f"Price is {gap:.1f}% above its 50-day average ({money(f['sma50'])}) — an uptrend."))
        else:
            items.append(_item("trend", "Trend", "sell",
                               f"Price is {-gap:.1f}% below its 50-day average ({money(f['sma50'])}) — a downtrend."))
    else:
        items.append(_item("trend", "Trend", "na", "Not enough price history yet for a 50-day average."))

    # 2. 20-day vs 50-day average
    ma = f["ma_signal"]
    if ma == "golden_cross":
        items.append(_item("ma", "Moving averages", "buy", "Golden cross today: the 20-day average just rose "
                           "above the 50-day — often an early sign of a new uptrend."))
    elif ma == "bullish":
        items.append(_item("ma", "Moving averages", "buy", "The 20-day average is above the 50-day — "
                           "recent prices are stronger than the longer run."))
    elif ma == "death_cross":
        items.append(_item("ma", "Moving averages", "sell", "Death cross today: the 20-day average just fell "
                           "below the 50-day — often an early sign of a downtrend."))
    elif ma == "bearish":
        items.append(_item("ma", "Moving averages", "sell", "The 20-day average is below the 50-day — "
                           "recent prices are weaker than the longer run."))
    else:
        items.append(_item("ma", "Moving averages", "na", "Not enough price history to compare the averages."))

    # 3. Momentum — MACD
    m = f["macd_signal"]
    if m == "bullish_cross":
        items.append(_item("macd", "Momentum (MACD)", "buy", "MACD just crossed above its signal line — momentum is turning up."))
    elif m == "bullish":
        items.append(_item("macd", "Momentum (MACD)", "buy", "MACD is above its signal line — upward momentum."))
    elif m == "bearish_cross":
        items.append(_item("macd", "Momentum (MACD)", "sell", "MACD just crossed below its signal line — momentum is turning down."))
    elif m == "bearish":
        items.append(_item("macd", "Momentum (MACD)", "sell", "MACD is below its signal line — downward momentum."))
    else:
        items.append(_item("macd", "Momentum (MACD)", "na", "Not enough price history for MACD."))

    # 4. RSI
    r = f["rsi"]
    if r is None:
        items.append(_item("rsi", "RSI", "na", "Not enough price history for RSI."))
    elif r < 30:
        items.append(_item("rsi", "RSI", "buy", f"RSI {r:.0f} — oversold: it has fallen hard and fast, and "
                           f"bounces often follow (not always)."))
    elif r > 70:
        items.append(_item("rsi", "RSI", "sell", f"RSI {r:.0f} — overbought: it has risen fast, and "
                           f"pull-backs often follow."))
    else:
        items.append(_item("rsi", "RSI", "neutral", f"RSI {r:.0f} — neither overbought nor oversold."))

    # 5. Where it sits in its 52-week range (context only — cuts both ways)
    hi, lo = f["week52_high"], f["week52_low"]
    if p is not None and hi and lo and hi > lo:
        pos = max(0.0, min(100.0, (p - lo) / (hi - lo) * 100))
        if pos <= 10:
            txt = (f"Near its 52-week low ({money(lo)}) — cheap compared with the past year, "
                   f"but a falling price can keep falling.")
        elif pos >= 90:
            txt = (f"Near its 52-week high ({money(hi)}) — strong, but you'd be paying close to "
                   f"the year's top price.")
        else:
            txt = f"{pos:.0f}% of the way from its 52-week low ({money(lo)}) to its high ({money(hi)})."
        items.append(_item("range", "52-week range", "neutral", txt, in_tally=False))
    else:
        items.append(_item("range", "52-week range", "na", "No 52-week range available.", in_tally=False))

    # 6. Valuation
    pe = f["pe"]
    if pe is not None and pe < 0:
        items.append(_item("value", "Valuation", "sell", "The company lost money over the last 12 months (negative P/E)."))
    elif f["market"] == MARKET_NSE:
        verdict, med = None, None
        if pe and sector_medians:
            try:
                from market_context import valuation_vs_sector
                v = valuation_vs_sector({"sector": f["sector"], "pe_ratio": pe}, sector_medians).get("pe_ratio")
                if v:
                    verdict, med = v["verdict"], v["sector_median"]
            except Exception:
                verdict = None
        if verdict == "cheaper than sector":
            items.append(_item("value", "Valuation", "buy", f"P/E {pe:.1f} vs {med:.1f} for its sector — "
                               f"cheaper than similar companies."))
        elif verdict == "pricier than sector":
            items.append(_item("value", "Valuation", "sell", f"P/E {pe:.1f} vs {med:.1f} for its sector — "
                               f"pricier than similar companies."))
        elif verdict:
            items.append(_item("value", "Valuation", "neutral", f"P/E {pe:.1f} vs {med:.1f} for its sector — "
                               f"in line with similar companies."))
        elif pe:
            items.append(_item("value", "Valuation", "neutral", f"P/E {pe:.1f} (no sector comparison available)."))
        else:
            items.append(_item("value", "Valuation", "na", "No P/E ratio available."))
    else:
        peg, fpe = f["peg"], f["forward_pe"]
        if peg is not None and peg > 0:
            if peg < 1:
                items.append(_item("value", "Valuation", "buy", f"PEG {peg:.2f} — the price looks low for the "
                                   f"earnings growth analysts expect."))
            elif peg > 2:
                items.append(_item("value", "Valuation", "sell", f"PEG {peg:.2f} — the price looks high for the "
                                   f"earnings growth analysts expect."))
            else:
                items.append(_item("value", "Valuation", "neutral", f"PEG {peg:.2f} — a fair price for its expected growth."))
        elif pe and fpe and fpe > 0:
            if fpe < pe * 0.9:
                items.append(_item("value", "Valuation", "buy", f"Forward P/E {fpe:.1f} is below today's {pe:.1f} — "
                                   f"earnings are expected to grow."))
            elif fpe > pe * 1.1:
                items.append(_item("value", "Valuation", "sell", f"Forward P/E {fpe:.1f} is above today's {pe:.1f} — "
                                   f"earnings are expected to shrink."))
            else:
                items.append(_item("value", "Valuation", "neutral", f"P/E {pe:.1f}, forward P/E {fpe:.1f} — earnings expected to be steady."))
        elif pe:
            items.append(_item("value", "Valuation", "neutral", f"P/E {pe:.1f}."))
        else:
            items.append(_item("value", "Valuation", "na", "No earnings-based valuation (normal for an ETF)."))

    # 7. Analysts / ratings
    if f["market"] != MARKET_NSE:
        rec = f["rec_key"]
        if rec in _REC_LABELS:
            lean = "buy" if rec in ("strong_buy", "buy") else "sell" if rec in ("sell", "strong_sell", "underperform") else "neutral"
            n = f["num_analysts"]
            txt = f"{n} analysts' consensus: {_REC_LABELS[rec]}" if n else f"Analysts' consensus: {_REC_LABELS[rec]}"
            if f["target_mean"] and p:
                up = (f["target_mean"] - p) / p * 100
                txt += f"; average price target {money(f['target_mean'])} ({up:+.0f}% from today)"
                if lean == "buy" and up < 0:
                    txt += " — the price is already above that target"
            items.append(_item("analysts", "Analysts", lean, txt + "."))
        else:
            items.append(_item("analysts", "Analysts", "na", "No analyst coverage."))
    else:
        mark, tr = f["analyst_mark"], f["tech_rating"]
        if mark is not None:
            label = ("Strong Buy" if mark <= 1.5 else "Buy" if mark <= 2.5 else "Hold" if mark <= 3.5
                     else "Sell" if mark <= 4.5 else "Strong Sell")
            lean = "buy" if mark <= 2.5 else "neutral" if mark <= 3.5 else "sell"
            items.append(_item("analysts", "Analysts", lean, f"Analysts' average rating: {label} ({mark:.1f} on a 1–5 scale)."))
        elif tr is not None:
            label = ("Strong Buy" if tr >= 0.5 else "Buy" if tr >= 0.1 else "Neutral" if tr > -0.1
                     else "Sell" if tr > -0.5 else "Strong Sell")
            lean = "buy" if tr >= 0.1 else "sell" if tr <= -0.1 else "neutral"
            items.append(_item("analysts", "TradingView rating", lean,
                               f"TradingView's technical rating (a summary of ~26 indicators): {label}."))
        else:
            items.append(_item("analysts", "Analysts", "na", "No analyst or TradingView rating available."))

    # 8. Dividend
    dy, payout = f["dividend_yield"], f["payout"]
    high_yield = 7.0 if f["market"] == MARKET_NSE else 3.0
    if payout is not None and payout > 100 and dy:
        items.append(_item("dividend", "Dividend", "sell", f"Pays out more than it earns ({payout:.0f}% of profit) — "
                           f"the {dy:.1f}% dividend may be at risk."))
    elif dy and dy >= high_yield:
        items.append(_item("dividend", "Dividend", "buy", f"Pays a {dy:.1f}% dividend yield — a strong income stream."))
    elif dy:
        items.append(_item("dividend", "Dividend", "neutral", f"Dividend yield {dy:.1f}%."))
    else:
        items.append(_item("dividend", "Dividend", "neutral", "Pays no dividend at the moment."))

    # 9. Overall factor score (scoring.score_stock)
    overall = (score or {}).get("overall")
    if overall is None:
        items.append(_item("score", "Factor score", "na", "No factor score (not enough data)."))
    elif overall >= 70:
        items.append(_item("score", "Factor score", "buy", f"Scores {overall}/100 on value, quality, momentum, "
                           f"dividend and liquidity combined — strong."))
    elif overall < 40:
        items.append(_item("score", "Factor score", "sell", f"Scores {overall}/100 on value, quality, momentum, "
                           f"dividend and liquidity combined — weak."))
    else:
        items.append(_item("score", "Factor score", "neutral", f"Scores {overall}/100 on value, quality, momentum, "
                           f"dividend and liquidity combined — middling."))

    # 10. Liquidity (NSE — many counters trade very little)
    if f["market"] == MARKET_NSE and f["value_traded"] is not None and f["value_traded"] < 1_000_000:
        items.append(_item("liquidity", "Liquidity", "sell",
                           f"Thinly traded (only {fmt_big(f['value_traded'], 'KES')} changed hands today) — "
                           f"it may be hard to sell quickly at a fair price."))

    # 11. Your own targets (shown, not tallied — see target_status)
    ts = target_status(p, buy_below, sell_above, cur)
    if ts["status"] == "buy_zone":
        items.append(_item("targets", "Your targets", "buy", ts["text"], in_tally=False))
    elif ts["status"] == "sell_zone":
        items.append(_item("targets", "Your targets", "sell", ts["text"], in_tally=False))
    elif ts["status"] == "between":
        items.append(_item("targets", "Your targets", "neutral", ts["text"], in_tally=False))
    return items


def tally(items):
    t = {"buy": 0, "sell": 0, "neutral": 0}
    for it in items:
        if it["in_tally"] and it["lean"] in t:
            t[it["lean"]] += 1
    return t


def summarize(t):
    """-> (css_token, plain-English summary)."""
    b, s = t["buy"], t["sell"]
    if b + s + t["neutral"] == 0:
        return "none", "Not enough data for signals yet"
    if b - s >= 2 and b >= 2 * s:
        return "positive", "Mostly positive signals"
    if s - b >= 2 and s >= 2 * b:
        return "negative", "Mostly negative signals"
    return "mixed", "Mixed signals — no clear direction"


def target_status(price, buy_below, sell_above, currency="KES"):
    """Where the price sits relative to YOUR buy/sell prices. Exactly at a
    target counts as reaching it."""
    money = lambda v: fmt_money(v, currency)  # noqa: E731
    if buy_below is None and sell_above is None:
        return {"status": "none", "text": "No targets set."}
    if price is None:
        return {"status": "unknown", "text": "No price today to compare with your targets."}
    if sell_above is not None and price >= sell_above:
        return {"status": "sell_zone", "text": f"At or above your sell price ({money(sell_above)})."}
    if buy_below is not None and price <= buy_below:
        return {"status": "buy_zone", "text": f"At or below your buy price ({money(buy_below)})."}
    parts = []
    if buy_below is not None:
        parts.append(f"needs to fall {(price - buy_below) / price * 100:.1f}% to reach your buy price ({money(buy_below)})")
    if sell_above is not None:
        parts.append(f"needs to rise {(sell_above - price) / price * 100:.1f}% to reach your sell price ({money(sell_above)})")
    text = "Between your targets: " if len(parts) == 2 else ""
    text += "; ".join(parts)
    return {"status": "between", "text": text[0].upper() + text[1:] + "."}


def since_added(price, added_price, added_date, history=None, today=None):
    """% change since you started watching. Uses the recorded price when you
    added it; for hand-typed rows without one, the first close on/after the
    'added' date if that's inside the price history. None if neither is known,
    or if it was only added today."""
    if price is None:
        return None
    when = _iso_date(added_date)
    if when is not None and when >= (today or dt.date.today()):
        return None            # added today: a "change since" would just be noise
    base, basis = (added_price, "price when added") if added_price else (None, None)
    if base is None and when is not None and history is not None:
        try:
            for ts, close in history["close"].dropna().items():
                if ts.date() >= when:
                    base, basis = float(close), f"close on {ts.date().isoformat()}"
                    break
        except Exception:
            base = None
    if not base or base <= 0:
        return None
    return {"pct": (price - base) / base * 100, "base": base, "basis": basis,
            "date": when.isoformat() if when else None}


def returns_from_history(history):
    """1W/1M/3M/6M/YTD % returns from a close-price history (calendar-day
    look-backs, so it works the same on any exchange). Omits any period the
    history doesn't reach back to."""
    out = {}
    try:
        closes = history["close"].dropna()
    except Exception:
        return out
    if len(closes) < 2:
        return out
    last, last_ts = float(closes.iloc[-1]), closes.index[-1]
    first_ts = closes.index[0]
    for label, days in (("1W", 7), ("1M", 30), ("3M", 91), ("6M", 182)):
        target = last_ts - dt.timedelta(days=days)
        prior = closes[closes.index <= target]
        if len(prior):
            base = float(prior.iloc[-1])
        elif first_ts - target <= dt.timedelta(days=7):
            base = float(closes.iloc[0])       # history starts a few days short — close enough
        else:
            continue
        if base > 0:
            out[label] = (last / base - 1) * 100
    year_start = last_ts.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    prior = closes[closes.index < year_start]
    if len(prior) and float(prior.iloc[-1]) > 0:
        out["YTD"] = (last / float(prior.iloc[-1]) - 1) * 100
    return out


def performance(market, fund, history):
    """Performance chips: TradingView's own figures for NSE, computed from the
    price history (plus Yahoo's 52-week change) for international."""
    fund = fund or {}
    computed = returns_from_history(history) if history is not None else {}
    if market == MARKET_NSE:
        keys = (("1W", "perf_1w"), ("1M", "perf_1m"), ("3M", "perf_3m"), ("6M", "perf_6m"),
                ("YTD", "perf_ytd"), ("1Y", "perf_1y"))
        out = {}
        for label, k in keys:
            v = _num(fund.get(k))
            out[label] = v if v is not None else computed.get(label)
        return {k: v for k, v in out.items() if v is not None}
    out = dict(computed)
    wk = _num(fund.get("week52_change_pct"))
    if wk is not None:
        out["1Y"] = wk
    order = ("1W", "1M", "3M", "6M", "YTD", "1Y")
    return {k: out[k] for k in order if k in out}


def holding_context(holding, price, currency):
    """'You own ...' line for a watched stock you also hold."""
    if not holding or not holding.get("quantity"):
        return None
    qty, avg = holding["quantity"], holding.get("avg_cost") or 0
    gain = (price - avg) / avg * 100 if (price is not None and avg) else None
    qty_s = f"{qty:,.0f}" if float(qty).is_integer() else f"{qty:,.4g}"
    text = f"You own {qty_s} share{'s' if qty != 1 else ''} (average cost {fmt_money(avg, currency)}"
    text += f", {gain:+.1f}% since)" if gain is not None else ")"
    return {"quantity": qty, "avg_cost": avg, "gain_pct": gain, "text": text}


def attention_items(view, today=None):
    """Things worth a look today, most important first: [(icon, text)]."""
    today = today or dt.date.today()
    f, out = view["facts"], []
    sym, cur = view["symbol"], view["facts"]["currency"]
    if not view["has_data"]:
        return [("⚠️", f"{sym}: no price data today — check the ticker is right, or try again later.")]
    ts = view["target"]["status"]
    if ts == "buy_zone":
        out.append(("🎯", f"{sym} is at or below your buy price ({fmt_money(view['buy_below'], cur)})."))
    elif ts == "sell_zone":
        out.append(("💰", f"{sym} reached your sell price ({fmt_money(view['sell_above'], cur)})."))
    p, hi, lo = f["price"], f["week52_high"], f["week52_low"]
    if p is not None and hi and p >= hi * 0.98:
        out.append(("🔺", f"{sym} is near its 52-week high ({fmt_money(hi, cur)})."))
    if p is not None and lo and p <= lo * 1.03:
        out.append(("🔻", f"{sym} is near its 52-week low ({fmt_money(lo, cur)})."))
    chg = f["change_pct"]
    if chg is not None and abs(chg) >= 5:
        out.append(("🚀" if chg > 0 else "📉", f"{sym} {'rose' if chg > 0 else 'fell'} {abs(chg):.1f}% today."))
    if f["rsi"] is not None and f["rsi"] < 30:
        out.append(("🟢", f"{sym} looks oversold (RSI {f['rsi']:.0f})."))
    elif f["rsi"] is not None and f["rsi"] > 70:
        out.append(("🔴", f"{sym} looks overbought (RSI {f['rsi']:.0f})."))
    if f["ma_signal"] == "golden_cross":
        out.append(("✨", f"{sym}: golden cross today (20-day average crossed above the 50-day)."))
    elif f["ma_signal"] == "death_cross":
        out.append(("⚠️", f"{sym}: death cross today (20-day average crossed below the 50-day)."))
    if f["macd_signal"] == "bullish_cross":
        out.append(("📈", f"{sym}: MACD turned up today."))
    elif f["macd_signal"] == "bearish_cross":
        out.append(("📉", f"{sym}: MACD turned down today."))
    ed = f["earnings_date"]
    if ed and 0 <= (ed - today).days <= 7:
        days = (ed - today).days
        when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
        out.append(("📅", f"{sym} reports earnings {when} ({ed.isoformat()})."))
    xd = f["ex_dividend_date"]
    if xd and 0 <= (xd - today).days <= 14:
        out.append(("💵", f"{sym} goes ex-dividend on {xd.isoformat()} — you must own it before then to get that dividend."))
    if (view.get("validation") or {}).get("status") == "mismatch":
        out.append(("❗", f"{sym}: today's price wasn't confirmed by a second source — double-check before acting."))
    return out


def build_entry_view(entry, analysis_result=None, fund=None, *, sector_medians=None, score=None,
                     holding=None, validation=None, news=None, detail_file=None, today=None):
    """Everything the watchlist page shows for one stock, computed from data
    already fetched. Pure: no network, no files."""
    market, symbol = entry["market"], entry["symbol"]
    facts = extract_facts(market, analysis_result, fund)
    history = (analysis_result or {}).get("data")
    cur = facts["currency"]
    buy, sell = entry.get("buy_below"), entry.get("sell_above")
    items = signal_checklist(facts, sector_medians=sector_medians, score=score,
                             buy_below=buy, sell_above=sell)
    t = tally(items)
    summary_token, summary_text = summarize(t)
    hi, lo, p = facts["week52_high"], facts["week52_low"], facts["price"]
    pos = (max(0.0, min(100.0, (p - lo) / (hi - lo) * 100))
           if (p is not None and hi and lo and hi > lo) else None)
    link, link_label = external_link(symbol, market)
    view = {
        "symbol": symbol, "market": market,
        "name": facts["name"] or symbol, "facts": facts,
        "has_data": p is not None,
        "has_history": history is not None and getattr(history, "empty", True) is False,
        "buy_below": buy, "sell_above": sell, "note": entry.get("note") or "",
        "added": entry.get("added"),
        "target": target_status(p, buy, sell, cur),
        "since_added": since_added(p, entry.get("added_price"), entry.get("added"), history, today=today),
        "range_pos": pos,
        "checklist": items, "tally": t, "summary": summary_text, "summary_token": summary_token,
        "performance": performance(market, fund, history),
        "holding": holding_context(holding, p, cur),
        "score": (score or {}).get("overall"),
        "validation": validation or {},
        "news": list(news or [])[:3],
        "detail_file": detail_file,
        "external_url": link, "external_label": link_label,
    }
    view["attention"] = attention_items(view, today=today)
    return view
