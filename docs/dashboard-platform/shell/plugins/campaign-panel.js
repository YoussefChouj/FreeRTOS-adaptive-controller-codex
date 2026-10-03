(function () {
  'use strict';

  var _api = null;
  var _pollingTimer = null;
  var _container = null;
  var _state = null;

  var CHECKLIST = [
    { id: 'pack_swapped', label: 'Pack swapped and labelled' },
    { id: 'drone_on_pad', label: 'Drone at pad centre, nose to the marked end wall' },
    { id: 'powered_in_place', label: 'Powered on in place (OF frame aligned)' },
    { id: 'rc_ready', label: 'RC transmitter on and in reach (ch10 kill)' },
    { id: 'phone_recording', label: 'Phone clamped and recording' },
    { id: 'operator_present', label: 'Operator stays in the room' }
  ];

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

  function fetchState() {
    if (!_api) return;
    fetch('/api/campaign/state')
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function() { return { error: 'HTTP ' + r.status }; })
            .then(function(data) {
              showPollError(data.error || ('HTTP ' + r.status));
              throw new Error('__HANDLED__');
            });
        }
        return r.json();
      })
      .then(function (data) {
        showPollError('');
        _state = data;
        renderState();
      })
      .catch(function (e) {
        if (e.message !== '__HANDLED__') {
          showPollError(e.message);
        }
      });
  }

  function renderState() {
    if (!_state) return;

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

    fetch('/api/campaign/go', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        campaign_path: pathInput.value.trim(),
        pack_id: packInput.value.trim(),
        checklist: checklist,
        source: 'operator'
      })
    })
    .then(function(r) {
      if (!r.ok) {
        checkGoReady();
        return r.json().catch(function() { return { error: 'HTTP ' + r.status }; })
          .then(function(data) { showError(data.error || ('HTTP ' + r.status)); });
      } else {
        showError('');
        for (var j = 0; j < CHECKLIST.length; j++) {
          var cb = q('cp-chk-' + CHECKLIST[j].id);
          if (cb) cb.checked = false;
        }
        checkGoReady();
        fetchState();
      }
    })
    .catch(function(e) {
      checkGoReady();
      showError(e.message);
    });
  }

  function sendCommand(cmd) {
    fetch('/api/campaign/' + cmd, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: 'operator' })
    })
    .then(function(r) {
      if (!r.ok) {
        return r.json().catch(function() { return { error: 'HTTP ' + r.status }; })
          .then(function(data) { showError(data.error || ('HTTP ' + r.status)); });
      } else {
        showError('');
        fetchState();
      }
    })
    .catch(function(e) {
      showError(e.message);
    });
  }

  function buildHTML() {
    var html = '<div id="cp-error" style="color:red;display:none;"></div>';
    html += '<div id="cp-poll-error" style="color:red;display:none;"></div>';

    html += '<div>Campaign Path: <input type="text" id="cp-path" value=""></div>';
    html += '<div>Pack ID: <input type="text" id="cp-pack-id" value=""> <span id="cp-wait-msg"></span></div>';

    html += '<div id="cp-checklist">';
    for (var i = 0; i < CHECKLIST.length; i++) {
      html += '<div><label><input type="checkbox" id="cp-chk-' + CHECKLIST[i].id + '"> ' + CHECKLIST[i].label + '</label></div>';
    }
    html += '</div>';

    html += '<div><button id="cp-go-btn" disabled>Go</button></div>';

    html += '<div>';
    html += '<button id="cp-pause-btn">Pause</button> ';
    html += '<button id="cp-land-btn">Land</button> ';
    html += '<button id="cp-abort-btn" style="color:red;font-size:1.2em;">Abort</button>';
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
  };

  if (typeof window.__registerPlugin__ === 'function') {
    window.__registerPlugin__('Campaign', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);
  }

})();
