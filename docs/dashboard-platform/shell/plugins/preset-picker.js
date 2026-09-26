/**
 * preset-picker.js — subscribe-preset dropdown in the sidebar header.
 *
 * Lists GET /api/presets (the default "dashboard" 4-slot flight layout plus
 * every preset in livewatch/multi_slot_presets.yaml) and switches the live
 * subscription as soon as the operator picks one (POST /api/presets/apply).
 * The service refuses the switch unless the drone reads disarmed: a switch
 * pre-clears every slot, so telemetry pauses for several seconds.
 */
(function () {
  'use strict';

  var POLL_IDLE_MS = 10000;
  var POLL_BUSY_MS = 1000;
  var _timer = null;
  var _busy = false;

  function q(id) { return typeof document !== 'undefined' ? document.getElementById(id) : null; }

  function install() {
    var anchor = q('record-control');
    if (!anchor || !anchor.parentNode) return false;
    if (q('preset-picker')) return true;
    var row = document.createElement('div');
    row.id = 'preset-picker';
    row.style.cssText = 'display:flex;align-items:center;flex-wrap:wrap;gap:6px;' +
      'width:100%;font-size:12px;margin-top:4px;';
    var label = document.createElement('span');
    label.textContent = 'Preset';
    label.style.cssText = 'color:var(--muted);';
    var sel = document.createElement('select');
    sel.id = 'preset-select';
    sel.title = 'Telemetry subscribe preset — applies immediately (disarmed only)';
    sel.style.cssText = 'flex:1 1 140px;min-width:0;background:var(--panel, #111);' +
      'color:var(--text);border:1px solid var(--border, #444);border-radius:4px;' +
      'padding:2px 4px;font-size:12px;';
    var status = document.createElement('span');
    status.id = 'preset-status';
    status.style.cssText = 'display:block;width:100%;font-size:10px;color:var(--muted);';
    row.appendChild(label);
    row.appendChild(sel);
    row.appendChild(status);
    anchor.parentNode.insertBefore(row, anchor.nextSibling);
    sel.addEventListener('change', function () { apply(sel.value); });
    return true;
  }

  function setStatus(text, color) {
    var el = q('preset-status');
    if (!el) return;
    el.textContent = text;
    el.style.color = color || 'var(--muted)';
  }

  function render(data) {
    var sel = q('preset-select');
    if (!sel || !data || !data.presets) return;
    var apply = data.apply || {};
    _busy = !!apply.busy;
    // Rebuild options only when the list changed; never while the operator
    // has the dropdown focused.
    var names = data.presets.map(function (p) { return p.name; }).join('|');
    if (sel._names !== names && document.activeElement !== sel) {
      sel.innerHTML = '';
      data.presets.forEach(function (p) {
        var o = document.createElement('option');
        o.value = p.name;
        var hz = (p.slots || []).map(function (s) { return s.slot + ':' + s.hz; }).join(' ');
        o.textContent = p.name + (hz ? '  [' + hz + ' Hz]' : '');
        o.title = p.description || '';
        sel.appendChild(o);
      });
      sel._names = names;
    }
    if (document.activeElement !== sel) sel.value = _busy ? apply.name : data.active;
    sel.disabled = _busy;
    var active = data.presets.filter(function (p) { return p.name === data.active; })[0];
    sel.title = (active && active.description) || sel.title;
    if (_busy) {
      setStatus('switching to ' + apply.name + '… telemetry pauses a few seconds', 'var(--yellow, #fc3)');
    } else if (apply.error) {
      setStatus(apply.name + ' failed: ' + apply.error + ' (restored dashboard)', 'var(--red)');
    } else {
      setStatus(active ? active.description : '');
    }
  }

  function poll() {
    if (_timer) clearTimeout(_timer);
    fetch('/api/presets')
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () { /* service restarting; keep polling */ })
      .finally(function () {
        _timer = setTimeout(poll, _busy ? POLL_BUSY_MS : POLL_IDLE_MS);
      });
  }

  function apply(name) {
    if (!name) return;
    setStatus('requesting ' + name + '…');
    fetch('/api/presets/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name })
    }).then(function (r) { return r.json(); })
      .then(function (res) {
        if (res && res.ok) { _busy = true; setStatus('switching to ' + name + '…', 'var(--yellow, #fc3)'); }
        else setStatus((res && res.error) || 'apply failed', 'var(--red)');
      })
      .catch(function () { setStatus('apply request failed', 'var(--red)'); })
      .finally(function () { setTimeout(poll, 300); });
  }

  function start() {
    if (install()) { poll(); return; }
    setTimeout(start, 500);
  }
  start();
})();
