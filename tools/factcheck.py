#!/usr/bin/env python3
"""
Old-vs-new fact check for dashboard changes: proves a redesign didn't drop or
change anything the pages tell you.

Two live runs never produce the same pages (live FX rate and news, caches
reset daily, the clock), so the inputs are recorded once and both versions of
the code render from that recording:

  record   run main.py once and save every page builder's inputs, plus what
           the builders fetch themselves (logos, Treasury rates, foreign
           flows, market pulse), to <dir>/inputs.pkl
  render   rebuild every page from the recording with one version of the
           code — clock frozen to the recording, network blocked
  compare  compare what two rendered folders tell the reader: numbers,
           labelled table cells and headline figures, sentences, links and
           meaning-carrying marks (✓ ⚠️ 🟢, gain/loss colours …)

  python tools/factcheck.py record  --out data/factcheck/run -- --no-email --detailed
  python tools/factcheck.py render  --inputs data/factcheck/run --code ../main-worktree --out data/factcheck/run/old
  python tools/factcheck.py render  --inputs data/factcheck/run --code . --out data/factcheck/run/new
  python tools/factcheck.py compare data/factcheck/run/old data/factcheck/run/new [--details]

`compare` prints counts only unless --details is given. Never pass --details
for a run on a real portfolio: the details are your numbers. Point the
pipeline at a sandbox (tools/make_sandbox.py) with PORTFOLIO_DIR,
REPORT_DIRECTORY, CACHE_DIR and LOG_FILE when recording.

Known differences between old and new can be accepted in
tools/factcheck_allow.txt (patterns, never values) and renamed labels mapped
in tools/factcheck_labels.json.
"""

import argparse
import datetime as dt
import fnmatch
import html as html_mod
import importlib
import json
import logging
import os
import pickle
import re
import socket
import sys
import types
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTRY_POINTS = ("generate_stock_report", "generate_international_stock_report",
                "generate_market_summary", "generate_index")
# Fetches the page builders make on their own while rendering.
FETCHERS = ("foreign_flows.load", "market_pulse.load_all",
            "treasury_securities.load_treasury", "company_logos.fetch_logo_base64")


def _fetch_key(fetcher, args, kwargs):
    if fetcher == "company_logos.fetch_logo_base64":
        return str(args[0] if args else kwargs.get("domain"))
    return "*"                                    # one call per run


# =============================================================== record
def record(out_dir, main_args):
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
    import report_generator
    import watchlist_report

    rec = {"now": dt.datetime.now().isoformat(timespec="seconds"), "calls": [],
           "fetch": defaultdict(dict), "watchlist": None, "watchlist_entries": None,
           "portfolio_dir": os.path.abspath(os.environ.get("PORTFOLIO_DIR", os.path.join(ROOT, "portfolio")))}

    def spy_method(name):
        orig = getattr(report_generator.ReportGenerator, name)

        def wrapper(self, *a, **k):
            rec["calls"].append(pickle.dumps((name, a, k)))     # as of the call
            return orig(self, *a, **k)
        setattr(report_generator.ReportGenerator, name, wrapper)

    for name in ENTRY_POINTS:
        spy_method(name)

    def spy_fetcher(fid):
        mod_name, fn = fid.split(".")
        mod = importlib.import_module(mod_name)
        orig = getattr(mod, fn)

        def wrapper(*a, **k):
            res = orig(*a, **k)
            rec["fetch"][fid][_fetch_key(fid, a, k)] = pickle.dumps(res)
            return res
        setattr(mod, fn, wrapper)

    for fid in FETCHERS:
        spy_fetcher(fid)

    orig_collect = watchlist_report.collect

    def collect(*a, **k):
        out = orig_collect(*a, **k)
        universe = set((k.get("preloaded") or {}).get("nse_universe") or ())
        rec["watchlist"] = pickle.dumps({"rows_ctx": out, "nse_universe": universe})
        return out
    watchlist_report.collect = collect

    orig_load = watchlist_report.wl.load_watchlist

    def load_watchlist(*a, **k):
        entries = orig_load(*a, **k)
        rec["watchlist_entries"] = pickle.dumps(entries)
        return entries
    watchlist_report.wl.load_watchlist = load_watchlist

    sys.argv = ["main.py"] + list(main_args)
    import main as main_mod
    try:
        main_mod.main()
    except SystemExit as e:
        if e.code not in (0, None):
            raise
    rec["fetch"] = dict(rec["fetch"])
    path = os.path.join(out_dir, "inputs.pkl")
    with open(path, "wb") as f:
        pickle.dump(rec, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"Recorded {len(rec['calls'])} page-builder call(s)"
          f"{' + the watchlist' if rec['watchlist'] else ''} -> {path}")


