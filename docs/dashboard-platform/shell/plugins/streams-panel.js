/**
 * streams-panel.js - Streams: what the four telemetry slots carry.
 *
 * The 8081 dashboard is the only owner of the drone link. This panel is the
 * one place that decides what slots 1-3 stream (slot 0 is fixed: the
 * dashboard layout depends on it). Everything is served by the ground
 * service (`/api/streams*`, ground_station/service/streams.py); this file is
 * only the view:
 *
 *   - slot table: name, rate, ranges/payload, per-slot + total link budget
 *   - per-slot editor: swap in a preset, or type variables (ELF autocomplete)
 *   - tab health: a tab that lost its data says "not streamed: slot N holds X"
 *     with a one-click Restore
 *   - logging (timed / rolling / unlimited) and a FireWater forward to VOFA+
 *
 * Swaps are allowed only while disarmed; the service refuses otherwise and
 * the controls are disabled to match.
 */
(function () {
  'use strict';

  var POLL_MS = 3000;
  var POLL_BUSY_MS = 800;
  var PLAN_DEBOUNCE_MS = 350;
  var AC_DEBOUNCE_MS = 200;
  var SWAPPABLE = [1, 2, 3];

  var _snap = null;        // last GET /api/streams
  var _presets = [];       // [{name, notes, n_vars, rates}]
  var _presetBody = {};    // name -> {slots:[{rate,vars}]}
  var _drafts = {};        // slot -> {vars:'text', rate:'text', name, source}
  var _open = {};          // slot -> editor open?
  var _plan = null;        // last POST /api/streams/plan for the drafts
  var _msg = '';
  var _msgBad = false;
  var _root = null;
  var _pollTimer = null;
  var _planTimer = null;
  var _acTimer = null;
  var _acSlot = null;
  var _acItems = [];
  var _fwdSeeded = false;
  var _onClick = null, _onInput = null, _onChange = null;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function q(id) { return document.getElementById(id); }

  // ---- HTTP (the service is same-origin) -----------------------------------
  function getJson(url) {
    return fetch(url).then(function (r) { return r.json(); });
  }
  function postJson(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; });
    });
  }

  function setMsg(text, bad) {
    _msg = text || '';
    _msgBad = !!bad;
    var el = q('st-msg');
    if (el) {
      el.textContent = _msg;
      el.className = 'st-msg' + (_msgBad ? ' st-bad' : '');
    }
  }

  // ---- pure helpers ----------------------------------------------------------
  function parseVars(text) {
    return String(text || '').split(/[\s,;]+/).filter(function (s) { return s; });
  }
  function fmtBps(b) {
    b = Number(b) || 0;
    return b >= 1000 ? (b / 1000).toFixed(1) + ' kB/s' : Math.round(b) + ' B/s';
  }
  function fmtRate(hz) {
    var n = Number(hz);
    if (!isFinite(n)) return '-';
    return (Math.round(n * 10) / 10) + ' Hz';
  }
  function bar(frac) {
    var f = Math.max(0, Math.min(1, Number(frac) || 0));
    var cls = f > 0.9 ? 'st-bar-hot' : (f > 0.7 ? 'st-bar-warm' : 'st-bar-ok');
    return '<span class="st-bar"><span class="st-bar-fill ' + cls +
           '" style="width:' + Math.round(f * 100) + '%"></span></span>';
  }
  function canSwap() {
    return !!(_snap && _snap.can_swap && !_snap.active_preset &&
              !(_snap.apply && _snap.apply.busy));
  }
  function gateReason() {
    if (!_snap) return 'loading';
    if (_snap.apply && _snap.apply.busy) return 'a swap is being applied';
    if (_snap.active_preset) return 'a full preset (' + _snap.active_preset + ') is active';
    if (!_snap.can_swap) return 'arm state is ' + _snap.arm_state + ' - swaps only while disarmed';
    return '';
  }
  function slotOf(n) {
    if (!_snap) return null;
    for (var i = 0; i < _snap.slots.length; i++) {
      if (_snap.slots[i].slot === n) return _snap.slots[i];
    }
    return null;
  }
  function planOf(n) {
    var p = _plan || (_snap && _snap.plan);
    if (!p || !p.slots) return null;
    for (var i = 0; i < p.slots.length; i++) {
      if (p.slots[i].slot === n) return p.slots[i];
    }
    return null;
  }
  // The plan the editors describe: slot 0 as it stands + every draft slot.
  function draftAssignments() {
    var out = [];
    (_snap ? _snap.slots : []).forEach(function (s) {
      var d = _drafts[s.slot];
      if (s.slot !== 0 && d) {
        out.push({ slot: s.slot, vars: parseVars(d.vars),
                   rate: parseFloat(d.rate) || 0, name: d.name || 'custom',
                   source: d.source || 'custom' });
      }
    });
    return out;
  }

  // ---- rendering -------------------------------------------------------------
  function renderHead() {
    var el = q('st-head');
    if (!el) return;
    if (!_snap) { el.innerHTML = '<span class="st-muted">loading streams...</span>'; return; }
    var p = _snap.plan || {};
    var frac = p.budget_bps ? (p.total_bps / p.budget_bps) : 0;
    var why = gateReason();
    el.innerHTML =
      '<span class="st-title">Link budget</span> ' + bar(frac) +
      ' <span class="st-num">' + fmtBps(p.total_bps) + ' / ' + fmtBps(p.budget_bps) + '</span>' +
      ' <span class="st-pill">' + esc(_snap.arm_state || '?') + '</span>' +
      (why ? ' <span class="st-warn">' + esc(why) + '</span>' : '') +
      (_snap.apply && _snap.apply.error
        ? ' <span class="st-bad">' + esc(_snap.apply.error) + '</span>' : '');
  }

  function renderTable() {
    var el = q('st-table');
    if (!el || !_snap) return;
    var maxBps = (_snap.plan && _snap.plan.budget_bps) || 1;
    var rows = _snap.slots.map(function (s) {
      var pl = planOf(s.slot) || {};
      var can = canSwap() && !s.fixed;
      var custom = !s.default;
      return '<tr>' +
        '<td class="st-num">' + s.slot + '</td>' +
        '<td>' + esc(s.name || '-') + (s.fixed ? ' <span class="st-pill">fixed</span>' : '') +
          (custom && !s.fixed ? ' <span class="st-pill st-pill-amber">custom</span>' : '') + '</td>' +
        '<td class="st-num">' + fmtRate(s.rate) + '</td>' +
        '<td class="st-num">' + (pl.ranges != null ? pl.ranges : '-') + '/' +
          (pl.max_ranges != null ? pl.max_ranges : '-') + '</td>' +
        '<td class="st-num">' + (pl.payload != null ? pl.payload : '-') + ' B</td>' +
        '<td>' + bar((pl.bps || 0) / maxBps) + ' <span class="st-num">' + fmtBps(pl.bps) + '</span></td>' +
        '<td>' + esc(s.state || '-') + '</td>' +
        '<td class="st-acts">' +
          (s.fixed ? '' :
            '<button class="st-btn" data-act="edit" data-slot="' + s.slot + '"' +
              (can ? '' : ' disabled') + '>Edit</button>' +
            (custom ? ' <button class="st-btn" data-act="restore" data-slot="' + s.slot + '"' +
              (can ? '' : ' disabled') + '>Restore</button>' : '')) +
        '</td></tr>';
    }).join('');
    el.innerHTML =
      '<table class="st-tbl"><thead><tr><th>Slot</th><th>Carries</th><th>Rate</th>' +
      '<th>Ranges</th><th>Payload</th><th>Budget</th><th>State</th><th></th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table>';
  }

  function renderTabs() {
    var el = q('st-tabs');
    if (!el || !_snap) return;
    var tabs = _snap.tabs || [];
    if (!tabs.length) { el.innerHTML = ''; return; }
    el.innerHTML = '<div class="st-title">Tabs</div>' + tabs.map(function (t) {
      if (t.ok) {
        return '<span class="st-chip st-chip-ok">' + esc(t.label) + '</span>';
      }
      var slots = t.restore_slots || [];
      return '<span class="st-chip st-chip-bad" title="' +
        esc((t.missing || []).map(function (m) { return m.var; }).join(', ')) + '">' +
        esc(t.label) + ': ' + esc(t.reason || 'not streamed') +
        (slots.length
          ? ' <button class="st-btn" data-act="restore" data-slot="' + slots.join(',') + '"' +
            (canSwap() ? '' : ' disabled') + '>Restore</button>'
          : '') + '</span>';
    }).join('');
  }

  function presetOptions(selected) {
    var opts = '<option value="">- preset -</option>';
    _presets.forEach(function (p) {
      opts += '<option value="' + esc(p.name) + '"' +
        (p.name === selected ? ' selected' : '') + '>' + esc(p.name) +
        ' (' + p.n_vars + ' vars)</option>';
    });
    return opts;
  }

  function renderEditors() {
    var el = q('st-editors');
    if (!el || !_snap) return;
    var html = '';
    SWAPPABLE.forEach(function (n) {
      if (!_open[n]) return;
      var d = _drafts[n] || { vars: '', rate: '', name: 'custom' };
      var pl = planOf(n);
      var errs = (_plan && _plan.slots) ? (planOf(n) || {}).errors : null;
      html += '<div class="st-editor" data-slot="' + n + '">' +
        '<div class="st-title">Slot ' + n + '</div>' +
        '<div class="st-row">' +
          '<select data-act="preset" data-slot="' + n + '">' + presetOptions(d.presetSel) + '</select>' +
          '<select data-act="part" data-slot="' + n + '"' +
            (d.presetSel ? '' : ' style="display:none"') + '>' + partOptions(d.presetSel, d.part) + '</select>' +
          '<label>Rate Hz <input class="st-in st-in-rate" data-act="rate" data-slot="' + n +
            '" value="' + esc(d.rate) + '"></label>' +
        '</div>' +
        '<textarea id="st-vars-' + n + '" class="st-in st-vars" data-act="vars" data-slot="' + n +
          '" rows="3" placeholder="variables: space, comma or newline separated (ELF names)">' +
          esc(d.vars) + '</textarea>' +
        '<div class="st-ac" id="st-ac-' + n + '"></div>' +
        '<div class="st-row st-stat" id="st-plan-' + n + '">' + planLine(pl, errs) + '</div>' +
        '<div class="st-row">' +
          '<button class="st-btn st-btn-go" data-act="apply" data-slot="' + n + '"' +
            (canSwap() ? '' : ' disabled') + '>Apply</button> ' +
          '<button class="st-btn" data-act="save-preset" data-slot="' + n + '">Save as preset</button> ' +
          '<button class="st-btn" data-act="close" data-slot="' + n + '">Close</button>' +
        '</div></div>';
    });
    el.innerHTML = html;
  }

  function partOptions(name, sel) {
    var body = name && _presetBody[name];
    if (!body) return '';
    return body.slots.map(function (s, i) {
      return '<option value="' + i + '"' + (String(i) === String(sel) ? ' selected' : '') + '>part ' +
        (i + 1) + ' (' + s.vars.length + ' vars @ ' + fmtRate(s.rate) + ')</option>';
    }).join('');
  }

  function planLine(pl, errs) {
    if (!pl) return '<span class="st-muted">no plan yet</span>';
    var bits = [pl.ranges + '/' + pl.max_ranges + ' ranges', pl.payload + ' B',
                'div ' + pl.divider + ' -> ' + fmtRate(pl.rate_real), fmtBps(pl.bps)];
    var out = '<span class="st-num">' + esc(bits.join(' | ')) + '</span>';
    (errs || pl.errors || []).forEach(function (e) {
      out += ' <span class="st-bad">' + esc(e) + '</span>';
    });
    (pl.vars || []).forEach(function (v) {
      if (v && v.ok === false) out += ' <span class="st-bad">' + esc(v.spec) + '?</span>';
    });
    return out;
  }

  function renderLogFwd() {
    if (!_snap) return;
    var lg = _snap.log || {};
    var st = q('st-log-status');
    if (st) {
      st.textContent = lg.active
        ? 'logging ' + (lg.name || '') + ' (' + lg.mode + ') ' + Math.round(lg.elapsed_s || 0) +
          ' s, ' + (lg.rows || 0) + ' rows'
        : 'not logging' + (lg.files && lg.files.length ? ' - last: ' + lg.files[lg.files.length - 1] : '');
    }
    var lgBtn = q('st-log-start'), lgStop = q('st-log-stop');
    if (lgBtn) lgBtn.disabled = !!lg.active;
    if (lgStop) lgStop.disabled = !lg.active;
    var fw = _snap.forward || {};
    var fs = q('st-fwd-status');
    if (fs) {
      fs.textContent = fw.active
        ? 'forwarding ' + (fw.channels || []).length + ' channels to ' + fw.addr + ' (' + (fw.sent || 0) + ' frames)'
        : 'forward off';
    }
    var fb = q('st-fwd-btn');
    if (fb) fb.textContent = fw.active ? 'Stop forward' : 'Forward to VOFA+';
    var ch = q('st-fwd-ch');
    if (ch && !_fwdSeeded && _snap.slots) {
      var names = [];
      _snap.slots.forEach(function (s) {
        if (s.slot !== 0) names = names.concat(s.vars.slice(0, 4));
      });
      ch.value = names.slice(0, 8).join(' ');
      _fwdSeeded = true;
    }
    var mode = q('st-log-mode'), secs = q('st-log-secs');
    if (mode && secs) secs.disabled = (mode.value === 'unlimited');
  }

  function renderDynamic() {
    renderHead();
    renderTable();
    renderTabs();
    renderLogFwd();
  }

  // ---- data loading ------------------------------------------------------------
  function refresh() {
    return getJson('/api/streams').then(function (snap) {
      _snap = snap;
      renderDynamic();
      // Editors only re-render when their gating flips; typing must not be lost.
      var gate = canSwap();
      if (_lastGate !== gate) { _lastGate = gate; renderEditors(); }
      schedulePoll();
    }).catch(function () { schedulePoll(); });
  }
  var _lastGate = null;

  function schedulePoll() {
    if (_pollTimer) clearTimeout(_pollTimer);
    var busy = _snap && _snap.apply && _snap.apply.busy;
    _pollTimer = setTimeout(refresh, busy ? POLL_BUSY_MS : POLL_MS);
  }

  function loadPresets() {
    return getJson('/api/streams/presets').then(function (r) {
      _presets = (r && r.presets) || [];
      renderEditors();
    }).catch(function () {});
  }

  function loadPresetBody(name) {
    if (_presetBody[name]) return Promise.resolve(_presetBody[name]);
    return getJson('/api/streams/presets/' + encodeURIComponent(name)).then(function (b) {
      if (b && b.slots) _presetBody[name] = b;
      return b;
    });
  }

  function schedulePlan() {
    if (_planTimer) clearTimeout(_planTimer);
    _planTimer = setTimeout(runPlan, PLAN_DEBOUNCE_MS);
  }

  function runPlan() {
    if (!_snap) return;
    // Slot 0 stays as-is; a slot being edited uses its draft.
    var slots = _snap.slots.map(function (s) {
      var d = _drafts[s.slot];
      if (s.slot !== 0 && _open[s.slot] && d) {
        return { rate: parseFloat(d.rate) || 0, vars: parseVars(d.vars) };
      }
      return { rate: s.rate, vars: s.vars };
    });
    postJson('/api/streams/plan', { slots: slots }).then(function (r) {
      if (r.status !== 200) return;
      _plan = r.body;
      SWAPPABLE.forEach(function (n) {
        var el = q('st-plan-' + n);
        if (el) el.innerHTML = planLine(planOf(n));
      });
      renderHead();
      renderTable();
    }).catch(function () {});
  }

  // ---- autocomplete (ELF symbols) -------------------------------------------------
  function lastToken(text) {
    var m = /[^\s,;]*$/.exec(text || '');
    return m ? m[0] : '';
  }
  function scheduleAutocomplete(slot) {
    if (_acTimer) clearTimeout(_acTimer);
    _acSlot = slot;
    _acTimer = setTimeout(function () { runAutocomplete(slot); }, AC_DEBOUNCE_MS);
  }
  function runAutocomplete(slot) {
    var d = _drafts[slot];
    var tok = d ? lastToken(d.vars) : '';
    var box = q('st-ac-' + slot);
    if (!box) return;
    if (tok.length < 2) { box.innerHTML = ''; _acItems = []; return; }
    var url = '/api/symbols?limit=12&prefix=' + encodeURIComponent(tok);
    // "parent." / "parent[" drills into members instead of matching a prefix.
    var m = /^(.*?)[.\[]$/.exec(tok);
    if (m && m[1]) url = '/api/symbols?parent=' + encodeURIComponent(m[1]);
    getJson(url).then(function (r) {
      _acItems = ((r && r.names) || []).slice(0, 12);
      box.innerHTML = _acItems.map(function (n, i) {
        return '<button class="st-ac-item" data-act="ac" data-slot="' + slot +
          '" data-i="' + i + '">' + esc(n) + '</button>';
      }).join('');
    }).catch(function () {});
  }
  function pickAutocomplete(slot, i) {
    var d = _drafts[slot];
    var name = _acItems[i];
    if (!d || !name) return;
    d.vars = d.vars.slice(0, d.vars.length - lastToken(d.vars).length) + name + ' ';
    _acItems = [];
    renderEditors();
    var ta = q('st-vars-' + slot);
    if (ta && ta.focus) ta.focus();
    schedulePlan();
  }

  // ---- actions -----------------------------------------------------------------------
  function openEditor(slot) {
    var s = slotOf(slot);
    if (!s) return;
    _open[slot] = true;
    if (!_drafts[slot]) {
      _drafts[slot] = { vars: s.vars.join(' '), rate: String(s.rate),
                        name: s.name, source: s.source || 'custom' };
    }
    renderEditors();
    schedulePlan();
  }

  function applySlot(slot) {
    var d = _drafts[slot];
    if (!d) return;
    var item = { slot: slot, vars: parseVars(d.vars), rate: parseFloat(d.rate) || 0,
                 name: d.name || 'custom', source: d.source || 'custom' };
    setMsg('applying slot ' + slot + '...');
    postJson('/api/streams/apply', { slots: [item] }).then(function (r) {
      if (r.status === 202) {
        setMsg('slot ' + slot + ' applying');
        _open[slot] = false; delete _drafts[slot];
        renderEditors();
      } else {
        setMsg((r.body && r.body.error) || ('apply failed (' + r.status + ')'), true);
      }
      refresh();
    }).catch(function (e) { setMsg('apply failed: ' + e, true); });
  }

  function restoreSlots(list) {
    var slots = list.filter(function (n) { return !isNaN(n); });
    if (!slots.length) return;
    setMsg('restoring slot ' + slots.join(', '));
    postJson('/api/streams/restore', { slots: slots }).then(function (r) {
      if (r.status >= 400) setMsg((r.body && r.body.error) || 'restore failed', true);
      else slots.forEach(function (n) { _open[n] = false; delete _drafts[n]; });
      renderEditors();
      refresh();
    }).catch(function (e) { setMsg('restore failed: ' + e, true); });
  }

  function savePreset(slot) {
    var d = _drafts[slot];
    if (!d) return;
    var name = (typeof window.prompt === 'function') ? window.prompt('Preset name') : '';
    if (!name) return;
    postJson('/api/streams/presets', {
      name: name, notes: '',
      slots: [{ rate: parseFloat(d.rate) || 0, vars: parseVars(d.vars) }]
    }).then(function (r) {
      if (r.status < 300) { setMsg('saved preset ' + name); loadPresets(); }
      else setMsg((r.body && r.body.error) || 'save failed', true);
    });
  }

  function chooseFromPreset(slot, name) {
    var d = _drafts[slot] || (_drafts[slot] = { vars: '', rate: '', name: 'custom' });
    d.presetSel = name || '';
    if (!name) { renderEditors(); return; }
    loadPresetBody(name).then(function (b) {
      if (!b || !b.slots || !b.slots.length) return;
      d.part = 0;
      fillFromPart(slot, name, 0);
    });
  }
  function fillFromPart(slot, name, idx) {
    var b = _presetBody[name];
    var d = _drafts[slot];
    if (!b || !d || !b.slots[idx]) return;
    d.part = idx;
    d.vars = b.slots[idx].vars.join(' ');
    d.rate = String(b.slots[idx].rate);
    d.name = name + (b.slots.length > 1 ? ' #' + (idx + 1) : '');
    d.source = 'preset:' + name;
    renderEditors();
    schedulePlan();
  }

  function startLog() {
    var mode = q('st-log-mode').value;
    var secs = parseFloat(q('st-log-secs').value) || 0;
    var body = { name: q('st-log-name').value.trim() || undefined, mode: mode };
    if (mode === 'timed') body.seconds = secs;
    if (mode === 'rolling') body.window_s = secs;
    postJson('/api/streams/log/start', body).then(function (r) {
      if (r.status >= 400) setMsg((r.body && r.body.error) || 'log start failed', true);
      refresh();
    });
  }
  function stopLog() {
    postJson('/api/streams/log/stop', {}).then(function () { refresh(); });
  }
  function toggleForward() {
    var on = _snap && _snap.forward && _snap.forward.active;
    var body = on ? { enable: false } : {
      enable: true, channels: parseVars(q('st-fwd-ch').value),
      addr: q('st-fwd-addr').value.trim() || undefined
    };
    postJson('/api/streams/forward', body).then(function (r) {
      if (r.status >= 400) setMsg((r.body && r.body.error) || 'forward failed', true);
      refresh();
    });
  }

  // ---- events (delegated) -----------------------------------------------------------------
  function target(e) {
    var t = e.target;
    while (t && t !== _root && !(t.getAttribute && t.getAttribute('data-act'))) t = t.parentNode;
    return (t && t !== _root) ? t : null;
  }
  function attr(t, k) { return t.getAttribute(k); }

  function handleClick(e) {
    var id = e.target && e.target.id;
    if (id === 'st-log-start') return startLog();
    if (id === 'st-log-stop') return stopLog();
    if (id === 'st-fwd-btn') return toggleForward();
    var t = target(e);
    if (!t) return;
    var act = attr(t, 'data-act');
    var slot = parseInt(attr(t, 'data-slot'), 10);
    if (act === 'edit') openEditor(slot);
    else if (act === 'close') { _open[slot] = false; renderEditors(); schedulePlan(); }
    else if (act === 'restore') restoreSlots(String(attr(t, 'data-slot')).split(',').map(Number));
    else if (act === 'apply') applySlot(slot);
    else if (act === 'save-preset') savePreset(slot);
    else if (act === 'ac') pickAutocomplete(slot, parseInt(attr(t, 'data-i'), 10));
  }

  function handleInput(e) {
    var t = target(e);
    if (!t) return;
    var act = attr(t, 'data-act');
    var slot = parseInt(attr(t, 'data-slot'), 10);
    var d = _drafts[slot];
    if (!d) return;
    if (act === 'vars') { d.vars = t.value; d.source = 'custom'; d.presetSel = ''; scheduleAutocomplete(slot); schedulePlan(); }
    else if (act === 'rate') { d.rate = t.value; schedulePlan(); }
  }

  function handleChange(e) {
    var t = target(e);
    if (!t) {
      if (e.target && (e.target.id === 'st-log-mode')) renderLogFwd();
      return;
    }
    var act = attr(t, 'data-act');
    var slot = parseInt(attr(t, 'data-slot'), 10);
    if (act === 'preset') chooseFromPreset(slot, t.value);
    else if (act === 'part') {
      var d = _drafts[slot];
      if (d && d.presetSel) fillFromPart(slot, d.presetSel, parseInt(t.value, 10) || 0);
    }
  }

  // ---- shell -------------------------------------------------------------------------------
  var CSS = [
    '.st-root { font-size: 12px; color: var(--text); display: flex; flex-direction: column; gap: 10px; padding: 8px; }',
    '.st-title { font-size: 10px; font-weight: 700; letter-spacing: 0.08em; color: var(--muted); text-transform: uppercase; margin-right: 6px; }',
    '.st-muted { color: var(--muted); }',
    '.st-num { font-family: Consolas, monospace; }',
    '.st-bad { color: var(--red); }',
    '.st-warn { color: var(--amber); }',
    '.st-pill { background: var(--accent); border-radius: 8px; padding: 1px 7px; font-size: 10px; }',
    '.st-pill-amber { background: var(--amber); color: #000; }',
    '.st-tbl { width: 100%; border-collapse: collapse; }',
    '.st-tbl th { text-align: left; font-size: 10px; color: var(--muted); padding: 3px 6px; }',
    '.st-tbl td { padding: 4px 6px; border-top: 1px solid var(--border); }',
    '.st-bar { display: inline-block; width: 90px; height: 8px; background: var(--bg); border-radius: 4px; vertical-align: middle; overflow: hidden; }',
    '.st-bar-fill { display: block; height: 100%; }',
    '.st-bar-ok { background: var(--green); } .st-bar-warm { background: var(--amber); } .st-bar-hot { background: var(--red); }',
    '.st-btn { background: var(--accent); color: var(--text); border: 1px solid var(--border); border-radius: 4px; padding: 3px 9px; cursor: pointer; font-size: 11px; }',
    '.st-btn[disabled] { opacity: 0.4; cursor: not-allowed; }',
    '.st-btn-go { background: var(--green); color: #000; }',
    '.st-editor { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 8px; display: flex; flex-direction: column; gap: 6px; }',
    '.st-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }',
    '.st-in { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 4px; padding: 3px 6px; font-family: Consolas, monospace; }',
    '.st-in-rate { width: 70px; }',
    '.st-vars { width: 100%; box-sizing: border-box; resize: vertical; }',
    '.st-ac { display: flex; flex-wrap: wrap; gap: 4px; }',
    '.st-ac-item { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 10px; padding: 1px 8px; cursor: pointer; font-family: Consolas, monospace; font-size: 11px; }',
    '.st-chip { display: inline-block; border-radius: 10px; padding: 2px 9px; margin: 2px 4px 2px 0; }',
    '.st-chip-ok { background: rgba(78,204,163,0.15); color: var(--green); }',
    '.st-chip-bad { background: rgba(233,69,96,0.15); color: var(--red); }',
    '.st-msg { min-height: 14px; color: var(--muted); }',
    '.st-logfwd { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }',
    '.st-box { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 8px; display: flex; flex-direction: column; gap: 6px; }'
  ].join('\n');

  function buildShell() {
    return '<style>' + CSS + '</style><div class="st-root">' +
      '<div id="st-head" class="st-row"></div>' +
      '<div id="st-table"></div>' +
      '<div id="st-editors"></div>' +
      '<div id="st-tabs"></div>' +
      '<div class="st-logfwd">' +
        '<div class="st-box"><div class="st-title">Logging</div>' +
          '<div class="st-row"><input id="st-log-name" class="st-in" placeholder="name (optional)">' +
            '<select id="st-log-mode" class="st-in"><option value="timed">timed</option>' +
            '<option value="rolling">rolling</option><option value="unlimited">unlimited</option></select>' +
            '<input id="st-log-secs" class="st-in st-in-rate" value="30" title="seconds / window"> s</div>' +
          '<div class="st-row"><button id="st-log-start" class="st-btn st-btn-go">Start</button>' +
            '<button id="st-log-stop" class="st-btn">Stop</button></div>' +
          '<div id="st-log-status" class="st-muted"></div></div>' +
        '<div class="st-box"><div class="st-title">VOFA+ forward (FireWater UDP)</div>' +
          '<div class="st-row"><input id="st-fwd-addr" class="st-in" value="127.0.0.1:1347"></div>' +
          '<textarea id="st-fwd-ch" class="st-in st-vars" rows="2" placeholder="channels (variable names)"></textarea>' +
          '<div class="st-row"><button id="st-fwd-btn" class="st-btn">Forward to VOFA+</button></div>' +
          '<div id="st-fwd-status" class="st-muted"></div></div>' +
      '</div>' +
      '<div id="st-msg" class="st-msg"></div></div>';
  }

  function teardown() {
    if (_pollTimer) clearTimeout(_pollTimer);
    if (_planTimer) clearTimeout(_planTimer);
    if (_acTimer) clearTimeout(_acTimer);
    _pollTimer = _planTimer = _acTimer = null;
    if (_root && _onClick) {
      _root.removeEventListener('click', _onClick);
      _root.removeEventListener('input', _onInput);
      _root.removeEventListener('change', _onChange);
    }
    _root = null;
  }

  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel('Streams', function (container) {
      teardown();
      _root = container;
      container.innerHTML = buildShell();
      _onClick = handleClick; _onInput = handleInput; _onChange = handleChange;
      container.addEventListener('click', _onClick);
      container.addEventListener('input', _onInput);
      container.addEventListener('change', _onChange);
      _lastGate = null;
      _fwdSeeded = false;
      loadPresets();
      refresh();
    });
  };
  window.__PLUGIN_DESTROY__ = function () {
    teardown();
    _snap = null; _presets = []; _presetBody = {}; _drafts = {}; _open = {};
    _plan = null; _msg = ''; _acItems = [];
  };

  // Test hooks (pure helpers and state accessors; the fake-DOM harness uses these).
  window.__STREAMS_TEST__ = {
    parseVars: parseVars, lastToken: lastToken, fmtBps: fmtBps, bar: bar,
    canSwap: canSwap, gateReason: gateReason, draftAssignments: draftAssignments,
    setSnap: function (s) { _snap = s; }, getSnap: function () { return _snap; },
    setDraft: function (n, d) { _drafts[n] = d; }, setOpen: function (n, v) { _open[n] = v; },
    renderTable: renderTable, renderTabs: renderTabs, renderHead: renderHead,
    renderEditors: renderEditors, renderLogFwd: renderLogFwd, planLine: planLine
  };

  window.__registerPlugin__('Streams', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);
})();
