/* NSE Dashboard — page behaviour. Inlined into every page by ui_theme.py.

   Everything here is an enhancement: without it the tabs stack into one
   page with jump links, charts are still drawn, tables are still readable.

   window.NSE.reveal(id) is the one way to bring any element into view — it
   switches tab, lifts a table filter, opens a collapsed section or card,
   then scrolls. The URL hash, the alert chips and the local app
   (app_assets/manage.js) all use it. */
(function () {
  'use strict';
  var d = document, html = d.documentElement;
  html.classList.add('js');

  function $(sel, root) { return (root || d).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || d).querySelectorAll(sel)); }
  function store(k, v) {
    try {
      if (v === undefined) return localStorage.getItem(k);
      if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v);
    } catch (e) { /* private mode: the choice just isn't remembered */ }
    return null;
  }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function closest(el, sel) { return el && el.closest ? el.closest(sel) : null; }

  // ------------------------------------------------------------ theme & hide amounts
  function syncToggles() {
    var dark = html.getAttribute('data-theme') === 'dark';
    $$('[data-theme-toggle]').forEach(function (b) {
      b.setAttribute('aria-pressed', dark ? 'true' : 'false');
      b.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
      var l = $('.btn-label', b); if (l) l.textContent = dark ? 'Light' : 'Dark';
    });
    var hidden = html.classList.contains('hide-amounts');
    $$('[data-hide-toggle]').forEach(function (b) {
      b.setAttribute('aria-pressed', hidden ? 'true' : 'false');
      b.title = hidden ? 'Show amounts' : 'Hide amounts (for when someone is looking over your shoulder)';
      var l = $('.btn-label', b); if (l) l.textContent = hidden ? 'Show' : 'Hide';
    });
  }
  function toggleTheme() {
    var t = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', t);
    store('nse-theme', t);
    syncToggles();
  }
  window.toggleTheme = toggleTheme;          // older pages call it from onclick
  function toggleHide() {
    var on = html.classList.toggle('hide-amounts');
    store('nse-hide', on ? '1' : null);
    syncToggles();
  }

  // ------------------------------------------------------------ tabs
  // The head script already chose the tab (html[data-tab]) before the page
  // painted; this keeps the tab bar in sync and handles clicks and keys.
  var tabsRoot = $('[data-tabs]');
  function panelFor(key) { return tabsRoot ? $('.tab-panel[data-panel="' + key + '"]', tabsRoot) : null; }
  function activate(key, opts) {
    var panel = panelFor(key);
    if (!panel) return false;
    html.setAttribute('data-tab', key);
    $$('.tab', tabsRoot).forEach(function (t) {
      var on = t.getAttribute('data-tab') === key;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
    });
    if (!opts || opts.history !== false) {
      try { history.replaceState(null, '', '#' + key); } catch (e) { /* file:// in some browsers */ }
    }
    d.dispatchEvent(new CustomEvent('nse:tab', { detail: { key: key } }));
    return true;
  }
  if (tabsRoot) {
    var tabs = $$('.tab', tabsRoot);
    var current = html.getAttribute('data-tab');
    if (!panelFor(current) && tabs[0]) current = tabs[0].getAttribute('data-tab');
    activate(current, { history: false });
    tabs.forEach(function (t, i) {
      t.addEventListener('click', function (e) {
        e.preventDefault();
        activate(t.getAttribute('data-tab'));
        var bar = closest(t, '.tab-bar');
        if (bar && bar.getBoundingClientRect().top < 0) bar.scrollIntoView({ block: 'start' });
      });
      t.addEventListener('keydown', function (e) {
        var step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!step) return;
        var next = tabs[(i + step + tabs.length) % tabs.length];
        next.focus();
        activate(next.getAttribute('data-tab'));
        e.preventDefault();
      });
    });
  }

  // ------------------------------------------------------------ reveal
  function reveal(id, opts) {
    var el = id ? d.getElementById(id) : null;
    if (!el) return false;
    if (tabsRoot && panelFor(id)) { activate(id, opts); }
    var panel = closest(el, '.tab-panel');
    if (panel) activate(panel.getAttribute('data-panel'), { history: false });
    var row = closest(el, 'tr.is-filtered') || (el.matches && el.matches('.is-filtered') ? el : null);
    if (row) clearFilter(closest(row, 'table'));
    if (el.classList.contains('is-extra') || closest(el, 'tr.is-extra')) showAllRows(closest(el, 'table'));
    for (var det = closest(el, 'details'); det; det = closest(det.parentElement, 'details')) det.open = true;
    var tp = el.classList.contains('toggle-panel') ? el : closest(el, '.toggle-panel');
    if (tp && tp.hidden) setPanel(tp, true);
    if (el.tagName === 'DETAILS') el.open = true;
    d.dispatchEvent(new CustomEvent('nse:reveal', { detail: { el: el } }));   // e.g. the watchlist opens a card
    el.scrollIntoView({ block: 'start', behavior: opts && opts.smooth ? 'smooth' : 'auto' });
    return true;
  }
  function onHash() {
    var id = decodeURIComponent((location.hash || '').slice(1));
    if (!id) return;
    if (panelFor(id)) { activate(id, { history: false }); return; }
    reveal(id);
  }
  window.addEventListener('hashchange', onHash);

  // ------------------------------------------------------------ tables
  function sortKey(cell, type) {
    if (!cell) return null;
    var raw = cell.getAttribute('data-sort');
    var t = raw !== null ? raw : (cell.textContent || '').trim();
    if (type === 'text') return t.toLowerCase();
    if (type === 'signal') {
      var m = { 'strong buy': 5, 'buy': 4, 'neutral': 3, 'hold': 3, 'sell': 2, 'strong sell': 1 };
      return m[t.toLowerCase()] !== undefined ? m[t.toLowerCase()] : null;
    }
    var n = parseFloat(String(t).replace(/[−–]/g, '-').replace(/[^0-9.\-]/g, ''));
    if (type === 'number') return isNaN(n) ? null : n;
    return isNaN(n) ? t.toLowerCase() : n;
  }
  function sortBy(table, th) {
    var body = table && table.tBodies[0];
    if (!body) return;
    var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
    var type = th.getAttribute('data-sort-type') || 'auto';
    var asc = th.getAttribute('aria-sort') !== 'ascending';
    $$('th', th.parentNode).forEach(function (h) {
      h.removeAttribute('aria-sort'); h.classList.remove('sort-asc', 'sort-desc');
    });
    th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
    th.classList.add(asc ? 'sort-asc' : 'sort-desc');
    var rows = Array.prototype.slice.call(body.rows);
    rows.sort(function (a, b) {
      var x = sortKey(a.cells[idx], type), y = sortKey(b.cells[idx], type);
      if (x === null && y === null) return 0;
      if (x === null) return 1;                  // blanks always last
      if (y === null) return -1;
      if (x < y) return asc ? -1 : 1;
      if (x > y) return asc ? 1 : -1;
      return 0;
    });
    rows.forEach(function (r) { body.appendChild(r); });
    applyLimit(table);
  }
  function applyLimit(table) {
    if (!table) return;
    var limit = parseInt(table.getAttribute('data-show-first') || '0', 10);
    var all = table.classList.contains('show-all-rows') || table.classList.contains('filtering');
    var shown = 0;
    Array.prototype.forEach.call(table.tBodies[0] ? table.tBodies[0].rows : [], function (r) {
      if (r.classList.contains('is-filtered')) return;
      shown += 1;
      r.classList.toggle('is-extra', !!limit && !all && shown > limit);
    });
  }
  function showAllRows(table) {
    if (!table) return;
    table.classList.add('show-all-rows');
    applyLimit(table);
    $$('[data-show-all-for="' + table.id + '"]').forEach(function (b) { b.hidden = true; });
  }
  function filterRows(table, q) {
    if (!table || !table.tBodies[0]) return;
    q = (q || '').trim().toLowerCase();
    table.classList.toggle('filtering', !!q);
    Array.prototype.forEach.call(table.tBodies[0].rows, function (r) {
      r.classList.toggle('is-filtered', !!q && (r.textContent || '').toLowerCase().indexOf(q) === -1);
    });
    applyLimit(table);
  }
  function clearFilter(table) {
    if (!table) return;
    $$('input[data-filter-for="' + table.id + '"]').forEach(function (i) { i.value = ''; });
    filterRows(table, '');
  }
  $$('table.data').forEach(function (t) {
    if (t.hasAttribute('data-sortable')) {
      $$('th[data-sort-type]', t).forEach(function (th) {
        th.tabIndex = 0;
        th.addEventListener('click', function () { sortBy(t, th); });
        th.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sortBy(t, th); }
        });
      });
    }
    applyLimit(t);
  });
  $$('input[data-filter-for]').forEach(function (inp) {
    inp.addEventListener('input', function () { filterRows(d.getElementById(inp.getAttribute('data-filter-for')), inp.value); });
  });
  $$('[data-show-all-for]').forEach(function (b) {
    b.addEventListener('click', function () { showAllRows(d.getElementById(b.getAttribute('data-show-all-for'))); });
  });
  // Older page bodies call these from onclick / onkeyup attributes.
  window.sortTable = function (th) { sortBy(closest(th, 'table'), th); };
  window.filterTable = function () {
    var i = d.getElementById('search');
    filterRows(d.getElementById('mainTable'), i ? i.value : '');
  };

  // ------------------------------------------------------------ charts
  // Each <figure class="chart"> carries its values once (JSON, numbers + a
  // format rule per series); every drawn view (1M / 3M / … ) is a slice of
  // them: data-o (first point), data-n (points), data-lo / data-hi (scale).
  var DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function fmtDay(iso) {
    var p = String(iso).split('-');
    if (p.length !== 3) return String(iso);
    var dt = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
    return DAYS[dt.getUTCDay()] + ' ' + dt.getUTCDate() + ' ' + MONTHS[dt.getUTCMonth()] + ' ' + dt.getUTCFullYear();
  }
  function shortNum(a) {
    var units = [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
    for (var i = 0; i < units.length; i++) {
      if (a >= units[i][0]) return String(+(a / units[i][0]).toFixed(1)) + units[i][1];
    }
    return Math.round(a).toLocaleString('en-US');
  }
  function fmtNum(v, f) {                    // the same rule as svg_charts.Fmt
    if (v === null || v === undefined) return null;
    f = f || {};
    var d = f.d || 0, a = Math.abs(v);
    var body = f.short ? shortNum(a) : a.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
    var sign = v < 0 && Number(a.toFixed(d)) !== 0 ? '−' : (f.sign && v > 0 ? '+' : '');
    return (f.p || '') + sign + body + (f.s || '');
  }
  function chartData(fig) {
    if (fig._data !== undefined) return fig._data;
    var el = $(':scope > .chart-data', fig), data = null;
    try { data = el ? JSON.parse(el.textContent) : null; } catch (e) { data = null; }
    if (data && !data.x && data.xref) {
      var other = d.getElementById(data.xref), od = other ? chartData(other) : null;
      if (od) { data.x = od.x; data.xf = od.xf; }
    }
    fig._data = data;
    return data;
  }
  function initPlot(fig, plot) {
    var data = chartData(fig);
    var xh = $('.xhair', plot);
    if (!data || !data.x || !xh) return;
    var o = +plot.getAttribute('data-o') || 0, n = +plot.getAttribute('data-n') || 0;
    var lo = +plot.getAttribute('data-lo'), hi = +plot.getAttribute('data-hi');
    var bars = plot.getAttribute('data-bars') === '1';
    var px = (plot.getAttribute('data-px') || '').split(',').filter(Boolean).map(Number);   // time-spaced points
    if (!n) return;
    var line = $('.xhair-line', xh), tip = $('.xhair-tip', xh);
    var dots = data.s.filter(function (s) { return !s.nodot; }).map(function (s) {
      var el = d.createElement('i');
      el.className = 'xhair-dot s-' + s.k;
      xh.appendChild(el);
      return { s: s, el: el };
    });
    function off(key) { return fig.classList.contains('hide-' + key); }
    function indexAt(clientX) {
      var r = plot.getBoundingClientRect();
      var f = Math.max(0, Math.min(1, (clientX - r.left) / (r.width || 1)));
      if (px.length === n) {                       // nearest snapshot in time
        var best = 0;
        for (var j = 1; j < n; j++) if (Math.abs(px[j] - f * 100) < Math.abs(px[best] - f * 100)) best = j;
        return best;
      }
      return bars ? Math.min(n - 1, Math.floor(f * n)) : Math.round(f * (n - 1));
    }
    function xPct(i) {
      if (px.length === n) return px[i];
      return bars ? (i + 0.5) / n * 100 : (n <= 1 ? 50 : i / (n - 1) * 100);
    }
    function yPct(v) { return hi === lo ? 50 : Math.max(0, Math.min(100, (hi - v) / (hi - lo) * 100)); }
    var cur = n - 1;
    function show(i, touch) {
      cur = i;
      var g = o + i;
      xh.hidden = false;
      plot.classList.toggle('touch', !!touch);
      var left = xPct(i);
      line.style.left = left + '%';
      dots.forEach(function (dt) {
        var v = dt.s.v[g];
        var hide = v === null || v === undefined || off(dt.s.k);
        dt.el.style.display = hide ? 'none' : '';
        if (!hide) { dt.el.style.left = left + '%'; dt.el.style.top = yPct(v) + '%'; }
      });
      var rows = data.s.filter(function (s) { return !off(s.k) && s.v[g] !== null && s.v[g] !== undefined; })
        .map(function (s) {
          var text = s.lo ? fmtNum(s.lo[g], s.f) + ' – ' + fmtNum(s.v[g], s.f) : fmtNum(s.v[g], s.f);
          return '<div class="r s-' + esc(s.k) + '"><span><i></i>' + esc(s.l) + '</span><span>' + esc(text) + '</span></div>';
        }).join('');
      var label = data.xf === 'day' ? fmtDay(data.x[g]) : data.x[g];
      tip.innerHTML = '<b>' + esc(label) + '</b>' + (rows || '<div class="r"><span>No value this day</span></div>');
      var w = plot.clientWidth, tw = tip.offsetWidth, px = left / 100 * w;
      var x = px + 14;
      if (x + tw > w) x = px - tw - 14;
      if (x < 0) x = Math.max(0, Math.min(w - tw, px - tw / 2));
      tip.style.left = x + 'px';
    }
    function hide() { xh.hidden = true; }
    plot.addEventListener('pointermove', function (e) { show(indexAt(e.clientX), e.pointerType !== 'mouse'); });
    plot.addEventListener('pointerdown', function (e) { show(indexAt(e.clientX), e.pointerType !== 'mouse'); });
    plot.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse') hide(); });
    plot.tabIndex = 0;
    plot.setAttribute('aria-label', 'Chart values: use the left and right arrow keys');
    plot.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        show(Math.max(0, Math.min(n - 1, cur + (e.key === 'ArrowRight' ? 1 : -1))));
        e.preventDefault();
      } else if (e.key === 'Home' || e.key === 'End') {
        show(e.key === 'Home' ? 0 : n - 1); e.preventDefault();
      } else if (e.key === 'Escape') { hide(); }
    });
    plot.addEventListener('focus', function () { show(cur); });
    plot.addEventListener('blur', hide);
    plot._hide = hide;
  }
  $$('.chart[data-chart]').forEach(function (fig) {
    $$('.chart-tf button', fig).forEach(function (b) {
      b.addEventListener('click', function () {
        var key = b.getAttribute('data-tf');
        $$(':scope > .chart-variant', fig).forEach(function (v) { v.hidden = v.getAttribute('data-tf') !== key; });
        $$('.chart-tf button', fig).forEach(function (o) { o.setAttribute('aria-pressed', o === b ? 'true' : 'false'); });
      });
    });
    $$('.lg-toggle', fig).forEach(function (b) {
      b.addEventListener('click', function () {
        var on = b.getAttribute('aria-pressed') !== 'false';
        b.setAttribute('aria-pressed', on ? 'false' : 'true');
        fig.classList.toggle('hide-' + b.getAttribute('data-series'), on);
      });
    });
    $$('.chart-plot', fig).forEach(function (p) { initPlot(fig, p); });
  });
  // A tap anywhere else puts away a pinned (touch) tooltip.
  d.addEventListener('pointerdown', function (e) {
    if (e.pointerType === 'mouse') return;
    $$('.chart-plot.touch').forEach(function (p) { if (!p.contains(e.target) && p._hide) p._hide(); });
  });

  // ------------------------------------------------------------ hover tips (mouse only)
  var tipEl = d.getElementById('hovertip');
  if (tipEl) {
    var place = function (e) {
      var x = e.clientX + 16, y = e.clientY + 16, w = tipEl.offsetWidth, h = tipEl.offsetHeight;
      if (x + w > window.innerWidth - 10) x = e.clientX - w - 16;
      if (y + h > window.innerHeight - 10) y = e.clientY - h - 16;
      tipEl.style.left = Math.max(6, x) + 'px';
      tipEl.style.top = Math.max(6, y) + 'px';
    };
    d.addEventListener('pointerover', function (e) {
      if (e.pointerType !== 'mouse') return;      // touch: a tap opens the link instead
      var el = closest(e.target, '[data-tip], [data-tip-ref]');
      if (!el) { tipEl.style.display = 'none'; return; }
      var content = el.getAttribute('data-tip');
      if (content === null) {
        var ref = d.getElementById(el.getAttribute('data-tip-ref'));
        content = ref ? ref.innerHTML : '';
      }
      if (!content) { tipEl.style.display = 'none'; return; }
      tipEl.innerHTML = content;               // built and escaped by the page generator
      tipEl.style.display = 'block';
      place(e);
    });
    d.addEventListener('pointermove', function (e) {
      if (e.pointerType === 'mouse' && tipEl.style.display === 'block') place(e);
    });
    window.addEventListener('scroll', function () { tipEl.style.display = 'none'; }, { passive: true });
  }

  // ------------------------------------------------------------ ⓘ explanations
  var nativePopover = typeof HTMLElement !== 'undefined' && HTMLElement.prototype.hasOwnProperty('popover');
  var lastTrigger = null;
  function placePop(pop, btn) {
    if (!pop || !btn || window.innerWidth <= 640) return;     // phones: a bottom sheet (CSS)
    var r = btn.getBoundingClientRect();
    pop.style.position = 'fixed';
    var w = Math.min(340, window.innerWidth - 32);
    var left = Math.max(8, Math.min(r.left - 16, window.innerWidth - w - 8));
    var top = r.bottom + 8;
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
    var h = pop.offsetHeight;
    if (top + h > window.innerHeight - 8) pop.style.top = Math.max(8, r.top - h - 8) + 'px';
  }
  d.addEventListener('click', function (e) {
    var b = closest(e.target, '[popovertarget]');
    if (b) {
      var pop = d.getElementById(b.getAttribute('popovertarget'));
      lastTrigger = b;
      if (!nativePopover && pop) {
        e.preventDefault();
        var open = !pop.classList.contains('open');
        $$('.pop.open').forEach(function (p) { p.classList.remove('open'); });
        if (open) { pop.classList.add('open'); placePop(pop, b); }
      }
      return;
    }
    if (!nativePopover && !closest(e.target, '.pop')) $$('.pop.open').forEach(function (p) { p.classList.remove('open'); });
  });
  if (nativePopover) {
    d.addEventListener('toggle', function (e) {
      if (e.target.classList && e.target.classList.contains('pop') && e.newState === 'open') placePop(e.target, lastTrigger);
    }, true);
  }

  // ------------------------------------------------------------ panels opened by a button
  // <button data-toggle-panel="id"> shows / hides <div id="id" hidden>.
  function setPanel(panel, open) {
    if (!panel) return;
    panel.hidden = !open;
    $$('[data-toggle-panel="' + panel.id + '"]').forEach(function (b) { b.setAttribute('aria-expanded', open ? 'true' : 'false'); });
  }
  d.addEventListener('click', function (e) {
    var b = closest(e.target, '[data-toggle-panel]');
    if (!b) return;
    var panel = d.getElementById(b.getAttribute('data-toggle-panel'));
    if (!panel) return;
    var open = panel.hidden;
    setPanel(panel, open);
    if (open) panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });

  // ------------------------------------------------------------ card lists (the watchlist)
  // A card opens in place, full width: <button data-expand="cardId"> in
  // <article class="… " id="cardId">. Lists sort by data-k-<key>, filter by
  // data-tags, and switch between views (cards / table) — remembered.
  function setExpanded(card, open) {
    if (!card) return;
    card.classList.toggle('expanded', open);
    $$('[data-expand="' + card.id + '"]').forEach(function (b) { b.setAttribute('aria-expanded', open ? 'true' : 'false'); });
  }
  d.addEventListener('click', function (e) {
    var face = closest(e.target, '[data-expand]');
    if (!face) return;
    var card = d.getElementById(face.getAttribute('data-expand'));
    if (!card) return;
    var open = !card.classList.contains('expanded');
    setExpanded(card, open);
    if (open) setTimeout(function () { card.scrollIntoView({ block: 'start', behavior: 'smooth' }); }, 30);
  });
  function sortCards(grid, key) {
    if (!grid) return;
    var cards = $$(':scope > [data-k-order]', grid);
    var desc = key === 'move' || key === 'score';
    cards.sort(function (a, b) {
      var x = a.getAttribute('data-k-' + key), y = b.getAttribute('data-k-' + key);
      if (key !== 'name') { x = +x; y = +y; } else { x = x.toLowerCase(); y = y.toLowerCase(); }
      if (x !== y) return (x < y ? -1 : 1) * (desc ? -1 : 1);
      return +a.getAttribute('data-k-order') - +b.getAttribute('data-k-order');
    });
    cards.forEach(function (c) { grid.appendChild(c); });
  }
  $$('[data-sort-cards]').forEach(function (sel) {
    sel.addEventListener('change', function () { sortCards(d.getElementById(sel.getAttribute('data-sort-cards')), sel.value); });
  });
  function filterCards(gridId, tag) {
    var grid = d.getElementById(gridId);
    if (!grid) return;
    $$('[data-filter-cards="' + gridId + '"]').forEach(function (o) { o.setAttribute('aria-pressed', o.getAttribute('data-filter') === tag ? 'true' : 'false'); });
    $$(':scope > [data-tags]', grid).forEach(function (c) {
      var tags = ' ' + c.getAttribute('data-tags') + ' ';
      c.classList.toggle('is-filtered', tag !== 'all' && tags.indexOf(' ' + tag + ' ') === -1);
    });
  }
  $$('[data-filter-cards]').forEach(function (b) {
    b.addEventListener('click', function () { filterCards(b.getAttribute('data-filter-cards'), b.getAttribute('data-filter')); });
  });
  function setView(group, name) {
    $$('[data-view-of="' + group + '"]').forEach(function (el) { el.hidden = el.getAttribute('data-view-name') !== name; });
    $$('[data-view-switch="' + group + '"]').forEach(function (o) { o.setAttribute('aria-pressed', o.getAttribute('data-view') === name ? 'true' : 'false'); });
    store('nse-view-' + group, name);
  }
  var viewGroups = {};
  $$('[data-view-switch]').forEach(function (b) {
    var g = b.getAttribute('data-view-switch');
    if (!(g in viewGroups)) viewGroups[g] = b.getAttribute('data-view');      // the first is the default
    b.addEventListener('click', function () { setView(g, b.getAttribute('data-view')); });
  });
  Object.keys(viewGroups).forEach(function (g) {
    var saved = store('nse-view-' + g);
    setView(g, saved && $('[data-view-switch="' + g + '"][data-view="' + saved + '"]') ? saved : viewGroups[g]);
  });
  d.addEventListener('nse:reveal', function (e) {
    var el = e.detail.el, card = el.matches && el.matches('[data-k-order]') ? el : closest(el, '[data-k-order]');
    if (!card) return;
    var view = closest(card, '[data-view-of]');
    if (view && view.hidden) setView(view.getAttribute('data-view-of'), view.getAttribute('data-view-name'));
    if (card.classList.contains('is-filtered') && card.parentNode.id) filterCards(card.parentNode.id, 'all');
    if (card.querySelector('[data-expand]')) setExpanded(card, true);
  });
  // In-page links (alert chips, "Open ›") go through reveal(), so a hidden
  // target is shown first — even when the URL already has that #hash.
  d.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    var a = closest(e.target, 'a[href^="#"]');
    if (!a || a.classList.contains('tab')) return;
    var id = decodeURIComponent(a.getAttribute('href').slice(1));
    if (!id || !d.getElementById(id)) return;
    e.preventDefault();
    try { history.replaceState(null, '', '#' + id); } catch (err) { /* file:// */ }
    reveal(id, { smooth: true });
  });

  // ------------------------------------------------------------ navigation drawer & toggles
  d.addEventListener('click', function (e) {
    if (closest(e.target, '[data-nav-toggle]')) { html.classList.toggle('nav-open'); return; }
    if (closest(e.target, '.nav-scrim')) { html.classList.remove('nav-open'); return; }
    if (closest(e.target, '[data-theme-toggle]')) { toggleTheme(); return; }
    if (closest(e.target, '[data-hide-toggle]')) { toggleHide(); return; }
  });
  d.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    html.classList.remove('nav-open');
    if (!nativePopover) $$('.pop.open').forEach(function (p) { p.classList.remove('open'); });
  });

  // ------------------------------------------------------------ print: show everything
  var reopened = [];
  window.addEventListener('beforeprint', function () {
    reopened = $$('details:not([open])');
    reopened.forEach(function (x) { x.open = true; });
  });
  window.addEventListener('afterprint', function () {
    reopened.forEach(function (x) { x.open = false; });
    reopened = [];
  });

  // ------------------------------------------------------------ start
  var top = $('.topbar');
  if (top) window.addEventListener('scroll', function () { top.classList.toggle('stuck', window.scrollY > 4); }, { passive: true });
  syncToggles();
  window.NSE = { reveal: reveal, activateTab: activate, showAllRows: showAllRows, clearFilter: clearFilter };
  if (location.hash) {
    var first = decodeURIComponent(location.hash.slice(1));
    if (panelFor(first)) {
      // A tab link opens at the top with the tab bar in view — the browser
      // jumps to the panel by itself, so undo that now and once more after
      // load (it can jump again then).
      window.scrollTo(0, 0);
      window.addEventListener('load', function () { if (window.scrollY < 2000) window.scrollTo(0, 0); });
    } else {
      onHash();
    }
  }
})();
