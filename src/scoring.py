"""
Transparent factor-scoring & screening module.

Combines the metrics the pipeline already gathers (valuation, quality,
momentum, dividend, liquidity) into a transparent 0-100 score PER FACTOR and
an overall blend. Every input and every point is exposed in `reasons`, so the
score is a screen you can inspect and tune — never a black box.

This is a mechanical screen of public metrics, NOT investment advice.

Also produces per-stock alerts (oversold, near 52-week low, strong signal,
high sustainable yield, illiquid, price-source mismatch) for the dashboard.
"""

from logger import get_logger

logger = get_logger(__name__)

# Default factor weights (must sum to 1.0). Tunable via Config.
DEFAULT_WEIGHTS = {
    "value": 0.25,
    "quality": 0.25,
    "momentum": 0.20,
    "dividend": 0.15,
    "liquidity": 0.15,
}


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _score_value(fund):
    """Lower P/E, P/B and PEG score higher. Returns (score, reasons)."""
    parts, reasons = [], []
    pe = fund.get("pe_ratio")
    if pe and pe > 0:
        s = _clamp(100 - (pe - 8) * 4)  # pe 8 -> 100, pe 33 -> 0
        parts.append(s)
        reasons.append(f"P/E {pe:.1f}")
    pb = fund.get("price_to_book")
    if pb and pb > 0:
        s = _clamp(100 - (pb - 1) * 30)  # pb 1 -> 100, pb ~4.3 -> 0
        parts.append(s)
        reasons.append(f"P/B {pb:.2f}")
    peg = fund.get("peg_ratio")
    if peg and peg > 0:
        s = _clamp(100 - (peg - 0.5) * 50)  # peg 0.5 -> 100, peg 2.5 -> 0
        parts.append(s)
        reasons.append(f"PEG {peg:.2f}")
    if not parts:
        return None, ["no valuation data"]
    return round(sum(parts) / len(parts)), reasons


def _score_quality(fund):
    """Higher ROE/margins, lower leverage score higher."""
    parts, reasons = [], []
    roe = fund.get("roe")
    if roe is not None:
        parts.append(_clamp(roe * 4))  # roe 25% -> 100
        reasons.append(f"ROE {roe:.1f}%")
    nm = fund.get("net_margin")
    if nm is not None:
        parts.append(_clamp(nm * 3.3))  # ~30% -> 100
        reasons.append(f"net margin {nm:.1f}%")
    de = fund.get("debt_to_equity")
    if de is not None and de >= 0:
        parts.append(_clamp(100 - de * 40))  # de 0 -> 100, de 2.5 -> 0
        reasons.append(f"D/E {de:.2f}")
    cr = fund.get("current_ratio")
    if cr is not None and cr > 0:
        parts.append(_clamp(cr * 50))  # cr 2 -> 100
        reasons.append(f"current ratio {cr:.2f}")
    if not parts:
        return None, ["no quality data"]
    return round(sum(parts) / len(parts)), reasons


def _score_momentum(analysis_result, fund):
    """Trend/MACD/RSI and 3-month performance."""
    parts, reasons = [], []
    signals = (analysis_result or {}).get("signals", {})
    latest = (analysis_result or {}).get("latest", {})

    overall = signals.get("overall")
    if overall == "bullish":
        parts.append(75); reasons.append("technical: bullish")
    elif overall == "bearish":
        parts.append(25); reasons.append("technical: bearish")
    elif overall == "neutral":
        parts.append(50); reasons.append("technical: neutral")

    rsi = latest.get("rsi")
    if rsi is not None:
        # Reward healthy uptrend (50-65); penalise overbought/oversold extremes
        if rsi > 70:
            parts.append(35)
        elif rsi < 30:
            parts.append(45)  # oversold: possible bounce, not strong momentum
        else:
            parts.append(_clamp(50 + (rsi - 50) * 2))
        reasons.append(f"RSI {rsi:.0f}")

    perf = fund.get("perf_3m")
    if perf is not None:
        parts.append(_clamp(50 + perf * 2))  # +25% -> 100
        reasons.append(f"3M {perf:+.1f}%")

    if not parts:
        return None, ["no momentum data"]
    return round(sum(parts) / len(parts)), reasons


def _score_dividend(fund):
    """Reward yield, but only if the payout looks sustainable."""
    parts, reasons = [], []
    dy = fund.get("dividend_yield")
    if dy is not None:
        parts.append(_clamp(dy * 12.5))  # 8% -> 100
        reasons.append(f"yield {dy:.1f}%")
    payout = fund.get("dividend_payout_ratio")
    if payout is not None and payout > 0:
        # 40-70% is healthy; >100% is unsustainable
        if payout > 100:
            parts.append(20)
        elif payout > 80:
            parts.append(55)
        else:
            parts.append(85)
        reasons.append(f"payout {payout:.0f}%")
    if not parts:
        return None, ["no dividend"]
    return round(sum(parts) / len(parts)), reasons


