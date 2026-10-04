(function () {
  'use strict';

  /* Workflow B campaign panel (WP-23; WP-35 design system): status banner, preflight table, campaign + pack
   * pickers, the operator checklist, Go, and big Pause / Land / Abort. Polls /api/campaign/state every second
   * through GSUI.poller (backs off after repeated failures; the banner is computed by the service), reads
   * /api/campaign/list for the pickers and /api/campaign/preflight on Refresh. Every failed action shows its
   * error in #cp-error and a toast (GSUI.report); no browser dialogs: a browser can block confirm/alert boxes.
   * Disabled controls say why: Go lists what is missing, Pause / Land / Abort name the runner status. The
   * safety buttons stay single click and are also on the flight strip (plugins/flight-strip.js). */

  var UI = null;
  var _api = null;
  var _poll = null;
  var _container = null;
  var _state = null;
  var _list = null;

  var CHECKLIST = [
    { id: 'pack_swapped', label: 'Pack swapped and labelled' },
    { id: 'drone_on_pad', label: 'Drone at pad centre, nose to the marked end wall' },
    { id: 'powered_in_place', label: 'Powered on in place (OF frame aligned)' },
    { id: 'rc_ready', label: 'RC transmitter on and in reach (ch10 kill)' },
    { id: 'phone_recording', label: 'Phone clamped and recording' },
    { id: 'operator_present', label: 'Operator stays in the room' }
  ];
  var ACTIONS = ['pause', 'land', 'abort'];

  // banner colour per runner status (status scale, ui/tokens.css); anything else (operator_needed,
  // arm_refused, error, ...) is a red pause
  var BANNER_STATUS = { idle: 'stale', waiting_for_go: 'warn', running: 'info', complete: 'ok' };

  function esc(v) { return UI.esc(v); }

  function q(id) {
    if (!_container) return null;
    return _container.querySelector('#' + id) || document.getElementById(id);
  }

  function fetchState() {
    if (!_api) return Promise.resolve();
    return UI.fetchJson('/api/campaign/state').then(function (data) {
      showPollError('');
      _state = data;
      renderState();
    }, function (e) {
      showPollError(e.message);
      throw e;
    });
  }

  function renderBanner() {
    var el = q('cp-banner');
    if (!el || !_state) return;
    el.textContent = _state.banner || _state.status;
    el.dataset.status = _state.status;
    el.style.background = UI.color(BANNER_STATUS[_state.status] || 'fail');
  }

  // Pause / Land / Abort: disabled (reason shown) only when the runner says no run is active
  function renderActions(reason) {
    ACTIONS.forEach(function (a) { UI.setDisabled(q('cp-' + a + '-btn'), reason); });
    var r = q('cp-actions-reason');
    if (r) r.textContent = reason;
  }

  function renderState() {
    if (!_state) return;
    renderBanner();
    renderActions(UI.campaignActive(_state) ? '' : 'no campaign running (' + _state.status + ')');

    var packIdInput = q('cp-pack-id');
    var waitMsg = q('cp-wait-msg');
    if (_state.status === 'waiting_for_go') {
      if (waitMsg) waitMsg.textContent = 'Waiting for pack ' + _state.waiting_pack;
      if (packIdInput && !packIdInput.dataset.touched) {
        packIdInput.value = _state.waiting_pack || '';
        packIdInput.dataset.touched = 'true';
      }
    } else {
      if (waitMsg) waitMsg.textContent = '';
      if (packIdInput) packIdInput.dataset.touched = '';
    }

    var statusLine = q('cp-status-line');
    if (statusLine) {
      statusLine.textContent = 'Status: ' + _state.status +
        ', Reason: ' + (_state.reason || 'none') +
        ', Control: ' + (_state.control || 'none');
    }

    var flightsBody = q('cp-flights-body');
    if (flightsBody) {
      flightsBody.innerHTML = UI.tableRows(FLIGHT_COLUMNS, _state.flights || [],
        { empty: 'No flights yet', emptyHint: 'rows appear as the runner finishes each flight' });
    }

    checkGoReady();
  }

  var FLIGHT_COLUMNS = [
    { key: 'flight_id', label: 'Flight' },
    { key: 'pack_id', label: 'Pack' },
    { key: 'experiment', label: 'Exp' },
    { key: 'j', label: 'j' },
    { key: 'decision', label: 'Decision' },
    { label: 'Abort', render: function (f) { return esc(f.abort_level) + '/' + esc(f.abort_reason); } },
    { label: 'Hover only', render: function (f) { return f.hover_only ? 'yes' : 'no'; } }
  ];

  // ── pickers: launch copies first (newest), then the saved templates; pack ids from packs.yaml ──────────
  function fetchList() {
    UI.fetchJson('/api/campaign/list')
      .then(function (data) { _list = data; renderPickers(); })
      .catch(function (e) { showError(UI.report('campaign list', e)); });
  }

  function option(value, label) {
    return '<option value="' + esc(value) + '">' + esc(label) + '</option>';
  }

  function renderPickers() {
    var campSel = q('cp-campaign-select');
    var packSel = q('cp-pack-select');
    if (!_list) return;
    if (campSel) {
      var html = option('', '-- pick a campaign --');
      (_list.launch || []).forEach(function (c) { html += option(c.path, 'launch: ' + c.name); });
      (_list.saved || []).forEach(function (c) { html += option(c.path, 'template: ' + c.name + ' (' + c.mode + ')'); });
      campSel.innerHTML = html;
    }
    if (packSel) {
      var phtml = option('', '-- pick a pack --');
      (_list.packs || []).forEach(function (p) { phtml += option(p, p); });
      packSel.innerHTML = phtml;
    }
  }

  function pickInto(selectId, inputId) {
    var sel = q(selectId), input = q(inputId);
    if (!sel || !input || !sel.value) return;
    input.value = sel.value;
    checkGoReady();
  }

  // ── preflight report (WP-32 D4): one checklist of the campaign_preflight rows plus the firmware pre-arm mask
  // as named PREFLIGHT FAIL lines; red rows first, the first red cause on top. Go stays blocked by the service
  // (campaign_api.py refuses Go on a red preflight) exactly as before: this view only explains why.
  var PREARM_SYMBOL = 'g_prearm_fail_mask';
  var PREARM_FIRST = 'g_prearm_first_fail';
  // fail & PREARM_ENABLE_ROW (API/prearm.c): a failed bit blocks the arm only when it is set here
  var PREARM_BLOCK = 'g_prearm_block_mask';
  // Bit order of prearm_bit_t in API/prearm.h (WP-40). A set bit outside this list shows as "bit N".
  var PREARM_BITS = ['estimator not ready', 'battery low at rest', 'RC link lost', 'not level on the pad (roll / pitch)',
    'wfb_safety trip latched', 'task rate low (system monitor)'];

  function runPreflight() {
    var path = (q('cp-path') || {}).value || '';
    var pack = (q('cp-pack-id') || {}).value || '';
    var summary = q('cp-preflight-summary');
    if (summary) summary.textContent = 'running preflight...';
    // the manifest answers "is the pre-arm mask in this firmware"; its failure must not hide the campaign rows
    var manifest = UI.fetchJson('/api/manifest', { timeoutMs: 15000 })
      .catch(function (e) { return { _error: e.message }; });
    UI.fetchJson('/api/campaign/preflight?campaign=' + encodeURIComponent(path.trim()) +
                 '&pack=' + encodeURIComponent(pack.trim()), { timeoutMs: 15000 })
      .then(function (res) {
        return manifest.then(function (m) {
          showError('');
          renderPreflight(res, firmwarePrearm(m, _api && _api.getState ? _api.getState() : null));
        });
      })
      .catch(function (e) {
        if (summary) summary.textContent = 'preflight failed';
        showError(UI.report('preflight', e));
      });
  }

  // the live value of a symbol in whichever slot carries it (key `sym` or `<prefix>.sym`)
  function liveValue(state, sym) {
    var streams = (state && state.streams) || {};
    var slots = Object.keys(streams);
    for (var i = 0; i < slots.length; i++) {
      var vals = (streams[slots[i]] && streams[slots[i]].values) || {};
      var keys = Object.keys(vals);
      for (var j = 0; j < keys.length; j++) {
        var k = keys[j];
        if ((k === sym || k.slice(-sym.length - 1) === '.' + sym) && vals[k] !== null && !isNaN(Number(vals[k]))) {
          return { value: Number(vals[k]), slot: slots[i] };
        }
      }
    }
    return null;
  }

  function hex4(n) { return '0x' + ('0000' + n.toString(16)).slice(-4); }

  // firmwarePrearm(manifest, shellState) -> preflight rows for the firmware pre-arm mask
  function firmwarePrearm(manifest, state) {
    var row = { name: 'firmware pre-arm', source: 'firmware' };
    if (!manifest || manifest._error) {
      return [Object.assign(row, { pass: null, value: 'capability manifest unavailable: ' +
        ((manifest && manifest._error) || 'no reply'), fix: 'check GET /api/manifest, then Refresh' })];
    }
    var names = (manifest.firmware_symbols && manifest.firmware_symbols.names) || [];
    if (names.indexOf(PREARM_SYMBOL) < 0) {
      return [Object.assign(row, { pass: null, status: 'stale', value: 'not in this firmware (' + PREARM_SYMBOL +
        ' is not in the capability manifest)', fix: 'needs the pre-arm firmware (WP-40); the campaign rows still apply' })];
    }
    var live = liveValue(state, PREARM_SYMBOL);
    if (!live) {
      return [Object.assign(row, { pass: null, value: 'in this firmware, not in telemetry',
        fix: 'subscribe ' + PREARM_SYMBOL + ' to a slot (Streams panel), then Refresh' })];
    }
    var mask = live.value >>> 0;
    if (mask === 0) return [Object.assign(row, { pass: true, value: 'all checks pass (mask ' + hex4(mask) + ', slot ' + live.slot + ')' })];
    var first = liveValue(state, PREARM_FIRST);
    var bits = [];
    for (var b = 0; b < 16; b++) if (mask & (1 << b)) bits.push(b);
    // the firmware's own first cause goes first when it publishes one
    if (first && bits.indexOf(first.value) > 0) bits = [first.value].concat(bits.filter(function (x) { return x !== first.value; }));
    // without the block mask in telemetry a failed bit stays red: whether it blocks the arm is unknown
    var block = liveValue(state, PREARM_BLOCK);
    var blockMask = block ? (block.value >>> 0) : null;
    return bits.map(function (b) {
      var line = { name: 'PREFLIGHT FAIL: ' + (PREARM_BITS[b] || 'bit ' + b), source: 'firmware', pass: false,
        value: PREARM_SYMBOL + ' bit ' + b + ' (mask ' + hex4(mask) + ', slot ' + live.slot + ')',
        fix: 'the firmware refuses to arm until this check passes' };
      if (blockMask === null) {
        line.fix = 'blocks the arm only if its PREARM_ENABLE_ROW bit is on; subscribe ' + PREARM_BLOCK + ' to know';
      } else if (!(blockMask & (1 << b))) {
        line.name = 'PREFLIGHT WARN: ' + (PREARM_BITS[b] || 'bit ' + b);
        line.pass = null;
        line.fix = 'report only (PREARM_ENABLE_ROW bit off): the firmware still arms; clear it before flight';
      }
      return line;
    });
  }

  function pfStatus(c) { return c.status || (c.pass === true ? 'ok' : (c.pass === false ? 'fail' : 'warn')); }
  var PF_ORDER = { fail: 0, warn: 1, stale: 2, ok: 3 };
  var PF_LABEL = { ok: 'PASS', fail: 'FAIL', warn: 'CHECK', stale: 'N/A' };

  var PREFLIGHT_COLUMNS = [
    { key: 'name', label: 'Check' },
    { label: 'Result', render: function (c) { return UI.pill(pfStatus(c), PF_LABEL[pfStatus(c)]); } },
    { key: 'value', label: 'Value' },
    { label: 'Fix', render: function (c) { return c.pass === true ? '' : esc(c.fix); } }
  ];

  // buildPreflightReport(serviceRows, firmwareRows) -> {rows: red first (stable), firstCause: the top red row}
  function buildPreflightReport(checks, fwRows) {
    var all = (checks || []).concat(fwRows || []).map(function (c, i) { return { c: c, i: i }; });
    all.sort(function (a, b) { return (PF_ORDER[pfStatus(a.c)] - PF_ORDER[pfStatus(b.c)]) || (a.i - b.i); });
    var rows = all.map(function (x) { return x.c; });
    return { rows: rows, firstCause: rows.length && pfStatus(rows[0]) === 'fail' ? rows[0] : null };
  }

  function renderPreflight(res, fwRows) {
    var body = q('cp-preflight-body');
    var summary = q('cp-preflight-summary');
    var report = buildPreflightReport((res && res.checks) || [], fwRows);
    var red = report.rows.filter(function (c) { return pfStatus(c) === 'fail'; }).length;
    var fwRed = (fwRows || []).filter(function (c) { return pfStatus(c) === 'fail'; }).length;
    var amber = report.rows.filter(function (c) { return pfStatus(c) === 'warn'; }).length;
    if (summary) {
      summary.textContent = !res.ok ? ('preflight: ' + red + ' red row(s), fix them before Go')
        : (fwRed ? 'campaign preflight OK; firmware pre-arm: ' + fwRed + ' PREFLIGHT FAIL line(s)'
          : 'preflight OK' + (amber ? ' (' + amber + ' to confirm by the checklist)' : ''));
      summary.style.color = UI.color(res.ok && !fwRed ? 'ok' : 'fail');
    }
    var cause = q('cp-first-cause');
    if (cause) {
      var fc = report.firstCause;
      cause.textContent = fc ? 'First cause: ' + fc.name + (fc.fix ? ': ' + fc.fix : '') : '';
      cause.style.display = fc ? 'block' : 'none';
    }
    if (!body) return;
    body.innerHTML = UI.tableRows(PREFLIGHT_COLUMNS, report.rows, {
      empty: 'No checks returned',
      rowClass: function (c) {
        var s = pfStatus(c);
        return (s === 'ok' ? 'cp-pf-pass' : (s === 'fail' ? 'cp-pf-fail' : 'cp-pf-unknown')) + ' gs-row--' + s +
          (c.source === 'firmware' ? ' cp-pf-firmware' : '');
      }
    });
  }

  // Go needs a path, a pack and all six ticks; the reason line lists what is still missing
  function checkGoReady() {
    var goBtn = q('cp-go-btn');
    if (!goBtn) return;
    var pathInput = q('cp-path');
    var packInput = q('cp-pack-id');
    var missing = [];
    if (!(pathInput && pathInput.value.trim() !== '')) missing.push('pick a campaign');
    if (!(packInput && packInput.value.trim() !== '')) missing.push('enter the pack ID');
    var unticked = CHECKLIST.filter(function (c) { var cb = q('cp-chk-' + c.id); return !cb || !cb.checked; }).length;
    if (unticked) missing.push('tick ' + unticked + ' checklist item' + (unticked > 1 ? 's' : ''));
    UI.setDisabled(goBtn, missing.join(', '), q('cp-go-reason'));
  }

  function showError(msg) {
    var errEl = q('cp-error');
    if (errEl) {
      errEl.textContent = msg;
      errEl.style.display = msg ? 'block' : 'none';
    }
  }

  function showPollError(msg) {
    var errEl = q('cp-poll-error');
    if (errEl) {
      errEl.textContent = msg;
      errEl.style.display = msg ? 'block' : 'none';
    }
    // runner state unknown: never leave the safety buttons disabled on stale information
    if (msg) renderActions('');
  }

  function handleGo() {
    var pathInput = q('cp-path');
    var packInput = q('cp-pack-id');
    UI.setDisabled(q('cp-go-btn'), 'sending Go...', q('cp-go-reason'));

    var checklist = {};
    for (var i = 0; i < CHECKLIST.length; i++) {
      var id = CHECKLIST[i].id;
      checklist[id] = !!q('cp-chk-' + id).checked;
    }

    UI.fetchJson('/api/campaign/go', {
      method: 'POST',
      timeoutMs: 15000,
      json: {
        campaign_path: pathInput.value.trim(),
        pack_id: packInput.value.trim(),
        checklist: checklist,
        source: 'operator'
      }
    })
    .then(function () {
      showError('');
      for (var j = 0; j < CHECKLIST.length; j++) {
        var cb = q('cp-chk-' + CHECKLIST[j].id);
        if (cb) cb.checked = false;
      }
      checkGoReady();
      if (_poll) _poll.now();
    })
    .catch(function (e) {
      checkGoReady();
      showError(UI.report('Go', e));
    });
  }

  function sendCommand(cmd) {
    UI.campaignCommand(cmd)
      .then(function () { showError(''); if (_poll) _poll.now(); })
      .catch(function (e) { showError(UI.report(cmd, e)); });
  }

  function buildHTML() {
    var html = '<div id="cp-banner" class="gs-banner">idle</div>';
    html += '<div id="cp-error" class="gs-error" style="display:none"></div>';
    html += '<div id="cp-poll-error" class="gs-error" style="display:none"></div>';

    html += '<div class="gs-section-title">Campaign and pack</div>';
    html += '<div class="gs-row"><span class="gs-label">Campaign</span><select id="cp-campaign-select" class="gs-input"></select>' +
            '<button id="cp-list-btn" class="gs-btn gs-btn--ghost">Reload list</button></div>';
    html += '<div class="gs-row"><span class="gs-label">Campaign path</span>' +
            '<input type="text" id="cp-path" class="gs-input" value="" size="60"></div>';
    html += '<div class="gs-row"><span class="gs-label">Pack</span><select id="cp-pack-select" class="gs-input"></select>' +
            '<span class="gs-label">Pack ID</span><input type="text" id="cp-pack-id" class="gs-input" value="">' +
            '<span id="cp-wait-msg" class="gs-reason"></span></div>';

    html += '<div class="gs-section-title">Preflight report</div>';
    html += '<div class="gs-row"><button id="cp-preflight-btn" class="gs-btn">Refresh preflight</button>' +
            '<span id="cp-preflight-summary" class="gs-label">preflight not run</span></div>';
    html += '<div id="cp-first-cause" class="gs-error" role="alert" style="display:none"></div>';
    html += '<table class="gs-table"><thead><tr><th>Check</th><th>Result</th><th>Value</th><th>Fix</th></tr></thead>' +
            '<tbody id="cp-preflight-body"></tbody></table>';

    html += '<div class="gs-section-title">Operator checklist</div><div id="cp-checklist">';
    for (var i = 0; i < CHECKLIST.length; i++) {
      html += '<div class="gs-row"><label><input type="checkbox" id="cp-chk-' + CHECKLIST[i].id + '"> ' +
              CHECKLIST[i].label + '</label></div>';
    }
    html += '</div>';

    html += '<div class="gs-row"><button id="cp-go-btn" class="gs-btn gs-btn--ok gs-btn--big" disabled>Go</button>' +
            '<span id="cp-go-reason" class="gs-reason"></span></div>';
    html += '<div id="cp-consent-note" class="gs-label">Pressing Go consents to the agent arming and disarming for this campaign.</div>';

    html += '<div class="gs-row">';
    html += '<button id="cp-pause-btn" class="gs-btn gs-btn--big">Pause<span class="gs-kbd">P</span></button>';
    html += '<button id="cp-land-btn" class="gs-btn gs-btn--warn gs-btn--big">Land</button>';
    html += '<button id="cp-abort-btn" class="gs-btn gs-btn--danger gs-btn--big">Abort</button>';
    html += '<span id="cp-actions-reason" class="gs-reason"></span>';
    html += '</div>';

    html += '<div id="cp-status-line" class="gs-label"></div>';

    html += '<div class="gs-section-title">Flights</div>';
    html += '<table class="gs-table"><thead><tr>' +
            FLIGHT_COLUMNS.map(function (c) { return '<th>' + esc(c.label) + '</th>'; }).join('') +
            '</tr></thead><tbody id="cp-flights-body"></tbody></table>';

    return html;
  }

  window.__PLUGIN_INIT__ = function (api) {
    UI = window.GSUI;
    _api = api;
    api.registerPanel('Campaign', function (container) {
      _container = container;
      container.innerHTML = buildHTML();

      var pathInput = q('cp-path');
      var packInput = q('cp-pack-id');
      if (pathInput) pathInput.addEventListener('input', checkGoReady);
      if (packInput) packInput.addEventListener('input', checkGoReady);

      for (var i = 0; i < CHECKLIST.length; i++) {
        var cb = q('cp-chk-' + CHECKLIST[i].id);
        if (cb) cb.addEventListener('change', checkGoReady);
      }

      var campSel = q('cp-campaign-select');
      var packSel = q('cp-pack-select');
      if (campSel) campSel.addEventListener('change', function () { pickInto('cp-campaign-select', 'cp-path'); });
      if (packSel) packSel.addEventListener('change', function () { pickInto('cp-pack-select', 'cp-pack-id'); });
      var listBtn = q('cp-list-btn');
      if (listBtn) listBtn.addEventListener('click', fetchList);
      var pfBtn = q('cp-preflight-btn');
      if (pfBtn) pfBtn.addEventListener('click', runPreflight);

      var goBtn = q('cp-go-btn');
      if (goBtn) goBtn.addEventListener('click', handleGo);

      ACTIONS.forEach(function (a) {
        var b = q('cp-' + a + '-btn');
        if (b) b.addEventListener('click', function () { sendCommand(a); });
      });

      if (_poll) _poll.stop();
      checkGoReady();
      fetchList();
      _poll = UI.poller(fetchState, { intervalMs: 1000 });
      _poll.now();
    });
  };

  window.__PLUGIN_DESTROY__ = function () {
    if (_poll) {
      _poll.stop();
      _poll = null;
    }
    _api = null;
    _container = null;
    _state = null;
    _list = null;
  };

  if (typeof window.__registerPlugin__ === 'function') {
    window.__registerPlugin__('Campaign', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);
  }

  // pure helpers of the preflight report, for the offline harness
  window.__gs_ui_state__ = window.__gs_ui_state__ || {};
  window.__gs_ui_state__.campaignPreflight = {
    firmwarePrearm: firmwarePrearm, buildPreflightReport: buildPreflightReport, PREARM_BITS: PREARM_BITS
  };

})();