# =============================================================== render
class _WarningCounter(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, r):
        self.messages.append(f"{r.name}: {r.getMessage()}")


def render(inputs_dir, code_dir, out_dir):
    code_dir, out_dir = os.path.abspath(code_dir), os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    sys.path[:0] = [os.path.join(code_dir, "src"), code_dir]
    with open(os.path.join(inputs_dir, "inputs.pkl"), "rb") as f:
        rec = pickle.load(f)
    # Same portfolio folder as the recording (history files are read from
    # it); everything else goes to the output folder.
    os.environ.update({"REPORT_DIRECTORY": out_dir, "CACHE_DIR": os.path.join(out_dir, "_cache"),
                       "LOG_FILE": os.path.join(out_dir, "render.log"),
                       "PORTFOLIO_DIR": rec["portfolio_dir"]})

    def _blocked(*a, **k):
        raise OSError("network blocked during a fact-check render")
    socket.socket.connect = _blocked
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked
    frozen = dt.datetime.fromisoformat(rec["now"])

    class FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.replace(tzinfo=tz) if tz else frozen

        @classmethod
        def today(cls):
            return frozen

    class FrozenDate(dt.date):
        @classmethod
        def today(cls):
            return frozen.date()

    clock = types.SimpleNamespace(datetime=FrozenDateTime, date=FrozenDate, timedelta=dt.timedelta,
                                  timezone=dt.timezone, time=dt.time)

    import config as config_mod
    import report_generator
    import watchlist_report
    from analysis_engine import AnalysisEngine
    report_generator.datetime = FrozenDateTime
    watchlist_report.dt = clock

    for fid in FETCHERS:
        mod_name, fn = fid.split(".")
        results = rec["fetch"].get(fid, {})

        def replay(*a, _fid=fid, _res=results, **k):
            blob = _res.get(_fetch_key(_fid, a, k), _res.get("*"))
            return pickle.loads(blob) if blob is not None else None
        setattr(importlib.import_module(mod_name), fn, replay)

    counter = _WarningCounter()
    logging.getLogger().addHandler(counter)
    logging.disable(logging.NOTSET)

    cfg = config_mod.Config()
    rg = report_generator.ReportGenerator(template_dir=cfg.template_dir, output_dir=out_dir,
                                          clean_old=False, cache_dir=cfg.cache_dir)
    failures = 0
    for blob in rec["calls"]:
        name, a, k = pickle.loads(blob)
        if name == "generate_market_summary":
            k = dict(k, report_type="html")
        try:
            getattr(rg, name)(*a, **k)
        except Exception as e:                      # keep going; report at the end
            failures += 1
            counter.messages.append(f"factcheck: {name} raised {type(e).__name__}: {e}")
    if rec["watchlist"]:
        w = pickle.loads(rec["watchlist"])
        entries = pickle.loads(rec["watchlist_entries"]) if rec["watchlist_entries"] else []
        watchlist_report.collect = lambda *a, **k: w["rows_ctx"]
        watchlist_report.wl.load_watchlist = lambda *a, **k: entries
        try:
            watchlist_report.generate_watchlist_page(
                cfg, rg, AnalysisEngine(config=cfg),
                preloaded={"fundamentals": dict.fromkeys(w["nse_universe"])},
                today=frozen.date(), fetch_news=False)
        except Exception as e:
            failures += 1
            counter.messages.append(f"factcheck: watchlist raised {type(e).__name__}: {e}")

    with open(os.path.join(out_dir, "render_warnings.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(counter.messages))
    pages = len([n for n in os.listdir(out_dir) if n.endswith(".html")])
    print(f"Rendered {pages} page(s) with {code_dir} — {len(counter.messages)} warning(s)"
          f"{f', {failures} failed call(s)' if failures else ''} (render_warnings.txt)")
    return 1 if failures else 0


# =============================================================== extract
_TS = re.compile(r"_\d{8}_\d{6}")
_BLOCK = {"p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "div", "section",
          "summary", "dt", "dd", "caption", "label", "pre", "blockquote", "figcaption",
          "header", "footer", "article", "aside", "details", "ul", "ol", "table", "tr",
          "tbody", "thead", "main", "nav", "body", "html", "button", "option"}
# Marks that carry meaning — kept as tokens. Everything else emoji-like is decoration.
_MARKS = [("⚠️", " [warning] "), ("⚠", " [warning] "), ("✓", " [ok] "), ("✔", " [ok] "),
          ("❗", " [warn] "), ("🕒", " [stale] "), ("🟢", " [green] "), ("🔴", " [red] "),
          ("⚪", " [grey] "), ("★", " [star] "), ("☆", " [nostar] "), ("▲", " + "), ("▼", " - "),
          ("⚡", " [accelerated] ")]
_DECOR = re.compile("[\U0001F000-\U0001FAFF←-⇿⌀-⏿①-⓿"
                    "■-◿☀-➿⤀-⥿⬀-⯿️‍⃣]")
# Classes whose meaning a colour or icon carries; mapped to tokens.
_CLASS_TOKENS = [(re.compile(r"^(positive|up|gain|pos)$"), "pos"),
                 (re.compile(r"^(negative|down|loss|neg)$"), "neg"),
                 (re.compile(r"^f(good|mid|bad)$"), r"\1"),
                 (re.compile(r"^(div-unverified|div-pay|div-none)$"), r"\1"),
                 (re.compile(r"^((?:bc|cal|chip|score|tone)-[a-z0-9_-]+)$"), r"\1"),
                 # a signal's colour: bullish = up (green), bearish = down (red); neutral and
                 # undefined carry no colour meaning
                 (re.compile(r"^bullish$"), "pos"), (re.compile(r"^bearish$"), "neg")]
_DATE_RES = [re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?)?"),
             re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}"),
             re.compile(r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}"),
             re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}")]
