"""
Plain-English explanations behind every ⓘ on the dashboard — one place, so
the same word is always explained the same way.

The company-figure entries come straight from
fundamental_analysis.METRIC_EXPLANATIONS (the explanations the stock pages
already used); the rest cover the portfolio, bonds, charts and market pages.
Written for someone who has never bought a share: what it is, and how to
read it.
"""

import re

# key -> (title, explanation)
_TERMS = {
    # ---- your money ----
    "net-worth": ("Net worth", "Everything you own on this dashboard added together, in Kenyan shillings: "
                  "your Kenyan shares at today's price, your bonds at their indicative value and your "
                  "international shares converted at today's USD/KES rate."),
    "cost-basis": ("What you put in", "The total you paid for your holdings, including every top-up. "
                   "Comparing it with today's value shows whether you are up or down overall."),
    "gain-loss": ("Gain / loss", "Today's value minus what you paid. It is only a gain or loss on paper "
                  "until you sell."),
    "total-return": ("Total return", "Your gain on the shares plus the dividends they paid, as a percentage of "
                     "what you put in — a fuller picture than the share price alone."),
    "day-change": ("Today", "How much the value moved since the previous trading day's close. Bonds don't "
                   "have a daily price here, so they are never part of this figure."),
    "weight": ("Weight", "How big a share of the total this holding is. A single holding with a large weight "
               "means more of your money depends on one company."),
    "allocation": ("Allocation", "How your money is split between Kenyan shares, bonds and international "
                   "shares. Spreading money across different kinds of investment lowers the risk that one "
                   "bad event hurts everything at once."),
    "fx-rate": ("USD/KES rate", "How many Kenyan shillings one US dollar buys today. International holdings "
                "are converted with this run's rate only — never an old or guessed one."),
    "lookback": ("Performance over time", "How your portfolio's value compares with its value one day, one week, "
                 "one month and one year ago, from the snapshots this dashboard saves each day you run it."),
    "est-dividend": ("Estimated yearly dividend", "Your number of shares multiplied by the most recent declared "
                     "dividend per share. Companies can change or skip dividends, so it is an estimate."),
    "yield-on-cost": ("Dividend yield on cost", "The yearly dividend as a percentage of what you paid, rather than "
                      "of today's price."),
    # ---- bonds ----
    "face-value": ("Face value", "The amount the government owes you and repays when the bond matures (or in "
                   "instalments, for amortizing bonds). Coupons are calculated on it."),
    "coupon": ("Coupon", "The interest a bond pays: face value × coupon rate ÷ 2, twice a year. The rate is "
               "fixed when the bond is issued and doesn't change afterwards."),
    "accrued-interest": ("Accrued interest", "Interest that has built up since the last coupon payment and will be "
                         "paid with the next one."),
    "indicative-value": ("Indicative value", "What the bond is roughly worth today: the face value still owed to "
                         "you plus accrued interest. It assumes the bond is worth exactly its face value, because "
                         "Kenya's bond market doesn't publish live prices for individual bonds."),
    "running-yield": ("Running yield", "The coupon rate after tax — the percentage of face value you receive each "
                      "year while you hold the bond."),
    "withholding-tax": ("Withholding tax", "Tax taken off bond interest before it reaches you: 10% for Treasury bonds "
                        "of 10 years or more, 15% for shorter ones. Infrastructure bonds (IFB) are exempt."),
    "amortization": ("Amortization", "Repaying a bond's face value in instalments before its final maturity date, "
                     "instead of all at once at the end."),
    "small-holder-rule": ("Small-holder rule", "Some infrastructure bond prospectuses say holdings up to KES 1 million "
                          "per account are repaid in full at the first amortization date — years before the final "
                          "maturity."),
    "price-sensitivity": ("Price / yield sensitivity", "If market interest rates rise above a bond's coupon, the bond "
                          "is worth less than face value if sold before maturity; if they fall, it's worth more. The "
                          "table shows by how much."),
    # ---- prices & charts ----
    "sma": ("Moving average (SMA)", "The average closing price over the last 20 or 50 trading days. A price above "
            "its moving average is a sign the trend is up; below it, down."),
    "bollinger": ("Bollinger bands", "A band around the 20-day average, two standard deviations wide. Prices near the "
                  "top of the band are high compared with recent trading, near the bottom low."),
    "macd": ("MACD", "Compares a fast and a slow moving average. When the MACD line crosses above its signal line, "
             "upward momentum is building; below it, downward."),
    "stochastic": ("Stochastic", "Where today's price sits within the last 14 days' high–low range, from 0 to 100. "
                   "Above 80 is near the top of the recent range, below 20 near the bottom."),
    "atr": ("ATR (average true range)", "How much the price typically moves in a day, in shillings. Higher means a "
            "more volatile stock."),
    "volume": ("Volume", "How many shares changed hands. Big price moves on high volume are more meaningful than "
               "moves on thin trading."),
    "range-52w": ("52-week range", "The lowest and highest price of the past year, and where today's price sits "
                  "between them."),
    "support-resistance": ("Support & resistance", "Price levels where the stock has repeatedly stopped falling "
                           "(support) or stopped rising (resistance) recently."),
    "signal": ("Signal", "A summary of what the price indicators say right now — not a prediction and not advice."),
    "tv-rating": ("TradingView rating", "TradingView's own technical rating, built from many indicators: Strong Buy, "
                  "Buy, Neutral, Sell or Strong Sell. It describes the trend, not the company's value."),
    "factor-score": ("Factor score", "A 0–100 screen built from five things: value (is it cheap?), quality (is it "
                     "profitable?), momentum (is the price rising?), dividend and liquidity (does it trade enough?). "
                     "70+ is strong, below 45 weak. A starting point for research, not advice."),
    "analyst-target": ("Analyst target", "The average price professional analysts expect within about a year, and "
                       "how far that is from today's price. Analysts are often wrong."),
    "price-check": ("Price check", "Whether TradingView's price matches the NSE's official close: ✓ verified, "
                    "❗ differs, 🕒 stale."),
    # ---- market ----
    "breadth": ("Market breadth", "How many stocks rose, fell or didn't move today. More risers than fallers means "
                "the market moved up broadly, not just a few big names."),
    "foreign-flows": ("Foreign investor flows", "How much foreign investors bought and sold on the NSE. Net selling "
                      "by foreigners often weighs on prices and the shilling."),
    "t-bill": ("Treasury bills", "Short government loans of 91, 182 or 364 days. The rate is what the government "
               "pays to borrow — a benchmark for every other interest rate."),
    "cbr": ("Central Bank Rate", "The Central Bank of Kenya's policy interest rate. Banks' lending and deposit rates "
            "tend to follow it."),
    "dividend-dates": ("Book closure & payment", "Own the shares before the book-closure date to qualify for the "
                       "dividend; it is paid on the payment date."),
    "earnings-date": ("Earnings date", "When the company is due to publish results. Prices often move sharply "
                      "around these dates."),
    # ---- company figures the stock pages show beyond the ones below ----
    "price-to-book": ("Price / book (P/B)", "The share price compared with the company's net worth on paper "
                      "(what it owns minus what it owes) per share. Below 1 means you pay less than that book "
                      "value — common for banks on the NSE."),
    "price-to-sales": ("Price / sales (P/S)", "The company's market value divided by a year's sales. Useful when "
                       "profits are small; lower is cheaper, but compare companies in the same industry."),
    "enterprise-value": ("Enterprise value (EV)", "What it would cost to buy the whole business: the value of all "
                         "its shares plus its debt, minus its cash."),
    "ev-ebitda": ("EV / EBITDA", "Enterprise value divided by a year's operating profit before interest, tax and "
                  "depreciation. Under 10 is usually considered cheap, above 20 expensive."),
    "ev-revenue": ("EV / revenue", "Enterprise value divided by a year's sales — like price / sales, but it "
                   "counts the company's debt too."),
    "roa": ("ROA (return on assets)", "A year's profit as a percentage of everything the company owns. Banks "
            "usually earn 1–3%; other companies 5% or more."),
    "fcf-margin": ("Free-cash-flow margin", "The share of sales left over as spare cash after running the "
                   "business and investing in it. Positive and rising is good."),
    "quick-ratio": ("Quick ratio", "Can the company pay its bills due within a year from cash and money owed to "
                    "it, without selling its stock of goods? 1 or more is comfortable."),
    "beta": ("Beta", "How much the share price tends to move compared with the whole market: 1 moves with the "
             "market, above 1 swings more, below 1 swings less."),
    "forward-pe": ("Forward P/E", "The share price divided by the profit analysts expect over the next year, "
                   "instead of last year's actual profit."),
    "net-debt": ("Net debt", "Debt minus cash. Below zero means the company holds more cash than it owes."),
    "ownership": ("Who owns the shares", "The share of the company held by insiders (directors and executives) "
                  "and by institutions such as funds and pension schemes."),
    "value-traded": ("Value traded", "The value of the shares that changed hands today. The higher it is, the "
                     "easier it is to buy or sell without moving the price."),
    "ex-dividend": ("Ex-dividend date", "Own the shares before this date to receive the declared dividend; buy on "
                    "or after it and the seller keeps that payment."),
    "ema": ("EMA (exponential moving average)", "A moving average that counts recent days more, so it reacts "
            "faster than the simple average."),
    "performance": ("Price performance", "How much the share price changed over each period. Dividends are not "
                    "included."),
    "ttm": ("TTM", "Trailing twelve months: the latest full year of results, whenever the company's year ends."),
}

