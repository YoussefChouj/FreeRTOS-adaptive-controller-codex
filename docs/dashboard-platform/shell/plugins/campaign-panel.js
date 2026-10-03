(function () {
  'use strict';

  /* Workflow B campaign panel (WP-23): status banner, preflight table, campaign + pack pickers, the operator
   * checklist, Go, and big Pause / Land / Abort. Reads /api/campaign/state every second (the banner is computed
   * by the service), /api/campaign/list for the pickers and /api/campaign/preflight on Refresh. Every failed
   * action shows its error in #cp-error; no browser dialogs: a browser can block confirm/alert boxes. */

  var _api = null;
  var _pollingTimer = null;
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

  // banner colour per runner status; anything else (operator_needed, arm_refused, error, ...) is a red pause
  var BANNER_COLORS = {
    idle: '#8888aa', waiting_for_go: '#f5a623', running: '#4ea8de', complete: '#4ecca3'
  };

  function escapeHtml(unsafe) {
    if (unsafe === undefined || unsafe === null) return '';
    return String(unsafe)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function q(id) {
    if (!_container) return null;
    return _container.querySelector('#' + id) || document.getElementById(id);
  }

  // fetch JSON; a non-2xx reply or a network error rejects with the server's error text
  function getJson(url, opts) {
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return { error: 'HTTP ' + r.status }; }).then(function (data) {
        if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status));
        return data;
      });
    });
  }

  function fetchState() {
    if (!_api) return;
    getJson('/api/campaign/state')
      .then(function (data) {
        showPollError('');
        _state = data;
        renderState();
      })
      .catch(function (e) { showPollError(e.message); });
  }

  function renderBanner() {
    var el = q('cp-banner');
    if (!el || !_state) return;
    el.textContent = _state.banner || _state.status;
    el.dataset.status = _state.status;
    el.style.background = BANNER_COLORS[_state.status] || '#e94560';
  }

  function renderState() {
    if (!_state) return;
    renderBanner();

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
      var rows = (_state.flights || []).map(function(f) {
        return '<tr>' +
          '<td>' + escapeHtml(f.flight_id) + '</td>' +
          '<td>' + escapeHtml(f.pack_id) + '</td>' +
          '<td>' + escapeHtml(f.experiment) + '</td>' +
          '<td>' + escapeHtml(f.j) + '</td>' +
          '<td>' + escapeHtml(f.decision) + '</td>' +
          '<td>' + escapeHtml(f.abort_level) + '/' + escapeHtml(f.abort_reason) + '</td>' +
          '<td>' + (f.hover_only ? 'yes' : 'no') + '</td>' +
          '</tr>';
      });
      flightsBody.innerHTML = rows.join('');
    }

    checkGoReady();
  }

  // ── pickers: launch copies first (newest), then the saved templates; pack ids from packs.yaml ──────────
  function fetchList() {
    getJson('/api/campaign/list')
      .then(function (data) { _list = data; renderPickers(); })
      .catch(function (e) { showError('campaign list: ' + e.message); });
  }

  function option(value, label) {
    return '<option value="' + escapeHtml(value) + '">' + escapeHtml(label) + '</option>';
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
    getJson('/api/campaign/preflight?campaign=' + encodeURIComponent(path.trim()) +
            '&pack=' + encodeURIComponent(pack.trim()))
      .then(function (res) { showError(''); renderPreflight(res); })
      .catch(function (e) {
        if (summary) summary.textContent = 'preflight failed';
        showError('preflight: ' + e.message);
      });
  }

  function renderPreflight(res) {
    var body = q('cp-preflight-body');
    var summary = q('cp-preflight-summary');
    var checks = (res && res.checks) || [];
    var red = checks.filter(function (c) { return c.pass === false; }).length;
    var amber = checks.filter(function (c) { return c.pass !== true && c.pass !== false; }).length;
    if (summary) {
      summary.textContent = res.ok ? ('preflight OK' + (amber ? ' (' + amber + ' to confirm by the checklist)' : ''))
                                   : ('preflight: ' + red + ' red row(s), fix them before Go');
      summary.style.color = res.ok ? '#4ecca3' : '#e94560';
    }
    if (!body) return;
    body.innerHTML = checks.map(function (c) {
      var cls = c.pass === true ? 'cp-pf-pass' : (c.pass === false ? 'cp-pf-fail' : 'cp-pf-unknown');
      var mark = c.pass === true ? 'PASS' : (c.pass === false ? 'FAIL' : 'CHECK');
      var color = c.pass === true ? '#4ecca3' : (c.pass === false ? '#e94560' : '#f5a623');
      return '<tr class="' + cls + '">' +
        '<td>' + escapeHtml(c.name) + '</td>' +
        '<td style="color:' + color + ';font-weight:700">' + mark + '</td>' +
        '<td>' + escapeHtml(c.value) + '</td>' +
        '<td>' + (c.pass === true ? '' : escapeHtml(c.fix)) + '</td>' +
        '</tr>';
    }).join('');
  }

  function checkGoReady() {
    var goBtn = q('cp-go-btn');
    if (!goBtn) return;

    var pathInput = q('cp-path');
    var packInput = q('cp-pack-id');

    var pathOk = pathInput && pathInput.value.trim() !== '';
    var packOk = packInput && packInput.value.trim() !== '';

    var checksOk = true;
    for (var i = 0; i < CHECKLIST.length; i++) {
      var cb = q('cp-chk-' + CHECKLIST[i].id);
      if (!cb || !cb.checked) {
        checksOk = false;
        break;
      }
    }

    goBtn.disabled = !(pathOk && packOk && checksOk);
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
  }

  function handleGo() {
    var goBtn = q('cp-go-btn');
    if (goBtn) goBtn.disabled = true;

    var pathInput = q('cp-path');
    var packInput = q('cp-pack-id');

    var checklist = {};
    for (var i = 0; i < CHECKLIST.length; i++) {
      var id = CHECKLIST[i].id;
      checklist[id] = !!q('cp-chk-' + id).checked;
    }

    getJson('/api/campaign/go', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        campaign_path: pathInput.value.trim(),
        pack_id: packInput.value.trim(),
        checklist: checklist,
        source: 'operator'
      })
    })
    .then(function () {
      showError('');
      for (var j = 0; j < CHECKLIST.length; j++) {
        var cb = q('cp-chk-' + CHECKLIST[j].id);
        if (cb) cb.checked = false;
      }
      checkGoReady();
      fetchState();
    })
    .catch(function (e) {
      checkGoReady();
      showError('Go: ' + e.message);
    });
  }

  function sendCommand(cmd) {
    getJson('/api/campaign/' + cmd, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: 'operator' })
    })
    .then(function () { showError(''); fetchState(); })
    .catch(function (e) { showError(cmd + ': ' + e.message); });
  }

  function buildHTML() {
    var big = 'font-size:1.3em;font-weight:700;padding:10px 22px;margin:4px;border-radius:6px;cursor:pointer;';
    var html = '<div id="cp-banner" style="padding:10px 12px;border-radius:6px;color:#fff;font-weight:700;' +
               'font-size:1.15em;margin-bottom:8px;background:#8888aa">idle</div>';
    html += '<div id="cp-error" style="color:red;display:none;"></div>';
    html += '<div id="cp-poll-error" style="color:red;display:none;"></div>';

    html += '<div>Campaign: <select id="cp-campaign-select"></select> ' +
            '<button id="cp-list-btn">Reload list</button></div>';
    html += '<div>Campaign Path: <input type="text" id="cp-path" value="" size="60"></div>';
    html += '<div>Pack: <select id="cp-pack-select"></select> ' +
            'Pack ID: <input type="text" id="cp-pack-id" value=""> <span id="cp-wait-msg"></span></div>';

    html += '<div style="margin-top:8px"><button id="cp-preflight-btn">Refresh preflight</button> ' +
            '<span id="cp-preflight-summary">preflight not run</span></div>';
    html += '<table><thead><tr><th>Check</th><th></th><th>Value</th><th>Fix</th></tr></thead>' +
            '<tbody id="cp-preflight-body"></tbody></table>';

    html += '<div id="cp-checklist">';
    for (var i = 0; i < CHECKLIST.length; i++) {
      html += '<div><label><input type="checkbox" id="cp-chk-' + CHECKLIST[i].id + '"> ' + CHECKLIST[i].label + '</label></div>';
    }
    html += '</div>';

    html += '<div><button id="cp-go-btn" style="' + big + '" disabled>Go</button></div>';

    html += '<div>';
    html += '<button id="cp-pause-btn" style="' + big + '">Pause</button> ';
    html += '<button id="cp-land-btn" style="' + big + 'background:#f5a623;">Land</button> ';
    html += '<button id="cp-abort-btn" style="' + big + 'background:#e94560;color:#fff;">Abort</button>';
    html += '</div>';

    html += '<div id="cp-consent-note">Pressing Go consents to the agent arming and disarming for this campaign.</div>';

    html += '<div id="cp-status-line"></div>';

    html += '<table><thead><tr><th>Flight</th><th>Pack</th><th>Exp</th><th>j</th><th>Decision</th><th>Abort</th><th>Hover Only</th></tr></thead><tbody id="cp-flights-body"></tbody></table>';

    return html;
  }

  window.__PLUGIN_INIT__ = function (api) {
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

      var pauseBtn = q('cp-pause-btn');
      var landBtn = q('cp-land-btn');
      var abortBtn = q('cp-abort-btn');
      if (pauseBtn) pauseBtn.addEventListener('click', function() { sendCommand('pause'); });
      if (landBtn) landBtn.addEventListener('click', function() { sendCommand('land'); });
      if (abortBtn) abortBtn.addEventListener('click', function() { sendCommand('abort'); });

      if (_pollingTimer) {
        clearInterval(_pollingTimer);
      }
      fetchList();
      fetchState();
      _pollingTimer = setInterval(fetchState, 1000);
    });
  };

  window.__PLUGIN_DESTROY__ = function () {
    if (_pollingTimer) {
      clearInterval(_pollingTimer);
      _pollingTimer = null;
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
