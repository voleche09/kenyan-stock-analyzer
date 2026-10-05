"""
Small HTML building blocks shared by every dashboard page, so the same thing
always looks and behaves the same way: headline figures (kpi), up/down
changes (delta), pills, cards, tabs, ⓘ explanations, weight and range bars,
tables and empty states.

Rules every helper follows:
  - Plain strings are escaped; markupsafe.Markup passes through untouched,
    so a helper's output can be nested inside another's.
  - Links must be http(s) or local (relative / #anchor) — never javascript:.
  - Meaning is never colour alone: changes carry ▲/▼ and a sign, and tone
    is exposed as data-tone="up|down|warn|…" (the fact check reads it too).
  - Private amounts carry class="private" so "Hide amounts" can mask them.
"""

import re

from markupsafe import Markup, escape

import glossary


def esc(value):
    """Escape text; leave Markup alone; None -> ''."""
    if value is None:
        return Markup("")
    return escape(value)


def attr(value):
    return escape("" if value is None else str(value))


# http(s), an #anchor, or a page here (never //host, javascript:, data:)
_SAFE_HREF = re.compile(r"^(https?://\S+|#[\w\-.]*|(?!/)[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)*(#[\w\-.]*)?)$")


def href(url):
    """A link target that's safe to print: http(s), an #anchor or a local
    page name. Anything else (javascript:, data:, …) becomes ''."""
    u = str(url or "").strip()
    return attr(u) if u and _SAFE_HREF.match(u) and not u.lower().startswith("javascript") else Markup("")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


# ------------------------------------------------------------------ small marks
def tone_of(value, *, neutral_band=0.0):
    if value is None:
        return "flat"
    return "up" if value > neutral_band else "down" if value < -neutral_band else "flat"


def delta(value, *, fmt="{:+.2f}%", suffix="", tone=None, private=False, title=None):
    """A change with an arrow and a sign: ▲ +2.35% / ▼ −1.10% / — (no data).
    The arrow and sign mean colour is never the only cue."""
    if value is None:
        return Markup('<span class="delta" data-tone="flat">—</span>')
    t = tone or tone_of(value)
    arrow = {"up": "▲", "down": "▼"}.get(t, "•")
    text = fmt.format(value).replace("-", "−")
    if t == "flat":
        text = text.lstrip("+")                   # "0.00%", not "+0.00%"
    cls = "delta private" if private else "delta"
    tip = f' title="{attr(title)}"' if title else ""
    return Markup(f'<span class="{cls}" data-tone="{t}"{tip}><i aria-hidden="true">{arrow}</i>'
                  f'{esc(text)}{esc(suffix)}</span>')


def pill(text, tone="neutral", *, title=None):
    tip = f' title="{attr(title)}"' if title else ""
    return Markup(f'<span class="pill" data-tone="{attr(tone)}"{tip}>{esc(text)}</span>')


def info(term, *, label=None):
    """An ⓘ button opening the plain-English explanation of a glossary term.
    The explanation itself is added once per page by glossary_popovers()."""
    key = glossary.key(term)
    if not glossary.has(key):
        return Markup("")
    what = label or glossary.title(key)
    return Markup(f'<button type="button" class="info" popovertarget="g-{key}" '
                  f'aria-label="What is {attr(what)}?">i</button>')


def glossary_popovers(html):
    """The popovers for every ⓘ used in `html` — call once per page."""
    keys = sorted(set(re.findall(r'popovertarget="g-([a-z0-9-]+)"', str(html))))
    out = []
    for k in keys:
        t, body = glossary.title(k), glossary.text(k)
        out.append(f'<div class="pop" popover id="g-{k}" role="dialog" aria-label="{attr(t)}">'
                   f'<b>{esc(t)}</b><p>{esc(body)}</p></div>')
    return Markup("".join(out))


