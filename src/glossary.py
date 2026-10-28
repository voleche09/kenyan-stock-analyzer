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
    "lookback": ("Performance over time", "How much your portfolio made or lost over the last day, week, month and "
                 "year, from the snapshots this dashboard saves each day you run it. Money you added (a purchase) or "
                 "took out (a sale) isn't counted as a gain or a loss — a sale's profit or loss is."),
    "est-dividend": ("Estimated yearly dividend", "Your number of shares multiplied by the annual dividend "
                     "per share (not a single interim or final installment). Companies can change or skip dividends, so it is an estimate."),
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
    "dividend-dates": ("Book closure & payment", "Book closure/record date identifies registered holders. The ex-dividend date is a separate "
                       "trading cutoff; settlement rules affect eligibility. Payment date is when cash is scheduled."),
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
    # ---- mutual funds ----
    "mutual-fund": ("Mutual fund", "A pot of money from many investors that professional managers invest for "
                    "them — in shares, bonds or other funds. You own units of the pot, so one purchase spreads your "
                    "money over hundreds of investments."),
    "nav": ("NAV (price per unit)", "Net asset value: everything the fund owns, minus its costs, divided by the "
            "number of units. The fund publishes it once each working day — it's the price you buy and sell at "
            "(before any sales charge)."),
    "units": ("Units", "Your share of a fund, like shares in a company. Your money's value = units × today's price "
              "per unit. Each top-up buys more units; a reinvested payout does too."),
    "avg-unit-cost": ("Average unit cost", "Everything you paid in ÷ the units you hold — including the sales "
                      "charge, because that money left your account. Reinvested payouts add units for free, which "
                      "brings this figure down."),
    "fund-payout": ("Payout (distribution)", "Cash an income fund pays out to its investors, usually monthly. On the "
                    "'ex-date' the price per unit drops by the payout, because that money leaves the fund — you "
                    "haven't lost it, it's paid to you or bought you more units."),
    "reinvest": ("Reinvested payouts", "Instead of paying the payout into your account, the bank uses it to buy "
                 "more units of the same fund, at that day's price. Your units grow; no new money goes in."),
    "accumulating": ("Accumulating fund", "A fund that never pays out: its income stays inside and makes the price "
                     "per unit grow instead. Its price change IS its total return."),
    "sales-charge": ("Sales charge", "A fee taken from each purchase before units are bought — e.g. 2% of a $50 "
                     "top-up is $1, so $49 buys units. It's charged once, when you buy."),
    "ongoing-charge": ("Ongoing charges (OCF)", "What the fund costs to run each year, as a % of your money — taken "
                       "out of the fund's price every day, so the returns shown are already after it. Lower is "
                       "better: 1% a year is $100 on every $10,000."),
    "annualised-return": ("Yearly return (annualised)", "The average yearly growth over a longer period: +10% a year "
                          "over 3 years means $10,000 grew like 10% every year (to about $13,310), even if the "
                          "actual years went up and down."),
    "fund-total-return": ("Total return", "The price change plus every payout, as if each payout had bought more "
                          "units on the day it was paid — the way fund managers report performance, so funds that "
                          "pay out and funds that don't compare fairly."),
    "calendar-return": ("Calendar-year return", "How much the fund made or lost from 1 January to 31 December of each "
                        "year (total return). Shows how bumpy the ride has been."),
    "volatility": ("Volatility", "How much the price swings, as a yearly %. Around 10% means a normal year's value "
                   "moves within about ±10% of its trend; higher means a bumpier ride."),
    "max-drawdown": ("Biggest fall (max drawdown)", "The largest drop from a high point to the low that followed, "
                     "in the period shown. It answers: 'how bad did it get for someone who bought at the top?'"),
    "risk-class": ("Risk class (1–7)", "Europe's standard risk scale for funds, 1 = lowest, 7 = highest. 'Official' is "
                   "the class in the fund's key information document (SRI); 'From its price' is worked out here from "
                   "how much weekly returns swung over up to 5 years, in the regulators' fixed bands (SRRI), so every "
                   "fund is measured the same way."),
    "peer-funds": ("Competitors (peer funds)", "Other funds in the same category that a bank could have offered you "
                   "instead. Compared on their accumulating share classes, so payouts are included for everyone."),
    "fund-category": ("Category", "The group a fund-rating company (Morningstar) puts the fund in by what it holds — "
                      "e.g. 'Asia Allocation' = a mix of Asian shares and bonds. Funds are fairest compared with "
                      "their own category."),
    "multi-asset": ("Multi-asset fund", "A fund that mixes shares, bonds and sometimes other things (gold, cash, "
                    "futures) and changes the mix as markets change — one fund instead of choosing each part "
                    "yourself."),
    "asset-mix": ("Asset mix", "What the fund holds: shares (growth, bumpier), bonds (income, steadier), cash, gold. "
                  "Funds that use futures can show more than 100% in one part and a negative cash figure — that's "
                  "borrowed exposure, not an error."),
    "top-up": ("Monthly top-up", "A standing instruction: the bank invests a fixed amount on the same day each "
               "month. The dashboard records it automatically, once per month, and works out the units from that "
               "day's price after the sales charge."),
    "estimated-units": ("Estimated units", "Units the dashboard worked out itself (amount after the sales charge ÷ "
                        "the price for the dealing day). Your bank's statement is the final word — copy its units "
                        "into the transaction and mark it confirmed."),
    "your-annual-return": ("Your yearly return", "Your own money's growth rate, counting WHEN each top-up went in and "
                           "each payout came out (a money-weighted return, like a savings rate). Shown once you've "
                           "tracked a fund for 3 months."),
    "fund-size": ("Fund size", "How much money the whole fund manages. Very small funds can close; very large ones "
                  "are well established."),
    "distribution-yield": ("Payout yield", "The last 12 months of payouts per unit ÷ today's price. It's income, but "
                           "income funds may pay part of it out of the fund's own capital — compare it with the "
                           "total return."),
    "price-cross-check": ("Price check", "The fund's prices come from two independent places (the fund manager and "
                          "the Financial Times) and are compared day by day; any difference is shown, never hidden. "
                          "Your statement's price is checked too."),
    "inflation": ("Inflation", "How much more the same everyday things cost than a year earlier (Kenya's consumer "
                  "price index, measured monthly by KNBS). Money that grows slower than this buys less each year."),
    "inflation-target": ("Inflation target", "The Central Bank of Kenya aims for 5% inflation; 2.5% to 7.5% is its "
                         "acceptable band. Outside it, the Central Bank Rate usually moves to pull inflation back."),
    "real-rate": ("Real interest rate", "An interest rate minus inflation: what the money really earns in buying "
                  "power. Positive = savers win; negative = prices rise faster than the interest."),
    "core-inflation": ("Core and non-core inflation", "Non-core = food and energy, which swing with harvests and fuel "
                       "prices. Core = everything else — the steadier, underlying trend."),
    "real-gain": ("Gain after inflation", "What your investments are worth now (plus income they paid you) minus "
                  "what you put in grown by inflation since — i.e. how much more (or less) you can buy than with "
                  "the money you put in."),
    "real-return": ("Real yearly return", "Your yearly growth rate after inflation — counting when each amount "
                    "went in and came out. Above 0% means your money is truly growing."),
    "profit-periods": ("Profit by day, week and month", "How much your units made or lost in US dollars: units held × "
                       "the change in price per unit, plus payouts. Top-ups are not profit, so they're left out, "
                       "and the sales charge counts as a small loss on the day it's taken."),
    "net-deposits": ("Money you deposited", "Everything you've sent to your broker, minus anything you took out. "
                     "It's the real amount of your own money in the account."),
    "account-value": ("Account value", "The shares you hold at today's prices plus the cash waiting in the account — "
                      "what the whole account is worth if you sold everything today."),
    "account-growth": ("Growth on your deposits", "Account value minus the money you deposited. Unlike the gain on "
                       "your holdings, it counts everything: profits from shares you sold, dividends, fees and today's "
                       "prices."),
    "realized-gain": ("Profit or loss on a sale", "What a sale brought in (after fees) minus what those shares, "
                      "units or bonds cost you at your average cost. Unlike a gain on paper, it's locked in — the "
                      "money is yours. What you still hold keeps the same average cost."),
    "average-cost": ("Average cost", "What you paid per share (or unit) on average across all your purchases. A sale "
                     "is measured against it, and doesn't change it for what's left."),
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
