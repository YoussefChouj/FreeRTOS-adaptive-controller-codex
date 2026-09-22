/* Phase 1B plugin A — Co-pilot Drawer.
 * A fixed right-side drawer giving the operator a live interface to talk to
 * the agent: an inbound message log fed by SSE `message` events and a textbox
 * that POSTs operator text to /api/agent/message. Opens/closes and reports
 * its UI state back to the agent (reportUiState) so the agent can observe it.
 *
 * Testability: after init it publishes its event handler and an immutable view
 * under window.__gs_ui_state__.copilot so the offline Node harness can drive
 * SSE events and assert behaviour without a browser.
 */
(function () {
  'use strict';
  var KEY = 'copilot';
  var messages = [];          // inbound log: {seq, text, kind, source, ts}
  var open = false;
  var handle = null;          // set in init from api.onAgentEvent

  function view() {
    return { messages: messages.slice(-20), open: open, drawerId: 'copilot-drawer' };
  }

  function pushMessage(entry) {
    messages.push(entry);
    if (messages.length > 200) messages.splice(0, messages.length - 200);
  }

  window.__registerPlugin__('Co-pilot', function (api) {
    // Drawer + toggle + message list + textbox. Built defensively so the
    // offline harness (no real DOM) does not crash the plugin.
    var drawer, toggle, list, input, send;
    try {
      drawer = document.createElement('div');
      drawer.id = 'copilot-drawer';
      drawer.setAttribute('data-testid', 'copilot-drawer');
      drawer.style.cssText = 'position:fixed;top:0;right:0;bottom:0;width:340px;max-width:90vw;'
        + 'background:var(--card,#1f2430);border-left:1px solid var(--green,#4ECCA3);'
        + 'transform:translateX(100%);transition:transform .18s ease;z-index:9000;'
        + 'box-shadow:-8px 0 24px rgba(0,0,0,.4);padding:12px;box-sizing:border-box;';

      toggle = document.createElement('button');
      toggle.textContent = 'Co-pilot';
      toggle.style.cssText = 'position:fixed;right:0;top:72px;z-index:9001;'
        + 'background:var(--green,#4ECCA3);border:none;color:#0a0d12;padding:8px 10px;'
        + 'border-radius:6px 0 0 6px;cursor:pointer;font-weight:600;';
      toggle.addEventListener('click', function () { setOpen(!open, api); });

      list = document.createElement('div');
      list.setAttribute('data-testid', 'copilot-list');
      list.style.cssText = 'height:calc(100% - 120px);overflow:auto;margin-top:48px;';

      input = document.createElement('input');
      input.setAttribute('data-testid', 'copilot-input');
      input.placeholder = 'Message the agent…';
      input.style.cssText = 'width:100%;padding:8px;border:1px solid rgba(230,233,239,.3);'
        + 'border-radius:4px;background:transparent;color:inherit;';

      send = document.createElement('button');
      send.textContent = 'Send';
      send.setAttribute('data-testid', 'copilot-send');
      send.style.cssText = 'margin-top:6px;width:100%;padding:8px;border-radius:4px;'
        + 'background:rgba(78,204,163,.15);border:1px solid var(--green,#4ECCA3);'
        + 'color:var(--green,#4ECCA3);cursor:pointer;';
      send.addEventListener('click', function () { sendMessage(input, api); });
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); sendMessage(input, api); }
      });

      drawer.appendChild(list);
      drawer.appendChild(input);
      drawer.appendChild(send);
      document.body.appendChild(drawer);
      document.body.appendChild(toggle);
    } catch (e) {
      console.warn('[copilot] non-DOM init skipped:', e && e.message);
    }

    function sendMessage(inputEl, a) {
      var text = inputEl && inputEl.value ? inputEl.value : '';
      if (!text) return;
      fetch('/api/agent/message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text, source: 'operator' }),
      }).catch(function () {});
      if (inputEl) inputEl.value = '';
    }

    function setOpen(v, a) {
      open = !!v;
      if (drawer) drawer.classList.toggle('open', open);
      try { drawer.style.transform = open ? 'translateX(0)' : 'translateX(100%)'; } catch (_) {}
      if (a && typeof a.reportUiState === 'function') a.reportUiState({ drawer_open: open });
    }

    handle = function (evt) {
      if (evt && (evt.event === 'message' || evt.event === 'activity')) {
        var d = evt.data || {};
        if (evt.event === 'activity' && d.kind !== 'message') return;
        var entry = {
          seq: d.seq, text: d.text || ((d.data || {}).text) || '',
          kind: d.kind || 'info', source: d.source || (d.data || {}).source || 'agent',
          ts: d.ts || d.t || Date.now() / 1000,
        };
        pushMessage(entry);
        renderList();
      }
    };
    api.onAgentEvent(handle);

    function renderList() {
      if (!list) return;
      list.innerHTML = '';
      messages.slice(-20).forEach(function (m) {
        var row = document.createElement('div');
        row.style.cssText = 'font-size:12px;padding:4px 0;border-bottom:1px solid rgba(230,233,239,.08);';
        var meta = document.createElement('div');
        meta.style.cssText = 'opacity:.5;font-size:10px;';
        meta.textContent = (m.source || 'agent') + ' · ' + (m.kind || '') + ' · #' + m.seq;
        var text = document.createElement('div');
        text.textContent = m.text || '(no text)';
        row.appendChild(meta);
        row.appendChild(text);
        list.appendChild(row);
      });
    }

    window.__gs_ui_state__ = window.__gs_ui_state__ || {};
    window.__gs_ui_state__[KEY] = { handle: handle, get: view, setOpen: setOpen };
  }, function () {
    try { if (drawer) drawer.remove(); } catch (_) {}
    try { if (toggle) toggle.remove(); } catch (_) {}
  }, { workspace: 'overview', description: 'Co-pilot drawer to converse with the agent' });
})();