# ------------------------------------------------------------------ headline figures
def kpi(label, value, *, sub=None, delta_html=None, term=None, tone=None, private=False,
        kpi_id=None, href_to=None, big=False):
    """One headline figure: a label (with an optional ⓘ), the value, and a
    line underneath. data-kpi carries the label for the fact check."""
    cls = "kpi" + (" kpi-big" if big else "")
    tone_attr = f' data-tone="{attr(tone)}"' if tone else ""
    vcls = "kpi-value num" + (" private" if private else "")
    sub_html = f'<div class="kpi-sub">{esc(sub)}</div>' if sub else ""
    dl = f'<div class="kpi-delta">{delta_html}</div>' if delta_html else ""
    idattr = f' id="{attr(kpi_id)}"' if kpi_id else ""
    inner = (f'<div class="kpi-label">{esc(label)}{info(term) if term else ""}</div>'
             f'<div class="{vcls}">{esc(value)}</div>{dl}{sub_html}')
    if href_to:
        inner += f'<a class="kpi-link" href="{href(href_to)}" aria-label="Open {attr(label)}"></a>'
    return Markup(f'<div class="{cls}" data-kpi="{attr(label)}"{tone_attr}{idattr}>{inner}</div>')


def kpi_row(items, *, cls=""):
    return Markup(f'<div class="kpis {attr(cls)}">{"".join(str(i) for i in items)}</div>')


# ------------------------------------------------------------------ containers
def card(body, *, title=None, sub=None, actions=None, card_id=None, cls="", term=None, icon=None):
    """A content card. `title` gets an optional ⓘ (term) and line icon."""
    head = ""
    if title:
        ic = icon_svg(icon) if icon else ""
        head = (f'<header class="card-head"><h3>{ic}{esc(title)}{info(term) if term else ""}</h3>'
                + (f'<p class="card-sub">{esc(sub)}</p>' if sub else "")
                + (f'<div class="card-actions">{actions}</div>' if actions else "") + "</header>")
    idattr = f' id="{attr(card_id)}"' if card_id else ""
    return Markup(f'<section class="card {attr(cls)}"{idattr}>{head}<div class="card-body">{body}</div></section>')


def section(body, *, title, sec_id=None, sub=None, actions=None, icon=None, term=None, cls=""):
    """A page section with a heading (h2) — the unit the in-page nav and
    deep links point at."""
    ic = icon_svg(icon) if icon else ""
    idattr = f' id="{attr(sec_id)}"' if sec_id else ""
    return Markup(
        f'<section class="page-section {attr(cls)}"{idattr}>'
        f'<header class="section-head"><h2>{ic}{esc(title)}{info(term) if term else ""}</h2>'
        + (f'<p class="section-sub">{esc(sub)}</p>' if sub else "")
        + (f'<div class="section-actions">{actions}</div>' if actions else "")
        + f'</header>{body}</section>')


def details(summary, body, *, open_=False, det_id=None, cls=""):
    """A collapsible block — explainers, long schedules, how-tos."""
    idattr = f' id="{attr(det_id)}"' if det_id else ""
    return Markup(f'<details class="fold {attr(cls)}"{idattr}{" open" if open_ else ""}>'
                  f'<summary>{esc(summary)}</summary><div class="fold-body">{body}</div></details>')


def tabs(tabs_id, panels, *, label="Sections"):
    """Server-rendered tabs. `panels`: [(key, title, body_html, count_or_None)].

    Every panel is in the page (find-in-page, print and the fact check see
    everything). Without JavaScript the panels simply stack and the tab bar
    works as jump links; with it, one panel shows at a time and the URL
    hash (#<key>) opens a tab directly.
    """
    bar, bodies = [], []
    for i, (key, title, body, count) in enumerate(panels):
        k = slug(key)
        badge = f' <span class="tab-count">{esc(count)}</span>' if count not in (None, "") else ""
        bar.append(f'<a class="tab" role="tab" href="#{k}" id="tab-{k}" aria-controls="{k}" '
                   f'data-tab="{k}"{" aria-selected=\"true\"" if i == 0 else ""}>{esc(title)}{badge}</a>')
        bodies.append(f'<section class="tab-panel" role="tabpanel" id="{k}" aria-labelledby="tab-{k}" '
                      f'data-panel="{k}"><h2 class="print-only">{esc(title)}</h2>{body}</section>')
    return Markup(f'<div class="tabs" data-tabs="{attr(tabs_id)}">'
                  f'<nav class="tab-bar" role="tablist" aria-label="{attr(label)}">{"".join(bar)}</nav>'
                  f'{"".join(bodies)}</div>')


def empty_state(title, body, *, icon="inbox", actions=None):
    return Markup(f'<div class="empty">{icon_svg(icon, cls="empty-icon")}<h3>{esc(title)}</h3>'
                  f'<p>{esc(body)}</p>{actions or ""}</div>')