_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:EAT|UTC|GMT|AM|PM|am|pm)?\b")
_NUM = re.compile(r"(?P<pre>KES|Ksh|KSh|USD|US\$|\$|£|€)?\s?(?P<sign>[+\-])?\s?(?P<pre2>KES|USD|\$)?\s?"
                  r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
                  r"(?P<suf>\s?(?:%|pp|bps|x\b|×|GBp|GBX|KES|USD|[KMBT]\b|bn\b|[Mm]illion\b|[Bb]illion\b|"
                  r"[Tt]rillion\b))?")
_UNIT = {"KES": "KES", "Ksh": "KES", "KSh": "KES", "USD": "USD", "US$": "USD", "$": "USD",
         "£": "GBP", "€": "EUR", "GBp": "GBp", "GBX": "GBp", "%": "%", "pp": "pp", "bps": "bps",
         "x": "x", "×": "x"}
_SCALE = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12, "bn": 1e9, "million": 1e6, "billion": 1e9, "trillion": 1e12}


def _clean(text):
    text = html_mod.unescape(text).replace("\xa0", " ").replace(" ", " ")
    for a, b in (("−", "-"), ("–", "-"), ("—", " - "), ("‒", "-"), ("‑", "-")):
        text = text.replace(a, b)
    for mark, tok in _MARKS:
        text = text.replace(mark, tok)
    text = _DECOR.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _label(text):
    """A label without the values some old labels carried inline, e.g.
    "Total gain / loss (+101.7%)" -> "total gain / loss" (the value itself
    is still checked as a number)."""
    t = _clean(text).lower()
    t = re.sub(r"\([^)]*\d[^)]*\)", " ", t)
    t = re.sub(r"\[(?:ok|warn|stale|green|red|grey|star|nostar|warning)\]", " ", t)
    return re.sub(r"[\s:*·|]+$", "", re.sub(r"\s+", " ", t)).strip(" -")


