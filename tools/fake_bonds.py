"""
Made-up Treasury bonds for the tests and the sandbox, so nothing in this
public repo uses anyone's real bonds as examples. They are invented issues
with invented terms, chosen to cover each kind of bond the dashboard
handles:

  IFB9/2021/7    an infrastructure bond repaid in three instalments
  IFB9/2022/11   an infrastructure bond under the small-holder rule
  IFB9/2020/15   another one under the small-holder rule, two instalments
  FXD9/2020/020  a taxed fixed-coupon bond repaid in full at maturity

  FAKE_BOND_REFERENCE   entries shaped like bonds_portfolio.BOND_REFERENCE
  FAKE_ALIASES          a typo alias, like bonds_portfolio._ALIASES
  use()                 context manager: the made-up bonds are known while it runs
  write_reference(path) the terms as JSON, for BOND_REFERENCE_EXTRA (sandbox runs)
"""

import contextlib
import datetime as dt
import json
from unittest import mock


def _every_six_months(first, last):
    """ISO dates from `first` to `last`, six months apart (same day of month)."""
    d0, d1 = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
    out, k = [], 1
    while True:
        m = d0.month - 1 + 6 * k
        d = d0.replace(year=d0.year + m // 12, month=m % 12 + 1)
        if d > d1:
            return out
        out.append(d.isoformat())
        k += 1


def _ifb(tenor, coupon, value_date, maturity, tranches, small_holder):
    return {
        "type": "IFB", "type_label": "Infrastructure Bond", "tenor_label": tenor,
        "coupon_pct": coupon, "tax_free": True, "withholding_pct": 0.0,
        "value_date": value_date, "maturity_date": maturity,
        "coupon_dates": _every_six_months(value_date, maturity),
        "redemption_structure": [{"date": d, "pct_of_original_face": p} for d, p in tranches],
        "small_holder_rule_confirmed": small_holder,
        "source": "Made-up bond for tests and the sandbox — not a real CBK issue", "source_url": "",
    }


FAKE_BOND_REFERENCE = {
    "IFB9/2021/7": _ifb("7-year", 12.8, "2021-04-12", "2028-04-12",
                        [("2026-04-12", 40.0), ("2027-04-12", 30.0), ("2028-04-12", 30.0)], False),
    "IFB9/2022/11": _ifb("11-year", 13.35, "2022-06-20", "2033-06-20",
                         [("2028-06-20", 25.0), ("2031-06-20", 25.0), ("2033-06-20", 50.0)], True),
    "IFB9/2020/15": _ifb("15-year", 12.15, "2020-09-07", "2035-09-07",
                         [("2029-09-07", 50.0), ("2035-09-07", 50.0)], True),
    "FXD9/2020/020": {
        "type": "FXD", "type_label": "Fixed-coupon Treasury Bond", "tenor_label": "20-year",
        "coupon_pct": 12.9, "tax_free": False, "withholding_pct": 10.0,
        "value_date": "2020-08-24", "maturity_date": "2040-08-24",
        "coupon_dates": None,                     # generated, like a real bullet FXD
        "redemption_structure": [{"date": "2040-08-24", "pct_of_original_face": 100.0}],
        "small_holder_rule_confirmed": False,
        "source": "Made-up bond for tests and the sandbox — not a real CBK issue", "source_url": "",
    },
}
FAKE_ALIASES = {"FDX9/2020/020": "FXD9/2020/020"}


def use():
    """While this runs, the made-up bonds (and their typo alias) are known to
    bonds_portfolio — alongside the real reference table, untouched."""
    import bonds_portfolio
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.dict(bonds_portfolio.BOND_REFERENCE, FAKE_BOND_REFERENCE))
    stack.enter_context(mock.patch.dict(bonds_portfolio._ALIASES, FAKE_ALIASES))
    return stack


def write_reference(path):
    """The made-up terms as JSON — point BOND_REFERENCE_EXTRA at it for a sandbox run."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(FAKE_BOND_REFERENCE, f, indent=2)
    return path