def banner(body, *, tone="info", title=None):
    """A notice: info / warn / danger / ok."""
    t = f"<b>{esc(title)}</b> " if title else ""
    return Markup(f'<div class="banner" data-tone="{attr(tone)}" role="note">{t}{esc(body)}</div>')


# ------------------------------------------------------------------ bars
def weight_bar(pct, *, tone="accent", label=None):
    """A thin bar showing a share (0–100%), e.g. a holding's weight."""
    if pct is None:
        return Markup("")
    w = max(0.0, min(float(pct), 100.0))
    aria = attr(label or f"{w:.0f}%")
    return Markup(f'<span class="wbar" data-tone="{attr(tone)}" role="img" aria-label="{aria}">'
                  f'<i style="width:{w:.1f}%"></i></span>')


def range_bar(low, high, value, *, marks=None, fmt=lambda v: f"{v:,.2f}", label="52-week range"):
    """Where `value` sits between `low` and `high` (e.g. the 52-week range),
    with optional marks [(value, label, css class)] such as your buy price.
    A fourth item False keeps the mark's value out of its hover text (for
    private amounts, which "Hide amounts" can't blur in a tooltip)."""
    if None in (low, high, value) or high <= low:
        return Markup('<span class="rbar rbar-empty">—</span>')

    def pos(v):
        return max(0.0, min((v - low) / (high - low) * 100, 100.0))
    m = "".join(f'<i class="rbar-mark {attr(c)}" style="left:{pos(v):.1f}%" '
                f'title="{attr(lab)}{": " + attr(fmt(v)) if (rest[0] if rest else True) else ""}"></i>'
                for v, lab, c, *rest in (marks or []) if v is not None)
    return Markup(
        f'<span class="rbar" role="img" aria-label="{attr(label)}: {attr(fmt(low))} to {attr(fmt(high))}, '
        f'now {attr(fmt(value))} ({pos(value):.0f}% of the way up)">'
        f'<span class="rbar-track"><i class="rbar-now" style="left:{pos(value):.1f}%"></i>{m}</span>'
        f'<span class="rbar-ends num"><span>{esc(fmt(low))}</span><span>{esc(fmt(high))}</span></span></span>')


# ------------------------------------------------------------------ tables
class Col:
    """A table column: header, how to sort it, alignment, an optional ⓘ."""

    def __init__(self, label, *, sort="auto", align=None, term=None, cls="", hide_sm=False, title=None):
        self.label, self.sort, self.term, self.cls = label, sort, term, cls
        self.align = align or ("right" if sort == "number" else "left")
        self.hide_sm, self.title = hide_sm, title


def cell(content, *, sort=None, tone=None, cls="", tip=None, tip_ref=None):
    """A table cell's content plus its sort key (data-sort) and tone.
    `tip_ref` names a <template> holding the cell's hover preview — for a
    preview used more than once on a page, written only once."""
    return {"html": content, "sort": sort, "tone": tone, "cls": cls, "tip": tip, "tip_ref": tip_ref}


