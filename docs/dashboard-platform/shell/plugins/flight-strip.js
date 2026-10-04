(function () {
  'use strict';

  /* Flight strip (WP-35, research WP-32 D1): one row above the workspace tabs, visible on every workspace and
   * while the sidebar is collapsed. Left: ARM, MODE, BAT, RC, LINK (age of the newest frame) and the campaign
   * status. Right: Pause / Land / Abort, the same POSTs as the Campaign panel (GSUI.campaignCommand), and the
   * theme toggle.
   *
   * Safety actions are single click and never delayed, like the Campaign panel. They are disabled, with the
   * reason shown, only when /api/campaign/state says no run is active; when that state is unknown (poll
   * failing) they stay enabled. P = Pause, ignored while typing in a field or the terminal (GSUI.shortcut).
   * No thresholds are invented: battery has no pack cutoff in this repo, so BAT only shows value and age. */

  var ARM_TEXT = { armed: 'ARMED', disarmed: 'DISARMED' };
  var MODE_KEYS = ['status.flymode', 'DroneStatus.FlyMode'];
  var VBAT_KEYS = ['status.vbat', 'real_voltage'];
  var RC_KEYS = ['status.sbus_lost'];
  var FLY_MODE_LABELS = ['Stop', 'SDK'];   // DroneStatus.FlyMode (API/flight_fsm.c), same table as path-panel.js
  var LINK_WARN_MS = 1000;                 // newest frame older than this: amber
  var LINK_LOST_MS = 3000;                 // the shell's "no telemetry in 3+ s" alarm limit: red
  var VALUE_STALE_MS = 3000;               // a strip value older than this is shown grey with its age
  var CAMPAIGN_POLL_MS = 1000;
  var CLOCK_MS = 500;
  var CAMPAIGN_STATUS = { idle: 'stale', waiting_for_go: 'warn', running: 'info', complete: 'ok' };
  var ACTIONS = ['pause', 'land', 'abort'];

  var UI = null;
  var _api = null, _root = null, _poll = null, _clock = null, _unkey = null;
  var _campaign = null, _campaignErr = '';

  function q(id) { return document.getElementById(id); }

  // value of the first key found in any slot (bare or slot-prefixed) and the age of that value in ms
  function find(state, keys, nowMs) {
    var streams = (state && state.streams) || {};
    var slots = Object.keys(streams);
    for (var k = 0; k < keys.length; k++) {
      for (var i = 0; i < slots.length; i++) {
        var s = streams[slots[i]] || {};
        var vals = s.values || {};
        var name = vals[keys[k]] != null ? keys[k] : null;
        if (!name) {
          var pref = Object.keys(vals).filter(function (n) { return n.replace(/^slot\d+\./, '') === keys[k]; });
          name = pref.length ? pref[0] : null;
        }
        if (!name) continue;
        var ts = (s._key_ts || {})[name] || s.last_update_ns || 0;
        return { val: Number(vals[name]), ageMs: ts ? nowMs - ts / 1e6 : null };
      }
    }
    return null;
  }

  function newestFrameAge(state, nowMs) {
    var streams = (state && state.streams) || {};
    var newest = 0;
    Object.keys(streams).forEach(function (k) {
      var t = (streams[k] && streams[k].last_update_ns) || 0;
      if (t > newest) newest = t;
    });
    return newest ? nowMs - newest / 1e6 : null;
  }

  function valueView(found, fmt) {
    if (!found || !isFinite(found.val)) return { status: 'stale', text: 'n/p', title: 'not published by this build' };
    if (found.ageMs != null && found.ageMs > VALUE_STALE_MS) {
      return { status: 'stale', text: fmt(found.val), title: 'last value ' + UI.fmtAge(found.ageMs) + ' ago' };
    }
    return null;
  }

  // pure: telemetry -> { arm, mode, bat, rc, link }, each { status, text, title }
  function evaluate(state, armState, nowMs) {
    var out = {};
    var arm = typeof armState === 'string' ? armState : null;
    out.arm = arm === 'armed' ? { status: 'warn', text: ARM_TEXT.armed, title: 'status.arm is non-zero' }
      : arm === 'disarmed' ? { status: 'ok', text: ARM_TEXT.disarmed, title: 'status.arm = 0 (fresh)' }
      : { status: 'stale', text: 'UNKNOWN', title: 'status.arm not received or frozen' };

    var fmtMode = function (v) { return FLY_MODE_LABELS[v] || ('mode ' + v); };
    var m = find(state, MODE_KEYS, nowMs);
    out.mode = valueView(m, fmtMode) || { status: 'info', text: fmtMode(m.val), title: 'DroneStatus.FlyMode = ' + m.val };

    var fmtV = function (v) { return v.toFixed(2) + ' V'; };
    var b = find(state, VBAT_KEYS, nowMs);
    out.bat = valueView(b, fmtV) || { status: 'ok', text: fmtV(b.val), title: 'battery voltage, ' + UI.fmtAge(b.ageMs) + ' old' };

    var r = find(state, RC_KEYS, nowMs);
    out.rc = valueView(r, function (v) { return v ? 'LOST' : 'OK'; }) ||
      (r.val ? { status: 'fail', text: 'LOST', title: 'status.sbus_lost = ' + r.val + ': RC receiver link down' }
             : { status: 'ok', text: 'OK', title: 'status.sbus_lost = 0' });

    var age = newestFrameAge(state, nowMs);
    if (!state || (!state.connected && age === null)) {
      out.link = { status: 'fail', text: 'NO LINK', title: 'service reports no drone connection' };
    } else if (age === null) {
      out.link = { status: 'fail', text: 'NO DATA', title: 'connected, no telemetry frame yet' };
    } else {
      var st = UI.ageStatus(age, LINK_WARN_MS, LINK_LOST_MS);
      out.link = { status: st === 'stale' ? 'fail' : st, text: UI.fmtAge(age), title: 'age of the newest telemetry frame' };
    }
    return out;
  }

  // pure: campaign poll -> { status, text, enabled, reason }
  function campaignView(camp, err) {
    if (err) return { status: 'stale', text: 'state unknown: ' + err, enabled: true, reason: '' };
    if (!camp) return { status: 'stale', text: 'loading...', enabled: true, reason: '' };
    var active = UI.campaignActive(camp);
    return {
      status: CAMPAIGN_STATUS[camp.status] || 'fail',
      text: camp.banner || camp.status,
      enabled: active,
      reason: active ? '' : 'no campaign running (' + camp.status + ')'
    };
  }

  function buildHTML() {
    function item(key, id) {
      return '<span class="fs-item"><span class="fs-key">' + key + '</span>' + UI.pill('stale', '...', '', id) + '</span>';
    }
    return item('ARM', 'fs-arm') + item('MODE', 'fs-mode') + item('BAT', 'fs-bat') + item('RC', 'fs-rc') +
      item('LINK', 'fs-link') +
      '<span class="fs-item fs-campaign"><span class="fs-key">CAMPAIGN</span>' + UI.pill('stale', '...', '', 'fs-campaign') +
      ' <span id="fs-campaign-text"></span></span>' +
      '<span class="fs-actions">' +
      '<span id="fs-reason" class="gs-reason"></span>' +
      '<button id="fs-pause" class="gs-btn" data-testid="strip-pause" title="Pause the campaign (key P)">Pause<span class="gs-kbd">P</span></button>' +
      '<button id="fs-land" class="gs-btn gs-btn--warn" data-testid="strip-land" title="Land now">Land</button>' +
      '<button id="fs-abort" class="gs-btn gs-btn--danger" data-testid="strip-abort" title="Abort the campaign">Abort</button>' +
      '<button id="fs-theme" class="gs-btn gs-btn--ghost" title="Switch dark / light theme">Theme</button>' +
      '</span>';
  }

  // called by the shell on every poll (with the state) and by the clock (without: ages keep counting)
  function renderTelemetry(fresh) {
    if (!_api) return;
    var state = fresh && fresh.streams ? fresh : (typeof _api.getState === 'function' ? _api.getState() : null);
    var arm = typeof _api.getArmState === 'function' ? _api.getArmState() : null;
    var v = evaluate(state, arm, Date.now());
    ['arm', 'mode', 'bat', 'rc', 'link'].forEach(function (k) { UI.setPill(q('fs-' + k), v[k].status, v[k].text, v[k].title); });
  }

  function renderCampaign() {
    var v = campaignView(_campaign, _campaignErr);
    UI.setPill(q('fs-campaign'), v.status, (_campaign && !_campaignErr) ? _campaign.status : 'unknown');
    var t = q('fs-campaign-text');
    if (t) { t.textContent = v.text; t.title = v.text; }
    ACTIONS.forEach(function (a) { UI.setDisabled(q('fs-' + a), v.reason); });
    var r = q('fs-reason');
    if (r) r.textContent = v.reason;
  }

  function fetchCampaign() {
    if (!_api) return Promise.resolve();
    return UI.fetchJson('/api/campaign/state').then(function (data) {
      _campaign = data; _campaignErr = ''; renderCampaign();
    }, function (e) {
      _campaignErr = e.message; renderCampaign();
      throw e;
    });
  }

  function send(cmd) {
    return UI.campaignCommand(cmd).then(function () {
      UI.toast(cmd + ' sent', 'ok');
      if (_poll) _poll.now();
    }, function (e) { UI.report(cmd, e); });
  }

  function onPauseKey() {
    var b = q('fs-pause');
    if (b && b.disabled) { UI.toast('Pause not sent: ' + campaignView(_campaign, _campaignErr).reason, 'warn'); return; }
    send('pause');
  }

  function init(api) {
    UI = window.GSUI;
    if (!UI) { console.error('[flight-strip] ui/ui-kit.js is not loaded: strip disabled'); return; }
    _root = q('flight-strip');
    if (!_root) { console.warn('[flight-strip] no #flight-strip in the shell'); return; }
    _api = api;
    _root.innerHTML = buildHTML();
    ACTIONS.forEach(function (a) {
      var b = q('fs-' + a);
      if (b) b.addEventListener('click', function () { send(a); });
    });
    var th = q('fs-theme');
    if (th) th.addEventListener('click', function () { UI.setTheme(UI.getTheme() === 'dark' ? 'light' : 'dark'); });
    if (typeof api.subscribe === 'function') api.subscribe(renderTelemetry, 'Flight Strip');
    _unkey = UI.shortcut('p', onPauseKey);
    _clock = setInterval(renderTelemetry, CLOCK_MS);
    _poll = UI.poller(fetchCampaign, { intervalMs: CAMPAIGN_POLL_MS });
    renderTelemetry();
    renderCampaign();
    _poll.now();
  }

  function destroy() {
    if (_poll) _poll.stop();
    if (_clock !== null) clearInterval(_clock);
    if (_unkey) _unkey();
    _poll = null; _clock = null; _unkey = null; _api = null; _campaign = null; _campaignErr = '';
  }

  window.__gs_ui_state__ = window.__gs_ui_state__ || {};
  window.__gs_ui_state__.flightStrip = {
    evaluate: evaluate,
    campaignView: campaignView,
    get: function () { return { campaign: _campaign, error: _campaignErr }; }
  };

  if (typeof window.__registerPlugin__ === 'function') {
    window.__registerPlugin__('Flight Strip', init, destroy,
      { workspace: 'all', description: 'Flight-critical state and campaign safety actions on every workspace' });
  }
})();
