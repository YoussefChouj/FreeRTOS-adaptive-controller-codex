/* Phase 1B plugin B — Header Mode Pill & Big STOP.
 * Top-of-page pill that shows the current agent mode (OFF / SUPERVISED /
 * AUTONOMOUS), updated live from the SSE `control` event, plus a prominent
 * STOP button that immediately posts mode=off to /api/agent/control.
 *
 * Exposes window.renderAgentModePill() so the shell's SSE dispatch can
 * refresh it directly (the pill holds the authoritative control state the
 * shell also keeps in _agentControl). For the offline Node harness it
 * publishes {handle, get} under window.__gs_ui_state__.headerMode.
 */
(function () {
  'use strict';
  var KEY = 'headerMode';
  var control = { mode: 'unknown', allow_agent_arm: false };
  var handle = null;

  function view() { return { control: control }; }

  window.__registerPlugin__('Agent Mode', function (api) {
    var pill, stop;
    try {
      pill = document.createElement('div');
      pill.id = 'agent-mode-pill';
      pill.setAttribute('data-testid', 'agent-mode-pill');
      pill.style.cssText = 'position:fixed;top:8px;left:50%;transform:translateX(-50%);'
        + 'z-index:9500;padding:6px 16px;border-radius:20px;font-weight:700;'
        + 'font-size:13px;letter-spacing:.5px;background:#1f2430;'
        + 'border:2px solid #5b6472;color:#e6e9ef;';

      stop = document.createElement('button');
      stop.id = 'agent-stop';
      stop.setAttribute('data-testid', 'agent-stop');
      stop.textContent = 'STOP';
      stop.style.cssText = 'position:fixed;top:44px;left:50%;transform:translateX(-50%);'
        + 'z-index:9501;padding:8px 26px;border-radius:6px;background:#f04a4a;'
        + 'color:#fff;font-weight:800;border:none;cursor:pointer;font-size:15px;'
        + 'display:none;';
      stop.addEventListener('click', function () {
        fetch('/api/agent/control', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: 'off', source: 'operator_stop' }),
        }).then(function (r) {
          try { return r.json(); } catch (_) { return {}; }
        }).then(function (res) {
          if (res && res.mode) control = { mode: res.mode, allow_agent_arm: res.allow_agent_arm };
        }).catch(function () {});
      });
      document.body.appendChild(pill);
      document.body.appendChild(stop);
    } catch (e) {
      console.warn('[headerMode] non-DOM init skipped:', e && e.message);
    }

    function apply(next) {
      control = next;
      if (pill) {
        pill.textContent = 'AGENT · ' + String(control.mode || 'unknown').toUpperCase();
        var color = control.mode === 'off' ? '#f04a4a'
                  : control.mode === 'autonomous' ? '#ffb02e' : '#4ECCA3';
        pill.style.borderColor = color;
        pill.style.color = color;
      }
      if (stop) stop.style.display = control.mode === 'off' ? 'none' : 'block';
    }

    // The shell calls this directly (typeof check in dispatchAgentEvent).
    window.renderAgentModePill = function (nextControl) {
      if (nextControl) apply(nextControl);
    };
    // Re-sync from the api's cached control if available.
    if (typeof api.getAgentControl === 'function') {
      var c0 = api.getAgentControl();
      if (c0 && c0.mode) apply(c0);
    }

    handle = function (evt) {
      if (evt && evt.event === 'control' && evt.data) apply(evt.data);
    };
    api.onAgentEvent(handle);

    window.__gs_ui_state__ = window.__gs_ui_state__ || {};
    window.__gs_ui_state__[KEY] = { handle: handle, get: view, apply: apply };
  }, function () {
    try { if (pill) pill.remove(); } catch (_) {}
    try { if (stop) stop.remove(); } catch (_) {}
  }, { workspace: 'all', description: 'Header mode pill + big STOP for the agent' });
})();