def _numbers(text):
    """(unit, sign, value, decimals, scale) for every number in `text`; dates
    become ('date', '', 'YYYY-MM-DD', 0, 1) and times are dropped."""
    out = []
    for rx in _DATE_RES:
        for m in rx.finditer(text):
            out.append(("date", "", m.group(0)[:10] if m.group(0)[:4].isdigit() else _label(m.group(0)), 0, 1.0))
        text = rx.sub(" ", text)
    text = _TIME.sub(" ", text)
    for m in _NUM.finditer(text):
        raw = m.group("num").replace(",", "")
        suf = (m.group("suf") or "").strip()
        unit = _UNIT.get(m.group("pre") or m.group("pre2") or "", "") or _UNIT.get(suf, "")
        scale = _SCALE.get(suf, _SCALE.get(suf.lower(), 1.0))
        dec = len(raw.split(".")[1]) if "." in raw else 0
        out.append((unit, "-" if m.group("sign") == "-" else "+", raw, dec, scale))
    return out


def _sentences(text):
    text = re.sub(r"\[[a-z-]+\]", " ", text)
    for rx in _DATE_RES:
        text = rx.sub("#", text)
    text = _TIME.sub(" ", text)
    text = re.sub(r"[+\-]?\$?\d[\d,]*(?:\.\d+)?", "#", text)
    out = []
    for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(#])", text):
        s = re.sub(r"\s+", " ", s).strip(" .;:,-").lower()
        if len(re.findall(r"[a-z]{2,}", s)) >= 4:
            out.append(s)
    return out


def _page_key(name):
    base = _TS.sub("", name[:-5])
    return base


def _drop_noise(soup):
    from bs4 import Comment
    for el in soup(["script", "style", "noscript", "template", "head"]):
        el.decompose()
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):     # <!-- notes --> aren't shown
        c.extract()
    for sel in (".nav", ".nav-bar", ".footer", ".sidebar", ".site-footer", ".header-actions", ".menu-btn", ".print-only",
                "#hovertip", "#themeBtn", ".theme-toggle", "[data-factcheck-skip]", "button.info",
                ".ticker-logo", ".ticker-logo-fallback", "[data-logo]"):          # logos are decoration
        for el in soup.select(sel):
            el.decompose()
    for el in soup.select('span[style*="display:none"]'):    # hidden numeric sort keys
        if re.fullmatch(r"\s*-?[\d.]+\s*", el.get_text()):
            el.decompose()
    for svg in soup.find_all("svg"):
        keep = " ".join(t.get_text(" ") for t in svg.find_all(["title", "desc"]))
        svg.replace_with(soup.new_string(f" {keep} "))


def _class_tokens(el):
    toks = []
    for node in [el] + el.find_all(True):
        for cls in node.get("class") or []:
            for rx, rep in _CLASS_TOKENS:
                if rx.match(cls):
                    toks.append(rx.sub(rep, cls))
        tone = node.get("data-tone")
        if tone:
            toks.append({"up": "pos", "down": "neg"}.get(tone, tone))
    return sorted(set(toks))


_TONE = {"up": "pos", "down": "neg"}


