/* Phase 1B plugin C — Ordered Approval Queue.
 * Maintains the pending agent proposals (critical commands like arm, motor,
 * param changes) in FIFO order as driven by SSE `approval` events:
 *   {queue:[...]}                 → full replace
 *   {plan_id, step_id, state}     → mark one step's decision
 * The operator reviews and approves/rejects only the earliest pending item
 * first, in order. Decisions POST to
 *   /api/agent/approvals/<plan_id>/<step_id>/approve|reject
 *
 * A control strip above the queue shows the agent mode and lets the operator
 * switch tier-0 param access between "partial" (each write waits here) and
 * "full" (autonomous mode only; asks for confirmation). POSTs
 * /api/agent/control {tier0_access, source:'operator'}.
 *
 * Model is a pure function so the Node harness can assert ordering without
 * a browser. Publish {handle, get, reducer} under window.__gs_ui_state__.approvals.
 */
(function () {
  'use strict';
  var KEY = 'approvals';
  var queue = [];   // array of approval dicts (already ordered oldest-first)
  var decided = {}; // "plan_id:step_id" -> 'approved'|'rejected'
  var handle = null;
  var control = null; // last /api/agent/control state

  function view() {
    return { pending: queue.slice(), decided: decided, oldest: queue[0] || null };
  }

  // Pure reducer: (state, event) -> state. `state` is {queue, decided}.
  function reducer(state, evt) {
    state = state || { queue: queue, decided: decided };
    var d = evt && evt.data ? evt.data : {};
    if (d && Array.isArray(d.queue)) {
      state.queue = d.queue.slice();
    } else if (d && d.plan_id && d.step_id && d.state) {
      state.decided[d.plan_id + ':' + d.step_id] = d.state;
      state.queue = state.queue.filter(function (q) {
        return !(q.plan_id === d.plan_id && q.step_id === d.step_id);
      });
    }
    return state;
  }

  function itemKey(x) { return (x && x.plan_id) + ':' + (x && x.step_id); }

  // Pending-count badge on the Approvals tab (item 2) so approvals stay visible
  // without occupying every workspace.
  function updateBadge() {
    var b = document.getElementById('approval-badge');
    if (!b) return;
    b.style.display = queue.length ? 'inline-block' : 'none';
    b.textContent = String(queue.length);
  }

  window.__registerPlugin__('Approvals', function (api) {
    var container;
    try {
      var name = 'Approvals';
      api.registerPanel(name, function (body) {
        container = body;
        body.setAttribute('data-testid', 'approval-queue');
      }, { workspace: 'approvals', gates: [], description: 'Ordered pending approval queue' });
    } catch (e) { console.warn('[approvals] panel skip:', e && e.message); }

    function decide(a, result) {
      var key = itemKey(a);
      if (key && (key in decided)) return; // already decided
      decideOne(api, a, result);
    }

    function setAccess(next) {
      if (next === 'full' && !window.confirm(
          'Grant FULL tier-0 access? In autonomous mode the agent will write '
          + 'flight-critical parameters (PID, MRAC, mixer, safety limits, gyro LPF) '
          + 'and EKF-into-control changes without asking you, and may arm the drone.')) {
        return;
      }
      fetch('/api/agent/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tier0_access: next, source: 'operator' }),
      }).then(function (r) { return r.json(); })
        .then(function (c) { if (c && c.mode) { control = c; render(); } })
        .catch(function () {});
    }

    function renderControl() {
      var bar = document.createElement('div');
      bar.setAttribute('data-testid', 'agent-control-strip');
      bar.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;align-items:center;'
        + 'margin-bottom:8px;';
      if (!control) {
        bar.textContent = 'Agent control: unavailable';
        container.appendChild(bar);
        return;
      }
      var full = control.tier0_access === 'full';
      var auto = control.mode === 'autonomous';
      var label = document.createElement('span');
      label.textContent = 'Mode: ' + control.mode + ' · Tier-0 params: '
        + (full ? 'FULL' : 'partial (approval)')
        + (auto ? '' : ' — applies in autonomous mode only');
      if (full && auto) label.style.cssText = 'color:#f5a524;font-weight:600;';
      var btn = document.createElement('button');
      btn.setAttribute('data-testid', 'tier0-access-toggle');
      btn.textContent = full ? 'Set partial access' : 'Grant full access';
      btn.disabled = control.mode === 'off';
      btn.addEventListener('click', function () {
        setAccess(full ? 'partial' : 'full');
      });
      bar.appendChild(label);
      bar.appendChild(btn);
      container.appendChild(bar);
    }

    function render() {
      if (!container) return;
      container.innerHTML = '';
      updateBadge();
      renderControl();
      var h = document.createElement('h4');
      h.textContent = 'Pending proposals (' + queue.length + ')';
      container.appendChild(h);
      if (!queue.length) {
        var none = document.createElement('div');
        none.textContent = 'No pending approvals.';
        none.style.cssText = 'opacity:.5;';
        container.appendChild(none);
        return;
      }
      queue.forEach(function (a, i) {
        var isOldest = i === 0;
        var row = document.createElement('div');
        row.setAttribute('data-testid', 'approval-item');
        row.style.cssText = 'padding:8px;margin:6px 0;border:1px solid rgba(230,233,239,.15);'
          + 'border-radius:6px;' + (isOldest ? '' : 'opacity:.55;');
        row.textContent = (isOldest ? '▶ ' : '') + (a.label || a.action || a.step_id)
          + ' — ' + (a.plan_id || '') + ':' + (a.step_id || '');
        container.appendChild(row);
        if (isOldest) {
          var app = document.createElement('button');
          app.textContent = 'Approve';
          app.addEventListener('click', function () { decide(a, 'approved'); });
          var rej = document.createElement('button');
          rej.textContent = 'Reject';
          rej.addEventListener('click', function () { decide(a, 'rejected'); });
          var rowBtns = document.createElement('span');
          rowBtns.appendChild(app); rowBtns.appendChild(rej);
          row.appendChild(rowBtns);
        }
      });
    }

    function apply(evt) {
      queue = reducer({ queue: queue, decided: decided }, evt).queue;
      decided = reducer({ queue: queue, decided: decided }, evt).decided;
      // second call above re-filters; fold carefully:
      var st = reducer({ queue: queue, decided: decided }, evt);
      queue = st.queue; decided = st.decided;
      render();
    }

    handle = function (evt) {
      if (evt && evt.event === 'approval') apply(evt);
      if (evt && evt.event === 'control' && evt.data) {
        control = evt.data;
        render();
      }
    };
    api.onAgentEvent(handle);
    fetch('/api/agent/control').then(function (r) { return r.json(); })
      .then(function (c) { if (c && c.mode) { control = c; render(); } })
      .catch(function () {});

    window.__gs_ui_state__ = window.__gs_ui_state__ || {};
    window.__gs_ui_state__[KEY] = { handle: handle, get: view, reducer: reducer };
  }, function () {
    /* panel teardown handled by shell */
  }, { workspace: 'overview', gates: [], description: 'Ordered pending approval queue' });
})();

// decision POST helper (module-level so the harness can stub fetch)
function decideOne(api, a, result) {
  var plan_id = a.plan_id, step_id = a.step_id;
  if (!plan_id || !step_id) return;
  fetch('/api/agent/approvals/' + encodeURIComponent(plan_id) + '/'
        + encodeURIComponent(step_id) + '/' + (result === 'approved' ? 'approve' : 'reject'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source: 'operator' }),
  }).catch(function () {});
}