# Company figures — the stock pages' existing explanations, reused as-is.
_FROM_METRICS = {
    "market_cap": "market-cap", "pe_ratio": "pe", "peg_ratio": "peg", "eps": "eps",
    "eps_growth": "eps-growth", "revenue_growth": "revenue-growth", "gross_margin": "gross-margin",
    "operating_margin": "operating-margin", "net_margin": "net-margin", "roe": "roe", "roic": "roic",
    "debt_to_equity": "debt-to-equity", "current_ratio": "current-ratio", "free_cash_flow": "free-cash-flow",
    "dividend_yield": "dividend-yield", "rsi": "rsi", "recommendation": "recommendation",
}
_loaded = False


def _load_metrics():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        from fundamental_analysis import METRIC_EXPLANATIONS
    except Exception:                       # glossary still works without them
        return
    for src, key in _FROM_METRICS.items():
        text = METRIC_EXPLANATIONS.get(src)
        if not text or key in _TERMS:
            continue
        title, _sep, body = text.partition(" — ")
        body = body.strip() if body else text
        _TERMS[key] = (title.strip(), body[:1].upper() + body[1:])


def key(term):
    return re.sub(r"[^a-z0-9]+", "-", str(term).lower()).strip("-")


def has(k):
    _load_metrics()
    return key(k) in _TERMS


def title(k):
    _load_metrics()
    return _TERMS[key(k)][0]


def text(k):
    _load_metrics()
    return _TERMS[key(k)][1]


def all_terms():
    _load_metrics()
    return dict(_TERMS)
