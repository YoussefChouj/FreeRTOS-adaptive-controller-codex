/* Phase 1B plugin D — Activity Timeline.
 * Chronological feed of everything that matters for the operator: operator
 * clicks/commands, agent messages, plan steps, approvals, recording events,
 * arm changes, stream stalls/recoveries, service starts/stops and code
 * reloads. Driven live by SSE `activity` events (each entry is already a
 * journal record {seq,t,iso,kind,source,actor,data}), and backfilled from
 * GET /api/agent/history on init.
 *
 * Upcoming plan steps are rendered below an explicit "now" divider in a
 * greyed style. Filters by source (operator/agent/system). Each item can be
 * expanded to view the raw JSON data.
 *
 * For the harness: publishes {handle, get, reducer} under
 * window.__gs_ui_state__.timeline.
 */
(function () {
  'use strict';
  var KEY = 'timeline';
  var items = [];          // rendered entries, ascending seq
  var planned = [];        // upcoming plan steps (below "now", greyed)
  var sourceFilter = null; // 'operator' | 'agent' | 'system' | null(all)
  var handle = null;

  function view() { return { items: items.slice(), planned: planned.slice(), filter: sourceFilter }; }

  function upsert(list, entry, key) {
    var idx = -1;
    if (key != null) {
      for (var i = 0; i < list.length; i++) if (list[i][key] === entry[key]) { idx = i; break; }
    }
    if (idx === -1) { list.push(entry); }
    else { list[idx] = entry; }
    list.sort(function (a, b) { return (a.seq || 0) - (b.seq || 0); });
  }

  // Record a journal entry (from SSE activity or /api/agent/history).
  function ingest(entry) {
    var kind = entry.kind;
    if (kind === 'plan_step') upsert(planned, entry, 'step_id');
    else upsert(items, entry, 'seq');
  }

  window.__registerPlugin__('Activity Timeline', function (api) {
    var container;
    try {
      api.registerPanel('Activity', function (body) {
        container = body;
        body.setAttribute('data-testid', 'activity-timeline');
        // source filter buttons
        ['all', 'operator', 'agent', 'system'].forEach(function (f) {
          var btn = document.createElement('button');
          btn.textContent = f;
          btn.addEventListener('click', function () { sourceFilter = f === 'all' ? null : f; render(); });
        });
      });
    } catch (e) { console.warn('[timeline] panel skip:', e && e.message); }

    // backfill from the history API when possible
    if (typeof fetch === 'function') {
      fetch('/api/agent/history?since=0&limit=200').then(function (r) { return r.json(); })
        .then(function (body) {
          (body.entries || []).forEach(ingest);
          render();
        }).catch(function () {});
    }

    handle = function (evt) {
      if (!evt) return;
      if (evt.event === 'activity' && evt.data) {
        ingest(evt.data);
      } else if (evt.event === 'plan' && evt.data) {
        // every planned step below "now" is future/greyed
        (evt.data.steps || []).forEach(function (s) {
          upsert(planned, { step_id: s.step_id, status: s.status || 'pending', plan_id: evt.data.plan_id }, 'step_id');
        });
      } else if (evt.event === 'step' && evt.data) {
        var sd = evt.data;
        upsert(items, {
          seq: sd.seq || 0, kind: 'plan_step', t: sd.ts || Date.now() / 1000,
          iso: '', source: 'agent', actor: 'agent', data: sd,
        }, 'seq');
        // completed step leaves the "planned" (future) list
        planned = planned.filter(function (p) {
          return !(p.step_id === sd.step_id && p.plan_id === sd.plan_id);
        });
      }
      render();
    };
    api.onAgentEvent(handle);

    function render() {
      if (!container) return;
      container.innerHTML = '';
      var shown = sourceFilter
        ? items.filter(function (i) { return i.source === sourceFilter; })
        : items.slice();
      shown.forEach(function (it) {
        container.appendChild(makeRow(it, false));
      });
      // "now" divider
      var nowRow = document.createElement('div');
      nowRow.setAttribute('data-testid', 'timeline-now');
      nowRow.textContent = '── now ──';
      nowRow.style.cssText = 'opacity:.4;margin:8px 0;text-align:center;';
      container.appendChild(nowRow);
      planned.forEach(function (p) {
        var row = makeRow(p, true); // greyed
        row.setAttribute('data-testid', 'timeline-upcoming');
        container.appendChild(row);
      });
      if (!shown.length && !planned.length) {
        var empty = document.createElement('div');
        empty.textContent = 'No activity yet.';
        container.appendChild(empty);
      }
    }

    function makeRow(it, upcoming) {
      var row = document.createElement('div');
      row.style.cssText = 'font-size:12px;padding:3px 0;display:flex;flex-wrap:wrap;'
        + 'align-items:baseline;gap:6px;'
        + (upcoming ? 'opacity:.4;font-style:italic;' : '');
      var head = document.createElement('span');
      head.style.cssText = 'overflow-wrap:anywhere;flex:1 1 auto;';
      head.textContent = '[' + (it.kind || '?') + '] ' + (it.actor || (it.source || '')) + ' ';
      var src = document.createElement('span');
      src.textContent = it.source || '';
      src.style.cssText = 'opacity:.5;';
      var toggle = document.createElement('button');
      toggle.textContent = '▸';
      toggle.addEventListener('click', function () {
        var pl = document.getElementById('raw-' + (it.seq || Math.random().toString(36).slice(2)));
        if (pl) pl.style.display = (pl.style.display === 'none') ? 'block' : 'none';
      });
      var raw = document.createElement('pre');
      raw.id = 'raw-' + (it.seq || Math.random().toString(36).slice(2));
      raw.style.cssText = 'display:none;font-size:10px;white-space:pre-wrap;background:rgba(255,255,255,.04);padding:4px;';
      raw.textContent = JSON.stringify(it.data != null ? it.data : it, null, 2);
      row.appendChild(toggle); row.appendChild(head); row.appendChild(src); row.appendChild(raw);
      return row;
    }

    window.__gs_ui_state__ = window.__gs_ui_state__ || {};
    window.__gs_ui_state__[KEY] = { handle: handle, get: view, ingest: ingest, setIsFilter: function (f) { sourceFilter = f; } };
  }, function () {}, { workspace: 'overview', description: 'Chronological activity timeline' });
})();