/* Phase 1B plugin C — Ordered Approval Queue.
 * Maintains the pending agent proposals (critical commands like arm, motor,
 * param changes) in FIFO order as driven by SSE `approval` events:
 *   {queue:[...]}                 → full replace
 *   {plan_id, step_id, state}     → mark one step's decision
 * The operator reviews and approves/rejects only the earliest pending item
 * first, in order. Decisions POST to
 *   /api/agent/approvals/<plan_id>/<step_id>/approve|reject
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

  window.__registerPlugin__('Approvals', function (api) {
    var container;
    try {
      var name = 'Approvals';
      api.registerPanel(name, function (body) {
        container = body;
        body.setAttribute('data-testid', 'approval-queue');
      });
    } catch (e) { console.warn('[approvals] panel skip:', e && e.message); }

    function decide(a, result) {
      var key = itemKey(a);
      if (key && (key in decided)) return; // already decided
      decideOne(api, a, result);
    }

    function render() {
      if (!container) return;
      container.innerHTML = '';
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
    };
    api.onAgentEvent(handle);

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