def _same_value(old, new):
    """Do two cell / headline values say the same thing? Numbers must match
    at the old value's precision (formatting and units may differ: "KES
    +39,885" = "+KES 39,885"), meaning tokens must match, and text without
    numbers must read the same."""
    if old == new:
        return True
    tok = re.compile(r"\[([a-z0-9_-]+)\]")
    if not set(tok.findall(old)) <= set(tok.findall(new)):     # a meaning may be added, never lost
        return False
    o_text, n_text = tok.sub(" ", old), tok.sub(" ", new)
    if not o_text.strip():
        return True                         # the old cell said nothing; showing more isn't a loss
    o_nums, n_nums = _numbers(o_text), _numbers(n_text)
    if not o_nums:
        norm = lambda t: re.sub(r"[\s\W_]+", " ", t).strip().lower()
        missing = {"", "-", "n a", "na", "none"}
        if norm(o_text) in missing and norm(n_text) in missing:
            return True
        # the same words, possibly with more around them ("EQTY" -> "EQTY Equity Group …")
        return f" {norm(o_text)} " in f" {norm(n_text)} "
    if len(o_nums) != len(n_nums):
        return False
    for (ou, osg, oraw, odec, osc), (nu, nsg, nraw, _nd, nsc) in zip(o_nums, n_nums):
        if ou == "date" or nu == "date":
            if (ou, oraw) != (nu, nraw):
                return False
            continue
        if osg != nsg or (ou and nu and ou != nu):
            return False
        if abs(float(nraw) * nsc - float(oraw) * osc) > 0.5 * 10 ** -odec * osc + 1e-9:
            return False
    return True


def _cell_value(el):
    text = _clean(el.get_text(" "))
    toks = _class_tokens(el)
    return text + ("".join(f" [{t}]" for t in toks) if toks else "")


