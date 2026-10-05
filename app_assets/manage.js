/*
 * Dashboard app UI — loaded by every dashboard page, but it only does anything
 * when the page is served by the local dashboard app (app.py). Opened any
 * other way (from disk, or the Docker nginx) this file simply doesn't load and
 * every .app-only control stays hidden.
 *
 * Adds: the "App running · 🔄 Update" pill, ☆ watch buttons, the watchlist
 * search/add/edit/remove controls, the "Record a purchase" form and the
 * "Your entries" list. All data is inserted with textContent — never as HTML.
 */
(function () {
  'use strict';

  var root = document.documentElement;
  var TOKEN = null;
  var GEN = null;                 // update "generation" when this page was loaded
  var WATCHING = {};              // "NSE:EQTY" -> true
  var lastStatus = null;
  var pollTimer = null;
  var reloadBannerShown = false;
  var PAGE = (location.pathname.split('/').pop() || 'index.html');

  // ------------------------------------------------------------------ helpers
  function h(tag, props, kids) {
    var e = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (k) {
        var v = props[k];
        if (v === null || v === undefined || v === false) return;
        if (k === 'class') e.className = v;
        else if (k === 'text') e.textContent = v;
        else if (k.indexOf('on') === 0) e.addEventListener(k.slice(2), v);
        else e.setAttribute(k, v === true ? '' : String(v));
      });
    }
    (kids || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return e;
  }

  function api(method, path, body) {
    var opts = { method: method, cache: 'no-store', headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-Dashboard-Token'] = TOKEN || '';
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        return { status: r.status, ok: r.ok, data: data || {} };
      });
    }).catch(function () {
      return { status: 0, ok: false, data: { error: "Can't reach the dashboard app — is its window still open?" } };
    });
  }

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  function fmtMoney(v, cur) {
    v = num(v);
    if (v === null) return '—';
    var d = Math.abs(v) >= 0.1 ? 2 : 4;
    var s = Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
    var sign = v < 0 ? '-' : '';
    if (cur === 'USD') return sign + '$' + s;
    if (!cur || cur === 'KES') return 'KES ' + sign + s;
    return sign + s + ' ' + cur;
  }

  function todayISO() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  function anchorFor(symbol, market) {
    return 'wl-' + (symbol + '-' + market).replace(/[^A-Za-z0-9_.-]/g, '_');
  }

  function markDirty(el, on) {
    if (el) el.setAttribute('data-ma-dirty', on ? '1' : '0');
  }

  function anyDirty() {
    return !!document.querySelector('[data-ma-dirty="1"]');
  }

  // ------------------------------------------------------------------ toasts
  var toastBox = null;
  function toast(message, kind, actions, ms, onDismiss) {
    if (!toastBox) {
      toastBox = h('div', { class: 'ma-toasts', 'aria-live': 'polite' });
      document.body.appendChild(toastBox);
    }
    var t = h('div', { class: 'ma-toast ma-toast-' + (kind || 'info'), role: 'status' }, [h('span', { text: message })]);
    (actions || []).forEach(function (a) {
      t.appendChild(h('button', {
        type: 'button', class: 'ma-toast-btn', text: a.label,
        onclick: function () { t.remove(); a.run(); }
      }));
    });
    t.appendChild(h('button', {
      type: 'button', class: 'ma-toast-x', 'aria-label': 'Dismiss', text: '×',
      onclick: function () { t.remove(); if (onDismiss) onDismiss(); }
    }));
    toastBox.appendChild(t);
    var life = ms === undefined ? 8000 : ms;
    if (life) setTimeout(function () { t.remove(); }, life);
    return t;
  }

  // ---- Undo that survives the page refreshing itself (60 seconds) ----
  var UNDO_KEY = 'ma-undo', UNDO_MS = 60000;
  function rememberUndo(u) {
    u.until = Date.now() + UNDO_MS;
    try { sessionStorage.setItem(UNDO_KEY, JSON.stringify(u)); } catch (e) { /* private mode: in-page only */ }
    showUndo(u);
  }
  function forgetUndo() {
    try { sessionStorage.removeItem(UNDO_KEY); } catch (e) { /* ignore */ }
  }
  function showUndo(u) {
    var left = u.until - Date.now();
    if (left <= 0) { forgetUndo(); return; }
    toast(u.label, 'ok', [{ label: 'Undo', run: function () { forgetUndo(); runUndo(u); } }], left, forgetUndo);
  }
  function runUndo(u) {
    if (u.kind === 'watchlist') undoRemove(u.data);
    else if (u.kind === 'holding') undoHolding(u.data.kind, u.data.removed);
  }
  function restoreUndo() {
    var u = null;
    try { u = JSON.parse(sessionStorage.getItem(UNDO_KEY) || 'null'); } catch (e) { u = null; }
    if (u) showUndo(u);
  }

  function errorText(r) {
    return (r && r.data && r.data.error) || 'Something went wrong — please try again.';
  }

  // ------------------------------------------------------------------ status pill
  var pill = null, pillText = null, pillFill = null, pillBtn = null;
  function buildPill() {
    var host = document.querySelector('.header-actions') || document.querySelector('.header');
    if (!host) return;
    pillText = h('span', { class: 'ma-pill-text', text: 'App running' });
    pillFill = h('i');
    pillBtn = h('button', {
      type: 'button', class: 'ma-pill-btn', text: '🔄 Update',
      title: "Download today's prices and rebuild every page", onclick: onUpdateClick
    });
    pill = h('div', { class: 'ma-pill', role: 'status', 'aria-live': 'polite' }, [
      h('span', { class: 'ma-dot' }), pillText, h('span', { class: 'ma-pill-bar' }, [pillFill]), pillBtn
    ]);
    // Dashboard pages: first in the header's action row. Per-stock pages: after the title.
    if (host.classList.contains('header-actions')) host.insertBefore(pill, host.firstChild);
    else host.appendChild(pill);
  }

  function onUpdateClick() {
    if (lastStatus && (lastStatus.running || (lastStatus.queued || []).length)) {
      toast('An update is already running — it will finish by itself.', 'info');
      return;
    }
    if (!window.confirm("Update the whole dashboard with today's prices?\n\nIt takes about 2–5 minutes. You can keep browsing; the pages refresh when it's done.")) return;
    api('POST', '/api/refresh', { kind: 'full' }).then(function (r) {
      if (!r.ok) { toast(errorText(r), 'error'); return; }
      toast("Updating… you can keep browsing — the pages refresh when it's done.", 'info');
      poll(true);
    });
  }

  function renderStatus(s) {
    if (!pill) return;
    var job = s.job;
    pill.classList.toggle('ma-busy', !!job || (s.queued || []).length > 0);
    pill.classList.toggle('ma-failed', !job && s.last && !s.last.ok);
    if (job) {
      pillText.textContent = job.step + ' ' + job.progress + '%';
      pillFill.style.width = Math.max(3, job.progress) + '%';
      pill.title = (job.kind === 'full' ? 'Updating the whole dashboard' : 'Updating your watchlist') +
        ' — running for ' + Math.round(job.elapsed) + 's';
    } else if ((s.queued || []).length) {
      pillText.textContent = 'Update starting…';
      pillFill.style.width = '3%';
      pill.title = '';
    } else if (s.last && !s.last.ok) {
      pillText.textContent = 'Last update failed — hover for details';
      pill.title = s.last.message;
      pillFill.style.width = '0';
    } else {
      var d = s.dashboard || {};
      pillText.textContent = d.exists ? (d.is_today ? 'Updated ' + d.updated_time : 'Data from ' + d.updated) : 'App running';
      pill.classList.toggle('ma-stale', !!d.exists && !d.is_today);
      pill.title = 'The dashboard app is running — keep its window open while you use these pages.';
      pillFill.style.width = '0';
    }
  }

  function poll(soon) {
    clearTimeout(pollTimer);
    if (soon) { pollTimer = setTimeout(doPoll, 400); return; }
    doPoll();
  }

  function doPoll() {
    api('GET', '/api/status').then(function (r) {
      if (!r.ok) {
        if (pillText) pillText.textContent = 'App not reachable — is its window open?';
        pollTimer = setTimeout(doPoll, 8000);
        return;
      }
      var s = r.data;
      var wasBusy = lastStatus && (lastStatus.running || (lastStatus.queued || []).length);
      lastStatus = s;
      if (GEN === null) GEN = s.generation;
      renderStatus(s);
      var busy = s.running || (s.queued || []).length;
      if (!busy && s.generation > GEN) onUpdated(s);
      else if (!busy && wasBusy && s.last && !s.last.ok) toast(s.last.message, 'error', null, 0);
      pollTimer = setTimeout(doPoll, busy ? 1500 : 5000);
    });
  }

  function onUpdated(s) {
    var kind = s.last ? s.last.kind : 'full';
    GEN = s.generation;
    // A watchlist-only update changes only the watchlist page (+ new per-stock pages).
    if (kind === 'watchlist' && PAGE !== 'watchlist.html') return;
    if (anyDirty()) {
      if (reloadBannerShown) return;
      reloadBannerShown = true;
      toast('This page has been updated — reload to see the changes (save what you were typing first).', 'info',
        [{ label: 'Reload', run: function () { location.reload(); } }], 0);
      return;
    }
    location.reload();
  }

  // ------------------------------------------------------------------ watchlist state + ☆ stars
  function key(market, symbol) { return market + ':' + symbol; }

  function refreshWatching() {
    return api('GET', '/api/watchlist').then(function (r) {
      WATCHING = {};
      if (r.ok) (r.data.entries || []).forEach(function (e) { WATCHING[key(e.market, e.symbol)] = true; });
      return WATCHING;
    });
  }

  function paintStar(b) {
    var on = !!WATCHING[key(b.getAttribute('data-market'), b.getAttribute('data-symbol'))];
    var long = b.getAttribute('data-long') === '1';
    b.classList.toggle('on', on);
    b.textContent = long ? (on ? '★ Watching' : '☆ Watch') : (on ? '★' : '☆');
    b.title = on ? 'On your watchlist — click to remove it' : 'Add ' + b.getAttribute('data-symbol') + ' to your watchlist';
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  }

  function paintAllStars() {
    document.querySelectorAll('.wl-star').forEach(paintStar);
  }

  function addToWatchlist(payload) {
    return api('POST', '/api/watchlist/add', payload).then(function (r) {
      if (r.ok) {
        WATCHING[key(payload.market, payload.symbol)] = true;
        paintAllStars();
        poll(true);
      }
      return r;
    });
  }

  function removeFromWatchlist(symbol, market) {
    return api('POST', '/api/watchlist/remove', { symbol: symbol, market: market }).then(function (r) {
      if (r.ok || r.status === 404) {
        delete WATCHING[key(market, symbol)];
        paintAllStars();
        poll(true);
      }
      return r;
    });
  }

  function undoRemove(removed) {
    var payload = {
      symbol: removed.symbol, market: removed.market, buy_below: removed.buy_below, sell_above: removed.sell_above,
      note: removed.note || '', added: removed.added, added_price: removed.added_price, undo: true
    };
    addToWatchlist(payload).then(function (r) {
      toast(r.ok ? removed.symbol + ' is back on your watchlist.' : errorText(r), r.ok ? 'ok' : 'error');
    });
  }

  function onStarClick(e) {
    var b = e.currentTarget;
    e.preventDefault();
    e.stopPropagation();
    var symbol = b.getAttribute('data-symbol'), market = b.getAttribute('data-market');
    b.disabled = true;
    if (WATCHING[key(market, symbol)]) {
      if (!window.confirm('Remove ' + symbol + ' from your watchlist?')) { b.disabled = false; return; }
      removeFromWatchlist(symbol, market).then(function (r) {
        b.disabled = false;
        if (r.ok) rememberUndo({ kind: 'watchlist', data: r.data.removed, label: r.data.message });
        else toast(errorText(r), 'error');
      });
      return;
    }
    var send = function (force) {
      addToWatchlist({ symbol: symbol, market: market, force: !!force }).then(function (r) {
        b.disabled = false;
        if (r.ok) {
          toast(r.data.message + ' Its card will be ready on the ⭐ Watchlist page in a few seconds.', 'ok',
            [{ label: 'Open Watchlist', run: function () { location.href = 'watchlist.html#' + anchorFor(symbol, market); } }]);
        } else if (r.status === 409) {
          WATCHING[key(market, symbol)] = true; paintAllStars();
          toast(errorText(r), 'info');
        } else if (r.data.offline) {
          toast(errorText(r), 'warn', [{ label: 'Add anyway', run: function () { b.disabled = true; send(true); } }], 0);
        } else {
          toast(errorText(r), 'error');
        }
      });
    };
    send(false);
  }

  function initStars() {
    var stars = document.querySelectorAll('.wl-star');
    if (!stars.length) return;
    stars.forEach(function (b) {
      b.setAttribute('data-long', /Watch/.test(b.textContent) ? '1' : '0');
      b.addEventListener('click', onStarClick);
    });
    refreshWatching().then(paintAllStars);
  }

  // ------------------------------------------------------------------ search box
  function searchBox(opts) {
    var input = h('input', {
      type: 'search', class: 'ma-input ma-search-input', placeholder: opts.placeholder, autocomplete: 'off',
      spellcheck: 'false', 'aria-label': opts.label || opts.placeholder, 'aria-autocomplete': 'list'
    });
    var list = h('div', { class: 'ma-results', role: 'listbox' });
    list.hidden = true;
    var wrap = h('div', { class: 'ma-search' }, [input, list]);
    var timer = null, seq = 0, items = [], rows = [], active = -1;

    function close() { list.hidden = true; active = -1; }
    function setActive(i) {
      if (!rows.length) return;
      active = (i + rows.length) % rows.length;
      rows.forEach(function (r, j) { r.classList.toggle('active', j === active); });
      rows[active].scrollIntoView({ block: 'nearest' });
    }
    function msg(text, cls) { list.appendChild(h('div', { class: 'ma-res-msg ' + (cls || ''), text: text })); }
    function pick(item) {
      close();
      input.value = item.symbol + ' — ' + item.name;
      opts.onPick(item);
    }
    function render(q, data) {
      list.textContent = '';
      items = []; rows = []; active = -1;
      var res = (data.results || []).filter(function (x) { return !opts.market || x.market === opts.market; });
      [['NSE', '🇰🇪 Nairobi Securities Exchange (NSE)'], ['INTL', '🌍 International (Yahoo Finance)']].forEach(function (g) {
        var group = res.filter(function (x) { return x.market === g[0]; });
        if (!group.length) return;
        list.appendChild(h('div', { class: 'ma-res-group', text: g[1] }));
        group.forEach(function (item) {
          var meta = [];
          if (item.market === 'INTL' && item.exchange) meta.push(item.exchange + (item.quote_type === 'ETF' ? ' · ETF' : ''));
          if (item.price !== null && item.price !== undefined) meta.push(fmtMoney(item.price, item.currency));
          var row = h('div', { class: 'ma-res', role: 'option', tabindex: '-1' }, [
            h('strong', { text: item.symbol }), h('span', { class: 'ma-res-name', text: item.name }),
            h('span', { class: 'ma-res-meta', text: meta.join(' · ') }),
            item.watching ? h('span', { class: 'ma-res-tag', text: '★ watching' }) : null
          ]);
          row.addEventListener('mousedown', function (e) { e.preventDefault(); pick(item); });
          items.push(item); rows.push(row);
          list.appendChild(row);
        });
      });
      if (!res.length && !data.error) msg('No matches for “' + q + '”. Try the company name (e.g. Equity, Apple) or its ticker (e.g. EQTY, AAPL).');
      if (data.error) msg(data.error, 'ma-err');
      if (data.intl_error && opts.market !== 'NSE') msg('International search is unavailable right now (' + data.intl_error + ') — check your internet connection.', 'ma-warn-text');
      if (data.nse_stale && opts.market !== 'INTL') msg('Showing NSE companies from an earlier list — today\'s prices load when you pick one.', 'ma-muted');
      list.hidden = false;
    }
    function run(q) {
      var my = ++seq;
      list.textContent = '';
      msg('Searching…', 'ma-muted');
      list.hidden = false;
      api('GET', '/api/search?q=' + encodeURIComponent(q)).then(function (r) {
        if (my !== seq) return;
        render(q, r.ok ? r.data : { results: [], error: errorText(r) });
      });
    }
    input.addEventListener('input', function () {
      clearTimeout(timer);
      if (opts.onType) opts.onType();
      var q = input.value.trim();
      if (!q) { close(); return; }
      timer = setTimeout(function () { run(q); }, 250);
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden && input.value.trim()) run(input.value.trim()); else setActive(active + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        if (!list.hidden && rows.length) pick(items[active >= 0 ? active : 0]);
      } else if (e.key === 'Escape') { close(); }
    });
    input.addEventListener('focus', function () { if (rows.length) list.hidden = false; });
    document.addEventListener('click', function (e) { if (!wrap.contains(e.target)) close(); });
    return { el: wrap, input: input, reset: function () { input.value = ''; close(); list.textContent = ''; rows = []; items = []; } };
  }

  function field(labelText, input, hint) {
    return h('label', { class: 'ma-field' }, [h('span', { class: 'ma-label', text: labelText }), input,
      hint ? h('span', { class: 'ma-hint', text: hint }) : null]);
  }

  function textInput(props) {
    return h('input', Object.assign({ type: 'text', class: 'ma-input', autocomplete: 'off' }, props || {}));
  }

  // ------------------------------------------------------------------ watchlist page
  function initWatchlistPage(panel) {
    var box = h('div', { class: 'ma-confirm' });
    var sb = searchBox({
      placeholder: 'Type a company or ticker — e.g. Equity, EABL, Apple, AAPL…',
      label: 'Search for a stock to watch',
      onPick: function (item) { showWatchConfirm(box, item, sb); },
      onType: function () { box.textContent = ''; markDirty(panel, false); }
    });
    panel.appendChild(h('h2', { text: '➕ Add a stock to your watchlist' }));
    panel.appendChild(h('p', { class: 'ma-help', text: 'Kenyan (NSE) or international — US, London, Johannesburg and more. Pick it from the list, optionally set the prices you\'d buy or sell at, and click Add.' }));
    panel.appendChild(sb.el);
    panel.appendChild(box);
    panel.addEventListener('input', function () { markDirty(panel, !!sb.input.value.trim()); });

    document.querySelectorAll('.wl-btn[data-wl-action]').forEach(function (b) {
      b.addEventListener('click', function () {
        if (b.getAttribute('data-wl-action') === 'edit') openEditor(b);
        else removeCard(b);
      });
    });

    var want = sessionStorage.getItem('ma-scroll');
    if (want) {
      sessionStorage.removeItem('ma-scroll');
      var target = document.getElementById(want);
      if (target) {
        // reveal() opens the card (or tab / section) first, so a card that's
        // filtered out or in the table view still comes into view.
        if (window.NSE && window.NSE.reveal) window.NSE.reveal(want, { smooth: true });
        else target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        target.classList.add('ma-flash');
      }
    }
  }

  function showWatchConfirm(box, item, sb) {
    box.textContent = '';
    var cur = item.market === 'NSE' ? 'KES' : (item.currency || '');
    var badge = item.market === 'NSE' ? '🇰🇪 NSE' : '🌍 ' + (item.exchange || 'International');
    var priceLine = h('div', { class: 'ma-hint', text: item.price !== null && item.price !== undefined ? 'Today: ' + fmtMoney(item.price, cur) : 'Checking today\'s price…' });
    box.appendChild(h('div', { class: 'ma-pick' }, [h('strong', { text: item.symbol }), ' ', h('span', { text: item.name }), ' ', h('span', { class: 'wl-mkt', text: badge })]));
    box.appendChild(priceLine);

    if (item.watching || WATCHING[key(item.market, item.symbol)]) {
      box.appendChild(h('p', { class: 'ma-ok' }, ['★ Already on your watchlist. ', h('a', { href: '#' + anchorFor(item.symbol, item.market), text: 'Jump to it →' })]));
      return;
    }
    var curLabel = function () { return cur ? ' (' + cur + ')' : ''; };
    var buy = textInput({ inputmode: 'decimal', placeholder: 'optional' });
    var sell = textInput({ inputmode: 'decimal', placeholder: 'optional' });
    var note = textInput({ maxlength: '200', placeholder: 'optional — e.g. "waiting for results"' });
    var buyField = field('Tell me when it falls to' + curLabel(), buy, 'Your "buy below" price');
    var sellField = field('…or rises to' + curLabel(), sell, 'Your "sell above" price');
    var err = h('div', { class: 'ma-error', role: 'alert' });
    var btn = h('button', { type: 'submit', class: 'ma-btn ma-btn-primary', text: '⭐ Add to watchlist' });
    var form = h('form', { class: 'ma-form', novalidate: true }, [
      h('div', { class: 'ma-row' }, [buyField, sellField, field('Note', note)]), err, h('div', { class: 'ma-actions' }, [btn])
    ]);
    box.appendChild(form);

    if (item.market === 'INTL') {
      api('GET', '/api/quote?symbol=' + encodeURIComponent(item.symbol) + '&market=INTL').then(function (r) {
        if (r.ok && r.data.found) {
          cur = r.data.currency || cur;
          priceLine.textContent = 'Today: ' + fmtMoney(r.data.price, cur) + (r.data.exchange ? ' · ' + r.data.exchange : '');
          buyField.querySelector('.ma-label').textContent = 'Tell me when it falls to' + curLabel();
          sellField.querySelector('.ma-label').textContent = '…or rises to' + curLabel();
        } else if (r.ok && !r.data.found) {
          priceLine.textContent = r.data.message;
          priceLine.className = 'ma-error';
        } else {
          priceLine.textContent = 'Couldn\'t check today\'s price (' + errorText(r) + ').';
        }
      });
    }

    function submit(force) {
      err.textContent = '';
      btn.disabled = true;
      btn.textContent = 'Adding…';
      addToWatchlist({
        symbol: item.symbol, market: item.market, buy_below: buy.value.trim(), sell_above: sell.value.trim(),
        note: note.value.trim(), force: !!force
      }).then(function (r) {
        btn.disabled = false;
        btn.textContent = '⭐ Add to watchlist';
        if (r.ok) {
          markDirty(box.closest('.app-only') || box, false);
          sb.reset();
          box.textContent = '';
          box.appendChild(h('div', { class: 'ma-progress-note' }, [
            h('span', { class: 'ma-spinner' }),
            r.data.message + ' Building its card with charts — this page refreshes by itself in about 15 seconds.'
          ]));
          sessionStorage.setItem('ma-scroll', anchorFor(item.symbol, item.market));
          return;
        }
        if (r.status === 409) { err.textContent = errorText(r); return; }
        err.textContent = errorText(r);
        if (r.data.offline) {
          err.appendChild(document.createTextNode(' '));
          err.appendChild(h('button', { type: 'button', class: 'ma-btn ma-btn-small', text: 'Add anyway', onclick: function () { submit(true); } }));
        }
        (r.data.suggestions || []).forEach(function (s) {
          err.appendChild(document.createTextNode(' '));
          err.appendChild(h('button', {
            type: 'button', class: 'ma-chip-btn', text: s.symbol + ' — ' + s.name,
            onclick: function () { sb.input.value = s.symbol; showWatchConfirm(box, { symbol: s.symbol, name: s.name, market: s.market, price: null }, sb); }
          }));
        });
      });
    }
    form.addEventListener('submit', function (e) { e.preventDefault(); submit(false); });
  }

  function cardFor(b) { return b.closest('.wl-card'); }

  function openEditor(b) {
    var card = cardFor(b);
    if (!card || card.querySelector('.ma-edit')) return;
    var cur = b.getAttribute('data-currency') || '';
    var buy = textInput({ inputmode: 'decimal', value: b.getAttribute('data-buy') || '', placeholder: 'none' });
    var sell = textInput({ inputmode: 'decimal', value: b.getAttribute('data-sell') || '', placeholder: 'none' });
    var note = textInput({ maxlength: '200', value: b.getAttribute('data-note') || '', placeholder: 'none' });
    var err = h('div', { class: 'ma-error', role: 'alert' });
    var save = h('button', { type: 'submit', class: 'ma-btn ma-btn-primary', text: 'Save' });
    var cancel = h('button', { type: 'button', class: 'ma-btn', text: 'Cancel' });
    var price = b.getAttribute('data-price');
    var form = h('form', { class: 'ma-form ma-edit', novalidate: true }, [
      h('h4', { text: '✏️ Your targets for ' + b.getAttribute('data-symbol') + (price ? ' (today ' + fmtMoney(price, cur) + ')' : '') }),
      h('div', { class: 'ma-row' }, [
        field('Buy if it falls to' + (cur ? ' (' + cur + ')' : ''), buy, 'Leave empty for no target'),
        field('Sell / take profit at' + (cur ? ' (' + cur + ')' : ''), sell, 'Leave empty for no target'),
        field('Note', note)
      ]), err, h('div', { class: 'ma-actions' }, [save, cancel])
    ]);
    markDirty(form, true);
    cancel.addEventListener('click', function () { form.remove(); });
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      err.textContent = '';
      save.disabled = true;
      api('POST', '/api/watchlist/update', {
        symbol: b.getAttribute('data-symbol'), market: b.getAttribute('data-market'),
        buy_below: buy.value.trim(), sell_above: sell.value.trim(), note: note.value.trim()
      }).then(function (r) {
        save.disabled = false;
        if (!r.ok) { err.textContent = errorText(r); return; }
        markDirty(form, false);
        form.textContent = '';
        form.appendChild(h('div', { class: 'ma-progress-note' }, [h('span', { class: 'ma-spinner' }),
          r.data.message + ' Updating the card — this page refreshes by itself in a few seconds.']));
        sessionStorage.setItem('ma-scroll', card.id);
        poll(true);
      });
    });
    card.appendChild(form);
    form.scrollIntoView({ behavior: 'smooth', block: 'center' });
    buy.focus({ preventScroll: true });
  }

  function removeCard(b) {
    var symbol = b.getAttribute('data-symbol'), market = b.getAttribute('data-market');
    if (!window.confirm('Remove ' + symbol + ' from your watchlist?\n\nYou can undo this straight away.')) return;
    b.disabled = true;
    removeFromWatchlist(symbol, market).then(function (r) {
      b.disabled = false;
      if (!r.ok && r.status !== 404) { toast(errorText(r), 'error'); return; }
      var card = cardFor(b);
      if (card) card.classList.add('ma-removed');
      document.querySelectorAll('a[href="#' + anchorFor(symbol, market) + '"]').forEach(function (a) {
        var tr = a.closest('tr');
        if (tr) tr.classList.add('ma-removed');
      });
      if (r.ok) rememberUndo({ kind: 'watchlist', data: r.data.removed, label: r.data.message });
    });
  }

  // ------------------------------------------------------------------ record a purchase
  var KIND_INFO = {
    nse: { tab: '🇰🇪 Kenyan stock (NSE)', market: 'NSE', cur: 'KES' },
    intl: { tab: '🌍 International stock (US$)', market: 'INTL', cur: 'USD' },
    bonds: { tab: '🏦 Government bond', market: null, cur: 'KES' }
  };

  function initPurchasePanel(panel) {
    panel.appendChild(h('h2', { text: '➕ Record a purchase' }));
    panel.appendChild(h('p', { class: 'ma-help', text: 'Bought shares or a bond? Record it here and your portfolio below updates by itself (in about 2 minutes). Made a mistake? Use "📝 Your entries" at the bottom of this page.' }));
    var tabs = h('div', { class: 'ma-tabs', role: 'tablist' });
    var body = h('div', { class: 'ma-tab-body' });
    panel.appendChild(tabs);
    panel.appendChild(body);
    var buttons = {};
    Object.keys(KIND_INFO).forEach(function (kind) {
      var b = h('button', { type: 'button', class: 'ma-tab', role: 'tab', text: KIND_INFO[kind].tab, onclick: function () { show(kind); } });
      buttons[kind] = b;
      tabs.appendChild(b);
    });
    function show(kind) {
      if (anyDirty() && body.getAttribute('data-ma-dirty') === '1' &&
          !window.confirm('Discard what you typed in this form?')) return;
      Object.keys(buttons).forEach(function (k) {
        buttons[k].classList.toggle('active', k === kind);
        buttons[k].setAttribute('aria-selected', k === kind ? 'true' : 'false');
      });
      body.textContent = '';
      markDirty(body, false);
      if (kind === 'bonds') buildBondForm(body); else buildStockForm(body, kind);
    }
    show('nse');
  }

  function purchaseFlow(container, kind, collect, resetForm) {
    var err = h('div', { class: 'ma-error', role: 'alert' });
    var review = h('div', { class: 'ma-review' });
    var reviewBtn = h('button', { type: 'submit', class: 'ma-btn ma-btn-primary', text: 'Review →' });
    var reviewRow = h('div', { class: 'ma-actions' }, [reviewBtn]);
    container.appendChild(err);
    container.appendChild(review);
    container.appendChild(reviewRow);
    function clearReview() { review.textContent = ''; reviewRow.hidden = false; }
    // Changing anything after a review means it has to be reviewed again.
    container.addEventListener('input', function () { if (review.childNodes.length) clearReview(); });

    function send(dry) {
      var payload = collect();
      if (!payload) return Promise.resolve(null);
      payload.kind = kind;
      payload.dry_run = dry;
      return api('POST', '/api/holdings/add', payload);
    }
    container.closest('form').addEventListener('submit', function (e) {
      e.preventDefault();
      err.textContent = '';
      review.textContent = '';
      reviewBtn.disabled = true;
      send(true).then(function (r) {
        reviewBtn.disabled = false;
        if (!r) return;
        if (!r.ok) { err.textContent = errorText(r); return; }
        var warn = (r.data.warnings || []);
        var save = h('button', { type: 'button', class: 'ma-btn ma-btn-primary', text: warn.length ? '✅ Yes, save it anyway' : '✅ Save purchase' });
        var change = h('button', { type: 'button', class: 'ma-btn', text: 'Change something', onclick: clearReview });
        review.appendChild(h('div', { class: 'ma-summary' }, [h('span', { text: '🧾 ' }), h('strong', { text: r.data.summary })]));
        warn.forEach(function (w) { review.appendChild(h('div', { class: 'ma-warn', text: '⚠️ ' + w })); });
        review.appendChild(h('div', { class: 'ma-actions' }, [save, change]));
        reviewRow.hidden = true;
        save.focus();
        save.addEventListener('click', function () {
          save.disabled = true;
          save.textContent = 'Saving…';
          send(false).then(function (r2) {
            if (!r2) return;
            if (!r2.ok) { err.textContent = errorText(r2); clearReview(); return; }
            clearReview();
            markDirty(container.closest('.ma-tab-body'), false);
            resetForm();
            toast(r2.data.message + ' Your portfolio page will update by itself in about 2 minutes.', 'ok', null, 15000);
            loadEntries();
            poll(true);
          });
        });
      });
    });
  }

  function buildStockForm(body, kind) {
    var info = KIND_INFO[kind];
    var picked = null;
    var pickedLine = h('div', { class: 'ma-hint' });
    var qty = textInput({ inputmode: 'decimal', placeholder: kind === 'nse' ? 'e.g. 500' : 'e.g. 10 or 1.5' });
    var price = textInput({ inputmode: 'decimal', placeholder: kind === 'nse' ? 'e.g. 34.50' : 'e.g. 190.25' });
    var date = h('input', { type: 'date', class: 'ma-input', value: todayISO(), max: todayISO() });
    var note = textInput({ maxlength: '200', placeholder: 'optional — e.g. broker' });
    var form = h('form', { class: 'ma-form', novalidate: true });
    var sb = searchBox({
      market: info.market,
      placeholder: kind === 'nse' ? 'Which NSE stock? e.g. Equity, EABL…' : 'Which stock? e.g. Apple, AAPL, MSFT…',
      label: 'Search for the stock you bought',
      onType: function () { picked = null; pickedLine.textContent = ''; },
      onPick: function (item) {
        picked = item;
        pickedLine.className = 'ma-hint';
        pickedLine.textContent = item.price !== null && item.price !== undefined ? 'Today: ' + fmtMoney(item.price, item.currency) : 'Checking…';
        if (kind === 'intl') {
          api('GET', '/api/quote?symbol=' + encodeURIComponent(item.symbol) + '&market=INTL').then(function (r) {
            if (picked !== item) return;
            if (r.ok && r.data.found) {
              if (r.data.currency && r.data.currency !== 'USD') {
                pickedLine.className = 'ma-error';
                pickedLine.textContent = item.symbol + ' is priced in ' + r.data.currency + ' — your international portfolio is tracked in US dollars, so it can\'t be added here. You can add it to your ⭐ watchlist instead.';
              } else {
                pickedLine.textContent = 'Today: ' + fmtMoney(r.data.price, 'USD') + (r.data.exchange ? ' · ' + r.data.exchange : '');
              }
            } else {
              pickedLine.textContent = r.ok ? r.data.message : errorText(r);
            }
          });
        }
        qty.focus();
      }
    });
    form.appendChild(field(kind === 'nse' ? 'Stock' : 'Stock (US-dollar listed)', sb.el));
    form.appendChild(pickedLine);
    form.appendChild(h('div', { class: 'ma-row' }, [
      field('Number of shares', qty),
      field('Price you paid per share (' + info.cur + ')', price, 'Per share — not the total'),
      field('Date you bought', date),
      field('Note', note)
    ]));
    body.appendChild(form);
    form.addEventListener('input', function () { markDirty(body, true); });
    purchaseFlow(form, kind, function () {
      var symbol = picked ? picked.symbol : sb.input.value.trim().split(/\s/)[0].toUpperCase();
      if (!symbol) {
        var e = form.querySelector('.ma-error');
        e.textContent = 'First search for the stock and pick it from the list.';
        sb.input.focus();
        return null;
      }
      return { symbol: symbol, quantity: qty.value.trim(), buy_price: price.value.trim(), buy_date: date.value, note: note.value.trim() };
    }, function () {
      sb.reset(); picked = null; pickedLine.textContent = '';
      qty.value = ''; price.value = ''; date.value = todayISO(); note.value = '';
    });
  }

  function buildBondForm(body) {
    var issue = h('select', { class: 'ma-input' }, [h('option', { value: '', text: 'Loading…' })]);
    var face = textInput({ inputmode: 'decimal', placeholder: 'e.g. 100000' });
    var pct = textInput({ inputmode: 'decimal', value: '100' });
    var date = h('input', { type: 'date', class: 'ma-input', value: todayISO(), max: todayISO() });
    var note = textInput({ maxlength: '200', placeholder: 'optional — e.g. bought via DhowCSD' });
    var form = h('form', { class: 'ma-form', novalidate: true }, [
      h('div', { class: 'ma-row' }, [
        field('Bond issue', issue, 'Only bonds whose terms are on file can be tracked accurately'),
        field('Face value (KES)', face),
        field('Price paid (% of face value)', pct, '100 = bought at par; e.g. 101.33'),
        field('Date you bought', date),
        field('Note', note)
      ])
    ]);
    body.appendChild(form);
    form.addEventListener('input', function () { markDirty(body, true); });
    api('GET', '/api/bonds/issues').then(function (r) {
      issue.textContent = '';
      issue.appendChild(h('option', { value: '', text: 'Choose the bond…' }));
      (r.ok ? r.data.issues : []).forEach(function (b) { issue.appendChild(h('option', { value: b.issue, text: b.label })); });
    });
    purchaseFlow(form, 'bonds', function () {
      if (!issue.value) { form.querySelector('.ma-error').textContent = 'Choose the bond issue first.'; return null; }
      return { issue: issue.value, face_value: face.value.trim(), purchase_price_pct: pct.value.trim(), purchase_date: date.value, note: note.value.trim() };
    }, function () { issue.value = ''; face.value = ''; pct.value = '100'; date.value = todayISO(); note.value = ''; });
  }

  // ------------------------------------------------------------------ your entries
  var entriesPanel = null;
  function initEntriesPanel(panel) {
    entriesPanel = panel;
    var hint = h('span', { class: 'ma-hint', text: '▼ click to show' });
    var details = h('details', { class: 'ma-details' }, [
      h('summary', {}, [h('h2', { text: '📝 Your entries — fix a mistake' }), hint]),
      h('p', { class: 'ma-help', text: 'Every purchase you\'ve recorded, exactly as it is stored in your private files. Typed something wrong? Remove the entry and record it again. A backup copy is saved before anything is removed.' }),
      h('div', { class: 'ma-entries' })
    ]);
    details.addEventListener('toggle', function () {
      hint.textContent = details.open ? '▲ click to hide' : '▼ click to show';
      if (details.open) loadEntries();
    });
    panel.appendChild(details);
  }

  function loadEntries() {
    if (!entriesPanel) return;
    var details = entriesPanel.querySelector('details');
    if (!details || !details.open) return;
    var box = entriesPanel.querySelector('.ma-entries');
    box.textContent = 'Loading…';
    Promise.all(['nse', 'intl', 'bonds'].map(function (k) { return api('GET', '/api/holdings?kind=' + k); })).then(function (rs) {
      box.textContent = '';
      ['nse', 'intl', 'bonds'].forEach(function (kind, i) {
        var r = rs[i];
        box.appendChild(h('h3', { class: 'ma-h3', text: KIND_INFO[kind].tab }));
        if (!r.ok) { box.appendChild(h('div', { class: 'ma-error', text: errorText(r) })); return; }
        renderEntries(box, kind, r.data);
      });
    });
  }

  function renderEntries(box, kind, data) {
    var entries = data.entries || [];
    if (!entries.length) { box.appendChild(h('p', { class: 'ma-hint', text: 'Nothing recorded yet.' })); return; }
    if (!data.editable) {
      box.appendChild(h('p', { class: 'ma-warn', text: 'These are in ' + data.file + ' (the older format). To change one, edit that file — or switch to the CSV format (see portfolio/README.md) to manage them here.' }));
    }
    var bonds = kind === 'bonds', cur = KIND_INFO[kind].cur;
    var head = bonds ? ['Bond', 'Face value', 'Price %', 'Date', 'Note', ''] : ['Stock', 'Shares', 'Price paid', 'Date', 'Note', ''];
    var tbody = h('tbody');
    entries.forEach(function (e) {
      var cells = bonds
        ? [e.issue, fmtMoney(e.face_value, 'KES'), (e.purchase_price_pct === null || e.purchase_price_pct === undefined) ? '100' : String(e.purchase_price_pct), e.purchase_date || '—', e.note || '']
        : [e.symbol, String(e.quantity), fmtMoney(e.buy_price, cur), e.buy_date || '—', e.note || ''];
      var tr = h('tr', {}, cells.map(function (c) { return h('td', { text: c }); }));
      var td = h('td');
      if (data.editable && e.line) {
        td.appendChild(h('button', {
          type: 'button', class: 'ma-btn ma-btn-small ma-btn-danger', text: 'Remove',
          onclick: function (ev) { removeEntry(ev.currentTarget, kind, e); }
        }));
      }
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
    box.appendChild(h('div', { class: 'table-wrap' }, [h('table', { class: 'ma-table' }, [
      h('thead', {}, [h('tr', {}, head.map(function (t) { return h('th', { text: t }); }))]), tbody
    ])]));
  }

  function describe(kind, e) {
    if (kind === 'bonds') return fmtMoney(e.face_value, 'KES') + ' face value of ' + e.issue + (e.purchase_date ? ' (' + e.purchase_date + ')' : '');
    return e.quantity + ' × ' + e.symbol + ' at ' + fmtMoney(e.buy_price, KIND_INFO[kind].cur) + (e.buy_date ? ' (' + e.buy_date + ')' : '');
  }

  function removeEntry(btn, kind, e) {
    if (!window.confirm('Remove this entry?\n\n' + describe(kind, e) + '\n\nA backup copy of the file is kept, and you can undo this straight away.')) return;
    btn.disabled = true;
    var expect = {};
    Object.keys(e).forEach(function (k) { if (k !== 'line') expect[k] = e[k]; });
    api('POST', '/api/holdings/remove', { kind: kind, line: e.line, expect: expect }).then(function (r) {
      if (!r.ok) { btn.disabled = false; toast(errorText(r), 'error', null, 0); loadEntries(); return; }
      rememberUndo({ kind: 'holding', data: { kind: kind, removed: r.data.removed }, label: r.data.message });
      loadEntries();
      poll(true);
    });
  }

  function undoHolding(kind, removed) {
    var payload = Object.assign({ kind: kind, undo: true }, removed);
    api('POST', '/api/holdings/add', payload).then(function (r2) {
      toast(r2.ok ? 'Put back: ' + r2.data.summary + '.' : errorText(r2), r2.ok ? 'ok' : 'error');
      loadEntries();
      poll(true);
    });
  }

  // ------------------------------------------------------------------ start
  function start() {
    api('GET', '/api/ping').then(function (r) {
      root.classList.remove('app-pending');
      if (!r.ok || r.data.app !== 'kenyan-stock-analyzer') return;
      return api('GET', '/api/session').then(function (s) {
        if (!s.ok || !s.data.token) return;
        TOKEN = s.data.token;
        root.classList.add('app-on');
        buildPill();
        initStars();
        var wlPanel = document.getElementById('wl-add-panel');
        if (wlPanel) { refreshWatching().then(function () { initWatchlistPage(wlPanel); }); }
        var buyPanel = document.getElementById('purchase-panel');
        if (buyPanel) initPurchasePanel(buyPanel);
        var entries = document.getElementById('entries-panel');
        if (entries) initEntriesPanel(entries);
        restoreUndo();
        poll();
      });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
