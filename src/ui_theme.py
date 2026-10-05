"""
The page shell every dashboard page shares — sidebar navigation, a slim top
bar, the design system and the page script — plus the small script in
<head> that picks the theme, "Hide amounts" and the open tab before the page
first paints.

The stylesheet (ui_theme.css) and script (ui_runtime.js) are real files next
to this one, read once per run and inlined into every page: the pages must
work offline, opened from disk, and the dashboard app's update step only
publishes top-level files, so a shared assets folder wouldn't survive.
"""

import json
import os
import re

from markupsafe import Markup, escape

import ui_kit

_HERE = os.path.dirname(os.path.abspath(__file__))
_files = {}


def _read(name):
    if name not in _files:
        with open(os.path.join(_HERE, name), encoding="utf-8") as f:
            _files[name] = f.read()
    return _files[name]


def _min_css(text):
    """The stylesheet without comments and spare whitespace (it's inlined
    into every page). Strings and calc() keep their inner spaces."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([{};,>])\s*", r"\1", text)
    return re.sub(r":\s+", ":", text).replace(";}", "}").strip()


def _min_js(text):
    """The page script without its comment lines and indentation. Line
    breaks stay, so automatic semicolons work exactly as in the source."""
    text = re.sub(r"\A/\*.*?\*/\s*", "", text, flags=re.S)
    lines = (ln.strip() for ln in text.splitlines())
    return "\n".join(ln for ln in lines if ln and not ln.startswith("//"))


def css():
    if "css-min" not in _files:
        _files["css-min"] = _min_css(_read("ui_theme.css"))
    return _files["css-min"]


def title_for(active_file):
    """The nav label of a page, e.g. 'portfolio.html' -> 'My Portfolio'."""
    for _g, items in NAV:
        for fn, label, _i in items:
            if fn == active_file:
                return label
    return None


def js():
    if "js-min" not in _files:
        _files["js-min"] = _min_js(_read("ui_runtime.js"))
    return _files["js-min"]


# (group heading or None, [(file, label, icon)])
NAV = [
    (None, [("index.html", "Overview", "home")]),
    ("My money", [("portfolio.html", "My Portfolio", "briefcase"),
                  ("watchlist.html", "Watchlist", "star")]),
    ("Market", [("visuals.html", "Market map", "map"),
                ("technicals.html", "Technicals", "activity"),
                ("fundamentals.html", "Fundamentals", "bar"),
                ("dividends.html", "Dividends", "coins"),
                ("earnings.html", "Next earnings", "calendar"),
                ("sectors.html", "Sectors", "layers"),
                ("foreign.html", "Foreign flows", "globe"),
                ("pulse.html", "Market pulse", "pulse"),
                ("bonds.html", "Govt bonds", "bank")]),
    ("Checks", [("quality.html", "Data quality", "shield")]),
]
NAV_FILES = [f for _g, items in NAV for f, _l, _i in items]

FAVICON = (
    "<link rel=\"icon\" href=\"data:image/svg+xml,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='7' fill='%234f46e5'/>"
    "<rect x='6' y='17' width='5' height='9' rx='1.2' fill='%23c7d2fe'/>"
    "<rect x='13.5' y='11' width='5' height='15' rx='1.2' fill='%23e0e7ff'/>"
    "<rect x='21' y='6' width='5' height='20' rx='1.2' fill='%23ffffff'/>"
    "</svg>\">")

_BRAND_MARK = ('<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="13" width="4" height="7" rx="1" fill="#c7d2fe"/>'
               '<rect x="10" y="8.5" width="4" height="11.5" rx="1" fill="#e0e7ff"/>'
               '<rect x="16" y="4" width="4" height="16" rx="1" fill="#fff"/></svg>')

# Runs in <head>, before anything paints: theme (saved choice, else the
# device setting), Hide amounts, and which tab the URL asks for. The tab
# map turns old section anchors (#stocks-holdings …) into their tab.
_HEAD_JS = (
    "(function(){var h=document.documentElement;h.classList.add('js');"
    "try{var t=localStorage.getItem('nse-theme')||"
    "(window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');"
    "h.setAttribute('data-theme',t);if(localStorage.getItem('nse-hide')==='1')h.classList.add('hide-amounts');}"
    "catch(e){h.setAttribute('data-theme','light');}"
    "var M=__MAP__,D=__DEFAULT__;if(!D)return;"
    "function pick(){var k='';try{k=decodeURIComponent(location.hash.slice(1));}catch(e){}"
    "h.setAttribute('data-tab',M[k]||h.getAttribute('data-tab')||D);}"
    "pick();window.addEventListener('hashchange',pick);})();")


def tab_layout(body_html):
    """From a page body built with ui_kit.tabs(): the CSS that shows only the
    chosen panel, the anchor -> tab map, and the default tab."""
    body = str(body_html)
    starts = [(m.start(), m.group(1)) for m in re.finditer(r'<section class="tab-panel"[^>]*data-panel="([a-z0-9-]+)"', body)]
    if not starts:
        return "", {}, None
    mapping = {}
    for i, (pos, key) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(body)
        mapping[key] = key
        for anchor in re.findall(r'\sid="([^"]+)"', body[pos:end]):
            mapping.setdefault(anchor, key)
    rules = "".join(f'html.js[data-tab="{k}"] .tabs>.tab-panel:not([data-panel="{k}"]){{display:none}}'
                    for _p, k in starts)
    return rules, mapping, starts[0][1]


def head_script(mapping, default):
    return (_HEAD_JS.replace("__MAP__", json.dumps(mapping, separators=(",", ":")).replace("<", "\\u003c"))
            .replace("__DEFAULT__", json.dumps(default)))


def nav_html(active_file):
    out = []
    for group, items in NAV:
        if group:
            out.append(f'<div class="nav-group">{escape(group)}</div>')
        for fn, label, icon in items:
            cls = "nav-item active" if fn == active_file else "nav-item"
            cur = ' aria-current="page"' if fn == active_file else ""
            out.append(f'<a href="{fn}" class="{cls}"{cur}>{ui_kit.icon_svg(icon)}<span>{escape(label)}</span></a>')
    return "".join(out)


def page(*, title, active_file, subtitle, body, app_head="", app_body="", private=False,
         extra_head="", footer=None):
    """A complete dashboard page.

    title       the page's name (top bar + <title>)
    active_file which nav entry is highlighted
    subtitle    the data-date / sources line under the title (Markup or text)
    body        the page content (Markup)
    app_head / app_body  the local app's stylesheet + script tags
    private     show the "Hide amounts" toggle (pages with your money on them)
    """
    body = Markup(body)
    rules, mapping, default = tab_layout(body)
    pops = ui_kit.glossary_popovers(body)
    hide_btn = ('<button type="button" class="icon-btn" data-hide-toggle aria-pressed="false" title="Hide amounts">'
                f'{ui_kit.icon_svg("eye")}<span class="btn-label">Hide</span></button>') if private else ""
    foot = footer if footer is not None else (
        "Generated by Kenyan Stock Analyzer · Information, not financial advice · "
        "Click any stock for its full report")
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<meta name="color-scheme" content="light dark">'
        f'<script>{head_script(mapping, default)}</script>'
        f'{FAVICON}<title>{escape(title)} · NSE Dashboard</title>'
        f'<style>{css()}{rules}</style>{extra_head}{app_head}</head><body>'
        '<div class="app">'
        '<aside class="sidebar" id="sidebar" aria-label="Main navigation">'
        f'<a class="brand" href="index.html"><span class="brand-mark">{_BRAND_MARK}</span>'
        '<span>NSE Dashboard<small>Nairobi Securities Exchange</small></span></a>'
        f'<nav class="nav" aria-label="Pages">{nav_html(active_file)}</nav></aside>'
        '<div class="nav-scrim" aria-hidden="true"></div>'
        '<div class="main">'
        '<header class="topbar">'
        '<button type="button" class="icon-btn menu-btn" data-nav-toggle aria-controls="sidebar" '
        f'aria-label="Open the menu">{ui_kit.icon_svg("menu")}</button>'
        f'<div class="topbar-titles"><h1>{escape(title)}</h1><div class="page-sub">{escape(subtitle)}</div></div>'
        f'<div class="header-actions">{hide_btn}'
        '<button type="button" class="icon-btn" data-theme-toggle id="themeBtn" aria-pressed="false" '
        f'title="Switch to dark mode">{ui_kit.icon_svg("moon")}<span class="btn-label">Dark</span></button>'
        '</div></header>'
        f'<main class="content" id="main">{body}'
        + ('<p class="hide-note footnote">Amounts are hidden on screen only — they are still in this file.</p>'
           if private else "") +
        f'</main><footer class="site-footer">{escape(foot)}</footer></div></div>'
        f'<div id="hovertip" role="tooltip"></div>{pops}'
        f'<script>{js()}</script>{app_body}</body></html>')
