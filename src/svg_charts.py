"""
Charts as inline SVG + HTML, generated in Python — they replace the
matplotlib PNGs.

How they are built, and why:
  - The data is drawn in an SVG stretched to its box
    (preserveAspectRatio="none", strokes that don't scale), so it stays
    crisp at any width. Text — axis labels, legends, values — is ordinary
    HTML placed by percentage, so 12 px text stays 12 px on a phone.
  - One <path> per series, with a gap wherever a value is missing (moving
    averages start late); bars are two paths (up/down); one invisible layer
    handles hover. No per-point elements.
  - The hover values travel once per chart as numbers plus a format rule
    (prefix, decimals, sign, suffix) that the page script applies exactly as
    Python would — so they read like the tables. The 1M / 3M / 6M views are
    separately drawn (each with its own scale) but share those values, and
    a chart can borrow another chart's dates.
  - Colours come from CSS classes and custom properties, so dark mode,
    print and the up/down marks all follow the theme.

Every function returns markupsafe.Markup; every label is escaped. Missing,
NaN or infinite values never reach the SVG — they become gaps.
"""

import datetime as dt
import json
import math
import re

from markupsafe import Markup, escape

VB_W = 1000.0          # viewBox width of every stretched plot
VB_H = 100.0           # …and height: y is "percent from the top"


# ------------------------------------------------------------------ numbers
def ok(v):
    """A plottable number (not None/NaN/inf)."""
    try:
        return v is not None and not isinstance(v, bool) and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def clean(values):
    """Floats, with None wherever a value is missing or not a number."""
    return [float(v) if ok(v) else None for v in (list(values) if values is not None else [])]


def _short_num(v):
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            s = f"{a / div:.1f}".rstrip("0").rstrip(".")
            return ("-" if v < 0 else "") + s + suf
    return f"{v:,.0f}"


class Fmt:
    """How a series' values read: Fmt("KES ", 2) -> "KES 1,234.50";
    Fmt(suffix="%", decimals=1, sign=True) -> "+2.5%"; short=True -> "1.2M".
    The page script applies the same rule (see ui_runtime.js fmtNum)."""

    def __init__(self, prefix="", decimals=2, suffix="", sign=False, short=False):
        self.prefix, self.decimals, self.suffix, self.sign, self.short = prefix, decimals, suffix, sign, short

    def __call__(self, v):
        if not ok(v):
            return "—"
        v = float(v)
        body = _short_num(abs(v)) if self.short else f"{abs(v):,.{self.decimals}f}"
        sign = "−" if v < 0 and round(abs(v), self.decimals) != 0 else ("+" if self.sign and v > 0 else "")
        return f"{self.prefix}{sign}{body}{self.suffix}"

    def spec(self):
        out = {"d": self.decimals}
        if self.prefix:
            out["p"] = self.prefix
        if self.suffix:
            out["s"] = self.suffix
        if self.sign:
            out["sign"] = 1
        if self.short:
            out["short"] = 1
        return out

    def round(self, v):
        """The value as stored for the page script (enough digits, no more)."""
        return None if v is None else round(v, max(self.decimals, 0) + (3 if self.short else 0))


