/* ui-kit.js: shared front-end helpers for the shell and every plugin (WP-35); exposes window.GSUI.
 * index.html loads it before any plugin. No dependency, no build step. Markup classes are in ui/components.css,
 * values in ui/tokens.css. Helpers return HTML strings or act on an element passed in, using only className /
 * textContent / innerHTML / disabled / title / style, so they also run inside the plugin harnesses' fake DOMs.
 *
 * Rules the helpers enforce (docs/dashboard-platform/ux-guide.md):
 *   - every caught error goes through GSUI.report (console + toast, returns the text for an in-panel line);
 *   - every request has a timeout (fetchJson) and every poll backs off after repeated failures (poller);
 *   - a disabled control says why next to it (setDisabled); risky actions use twoClick, never window.confirm. */
(function (root) {
  'use strict';

  var STATUSES = ['ok', 'warn', 'fail', 'stale', 'info'];
  var GET_TIMEOUT_MS = 5000;
  var POST_TIMEOUT_MS = 10000;
  var TOAST_MS = 6000;
  var TOAST_FAIL_MS = 10000;
  var TOAST_MAX = 4;
  var THEME_KEY = 'gs_theme';

  function doc() { return typeof document !== 'undefined' ? document : null; }

  function esc(v) {
    if (v === undefined || v === null) return '';
    return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function normStatus(s) { return STATUSES.indexOf(s) >= 0 ? s : 'stale'; }

  // CSS colour of a status, for code that must set a colour directly (banner background, canvas, SVG)
  function color(status) { return 'var(--gs-' + normStatus(status) + ')'; }

  function toggleClass(el, cls, on) {
    if (!el) return;
    var list = String(el.className || '').split(/\s+/).filter(Boolean);
    var i = list.indexOf(cls);
    if (on === undefined) on = i < 0;
    if (on && i < 0) list.push(cls);
    if (!on && i >= 0) list.splice(i, 1);
    el.className = list.join(' ');
  }

  // replace any <base>--<status> modifier on el with the one for status
  function setStatusClass(el, base, status) {
    if (!el) return;
    STATUSES.forEach(function (s) { toggleClass(el, base + '--' + s, false); });
    toggleClass(el, base, true);
    toggleClass(el, base + '--' + normStatus(status), true);
  }

  // ── markup builders ─────────────────────────────────────────────────────────────────────────────────
  function pill(status, text, title, id) {
    var s = normStatus(status);
    return '<span' + (id ? ' id="' + esc(id) + '"' : '') + ' class="gs-pill gs-pill--' + s + '" data-status="' + s + '"' +
      (title ? ' title="' + esc(title) + '"' : '') + '>' + esc(text) + '</span>';
  }

  // update an existing pill element in place (no innerHTML churn on every poll)
  function setPill(el, status, text, title) {
    if (!el) return;
    setStatusClass(el, 'gs-pill', status);
    el.textContent = text;
    if (title !== undefined) el.title = title;
  }

  function empty(text, hint) {
    return '<div class="gs-empty">' + esc(text) +
      (hint ? '<span class="gs-empty__hint">' + esc(hint) + '</span>' : '') + '</div>';
  }

  // columns: [{ key, label, render(row) -> trusted HTML }]; opts: { empty, emptyHint, rowClass(row) }
  function tableRows(columns, rows, opts) {
    opts = opts || {};
    if (!rows || !rows.length) {
      return '<tr><td colspan="' + columns.length + '">' + empty(opts.empty || 'No rows', opts.emptyHint) + '</td></tr>';
    }
    return rows.map(function (r) {
      var cls = opts.rowClass ? opts.rowClass(r) : '';
      return '<tr' + (cls ? ' class="' + esc(cls) + '"' : '') + '>' + columns.map(function (c) {
        return '<td>' + (c.render ? c.render(r) : esc(r[c.key])) + '</td>';
      }).join('') + '</tr>';
    }).join('');
  }

  function table(columns, rows, opts) {
    opts = opts || {};
    return '<table class="gs-table"><thead><tr>' +
      columns.map(function (c) { return '<th>' + esc(c.label) + '</th>'; }).join('') + '</tr></thead>' +
      '<tbody' + (opts.bodyId ? ' id="' + esc(opts.bodyId) + '"' : '') + '>' + tableRows(columns, rows, opts) +
      '</tbody></table>';
  }

  // ── freshness: age of the last frame ─────────────────────────────────────────────────────────────────
  function fmtAge(ms) {
    if (ms === null || ms === undefined || !isFinite(ms)) return 'no data';
    if (ms < 0) ms = 0;
    if (ms < 10000) return (ms / 1000).toFixed(1) + ' s';
    if (ms < 120000) return Math.round(ms / 1000) + ' s';
    return Math.round(ms / 60000) + ' min';
  }

  // ok below warnMs, warn below staleMs, stale from staleMs on or when there is no frame at all
  function ageStatus(ms, warnMs, staleMs) {
    if (ms === null || ms === undefined || !isFinite(ms)) return 'stale';
    if (ms < (warnMs || 1000)) return 'ok';
    if (ms < (staleMs || 3000)) return 'warn';
    return 'stale';
  }

  function staleMark(ms, warnMs, staleMs) {
    return '<span class="gs-age gs-age--' + ageStatus(ms, warnMs, staleMs) +
      '" title="age of the last frame">' + esc(fmtAge(ms)) + '</span>';
  }

  // ── controls ─────────────────────────────────────────────────────────────────────────────────────────
  // reason '' enables; any other text disables and is shown in reasonEl (and the tooltip)
  function setDisabled(btn, reason, reasonEl) {
    if (!btn) return;
    if (btn._gsTitle === undefined) btn._gsTitle = btn.title || '';
    btn.disabled = !!reason;
    btn.title = reason ? 'Disabled: ' + reason : btn._gsTitle;
    if (reasonEl) reasonEl.textContent = reason || '';
  }

  // Two-click confirm for actions that add risk (grant access, bench mode, resets); Abort / Land / Pause
  // never use it. The first click arms the button ("Confirm <label>?") for windowMs, a second click inside
  // the window confirms. State lives on the button, so handlers that decide per click can call
  // confirmClick(btn) directly: true = this click confirms, false = the button is now armed.
  function disarmClick(btn) {
    var st = btn && btn._gsConfirm;
    if (!st) return;
    if (st.timer !== null) clearTimeout(st.timer);
    st.timer = null;
    st.until = 0;
    if (st.label !== null) btn.innerHTML = st.label;
    st.label = null;
    toggleClass(btn, 'is-armed', false);
  }

  function isArmed(btn) {
    var st = btn && btn._gsConfirm;
    return !!(st && st.until && Date.now() < st.until);
  }

  function confirmClick(btn, opts) {
    opts = opts || {};
    var windowMs = opts.windowMs || 5000;
    if (isArmed(btn)) { disarmClick(btn); return true; }
    var st = btn._gsConfirm || (btn._gsConfirm = { until: 0, timer: null, label: null });
    st.label = btn.innerHTML;
    st.until = Date.now() + windowMs;
    btn.textContent = opts.armedLabel || ('Confirm ' + (btn.textContent || 'action') + '?');
    toggleClass(btn, 'is-armed', true);
    st.timer = setTimeout(function () { disarmClick(btn); }, windowMs);
    return false;
  }

  // twoClick(btn, onConfirm[, {windowMs, armedLabel, onArm}]): binds the click handler
  function twoClick(btn, onConfirm, opts) {
    opts = opts || {};
    function onClick(ev) {
      if (!btn || btn.disabled) return;
      if (confirmClick(btn, opts)) { onConfirm(ev); return; }
      if (opts.onArm) opts.onArm(opts.windowMs || 5000);
    }
    if (btn && btn.addEventListener) btn.addEventListener('click', onClick);
    return {
      click: onClick,
      disarm: function () { disarmClick(btn); },
      isArmed: function () { return isArmed(btn); }
    };
  }

  // ── toast + the one error path ───────────────────────────────────────────────────────────────────────
  function toast(text, status, ms) {
    var d = doc();
    if (!d || typeof d.createElement !== 'function' || !d.body) return null;
    var s = normStatus(status || 'info');
    var box = d.getElementById('gs-toasts');
    if (!box) {
      box = d.createElement('div');
      box.id = 'gs-toasts';
      box.setAttribute('role', 'status');
      box.setAttribute('aria-live', 'polite');
      d.body.appendChild(box);
    }
    while (box.children && box.children.length >= TOAST_MAX) box.removeChild(box.children[0]);
    var el = d.createElement('div');
    el.className = 'gs-toast gs-toast--' + s;
    el.textContent = text;
    box.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); },
      ms || (s === 'fail' ? TOAST_FAIL_MS : TOAST_MS));
    return el;
  }

  var _warned = {};
  // report(where, err[, {toast:false, once:key}]) -> 'where: message'; logs it and shows a red toast
  function report(where, err, opts) {
    opts = opts || {};
    var msg = err && err.message ? err.message : String(err);
    var text = where ? where + ': ' + msg : msg;
    if (opts.once) {
      if (_warned[opts.once]) return text;
      _warned[opts.once] = true;
    }
    if (typeof console !== 'undefined' && console.warn) console.warn('[gs] ' + text);
    if (opts.toast !== false) toast(text, 'fail');
    return text;
  }

  // localStorage that never throws: a blocked storage is reported once, then the default is used
  function store(key, value) {
    try {
      if (typeof localStorage === 'undefined') return null;
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
      return value;
    } catch (e) {
      report('localStorage', e, { toast: false, once: 'storage' });
      return null;
    }
  }

  // ── fetch + poll ─────────────────────────────────────────────────────────────────────────────────────
  // fetchJson(url[, {method, headers, body, json, timeoutMs}]) resolves the JSON body; a non-2xx reply, a
  // network error or no reply within timeoutMs rejects with the server's error text or a plain reason.
  function fetchJson(url, opts) {
    opts = opts || {};
    var init = {};
    if (opts.method) init.method = opts.method;
    if (opts.headers) init.headers = opts.headers;
    if (opts.body !== undefined) init.body = opts.body;
    if (opts.json !== undefined) {
      init.method = init.method || 'POST';
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(opts.json);
    }
    var timeoutMs = opts.timeoutMs || (init.method && init.method !== 'GET' ? POST_TIMEOUT_MS : GET_TIMEOUT_MS);
    var ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    if (ctrl) init.signal = ctrl.signal;
    var timer = null;
    var timeout = new Promise(function (resolve, reject) {
      timer = setTimeout(function () {
        if (ctrl) ctrl.abort();
        reject(new Error('no reply in ' + Math.round(timeoutMs / 1000) + ' s'));
      }, timeoutMs);
    });
    var req = fetch(url, init).then(function (r) {
      return r.json().catch(function () { return { error: 'HTTP ' + r.status }; }).then(function (data) {
        if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
        return data;
      });
    });
    return Promise.race([req, timeout]).then(
      function (data) { clearTimeout(timer); return data; },
      function (e) { clearTimeout(timer); throw e; });
  }

  // poller(fn, {intervalMs, backoffAfter, maxBackoff}): runs fn (returns a promise) every intervalMs on one
  // setInterval; never two runs in flight; from the backoffAfter-th failure in a row the gap doubles up to
  // maxBackoff x intervalMs. fn handles its own display and rethrows so the poller sees the failure.
  function poller(fn, opts) {
    opts = opts || {};
    var after = opts.backoffAfter || 2;
    var maxSkip = opts.maxBackoff || 8;
    var fails = 0, skip = 0, busy = false, id = null;
    function run() {
      if (busy) return;
      busy = true;
      var p;
      try { p = Promise.resolve(fn()); } catch (e) { p = Promise.reject(e); }
      p.then(function () { fails = 0; skip = 0; }, function () {
        fails++;
        if (fails >= after) skip = Math.min(Math.pow(2, fails - after + 1), maxSkip) - 1;
      }).then(function () { busy = false; });
    }
    function tick() {
      if (skip > 0) { skip--; return; }
      run();
    }
    id = setInterval(tick, opts.intervalMs || 1000);
    return {
      now: run,
      stop: function () { if (id !== null) { clearInterval(id); id = null; } },
      failures: function () { return fails; }
    };
  }

  // ── keyboard shortcut with a focus guard ─────────────────────────────────────────────────────────────
  function isTypingTarget(t) {
    if (!t) return false;
    var tag = t.tagName ? String(t.tagName).toUpperCase() : '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable) return true;
    return !!(typeof t.closest === 'function' && t.closest('.xterm, [data-gs-no-shortcuts]'));
  }

  // shortcut('p', fn): plain key only (no Ctrl/Alt/Meta, no auto-repeat), ignored while typing in a field or
  // the terminal. Returns the function that removes it.
  function shortcut(key, fn) {
    var d = doc();
    if (!d || typeof d.addEventListener !== 'function') return function () {};
    key = String(key).toLowerCase();
    function onKey(ev) {
      if (!ev || ev.ctrlKey || ev.metaKey || ev.altKey || ev.repeat) return;
      if (String(ev.key || '').toLowerCase() !== key) return;
      if (isTypingTarget(ev.target) || isTypingTarget(d.activeElement)) return;
      if (ev.preventDefault) ev.preventDefault();
      fn(ev);
    }
    d.addEventListener('keydown', onKey);
    return function () { if (typeof d.removeEventListener === 'function') d.removeEventListener('keydown', onKey); };
  }

  // ── theme: <html data-theme>, dark by default ────────────────────────────────────────────────────────
  function htmlEl() { var d = doc(); return d && d.documentElement && d.documentElement.setAttribute ? d.documentElement : null; }
  function getTheme() { var h = htmlEl(); return (h && h.getAttribute('data-theme')) === 'light' ? 'light' : 'dark'; }
  function setTheme(t) {
    t = t === 'light' ? 'light' : 'dark';
    var h = htmlEl();
    if (h) h.setAttribute('data-theme', t);
    store(THEME_KEY, t);
    return t;
  }
  function initTheme() { var h = htmlEl(); if (h) h.setAttribute('data-theme', store(THEME_KEY) === 'light' ? 'light' : 'dark'); }

  // ── campaign safety actions, shared by the flight strip and the Campaign panel ───────────────────────
  // A run accepts pause / land / abort only while its runner thread lives (campaign_api.py state()).
  var CAMPAIGN_ACTIVE = ['running', 'waiting_for_go'];
  function campaignActive(state) { return !!state && CAMPAIGN_ACTIVE.indexOf(state.status) >= 0; }
  function campaignCommand(cmd) {
    return fetchJson('/api/campaign/' + cmd, { method: 'POST', json: { source: 'operator' } });
  }

  root.GSUI = {
    version: 1,
    STATUSES: STATUSES,
    esc: esc, color: color, toggleClass: toggleClass, setStatusClass: setStatusClass,
    pill: pill, setPill: setPill, empty: empty, table: table, tableRows: tableRows,
    fmtAge: fmtAge, ageStatus: ageStatus, staleMark: staleMark,
    setDisabled: setDisabled, twoClick: twoClick, confirmClick: confirmClick, disarmClick: disarmClick,
    toast: toast, report: report, store: store,
    fetchJson: fetchJson, poller: poller,
    isTypingTarget: isTypingTarget, shortcut: shortcut,
    getTheme: getTheme, setTheme: setTheme, initTheme: initTheme,
    campaignActive: campaignActive, campaignCommand: campaignCommand
  };
  initTheme();
})(typeof window !== 'undefined' ? window : this);
