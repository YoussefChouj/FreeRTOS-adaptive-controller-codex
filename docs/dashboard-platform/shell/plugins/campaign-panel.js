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

  // ── preflight: one GET, one row per check; red rows show their fix ───────────────────────────────────
  function runPreflight() {
    var path = (q('cp-path') || {}).value || '';
    var pack = (q('cp-pack-id') || {}).value || '';
    var summary = q('cp-preflight-summary');
    if (summary) summary.textContent = 'running preflight...';
    UI.fetchJson('/api/campaign/preflight?campaign=' + encodeURIComponent(path.trim()) +
                 '&pack=' + encodeURIComponent(pack.trim()), { timeoutMs: 15000 })
      .then(function (res) { showError(''); renderPreflight(res); })
      .catch(function (e) {
        if (summary) summary.textContent = 'preflight failed';
        showError(UI.report('preflight', e));
      });
  }

  function pfStatus(c) { return c.pass === true ? 'ok' : (c.pass === false ? 'fail' : 'warn'); }

  var PREFLIGHT_COLUMNS = [
    { key: 'name', label: 'Check' },
    { label: 'Result', render: function (c) { return UI.pill(pfStatus(c), c.pass === true ? 'PASS' : (c.pass === false ? 'FAIL' : 'CHECK')); } },
    { key: 'value', label: 'Value' },
    { label: 'Fix', render: function (c) { return c.pass === true ? '' : esc(c.fix); } }
  ];

  function renderPreflight(res) {
    var body = q('cp-preflight-body');
    var summary = q('cp-preflight-summary');
    var checks = (res && res.checks) || [];
    var red = checks.filter(function (c) { return c.pass === false; }).length;
    var amber = checks.filter(function (c) { return c.pass !== true && c.pass !== false; }).length;
    if (summary) {
      summary.textContent = res.ok ? ('preflight OK' + (amber ? ' (' + amber + ' to confirm by the checklist)' : ''))
                                   : ('preflight: ' + red + ' red row(s), fix them before Go');
      summary.style.color = UI.color(res.ok ? 'ok' : 'fail');
    }
    if (!body) return;
    body.innerHTML = UI.tableRows(PREFLIGHT_COLUMNS, checks, {
      empty: 'No checks returned',
      rowClass: function (c) {
        var s = pfStatus(c);
        return (s === 'ok' ? 'cp-pf-pass' : (s === 'fail' ? 'cp-pf-fail' : 'cp-pf-unknown')) + ' gs-row--' + s;
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

    html += '<div class="gs-section-title">Preflight</div>';
    html += '<div class="gs-row"><button id="cp-preflight-btn" class="gs-btn">Refresh preflight</button>' +
            '<span id="cp-preflight-summary" class="gs-label">preflight not run</span></div>';
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

})();