def nice_ticks(lo, hi, target=5):
    """Round axis ticks covering lo..hi, stepping 1/2/2.5/5 × 10^k.
    Returns (ticks, decimals-to-show)."""
    if not (ok(lo) and ok(hi)):
        return [], 0
    lo, hi = float(min(lo, hi)), float(max(lo, hi))
    if hi == lo:                                   # a flat line: give it some room
        pad = abs(lo) * 0.02 or 1.0
        lo, hi = lo - pad, hi + pad
    raw = (hi - lo) / max(target - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw * 0.999:
            break
    start = math.floor(lo / step + 1e-9) * step
    ticks, t = [], start
    while t < hi - 1e-9 * step:
        ticks.append(t)
        t += step
    ticks.append(t)
    ticks = [round(x, 10) for x in ticks]
    decimals = max(0, -int(math.floor(math.log10(step) + 1e-9)) + (1 if m == 2.5 else 0))
    return ticks, decimals


def _y(v, lo, hi):
    return (hi - v) / (hi - lo) * VB_H if hi != lo else VB_H / 2


def _x(i, n):
    return VB_W / 2 if n <= 1 else i / (n - 1) * VB_W


def _path(xs, ys):
    """A polyline that lifts the pen over missing values."""
    out, pen = [], False
    for x, y in zip(xs, ys):
        if y is None:
            pen = False
            continue
        out.append(f"{'L' if pen else 'M'}{x:.1f},{y:.1f}")
        pen = True
    return "".join(out)


def _band(xs, top, bottom):
    """Filled area between two lines, one closed shape per unbroken run."""
    shapes, run = [], []
    for x, a, b in list(zip(xs, top, bottom)) + [(None, None, None)]:
        if a is None or b is None:
            if len(run) > 1:
                fwd = "L".join(f"{x:.1f},{a:.1f}" for x, a, _b in run)
                back = "L".join(f"{x:.1f},{b:.1f}" for x, _a, b in reversed(run))
                shapes.append(f"M{fwd}L{back}Z")
            run = []
        else:
            run.append((x, a, b))
    return "".join(shapes)


def _json(data):
    """JSON safe to embed in a <script> block."""
    return Markup(json.dumps(data, allow_nan=False, separators=(",", ":"))
                  .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _slug(text):
    return "".join(c if c.isalnum() else "-" for c in str(text)).strip("-").lower()


# http(s), an #anchor, or a page here (never //host, javascript:, data:)
_LINK = re.compile(r"^(https?://\S+|#[\w\-.]*|(?!/)[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)*(#[\w\-.]*)?)$")


def _safe_link(url):
    """http(s), an #anchor or a local page — anything else is dropped."""
    u = str(url or "").strip()
    return u if u and _LINK.match(u) and not u.lower().startswith("javascript") else ""


# ------------------------------------------------------------------ dates
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _as_date(d):
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    if hasattr(d, "to_pydatetime"):
        return d.to_pydatetime().date()
    try:
        return dt.date.fromisoformat(str(d)[:10])
    except ValueError:
        return None


def fmt_day(d):
    d = _as_date(d)
    return f"{d.strftime('%a')} {d.day} {_MON[d.month - 1]} {d.year}" if d else ""


def date_ticks(dates, weekly=False):
    """(index, label) ticks: the first trading day of each month — or of
    each week for short ranges — with the year shown at January."""
    ds = [_as_date(d) for d in dates]
    ticks, prev = [], None
    for i, d in enumerate(ds):
        if d is None:
            continue
        key = d.isocalendar()[:2] if weekly else (d.year, d.month)
        if prev is not None and key != prev:
            if weekly:
                label = f"{d.day} {_MON[d.month - 1]}"
            else:
                label = _MON[d.month - 1] + (f" ’{str(d.year)[2:]}" if d.month == 1 else "")
            ticks.append((i, label))
        prev = key
    if len(ticks) > 8:                         # keep it readable on a phone
        ticks = ticks[::math.ceil(len(ticks) / 8)]
    return ticks


def x_ticks(x, weekly=False):
    """Dates get calendar ticks; anything else (e.g. yields) every k-th label."""
    if x and all(_as_date(v) for v in x):
        return date_ticks(x, weekly=weekly)
    n = len(x)
    step = max(1, math.ceil(n / 7))
    return [(i, str(v)) for i, v in enumerate(x) if i % step == 0 or i == n - 1]


def _x_payload(x):
    """Dates travel as ISO days (formatted by the page script); anything
    else as its label."""
    ds = [_as_date(v) for v in x]
    if ds and all(ds):
        return {"x": [d.isoformat() for d in ds], "xf": "day"}
    return {"x": [str(v) for v in x], "xf": "label"}


# ------------------------------------------------------------------ pieces
def _axis_y(ticks, decimals, lo, hi, fmt=None):
    spans = "".join(
        f'<span style="top:{_y(t, lo, hi):.2f}%">{escape(fmt(t) if fmt else f"{t:,.{decimals}f}")}</span>'
        for t in ticks)
    return f'<div class="chart-y" aria-hidden="true">{spans}</div>'


def _axis_x(ticks, n, bars=False):
    pos = (lambda i: (i + 0.5) / n * 100) if bars else (lambda i: _x(i, n) / VB_W * 100)
    spans = "".join(f'<span style="left:{pos(i):.2f}%">{escape(lab)}</span>' for i, lab in ticks)
    return f'<div class="chart-x" aria-hidden="true">{spans}</div>'


def _grid(ticks, lo, hi):
    d = "".join(f"M0,{_y(t, lo, hi):.1f}H{VB_W:.0f}" for t in ticks)
    return f'<path class="grid" d="{d}"/>' if d else ""


def _svg(inner, title, desc=""):
    return (f'<svg class="chart-svg" viewBox="0 0 {VB_W:.0f} {VB_H:.0f}" preserveAspectRatio="none" '
            f'role="img" aria-label="{escape(title)}"><title>{escape(title)}</title>'
            + (f"<desc>{escape(desc)}</desc>" if desc else "") + f"{inner}</svg>")


def _summary(values, fmt):
    v = [x for x in values if x is not None]
    if not v:
        return "no data"
    first, last = v[0], v[-1]
    chg = (last - first) / first * 100 if first else None
    s = f"from {fmt(first)} to {fmt(last)}; low {fmt(min(v))}, high {fmt(max(v))}"
    return s + (f"; {chg:+.1f}% over the period" if chg is not None else "")


_XHAIR = '<div class="xhair" hidden><i class="xhair-line"></i><div class="xhair-tip" role="status"></div></div>'


# ------------------------------------------------------------------ series
class Series:
    """One line, band or set of columns on a chart.

    key     short id, used for CSS (s-<key>) and the legend toggles
    label   legend / tooltip text
    values  numbers aligned with the chart's x values (None = gap)
    kind    'line' | 'band' (values = top, lower = bottom) |
            'bars' (columns up/down from zero, e.g. the MACD histogram)
    fmt     a Fmt — how the value reads in the tooltip
    toggle  give it an on/off chip in the legend
    """

    def __init__(self, key, label, values, *, kind="line", lower=None, fmt=None, toggle=False,
                 tooltip=True):
        self.key, self.label, self.kind = _slug(key), label, kind
        self.values = clean(values)
        self.lower = clean(lower) if lower is not None else None
        self.fmt = fmt if isinstance(fmt, Fmt) else Fmt()
        self.toggle, self.tooltip = toggle, tooltip

    def payload(self):
        out = {"k": self.key, "l": self.label, "f": self.fmt.spec(),
               "v": [self.fmt.round(v) for v in self.values]}
        if self.kind == "band":
            out["lo"] = [self.fmt.round(v) for v in self.lower]
        if self.kind != "line":
            out["nodot"] = 1
        return out


def _scale(series, ref_lines, include_zero, y_range):
    """(ticks, decimals, lo, hi, the reference lines that fit)."""
    vals = []
    for s in series:
        vals += [v for v in s.values if v is not None]
        if s.lower:
            vals += [v for v in s.lower if v is not None]
    if not vals:
        return None
    # Reference lines (your buy / sell prices…) widen the scale only when
    # they're near the data; a far-away one would squash the line flat, so
    # it's left off and listed in words instead (see refs_off_chart).
    span = (max(vals) - min(vals)) or abs(max(vals)) * 0.02 or 1.0
    near = [r for r in ref_lines if ok(r[0]) and min(vals) - span * 0.5 <= r[0] <= max(vals) + span * 0.5]
    vals += [r[0] for r in near]
    if include_zero or any(s.kind == "bars" for s in series):
        vals.append(0.0)
    if y_range:
        lo, hi = y_range
        ticks, dec = nice_ticks(lo, hi)
        ticks = [t for t in ticks if lo - 1e-9 <= t <= hi + 1e-9]
        near = [r for r in ref_lines if ok(r[0]) and lo <= r[0] <= hi]
    else:
        ticks, dec = nice_ticks(min(vals), max(vals))
        lo, hi = ticks[0], ticks[-1]
    return ticks, dec, lo, hi, near


def refs_off_chart(values, ref_lines):
    """Labels of reference lines a chart of `values` leaves out because they
    are too far from the data — list them in words next to the chart."""
    vals = [v for v in clean(values) if v is not None]
    if not vals:
        return []
    span = (max(vals) - min(vals)) or abs(max(vals)) * 0.02 or 1.0
    return [r[2] for r in ref_lines
            if ok(r[0]) and not (min(vals) - span * 0.5 <= r[0] <= max(vals) + span * 0.5)]


def _time_positions(dates):
    """x positions (0..VB_W) proportional to time, for irregular snapshots."""
    ds = [_as_date(d) for d in dates]
    if len(ds) < 2 or not all(ds):
        return None
    span = (ds[-1] - ds[0]).days or 1
    return [(d - ds[0]).days / span * VB_W for d in ds]


def _plot(series, start, count, *, height, title, ref_lines, include_zero, y_range, y_fmt,
          dates, weekly, tf=None, default=True, bars_mode=False, time_axis=False):
    """One drawn view: points start .. start+count-1 of the data."""
    sl = slice(start, start + count)
    sub = [Series(s.key, s.label, s.values[sl], kind=s.kind, lower=s.lower[sl] if s.lower else None,
                  fmt=s.fmt) for s in series]
    sc = _scale(sub, ref_lines, include_zero, y_range)
    head = ('<div class="chart-variant"' + (f' data-tf="{escape(tf)}"' if tf else "")
            + ("" if default else " hidden") + (" data-default" if default else "") + ">")
    if sc is None:
        return head + '<div class="chart-empty">No data for this period</div></div>'
    ticks, dec, lo, hi, refs = sc
    n = count
    tpos = _time_positions(dates[sl]) if time_axis else None
    xs = tpos or [((i + 0.5) / n * VB_W) if bars_mode else _x(i, n) for i in range(n)]
    parts = [_grid(ticks, lo, hi)]
    if (include_zero or any(s.kind == "bars" for s in sub)) and lo < 0 < hi:
        parts.append(f'<path class="zero" d="M0,{_y(0, lo, hi):.1f}H{VB_W:.0f}"/>')
    for v, cls, _label in refs:
        parts.append(f'<path class="ref {escape(cls)}" d="M0,{_y(v, lo, hi):.1f}H{VB_W:.0f}"/>')

    def ys(vals):
        return [_y(min(max(v, lo), hi), lo, hi) if v is not None else None for v in vals]
    for s in sub:
        if s.kind == "band":
            parts.append(f'<path class="band s-{s.key}" d="{_band(xs, ys(s.values), ys(s.lower))}"/>')
        elif s.kind == "bars":
            zero = _y(0.0, lo, hi) if lo <= 0 <= hi else (VB_H if lo > 0 else 0.0)
            w = VB_W / max(n, 1) * (0.7 if bars_mode else 0.6)
            up, down = [], []
            for xv, v, yv in zip(xs, s.values, ys(s.values)):
                if v is None:
                    continue
                top, bottom = min(yv, zero), max(yv, zero)
                (up if v >= 0 else down).append(f"M{xv - w / 2:.1f},{top:.1f}h{w:.2f}V{bottom:.1f}h{-w:.2f}Z")
            parts.append(f'<path class="bar up s-{s.key}" d="{"".join(up)}"/>'
                         f'<path class="bar down s-{s.key}" d="{"".join(down)}"/>')
        else:
            parts.append(f'<path class="ln s-{s.key}" d="{_path(xs, ys(s.values))}"/>')
    refs_html = "".join(
        f'<span class="ref-label {escape(cls)}" style="top:{_y(v, lo, hi):.2f}%">{escape(label)}</span>'
        for v, cls, label in refs if label)
    # Lines too far from this view's data to draw without squashing it are
    # named under it instead — each view says exactly what it leaves out.
    left_out = [r[2] for r in ref_lines if ok(r[0]) and r[2] and r not in refs]
    note = (f'<p class="chart-note">Not drawn — too far from the price to fit: {escape(", ".join(left_out))}.</p>'
            if left_out else "")
    main = sub[0]
    desc = f"{main.label} {_summary(main.values, main.fmt)}"
    attrs = (f'data-o="{start}" data-n="{n}" data-lo="{lo:g}" data-hi="{hi:g}"'
             + (' data-bars="1"' if bars_mode else "")
             + (f' data-px="{",".join(f"{p / VB_W * 100:.2f}" for p in tpos)}"' if tpos else ""))
    ticks_x = x_ticks(dates[sl], weekly=weekly)
    if tpos:
        axis = "".join(f'<span style="left:{tpos[i] / VB_W * 100:.2f}%">{escape(lab)}</span>' for i, lab in ticks_x)
        axis = f'<div class="chart-x" aria-hidden="true">{axis}</div>'
    else:
        axis = _axis_x(ticks_x, n, bars=bars_mode)
    return (head + f'<div class="chart-body" style="--h:{height}px">'
            + _axis_y(ticks, dec, lo, hi, y_fmt)
            + f'<div class="chart-plot" {attrs}>' + _svg("".join(parts), title, desc) + refs_html
            + axis + _XHAIR + '</div></div>' + note + '</div>')


def chart(chart_id, x, series, *, title, height=240, ref_lines=None, include_zero=False,
          timeframes=None, y_fmt=None, note=None, legend=True, y_range=None, head_extra="",
          small=False, x_ref=None, bars_mode=False, legend_extra="", time_axis=False, show_title=True):
    """A chart with crosshair tooltips.

    x           dates (or labels), oldest first
    series      [Series], the first one is the main line
    ref_lines   [(value, css class, label)] horizontal reference lines
    timeframes  [("1M", 21), ("3M", 63), ("8M", None)]: separately scaled
                views of the last N points (None = all). The last is the
                default — the only one shown without JavaScript, and the one
                that prints.
    y_range     fix the scale, e.g. (0, 100) for RSI
    head_extra  Markup added after the title (e.g. an ⓘ button)
    x_ref       id of another chart whose dates this one shares
    """
    ref_lines = ref_lines or []
    cid = _slug(chart_id)
    x = list(x)
    n = len(x)
    legend_html = ""
    if legend:
        chips = []
        for s in series:
            if s.toggle:
                chips.append(f'<button type="button" class="lg lg-toggle s-{s.key}" data-series="{s.key}" '
                             f'aria-pressed="true">{escape(s.label)}</button>')
            else:
                chips.append(f'<span class="lg s-{s.key}">{escape(s.label)}</span>')
        legend_html = f'<div class="chart-legend">{"".join(chips)}{legend_extra}</div>'
    common = dict(height=height, ref_lines=ref_lines, include_zero=include_zero, y_range=y_range,
                  y_fmt=y_fmt, dates=x, bars_mode=bars_mode, time_axis=time_axis)
    tf_html, views = "", []
    if timeframes and n:
        last = timeframes[-1][0]
        btns = "".join(f'<button type="button" data-tf="{escape(k)}" '
                       f'aria-pressed="{"true" if k == last else "false"}">{escape(k)}</button>'
                       for k, _c in timeframes)
        tf_html = f'<div class="chart-tf" role="group" aria-label="Time range">{btns}</div>'
        for key, count in timeframes:
            c = min(count or n, n)
            views.append(_plot(series, n - c, c, title=f"{title} — {key}", weekly=c <= 30, tf=key,
                               default=key == last, **common))
    else:
        views.append(_plot(series, 0, n, title=title, weekly=n <= 30, **common))
    data = {"s": [s.payload() for s in series if s.tooltip]}
    if x_ref:
        data["xref"] = _slug(x_ref)
    else:
        data.update(_x_payload(x))
    note_html = f'<p class="chart-note">{escape(note)}</p>' if note else ""
    return Markup(
        f'<figure class="chart{" chart-sm" if small else ""}" id="{cid}" data-chart>'
        f'<figcaption class="chart-head">'
        + (f'<span class="chart-title">{escape(title)}{head_extra}</span>' if show_title else "")
        + f'{legend_html}{tf_html}</figcaption>' + "".join(views) + note_html
        + f'<script type="application/json" class="chart-data">{_json(data)}</script></figure>')


def timeframes_for(dates):
    """1M / 3M / 6M views shorter than the data, plus the whole history
    (named by its length) as the default — the old charts always showed
    everything, so the default still does."""
    n = len(dates)
    ds = [d for d in (_as_date(v) for v in dates) if d]
    months = max(1, round((ds[-1] - ds[0]).days / 30.44)) if len(ds) > 1 else 1
    full = f"{months // 12}Y" if months >= 12 and months % 12 == 0 else f"{months}M"
    out = [(f"{m}M", c) for m, c in ((1, 21), (3, 63), (6, 126)) if c < n - 5 and f"{m}M" != full]
    return out + [(full, None)]


# ------------------------------------------------------------------ sparkline
def sparkline(values, *, label, fmt=None, refs=None, tone=None, height=34, area=True):
    """A tiny trend line for tables and cards. `refs` are dashed horizontal
    reference lines [(value, css class)] such as your buy / sell prices;
    ones far outside the line's range are left out instead of squashing it."""
    fmt = fmt or Fmt()
    ys = clean(values)
    vals = [v for v in ys if v is not None]
    if len(vals) < 2:
        return Markup('<span class="spark spark-empty" aria-label="No price history">—</span>')
    lo, hi = min(vals), max(vals)
    span = hi - lo or abs(hi) * 0.02 or 1.0
    shown = [(v, c) for v, c in (refs or []) if ok(v) and lo - span * 0.5 <= v <= hi + span * 0.5]
    for v, _c in shown:
        lo, hi = min(lo, v), max(hi, v)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.02 or 1.0
    lo, hi = lo - pad, hi + pad
    n = len(ys)
    xs = [_x(i, n) for i in range(n)]
    pts = [_y(v, lo, hi) if v is not None else None for v in ys]
    trend = tone or ("up" if vals[-1] > vals[0] else "down" if vals[-1] < vals[0] else "flat")
    lines = "".join(f'<path class="ref {escape(c)}" d="M0,{_y(v, lo, hi):.1f}H{VB_W:.0f}"/>' for v, c in shown)
    idx = [i for i, p in enumerate(pts) if p is not None]
    first, last = idx[0], idx[-1]
    line = _path(xs, pts)
    area = ""
    if area and all(pts[i] is not None for i in range(first, last + 1)):   # no gaps: shade under the line
        area = f'<path class="spark-area" d="{line}L{xs[last]:.1f},{VB_H:.0f}L{xs[first]:.1f},{VB_H:.0f}Z"/>'
    desc = f"{label}: {_summary(ys, fmt)}"
    return Markup(
        f'<span class="spark" data-tone="{trend}" style="--h:{height}px" role="img" aria-label="{escape(desc)}">'
        f'<svg viewBox="0 0 {VB_W:.0f} {VB_H:.0f}" preserveAspectRatio="none" aria-hidden="true">'
        f'{area}{lines}<path class="spark-line" d="{line}"/></svg>'
        f'<i class="spark-dot" style="left:{xs[last] / VB_W * 100:.1f}%;top:{pts[last]:.1f}%"></i></span>')


# ------------------------------------------------------------------ bars (horizontal, labelled)
def hbars(rows, *, title=None, fmt=None, diverging=False, max_value=None, tone_from_sign=True,
          empty="No data"):
    """Horizontal bars in HTML (crisp, accessible, wrap on phones).

    rows: [{"label", "value", "sub"?, "href"?, "tone"?, "tip"?}]
    diverging: bars grow left (negative) or right (positive) from a centre line.
    """
    fmt = fmt or Fmt()
    rows = [r for r in rows if ok(r.get("value"))]
    if not rows:
        return Markup(f'<div class="chart-empty">{escape(empty)}</div>')
    peak = max_value or max(abs(float(r["value"])) for r in rows) or 1.0
    out = []
    for r in rows:
        v = float(r["value"])
        tone = r.get("tone") or (("up" if v > 0 else "down" if v < 0 else "flat") if tone_from_sign else "accent")
        pct = min(abs(v) / peak * (50 if diverging else 100), 100)
        style = (f"left:{50 if v >= 0 else 50 - pct:.2f}%;width:{pct:.2f}%" if diverging else f"width:{pct:.2f}%")
        label = escape(r["label"])
        link = _safe_link(r.get("href"))
        if link:
            label = Markup(f'<a href="{escape(link)}">{label}</a>')
        sub = f'<small>{escape(r["sub"])}</small>' if r.get("sub") else ""
        tip = f' data-tip="{escape(r["tip"])}"' if r.get("tip") else ""
        out.append(f'<div class="hbar{" hbar-div" if diverging else ""}" data-tone="{tone}"{tip}>'
                   f'<span class="hbar-label">{label}{sub}</span>'
                   f'<span class="hbar-track"><i style="{style}"></i></span>'
                   f'<span class="hbar-value num">{escape(fmt(v))}</span></div>')
    head = f'<div class="chart-title">{escape(title)}</div>' if title else ""
    return Markup(f'<div class="hbars">{head}{"".join(out)}</div>')


# ------------------------------------------------------------------ donut & ring
def donut(chart_id, segments, *, title, center_value="", center_label="", fmt=None, legend=True, size=168,
          legend_values=True):
    """A donut drawn with dashed circles (so a single 100% slice works).
    segments: [(label, value, css colour class)]; values must be ≥ 0."""
    fmt = fmt or Fmt(decimals=0)
    segs = [(lab, float(v), cls) for lab, v, cls in segments if ok(v) and float(v) > 0]
    total = sum(v for _l, v, _c in segs)
    if not total:
        return Markup('<div class="chart-empty">Nothing to show yet</div>')
    r = 15.9155                      # circumference 100 → dash lengths are percentages
    rings, at, legend_items = [], 0.0, []
    for lab, v, cls in segs:
        share = v / total * 100
        rings.append(f'<circle class="seg {escape(cls)}" r="{r}" cx="21" cy="21" '
                     f'stroke-dasharray="{share:.3f} {100 - share:.3f}" stroke-dashoffset="{25 - at:.3f}">'
                     f'<title>{escape(lab)}: {escape(fmt(v))} ({share:.1f}%)</title></circle>')
        legend_items.append(f'<li><i class="sw {escape(cls)}"></i><span>{escape(lab)}</span>'
                            f'<b class="num">{share:.0f}%</b>'
                            + (f'<small class="num private">{escape(fmt(v))}</small>' if legend_values else "")
                            + "</li>")
        at += share
    center = (f'<div class="donut-center"><b class="num private">{escape(center_value)}</b>'
              f'<small>{escape(center_label)}</small></div>') if center_value or center_label else ""
    summary = "; ".join(f"{lab} {v / total * 100:.0f}%" for lab, v, _c in segs)
    return Markup(
        f'<figure class="donut" id="{_slug(chart_id)}"><div class="donut-ring" style="--size:{size}px">'
        f'<svg viewBox="0 0 42 42" role="img" aria-label="{escape(title)}: {escape(summary)}">'
        f'<title>{escape(title)}</title><circle class="seg-track" r="{r}" cx="21" cy="21"/>'
        f'{"".join(rings)}</svg>{center}</div>'
        + (f'<ul class="donut-legend">{"".join(legend_items)}</ul>' if legend else "") + '</figure>')


def ring(value, *, max_value=100, label="", tone=None, size=56):
    """A score ring (e.g. 72/100), coloured good / fair / weak."""
    if not ok(value):
        return Markup(f'<span class="ring ring-empty" style="--size:{size}px" aria-label="No score">—</span>')
    v = max(0.0, min(float(value), float(max_value)))
    share = v / max_value * 100
    tone = tone or ("good" if share >= 70 else "fair" if share >= 45 else "weak")
    return Markup(
        f'<span class="ring" data-tone="{tone}" style="--size:{size}px" role="img" '
        f'aria-label="{escape(label or "Score")}: {v:.0f} out of {max_value:g}">'
        f'<svg viewBox="0 0 36 36" aria-hidden="true"><circle class="ring-track" r="15.9155" cx="18" cy="18"/>'
        f'<circle class="ring-arc" r="15.9155" cx="18" cy="18" stroke-dasharray="{share:.2f} {100 - share:.2f}" '
        f'stroke-dashoffset="25"/></svg><b class="num">{v:.0f}</b></span>')


# ------------------------------------------------------------------ stock-page presets
def _col(df, name):
    """A column as a list aligned with df's rows, or all-None if missing."""
    return list(df[name]) if name in getattr(df, "columns", ()) else [None] * len(df)


def price_chart(chart_id, df, *, money, refs=None, title="Price", height=300, head_extra=""):
    """The main price chart for a stock: daily close, the 20- and 50-day
    averages and the Bollinger band, with 1M / 3M / 6M / full-history views.
    `money` is a Fmt (e.g. Fmt("KES ", 2)); `refs` [(value, css class,
    label)] such as your buy / sell prices or average cost."""
    dates = list(df.index)
    series = [
        Series("price", "Close", _col(df, "close"), fmt=money),
        Series("sma20", "20-day average", _col(df, "sma_20"), fmt=money, toggle=True),
        Series("sma50", "50-day average", _col(df, "sma_50"), fmt=money, toggle=True),
        Series("bb", "Bollinger band", _col(df, "bb_upper"), kind="band", lower=_col(df, "bb_lower"),
               fmt=money, toggle=True),
    ]
    big = max((abs(v) for v in clean(_col(df, "close")) if v is not None), default=0) >= 10000
    # A reference line too far from the price is named under the chart
    # instead of squashing the line (each 1M / 3M / … view lists its own).
    return chart(chart_id, dates, series, title=title, height=height, ref_lines=refs,
                 timeframes=timeframes_for(dates), head_extra=head_extra,
                 y_fmt=_short_num if big else None)        # axis ticks: 80, 100, 120 — not 80.00


def indicator_charts(prefix, df, *, money, x_ref=None, extras=None):
    """The small charts under the price chart, over the full period:
    volume, RSI, MACD, stochastic and ATR. They borrow the price chart's
    dates (x_ref). `extras` maps "volume"/"rsi"/"macd"/"stoch"/"atr" to
    Markup for the caption, e.g. an ⓘ button."""
    extras = extras or {}
    dates = list(df.index)
    one = Fmt(decimals=1)
    signed = Fmt(decimals=2, sign=True)
    common = dict(height=120, small=True, x_ref=x_ref)
    vols = clean(_col(df, "volume"))
    closes = clean(_col(df, "close"))
    # Volume columns are green on days the price closed up, red when down —
    # carried as the sign of the drawn value; the tooltip shows the size.
    signed_vol = [None if v is None else (v if i > 0 and closes[i] is not None and closes[i - 1] is not None
                                          and closes[i] >= closes[i - 1] else -v)
                  for i, v in enumerate(vols)]
    volume = Markup('<div class="chart-empty">No volume data</div>')
    if any(v is not None for v in vols):
        vs = Series("vol", "Volume", signed_vol, kind="bars", fmt=Fmt(decimals=0, short=True))
        vs.values = [abs(v) if v is not None else None for v in signed_vol]
        vs.updown = signed_vol
        volume = _volume(f"{prefix}-volume", dates, vs, _col(df, "volume_sma_20"),
                         head_extra=extras.get("volume", ""), x_ref=x_ref)
    return {
        "volume": volume,
        "rsi": chart(f"{prefix}-rsi", dates, [Series("rsi", "RSI (14)", _col(df, "rsi"), fmt=one)],
                     title="RSI — momentum", y_range=(0, 100),
                     ref_lines=[(70, "sell", "70 · overbought"), (30, "buy", "30 · oversold")],
                     head_extra=extras.get("rsi", ""), **common),
        "macd": chart(f"{prefix}-macd", dates,
                      [Series("macd", "MACD", _col(df, "macd"), fmt=signed),
                       Series("signal", "Signal", _col(df, "macd_signal"), fmt=signed),
                       Series("hist", "Histogram", _col(df, "macd_hist"), kind="bars", fmt=signed)],
                      title="MACD — trend momentum", include_zero=True,
                      head_extra=extras.get("macd", ""), **common),
        "stoch": chart(f"{prefix}-stoch", dates,
                       [Series("k", "%K", _col(df, "stoch_k"), fmt=one),
                        Series("d", "%D", _col(df, "stoch_d"), fmt=one)],
                       title="Stochastic — place in the recent range", y_range=(0, 100),
                       ref_lines=[(80, "sell", "80"), (20, "buy", "20")],
                       head_extra=extras.get("stoch", ""), **common),
        "atr": chart(f"{prefix}-atr", dates, [Series("atr", "ATR (14)", _col(df, "atr"), fmt=money)],
                     title="ATR — typical daily move", head_extra=extras.get("atr", ""), **common),
    }


def _volume(chart_id, dates, vs, avg, *, head_extra="", x_ref=None, height=120):
    """Volume columns coloured by the day's direction (two paths), with the
    20-day average as a line."""
    n = len(dates)
    vals = [v for v in vs.values if v is not None]
    ticks, _dec = nice_ticks(0, max(vals), target=3)
    lo, hi = 0.0, ticks[-1]
    w = VB_W / max(n, 1)
    up, down = [], []
    for i, (v, s) in enumerate(zip(vs.values, vs.updown)):
        if v is None:
            continue
        x0 = i * w + w * 0.15
        (up if s >= 0 else down).append(f"M{x0:.1f},{_y(v, lo, hi):.1f}h{w * 0.7:.2f}V{VB_H:.0f}h{-w * 0.7:.2f}Z")
    av = clean(avg)
    xs = [(i + 0.5) * w for i in range(n)]
    parts = [_grid(ticks, lo, hi), f'<path class="bar up" d="{"".join(up)}"/>',
             f'<path class="bar down" d="{"".join(down)}"/>',
             f'<path class="ln s-avg" d="{_path(xs, [_y(v, lo, hi) if v is not None else None for v in av])}"/>']
    avg_s = Series("avg", "20-day average", av, fmt=vs.fmt)
    data = {"s": [vs.payload(), avg_s.payload()]}
    data["s"][0]["nodot"] = 1
    if x_ref:
        data["xref"] = _slug(x_ref)
    else:
        data.update(_x_payload(dates))
    title = "Volume — shares traded"
    return Markup(
        f'<figure class="chart chart-sm" id="{_slug(chart_id)}" data-chart>'
        f'<figcaption class="chart-head"><span class="chart-title">{escape(title)}{head_extra}</span>'
        '<div class="chart-legend"><span class="lg s-up">Price up that day</span>'
        '<span class="lg s-down">Price down</span><span class="lg s-avg">20-day average</span></div></figcaption>'
        f'<div class="chart-variant" data-default><div class="chart-body" style="--h:{height}px">'
        + _axis_y(ticks, 0, lo, hi, _short_num) +
        f'<div class="chart-plot" data-o="0" data-n="{n}" data-lo="0" data-hi="{hi:g}" data-bars="1">'
        + _svg("".join(parts), title, f"{title}: {_summary(vs.values, vs.fmt)}")
        + _axis_x(date_ticks(dates), n, bars=True) + _XHAIR
        + f'</div></div></div><script type="application/json" class="chart-data">{_json(data)}</script></figure>')
