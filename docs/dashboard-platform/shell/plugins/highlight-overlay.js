/* Phase 1B plugin E — Highlight Overlay & UI Action Executor.
 * Draws a persistent animated overlay frame around whatever the agent is
 * looking at / interacting with (the highlight target's bounding box), in
 * reaction to SSE `ui_action` events with action 'highlight'.
 *
 * The shell already executes ui_action commands (see index.html runUiAction)
 * and acks them; this plugin renders the *visual* overlay so the operator can
 * literally follow the agent's gaze. It also offers run(action) so the
 * offline harness can verify the executor without a real DOM (it records the
 * target + ack intent in __gs_ui_state__.highlight.executedTrace).
 *
 * Publishes {handle, get, run, executedTrace} under window.__gs_ui_state__.highlight.
 */
(function () {
  'use strict';
  var KEY = 'highlight';
  var active = null;            // { testid, text }
  var executedTrace = [];       // log of executed ui_action commands
  var handle = null;

  function view() { return { active: active, executed: executedTrace.slice(-20) }; }

  function findEl(testid) {
    if (!testid) return null;
    if (typeof window.CSS !== 'undefined' && window.CSS.escape && document.querySelector) {
      try { return document.querySelector('[data-testid="' + window.CSS.escape(testid) + '"]'); } catch (_) {}
    }
    if (document.querySelectorAll) {
      var all = document.querySelectorAll('[data-testid]');
      for (var i = 0; i < all.length; i++) {
        if (all[i].getAttribute('data-testid') === testid) return all[i];
      }
    }
    return null;
  }

  function run(action, ack) {
    var act = (action || {}).action || 'highlight';
    var args = (action || {}).args || {};
    var rec = { action: act, testid: args.testid, ok: false, error: null };
    if (act !== 'highlight') { rec.error = 'unsupported action: ' + act; executedTrace.push(rec); return rec; }
    var el = findEl(args.testid);
    if (!el) { rec.error = 'no element with data-testid=' + args.testid; executedTrace.push(rec); return rec; }
    active = { testid: args.testid, text: args.text || args.label || null };
    rec.ok = true;
    executedTrace.push(rec);
    drawOverlay(el, active);
    if (typeof ack === 'function') ack(true);
    return rec;
  }

  function drawOverlay(el, a) {
    try {
      var box = document.createElement('div');
      box.id = 'ag-highlight-overlay';
      box.style.cssText = 'position:absolute;z-index:9600;pointer-events:none;'
        + 'border:2px solid var(--green,#4ECCA3);box-shadow:0 0 12px rgba(78,204,163,.7);'
        + 'border-radius:4px;transition:all .15s ease;';
      var rect = el.getBoundingClientRect();
      box.style.left = rect.left + 'px'; box.style.top = rect.top + 'px';
      box.style.width = rect.width + 'px'; box.style.height = rect.height + 'px';
      var old = document.getElementById('ag-highlight-overlay');
      if (old && old.remove) old.remove();
      document.body.appendChild(box);
      if (a.text) box.setAttribute('data-ag-label', a.text);
    } catch (e) { /* non-DOM harness */ }
  }

  window.__registerPlugin__('Highlight Gaze', function (api) {
    // Re-run any persisted ui_action when asked (also acks the agent).
    var running = {};
    handle = function (evt) {
      if (evt && evt.event === 'ui_action') {
        var a = evt.data || {};
        var key = (a.plan_id || '') + ':' + (a.step_id || '');
        if (running[key]) return; // already handled (shell also executes)
        running[key] = true;
        var res = run(a, function (ok) {
          if (typeof api.ackUiAction === 'function' && a.plan_id && a.step_id) {
            api.ackUiAction(a.plan_id, a.step_id, ok, res.error || null);
          }
        });
      }
    };
    api.onAgentEvent(handle);

    window.__gs_ui_state__ = window.__gs_ui_state__ || {};
    window.__gs_ui_state__[KEY] = {
      handle: handle, get: view, run: run,
      get executedTrace() { return executedTrace; },
    };
  }, function () {
    try { var el = document.getElementById('ag-highlight-overlay'); if (el && el.remove) el.remove(); } catch (_) {}
  }, { workspace: 'all', description: 'Highlight overlay + UI action executor' });
})();