def extract(path):
    from bs4 import BeautifulSoup
    with open(path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    ids = [el["id"] for el in soup.select("[id]")]          # duplicates anywhere count
    templates = {t.get("id"): t.decode_contents() for t in soup.select("template[id]")}
    _drop_noise(soup)                                        # page chrome isn't information
    tips = []
    for el in soup.select("[data-tip], [data-tip-ref]"):
        raw = el.get("data-tip")
        raw = html_mod.unescape(raw) if raw is not None else templates.get(el.get("data-tip-ref"), "")
        tips.append(BeautifulSoup(raw, "html.parser").get_text(" "))
    attrs = [el.get(a) for a in ("title", "alt", "aria-label")
             for el in soup.select(f"[{a}]") if el.get(a)]
    links = sorted({_TS.sub("_TS", a["href"]) for a in soup.select("a[href]")
                    if not a["href"].startswith("javascript:")})

    facts = {"cells": [], "kpis": [], "numbers": Counter(), "sentences": Counter(),
             "marks": Counter(), "links": links, "ids": ids}
    for table in soup.find_all("table"):
        heads = [_label(th.get_text(" ")) for th in table.select("thead th")]
        rows = table.select("tbody tr") or table.find_all("tr")[1 if not heads else 0:]
        if not heads:
            first = table.find("tr")
            heads = [_label(c.get_text(" ")) for c in first.find_all(["th", "td"])] if first else []
        for tr in rows:
            cells = tr.find_all(["td", "th"], recursive=False)
            if not cells or tr.find_parent("thead"):
                continue
            key = _clean(cells[0].get_text(" ")).split(" ")[0] if cells[0].get_text(strip=True) else ""
            for h, c in zip(heads, cells):
                facts["cells"].append((key, h, _cell_value(c)))
    for card in soup.select(".stat-card, .metric, .kpi, [data-kpi]"):
        lab = card.get("data-kpi") or ""
        if not lab:
            le = card.select_one(".stat-label, .label, .kpi-label")
            lab = le.get_text(" ") if le else ""
        ve = card.select_one(".stat-value, .value, .kpi-value")
        if lab and ve:
            own = card.get("data-tone")               # a tone on the card colours its value
            value = _cell_value(ve)
            if own and f"[{_TONE.get(own, own)}]" not in value:
                value += f" [{_TONE.get(own, own)}]"
            facts["kpis"].append((_label(lab), value))
    # The old net-worth hero: a total, then one card per part with
    # "label … value" rows — labelled "<part> · <row>".
    for hero in soup.select(".networth-hero"):
        le, ve = hero.select_one(".nw-total-label"), hero.select_one(".nw-total-value")
        if ve:
            facts["kpis"].append(("total net worth", _cell_value(ve)))
    for card in soup.select(".nw-card"):
        h = card.find("h4")
        part = _label(h.get_text(" ")).replace("jump ↓", "").replace("jump", "").strip() if h else ""
        for row in card.select(".nw-row"):
            span, val = row.find("span"), row.find("b")
            if span and val:
                facts["kpis"].append((f"{part} · {_label(span.get_text(' '))}", _cell_value(val)))

    # Text, grouped by its nearest block element so inline markup doesn't
    # split sentences.
    blocks = defaultdict(list)
    for s in soup.find_all(string=True):
        if not s.strip():
            continue
        parent = s.parent
        while parent is not None and parent.name not in _BLOCK:
            parent = parent.parent
        blocks[id(parent)].append(str(s))
    texts = [" ".join(parts) for parts in blocks.values()] + tips + attrs
    for t in texts:
        c = _clean(t)
        for n in _numbers(c):
            facts["numbers"][n] += 1
        for s in _sentences(c):
            facts["sentences"][s] += 1
        for m in re.findall(r"\[(warning|ok|warn|stale|green|red|grey|star|nostar|accelerated)\]", c):
            facts["marks"][m] += 1
    return facts


# =============================================================== compare
class _NumberIndex:
    """The new page's numbers, bucketed by (unit, sign) and sorted, so each
    old number is looked up with a binary search."""

    def __init__(self, numbers):
        import bisect
        self._bisect = bisect
        self.dates = {n for n in numbers if n[0] == "date"}
        buckets = defaultdict(list)
        for unit, sign, raw, dec, scale in numbers:
            if unit != "date":
                buckets[(unit, sign)].append((float(raw) * scale, 0.5 * 10 ** -dec * scale))
        self.buckets = {}
        for k, vals in buckets.items():
            vals.sort()
            self.buckets[k] = ([v for v, _t in vals], vals, max(t for _v, t in vals))

    def match(self, old):
        """'ok' if the old number is shown at its old precision or better,
        'precision' if only less precisely, None if not at all."""
        unit, sign, raw, dec, scale = old
        if unit == "date":
            return "ok" if old in self.dates else None
        if (unit, sign) not in self.buckets:
            return None
        keys, vals, max_tol = self.buckets[(unit, sign)]
        v, tol = float(raw) * scale, 0.5 * 10 ** -dec * scale
        lo = self._bisect.bisect_left(keys, v - max(tol, max_tol) - 1e-9)
        hi = self._bisect.bisect_right(keys, v + max(tol, max_tol) + 1e-9)
        loose = None
        for nv, ntol in vals[lo:hi]:
            if abs(nv - v) <= tol + 1e-9:
                return "ok"
            if abs(nv - v) <= ntol + 1e-9:
                loose = "precision"
        return loose


def _load_rules():
    allow, labels = [], {}
    p = os.path.join(ROOT, "tools", "factcheck_allow.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    cat, page, rx = line.split(None, 2)
                    allow.append((cat, page, re.compile(rx)))
    p = os.path.join(ROOT, "tools", "factcheck_labels.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            labels = json.load(f)
    return allow, labels


def compare(old_dir, new_dir, details=False):
    allow, labels = _load_rules()
    col_map = {k.lower(): v.lower() for k, v in labels.get("columns", {}).items()}
    kpi_map = {k.lower(): v.lower() for k, v in labels.get("kpis", {}).items()}

    def allowed(cat, page, text):
        return any(c in (cat, "*") and fnmatch.fnmatch(page, pg) and rx.search(text)
                   for c, pg, rx in allow)

    def pages(d):
        return {_page_key(n): os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".html")}

    old_pages, new_pages = pages(old_dir), pages(new_dir)
    report = defaultdict(lambda: defaultdict(list))
    for key in sorted(set(old_pages) - set(new_pages)):
        if not allowed("page", key, key):
            report[key]["page missing"].append(key)
    for key in sorted(set(old_pages) & set(new_pages)):
        o, n = extract(old_pages[key]), extract(new_pages[key])
        index = _NumberIndex(n["numbers"])
        for num in o["numbers"]:
            hit = index.match(num)
            text = f"{num[0]} {num[1]}{num[2]}"
            if hit is None and not allowed("number", key, text):
                report[key]["number lost"].append(text)
            elif hit == "precision" and not allowed("precision", key, text):
                report[key]["shown less precisely"].append(text)
        new_text = " \u2063 ".join(n["sentences"])
        for s in o["sentences"]:
            if s not in n["sentences"] and s not in new_text and not allowed("sentence", key, s):
                report[key]["sentence lost"].append(s)
        new_cells = defaultdict(set)
        new_cell_values = defaultdict(set)
        for rk, h, v in n["cells"]:
            new_cells[(rk, h)].add(v)
            new_cell_values[rk].add(v)
        for rk, h, v in o["cells"]:
            h2 = col_map.get(h, h)
            text = f"{rk} | {h} | {v}"
            if (rk, h2) in new_cells:
                change = f"{text}  ->  {sorted(new_cells[(rk, h2)])}"
                if not any(_same_value(v, nv) for nv in new_cells[(rk, h2)]) and not allowed("cell", key, change):
                    report[key]["cell changed"].append(change)
            elif any(_same_value(v, nv) for nv in new_cell_values[rk]):
                if not allowed("cell-moved", key, text):
                    report[key]["cell moved"].append(text)
            elif not allowed("cell", key, text):
                report[key]["cell lost"].append(text)
        new_kpis = defaultdict(set)
        for lab, v in n["kpis"]:
            new_kpis[lab].add(v)
        for lab, v in o["kpis"]:
            lab2 = kpi_map.get(lab, lab)
            text = f"{lab} = {v}"
            if lab2 in new_kpis:
                change = f"{text}  ->  {sorted(new_kpis[lab2])}"
                if not any(_same_value(v, nv) for nv in new_kpis[lab2]) and not allowed("kpi", key, change):
                    report[key]["headline figure changed"].append(change)
            elif not allowed("kpi", key, text):
                report[key]["headline figure lost"].append(text)
        for href in sorted(set(o["links"]) - set(n["links"])):
            if not allowed("link", key, href):
                report[key]["link lost"].append(href)
        for mark, cnt in o["marks"].items():
            lost = cnt - n["marks"].get(mark, 0)
            if lost > 0 and not allowed("mark", key, f"{mark} ×{lost}"):
                report[key]["mark lost"].append(f"{mark} ×{lost}")
        dup = [i for i, c in Counter(n["ids"]).items() if c > 1]
        if dup:
            report[key]["duplicate id"].extend(sorted(dup))

    total = sum(len(v) for cats in report.values() for v in cats.values())
    print(f"Fact check: {len(old_pages)} old page(s), {len(new_pages)} new page(s) — "
          f"{total} unexplained difference(s)")
    for key in sorted(report):
        cats = report[key]
        print(f"  {key}: " + ", ".join(f"{len(v)} {c}" for c, v in sorted(cats.items())))
        if details:
            for c, items in sorted(cats.items()):
                for it in items[:60]:
                    print(f"      [{c}] {it}")
                if len(items) > 60:
                    print(f"      … {len(items) - 60} more")
    return 1 if total else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--out", required=True)
    r.add_argument("main_args", nargs=argparse.REMAINDER)
    p = sub.add_parser("render")
    p.add_argument("--inputs", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("old")
    c.add_argument("new")
    c.add_argument("--details", action="store_true")
    a = ap.parse_args()
    if a.cmd == "record":
        args = a.main_args[1:] if a.main_args[:1] == ["--"] else a.main_args
        record(a.out, args)
    elif a.cmd == "render":
        sys.exit(render(a.inputs, a.code, a.out))
    else:
        sys.exit(compare(a.old, a.new, a.details))


if __name__ == "__main__":
    main()