def table(cols, rows, *, table_id=None, caption=None, sortable=True, filter_placeholder=None,
          show_first=None, empty="Nothing to show", row_attrs=None, cls=""):
    """A data table: sticky header, right-aligned numbers, sortable columns
    (sort keys live in data-sort, never in hidden text), an optional filter
    box and "Show all N" after `show_first` rows."""
    if not rows:
        return empty_state(empty, "") if empty else Markup("")
    tid = attr(table_id) if table_id else ""
    ths = []
    for i, c in enumerate(cols):
        cl = " ".join(x for x in (c.cls, f"a-{c.align}", "hide-sm" if c.hide_sm else "") if x)
        srt = f' data-sort-type="{attr(c.sort)}"' if sortable and c.sort else ""
        tip = f' title="{attr(c.title)}"' if c.title else ""
        # The last word, the ⓘ and the sort arrow stay together when a header wraps.
        head, _sp, last = str(c.label).rpartition(" ")
        label = (f'{esc(head)} ' if head else "") + f'<span class="th-end">{esc(last)}{info(c.term) if c.term else ""}</span>'
        ths.append(f'<th scope="col" class="{cl}"{srt}{tip}>{label}</th>')
    trs = []
    for r_i, row in enumerate(rows):
        tds = []
        for c, v in zip(cols, row):
            v = v if isinstance(v, dict) else {"html": v}
            cl = " ".join(x for x in (c.cls, v.get("cls"), f"a-{c.align}", "hide-sm" if c.hide_sm else "") if x)
            extra = ""
            if v.get("sort") is not None:
                extra += f' data-sort="{attr(v["sort"])}"'
            if v.get("tone"):
                extra += f' data-tone="{attr(v["tone"])}"'
            if v.get("tip"):
                extra += f' data-tip="{attr(v["tip"])}"'
            if v.get("tip_ref"):
                extra += f' data-tip-ref="{attr(v["tip_ref"])}"'
            tds.append(f'<td class="{cl}"{extra}>{esc(v.get("html"))}</td>')
        ra = (row_attrs[r_i] if row_attrs else "") or ""
        trs.append(f"<tr{' ' + ra if ra else ''}>{''.join(tds)}</tr>")
    filt = ""
    if filter_placeholder and tid:
        filt = (f'<div class="table-tools"><input type="search" class="table-filter" data-filter-for="{tid}" '
                f'placeholder="{attr(filter_placeholder)}" aria-label="{attr(filter_placeholder)}"></div>')
    limit = f' data-show-first="{int(show_first)}"' if show_first and len(rows) > show_first else ""
    cap = f"<caption>{esc(caption)}</caption>" if caption else ""
    more = (f'<button type="button" class="show-all" data-show-all-for="{tid}">Show all {len(rows)}</button>'
            if limit and tid else "")
    return Markup(
        f'{filt}<div class="table-wrap"><table class="data {attr(cls)}"{f" id=\"{tid}\"" if tid else ""}'
        f'{" data-sortable" if sortable else ""}{limit}>{cap}<thead><tr>{"".join(ths)}</tr></thead>'
        f'<tbody>{"".join(trs)}</tbody></table></div>{more}')


# ------------------------------------------------------------------ icons
# A small line-icon set (24×24, stroke = currentColor) replacing emoji in
# navigation and headings. Paths are original simple geometric shapes.
_ICONS = {
    "home": "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z",
    "briefcase": "M3 8h18v11H3zM8 8V5h8v3M3 13h18",
    "star": "M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z",
    "map": "M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2zM9 4v14M15 6v14",
    "activity": "M3 12h4l3-8 4 16 3-8h4",
    "bar": "M4 20V10M10 20V4M16 20v-7M22 20H2",
    "percent": "M5 19L19 5M7 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
    "calendar": "M4 6h16v14H4zM4 10h16M9 3v4M15 3v4",
    "layers": "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
    "globe": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18",
    "pulse": "M3 12h3l2-5 4 10 2-5h7",
    "bank": "M3 10l9-6 9 6M5 10v8M9 10v8M15 10v8M19 10v8M3 20h18",
    "shield": "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
    "coins": "M8 8a5 2.5 0 1 0 10 0 5 2.5 0 1 0-10 0M8 8v4c0 1.4 2.2 2.5 5 2.5s5-1.1 5-2.5V8M6 13c-1.8.4-3 1.2-3 2.1 0 1.4 2.7 2.5 6 2.5 1.7 0 3.2-.3 4.3-.7",
    "news": "M4 5h13v14H5a1 1 0 0 1-1-1zM17 9h3v9a1 1 0 0 1-1 1h-2M7 9h7M7 13h7M7 16h4",
    "inbox": "M3 13l3-8h12l3 8v6H3zM3 13h5l1 2h6l1-2h5",
    "info": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7.5v.5",
    "bell": "M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 21h4",
    "plus": "M12 5v14M5 12h14",
    "eye": "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    "moon": "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z",
    "menu": "M4 7h16M4 12h16M4 17h16",
    "check": "M5 12l5 5 9-10",
    "alert": "M12 4l9 16H3zM12 10v4M12 17v.5",
    "trend-up": "M3 17l6-6 4 4 8-8M15 7h6v6",
    "pie": "M12 3v9h9a9 9 0 1 1-9-9zM15 3.5A9 9 0 0 1 20.5 9H15z",
    "clock": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
    "flag": "M5 21V4M5 4h11l-2 4 2 4H5",
}


def icon_svg(name, *, cls="icon"):
    d = _ICONS.get(name)
    if not d:
        return Markup("")
    return Markup(f'<svg class="{attr(cls)}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
                  f'<path d="{d}"/></svg>')