def _score_liquidity(fund):
    """Higher traded value = easier to enter/exit. KES value traded per day."""
    vt = fund.get("value_traded")
    if vt is None or vt <= 0:
        return None, ["no liquidity data"]
    # 100M KES/day -> ~100; 1M -> ~30
    import math
    s = _clamp((math.log10(vt) - 6) * 33)  # 1e6 ->0, 1e9 ->99
    return round(s), [f"traded KES {vt/1e6:.1f}M"]


def score_stock(symbol, analysis_result, fund, weights=None):
    """
    Produce a transparent factor score for one stock.

    Returns dict:
        {overall, value, quality, momentum, dividend, liquidity, reasons}
    Sub-scores are 0-100 or None when data is missing. `overall` is the
    weighted blend of the available sub-scores (weights renormalised).
    """
    weights = weights or DEFAULT_WEIGHTS
    fund = fund or {}

    subs = {
        "value": _score_value(fund),
        "quality": _score_quality(fund),
        "momentum": _score_momentum(analysis_result, fund),
        "dividend": _score_dividend(fund),
        "liquidity": _score_liquidity(fund),
    }

    scores = {k: v[0] for k, v in subs.items()}
    reasons = {k: v[1] for k, v in subs.items()}

    # Weighted blend over available sub-scores only
    num = 0.0
    den = 0.0
    for k, s in scores.items():
        if s is not None:
            w = weights.get(k, 0)
            num += s * w
            den += w
    overall = round(num / den) if den > 0 else None

    return {
        "symbol": symbol,
        "overall": overall,
        **scores,
        "reasons": reasons,
    }


def generate_alerts(symbol, analysis_result, fund, validation=None):
    """
    Produce a list of short, transparent alert strings for one stock.
    Each alert states the fact that triggered it.
    """
    alerts = []
    latest = (analysis_result or {}).get("latest", {})
    signals = (analysis_result or {}).get("signals", {})
    fund = fund or {}

    rsi = latest.get("rsi")
    if rsi is not None:
        if rsi < 30:
            alerts.append(f"🟢 Oversold (RSI {rsi:.0f})")
        elif rsi > 70:
            alerts.append(f"🔴 Overbought (RSI {rsi:.0f})")

    # 52-week proximity
    close = latest.get("close")
    hi = fund.get("price_52w_high")
    lo = fund.get("price_52w_low")
    if close and hi and hi > 0 and close >= hi * 0.98:
        alerts.append("🔺 Near 52-week high")
    if close and lo and lo > 0 and close <= lo * 1.03:
        alerts.append("🔻 Near 52-week low")

    # Strong technical signal
    tr = fund.get("tech_rating")
    if tr is not None:
        if tr >= 0.5:
            alerts.append("⭐ TradingView: Strong Buy signal")
        elif tr <= -0.5:
            alerts.append("⚠️ TradingView: Strong Sell signal")

    # Fresh MACD cross
    if signals.get("macd") == "bullish_cross":
        alerts.append("📈 MACD bullish crossover today")
    elif signals.get("macd") == "bearish_cross":
        alerts.append("📉 MACD bearish crossover today")

    # High sustainable dividend yield
    dy = fund.get("dividend_yield")
    payout = fund.get("dividend_payout_ratio")
    if dy and dy >= 8 and (payout is None or payout <= 100):
        alerts.append(f"💰 High dividend yield ({dy:.1f}%)")

    # Upcoming dividend ex-date
    if fund.get("dividend_ex_date_is_upcoming") and fund.get("dividend_ex_date"):
        alerts.append(f"📅 Ex-dividend {fund['dividend_ex_date']}")

    # Illiquid warning
    vt = fund.get("value_traded")
    if vt is not None and vt < 1_000_000:
        alerts.append("💧 Thinly traded (hard to exit)")

    # Price-source disagreement
    if validation and validation.get("status") == "mismatch":
        alerts.append(f"❗ Price unverified — {validation.get('note', '')}")
    elif validation and validation.get("status") == "stale":
        alerts.append(f"🕒 {validation.get('note', '')}")

    return alerts


# ---- Test ----
if __name__ == "__main__":
    import json, glob
    files = glob.glob("../data/fundamentals_*.json")
    if files:
        data = json.load(open(files[0]))
        for sym in ["SCOM", "KCB", "EQTY", "HAFR"]:
            f = data.get(sym, {})
            sc = score_stock(sym, {}, f)
            print(f"\n{sym}: overall={sc['overall']}  "
                  f"V={sc['value']} Q={sc['quality']} M={sc['momentum']} "
                  f"D={sc['dividend']} L={sc['liquidity']}")
            print("   alerts:", generate_alerts(sym, {}, f))
