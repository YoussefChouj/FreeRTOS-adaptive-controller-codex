/**
 * slot-manager-panel.js ?? Slot Manager / Observability panel (S15 new)
 *
 * Addresses jiang's complaint: "I don't have observability on what slot
 * I am selecting." This panel provides:
 *
 *   1. A table of all active slots (id, tag, sample count, rate Hz, loss %,
 *      var count, last update ago)
 *   2. A per-slot collapsible "Channels" list showing every key in
 *      `values`, with current numeric value formatted using a key-prefix
 *      to unit inference
 *   3. A "Subscribe slot" button row (1/2/3/9/10/11/12) that triggers
 *      a subscribe via `shellApi.subscribeSlot(slot, divider, ranges)`
 *      (added to shellApi in S15) or, as a fallback, POSTs to
 *      `/commands` with command_id=33 (the 0x21 subscribe wrapper) ?? see
 *      `submitSubscribe()` below.
 *   4. A "Refresh schema" button that forces a re-subscribe on slot 0
 *
 * Unit inference (key prefix ?? unit):
 *   - status.*            ?? - (boolean-ish flags)
 *   - mrac.*              ?? -
 *   - ekf.pos_*           ?? m
 *   - ekf.vel_*           ?? m/s
 *   - ekf.bias_*          ?? rad/s
 *   - *vbat*              ?? V
 *   - *temp*              ?? ??C
 *   - *press* / *hpa*     ?? hPa
 *   - *alt*               ?? m
 *   - default numeric     ?? raw float
 */
(function () {
  'use strict';

  // ???? Config ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
  // SUBSCRIBABLE_SLOTS mirrors SUBSCRIBE_MAX_SLOTS = 4 in API/subscribe.h
  // -- the firmware only accepts slot 0..3. Slots 9..12 were a host-side
  // design idea for "typed-stream equivalents" that the firmware never
  // implemented; requesting them gets an "E:bad slot" rejection. Hide them
  // here so the dashboard never builds a frame the drone will refuse.
  //
  // Slot 0 IS subscribable: clicking it re-runs `_request_slot0_schema`
  // (the dashboard layout auto-subscribe), which is exactly what the
  // "Refresh schema" button used to do in isolation. Surfacing it here
  // lets the operator re-subscribe slot 0 explicitly with a chosen
  // divider instead of being stuck at the auto-default (divider=4).
  var SUBSCRIBABLE_SLOTS = [0, 1, 2, 3];
  var SUBSCRIBE_DIVIDER_DEFAULT = 1;

  // Send_Task cadence assumption for the Hz -> divider mapping. The
  // normal-flight loop was hardware-measured at 99.99994 Hz on 2026-09-20
  // (SystemCoreClock 168 MHz, g_send_prof.period_cycles 1680001). The
  // SysID/optical-flow branch (USER/main.c:305, vTaskDelay 5 ms relative)
  // was measured at 80.2 Hz in MIXED mode. The cadence is therefore an
  // assumption the form states visibly, never a universal constant: the
  // operator can edit it or use the two quick-set buttons.
  var DEFAULT_CADENCE_HZ = 100;
  var MIXED_CADENCE_HZ = 80.2;
  var WIRE_MAX_DIVIDER = 255; // divider is one wire byte (0 = STOP, 1..255)

  // ???? State ??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
  var _state = null;
  var _expanded = {}; // slotId ?? bool
  var _selectedSlot = null;        // last slot the user subscribed to
  var _selectedAt   = null;        // wallclock ms when last subscribed

  // ???? DOM helpers ??????????????????????????????????????????????????????????????????????????????????????????????????????????????
  function q(id) { return document.getElementById(id); }

  // ??? Hz -> divider math (mirror of rate_planner.choose_divider) ???
  // The achievable rates are the discrete ladder cadence/1, cadence/2,
  // cadence/3, ... so desired 30 Hz at 100 Hz can only be 33.3 (div 3)
  // or 25 (div 4). Policy matches the backend: fastest ladder rung that
  // does NOT exceed the request -> divider = ceil(cadence/desired - eps),
  // clipped to >= 1. The wire byte's 1..255 range is applied afterwards.
  function _chooseDivider(desiredHz, cadenceHz) {
    if (!(desiredHz > 0) || !(cadenceHz > 0)) return null;
    return Math.max(1, Math.ceil(cadenceHz / desiredHz - 1e-9));
  }

  function _fmtHz(v) {
    if (typeof v !== 'number' || !isFinite(v)) return String(v);
    // Up to three decimals, trailing zeros trimmed (25.000 -> "25").
    return v.toFixed(3).replace(/\.?0+$/, '');
  }

  // Read the rate form and return the honest plan: what was asked, what
  // divider goes on the wire, and what Hz the slot will actually run at.
  function _rateChoice() {
    var desired = parseFloat(q('sm-hz').value);
    var cadence = parseFloat(q('sm-cadence').value);
    var divider = _chooseDivider(desired, cadence);
    if (divider === null) return null;
    var wireDivider = Math.min(divider, WIRE_MAX_DIVIDER);
    return {
      desired: desired,
      cadence: cadence,
      divider: wireDivider,
      cappedToWireMax: divider > WIRE_MAX_DIVIDER,
      achieved: cadence / wireDivider,
    };
  }

  function renderHzReadout() {
    var el = q('sm-hz-readout');
    if (!el) return;
    var choice = _rateChoice();
    if (!choice) {
      el.textContent = 'Enter a desired rate in Hz (cadence must also be > 0).';
      return;
    }
    var exact = Math.abs(choice.achieved - choice.desired) < 1e-6;
    var msg = 'Asked ' + _fmtHz(choice.desired) + ' Hz → you will get ' +
              _fmtHz(choice.achieved) + ' Hz (wire divider ' + choice.divider + ')' +
              (exact ? '' : ' — not an exact ladder rung') +
              ' · based on cadence ' + _fmtHz(choice.cadence) + ' Hz';
    if (choice.cappedToWireMax) {
      msg += ' · divider limited to ' + WIRE_MAX_DIVIDER + ' (slowest rung)';
    }
    el.textContent = msg;
  }

  // ???? Unit inference from key prefix ????????????????????????????????????????????????????????????????????????
  function inferUnit(key) {
    var k = key.toLowerCase();
    if (/^(status|mrac)\./.test(key)) return '';
    if (k.indexOf('pos_x') !== -1 || k.indexOf('pos_y') !== -1 || k.indexOf('pos_z') !== -1 ||
        k.indexOf('alt')   !== -1) return 'm';
    if (k.indexOf('vel_x') !== -1 || k.indexOf('vel_y') !== -1 || k.indexOf('vel_z') !== -1) return 'm/s';
    if (k.indexOf('bias_gyro') === 0) return 'rad/s';
    if (k.indexOf('bias_accel') === 0) return 'm/s?';
    if (k.indexOf('vbat') !== -1 || k.indexOf('volt') !== -1) return 'V';
    if (k.indexOf('temp') !== -1) return '??C';
    if (k.indexOf('press') !== -1 || k.indexOf('hpa') !== -1) return 'hPa';
    if (k.indexOf('rpm') !== -1) return 'rpm';
    if (k.indexOf('current') !== -1) return 'A';
    if (k.indexOf('count') !== -1 || k.indexOf('depth') !== -1 || k.indexOf('bytes') !== -1) return '';
    if (k.indexOf('seq') !== -1) return '';
    return '';
  }

  function formatVal(key, v) {
    if (v == null) return '??';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'number') {
      if (Number.isInteger(v)) return v.toLocaleString();
      var abs = Math.abs(v);
      if (abs !== 0 && (abs < 0.01 || abs >= 1e5)) return v.toExponential(3);
      return v.toFixed(4);
    }
    return String(v);
  }

  // ??? Subscribe trigger ??????????????????????????????????????????????????????
  // The shell always exposes `shellApi.subscribeSlot(slot, divider, ranges)`
  // which POSTs to the dedicated /subscribe endpoint (added S15). The
  // /commands endpoint used to interpret command_id=33 as a 0x21 wrapper,
  // but the firmware only recognises 0x21 envelopes built and shipped by
  // WifiBridge.subscribe_slot() ? not as generic 0xCC 0xDD command frames.
  // The previous "POST /commands fallback" was a TODO marker that never
  // worked and silently swallowed the user's intent; the typed /subscribe
  // path is now canonical so we removed the fallback entirely.
  function submitSubscribe(api, slotId, divider, ranges) {
    if (!api || typeof api.subscribeSlot !== 'function') {
      // Shell API contract violation ? index.html always wires subscribeSlot.
      // Surface as a structured error so the user sees something instead of
      // silent failure.
      return Promise.resolve({
        ok: false,
        error: 'shellApi.subscribeSlot is unavailable (shell version mismatch?)',
        note: 'Reload the page; if the problem persists, check the browser console for shell load errors.',
      });
    }
    return _previewSubscribe(api, slotId, divider, ranges).then(function (preview) {
      _previewResult = preview;
      renderPreview();
      if (preview && preview.unresolved && preview.unresolved.length > 0) {
        return {
          ok: false,
          via: 'subscribe_preview_blocked',
          error: preview.unresolved.length + ' DWARF name(s) unresolved against the ELF: ' +
                 preview.unresolved.slice(0, 5).join(', ') +
                 (preview.unresolved.length > 5 ? ', +' + (preview.unresolved.length - 5) + ' more' : ''),
          note: 'Edit the ranges input to remove the unresolved names, then click Subscribe again. The /subscribe/preview endpoint is the single source of truth for what will resolve.',
          preview: preview,
        };
      }
      return api.subscribeSlot(slotId, divider || SUBSCRIBE_DIVIDER_DEFAULT, ranges || [])
        .then(function (data) {
          return { ok: true, via: 'shellApi.subscribeSlot', data: data, preview: preview };
        })
        .catch(function (err) {
          return {
            ok: false,
            via: 'shellApi.subscribeSlot',
            error: err && err.message ? err.message : String(err),
            note: 'subscribe rejected by /subscribe endpoint ? check that the FC is in MIXED or SUBSCRIBE_ONLY mode and that the dashboard layout is loaded',
            preview: preview,
          };
        });
    });
  }

  // S16 ? subscribe preview: run validation against the firmware ELF
  // BEFORE sending the actual 0x21 envelope. Forward-looking pattern
  // from PLANNING_PROMPT ?8 Pattern 3. The /subscribe/preview endpoint
  // (added in api.py) is pure: it never opens the WiFi socket, never
  // touches _pending_schema_ranges. Safe to call on every keypress.
  function _previewSubscribe(api, slotId, divider, ranges) {
    if (!api || typeof api.subscribeSlot !== 'function') {
      return Promise.resolve(null);
    }
    if (typeof api.previewSubscribe === 'function') {
      return api.previewSubscribe(slotId, divider, ranges).catch(function () { return null; });
    }
    return fetch('/subscribe/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slot: slotId, divider: divider, ranges: ranges || [] }),
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).catch(function () { return null; });
  }

  // ???? Per-slot metadata reading ????????????????????????????????????????????????????????????????????????????????????
  function readSlotMeta(s, slotId) {
    if (!s) return null;
    var vals = s.values || {};
    var prefix = 'slot' + slotId + '.';
    return {
      tag:      s.tag != null ? s.tag : null,
      sequence: s.sequence != null ? s.sequence : (vals[prefix + 'seq'] != null ? vals[prefix + 'seq'] : null),
      received: s.received != null ? s.received : (vals[prefix + 'received'] != null ? vals[prefix + 'received'] : 0),
      dropped:  s.dropped  != null ? s.dropped  : (vals[prefix + 'dropped']  != null ? vals[prefix + 'dropped']  : 0),
      loss_pct: s.loss_pct != null ? s.loss_pct : (vals[prefix + 'loss_pct'] != null ? vals[prefix + 'loss_pct'] : 0),
    };
  }

  // Rolling rate calculation
  var _prevSlot = {};
  function computeRate(slotId, meta) {
    if (!meta || meta.sequence == null) return 0;
    var now = Date.now();
    var prev = _prevSlot[slotId];
    _prevSlot[slotId] = { seq: meta.sequence, ts: now };
    if (!prev) return 0;
    var dSeq = meta.sequence - prev.seq;
    var dT = (now - prev.ts) / 1000;
    if (dT <= 0 || dSeq <= 0) return 0;
    return dSeq / dT;
  }

  function countVars(values) {
    if (!values) return 0;
    var n = 0;
    var metaRe = /^(slot\d+\.|seq|received|dropped|loss_pct|t_ms|source_time_ms)/;
    Object.keys(values).forEach(function (k) {
      if (!metaRe.test(k)) n++;
    });
    return n;
  }

  // ???? Build panel HTML ????????????????????????????????????????????????????????????????????????????????????????????????????
  function buildHTML() {
    return [
      '<style>',
      '.sm-container { display:flex; flex-direction:column; gap:12px; }',
      '.sm-summary { display:flex; align-items:center; justify-content:space-between; padding:8px 12px;',
      '  background:rgba(0,0,0,0.2); border-radius:6px; }',
      '.sm-subscribe { display:flex; gap:6px; align-items:center; flex-wrap:wrap;',
      '  padding:8px 10px; background:var(--bg); border:1px solid var(--border); border-radius:4px; }',
      '.sm-slot-btn { padding:5px 10px; border-radius:4px; font-size:11px; font-weight:600; cursor:pointer;',
      '  background:var(--bg); color:var(--text); border:1px solid var(--border); }',
      '.sm-slot-btn:hover { border-color:var(--accent); }',
      '.sm-slot-btn.active { background:var(--accent); color:var(--text); }',
      '.sm-preview { padding:6px 10px; border-radius:4px; font-size:11px; background:rgba(0,0,0,0.18);',
      '  border:1px solid var(--border); }',
      '.sm-table { width:100%; border-collapse:collapse; font-size:12px; }',
      '.sm-table th { text-align:left; font-size:10px; font-weight:600; color:var(--muted);',
      '  text-transform:uppercase; letter-spacing:0.06em; padding:4px 8px;',
      '  border-bottom:1px solid var(--border); }',
      '.sm-table td { padding:5px 8px; font-family:Consolas, monospace; }',
      '.sm-table tr:hover td { background:rgba(255,255,255,0.03); }',
      '.sm-row-clickable { cursor:pointer; }',
      '.sm-channels-list { padding:6px 12px; background:rgba(0,0,0,0.15); border-top:1px solid var(--border); }',
      '.sm-channel-row { display:flex; justify-content:space-between; padding:2px 0; font-family:Consolas, monospace; font-size:11px; }',
      '.sm-channel-row:hover { background:rgba(255,255,255,0.03); }',
      '.sm-channel-key { color:var(--amber); }',
      '.sm-channel-val { color:var(--green); }',
      '.sm-channel-unit { color:var(--muted); margin-left:4px; }',
      '.sm-result { font-size:11px; padding:6px 10px; border-radius:4px; background:var(--bg);',
      '  border:1px solid var(--border); margin-top:6px; }',
      '.sm-result-ok    { color:var(--green); }',
      '.sm-result-err   { color:var(--red); }',
      '.sm-selection { display:flex; align-items:center; gap:8px; padding:6px 10px;',
      '  border-radius:4px; font-size:12px; background:var(--bg);',
      '  border:1px solid var(--accent); }',
      '.sm-selection-empty { display:none; }',
      '.sm-selection-label { color:var(--muted); font-size:11px; }',
      '.sm-selection-id    { color:var(--accent); font-weight:700; font-family:Consolas,monospace; }',
      '.sm-selection-meta  { color:var(--muted); font-size:11px; }',
      '.loss-warn    { color:var(--amber); }',
      '.loss-critical { color:var(--red); }',
      '.sm-fresh { display:inline-block; margin-left:6px; padding:1px 6px;',
      '  border-radius:8px; font-size:10px; font-weight:600; font-family:Consolas,monospace; }',
      '.fresh-ok    { background:rgba(78,204,163,0.15); color:var(--green); }',
      '.fresh-mixed { background:rgba(245,166,35,0.15); color:var(--amber); }',
      '.fresh-bad   { background:rgba(233,69,96,0.15); color:var(--red); }',
      // Row-status colors ? driven by /health/slots status field.
      // live = all keys fresh (green), mixed = some stale (amber),
      // stale = all stale (red), dead = no data (dark/muted).
      '.sm-row-live  td { background:rgba(78,204,163,0.04); }',
      '.sm-row-mixed td { background:rgba(245,166,35,0.04); }',
      '.sm-row-stale td { background:rgba(233,69,96,0.06); }',
      '.sm-row-dead  td { opacity:0.45; }',
      // Symbol picker
      '.sm-picker { padding:8px 10px; background:var(--bg); border:1px solid var(--border); border-radius:4px; }',
      '.sm-picker-result { display:flex; align-items:center; gap:6px; padding:2px 6px;',
      '  font-family:Consolas,monospace; font-size:11px; }',
      '.sm-picker-result:hover { background:rgba(255,255,255,0.04); }',
      '.sm-picker-name { flex:1; color:var(--amber); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }',
      '.sm-picker-add, .sm-picker-drill { padding:1px 7px; font-size:11px; line-height:1.4; }',
      '.sm-picker-drill { color:var(--accent); }',
      '.sm-crumb { cursor:pointer; color:var(--accent); }',
      '.sm-crumb:hover { text-decoration:underline; }',
      '</style>',

      '<div class="sm-container">',
      // Top summary row
      '  <div class="sm-summary">',
      '    <div>',
      '      <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Active Slots</div>',
      '      <div id="sm-active-count" style="font-size:18px;font-weight:700;font-family:Consolas,monospace">??</div>',
      '    </div>',
      '    <div style="display:flex;gap:8px">',
      '      <button id="sm-refresh-schema" class="sm-slot-btn">? Refresh schema</button>',
      '    </div>',
      '  </div>',

      // Subscribe controls
      '  <div class="sm-subscribe">',
      '    <span style="font-size:11px;color:var(--muted);margin-right:6px" title="Slots are independent telemetry channels. You can subscribe to up to 4 concurrently.">Telemetry Channel (Slot):</span>',
      SUBSCRIBABLE_SLOTS.map(function (s) {
        return '<button class="sm-slot-btn sm-channel-btn' + (s === 0 ? ' active' : '') + '" data-slot="' + s + '">' + s + '</button>';
      }).join(''),
      '    <span id="sm-rate-wrap" style="margin-left:8px;display:inline-flex;align-items:center;gap:4px">',
      '      <span style="font-size:11px;color:var(--muted)">rate</span>',
      '      <input id="sm-hz" type="number" min="0.4" max="200" step="1" value="100" title="Desired slot rate in Hz. The firmware runs a discrete ladder (cadence/divider); the readout shows the honest achievable rate." style="width:56px;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:2px 6px;font-family:Consolas,monospace;font-size:11px"/>',
      '      <span style="font-size:11px;color:var(--muted)">Hz desired</span>',
      '      <span style="font-size:10px;color:var(--muted);margin-left:6px">at cadence</span>',
      '      <input id="sm-cadence" type="number" min="1" max="250" step="0.1" value="100" title="Assumed Send_Task cadence in Hz. Measured 99.99994 Hz in normal flight (2026-09-20) and 80.2 Hz in MIXED/SysID mode. All Hz arithmetic scales from this." style="width:56px;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:2px 6px;font-family:Consolas,monospace;font-size:11px"/>',
      '      <span style="font-size:10px;color:var(--muted)">Hz</span>',
      '      <button class="sm-slot-btn sm-cadence-btn" data-cadence="100" title="Normal-flight Send_Task, hardware-measured 99.99994 Hz on 2026-09-20">flight 100</button>',
      '      <button class="sm-slot-btn sm-cadence-btn" data-cadence="80.2" title="SysID / optical-flow branch, measured 80.2 Hz in MIXED mode (USER/main.c:305)">MIXED 80.2</button>',
      '    </span>',
      '  </div>',
      // Honest Hz readout: asked vs achieved, the wire divider kept as
      // secondary info, and the cadence the arithmetic was based on.
      '  <div id="sm-hz-readout" class="sm-preview"></div>',

      // Subscribe preview ? shows how many DWARF names resolve against
      // the firmware ELF before the user clicks Subscribe. Empty until
      // the user types ranges or selects a different slot. The preview
      // re-runs on every input change (debounced via refreshPreview).
      '  <div id="sm-preview" class="sm-preview" style="display:none"></div>',

      // Symbol browser (C1): type-to-filter over real DWARF names from
      // GET /api/symbols, drill into structs/arrays, click + to add a name
      // to the ranges textarea. The textarea and presets stay as the
      // expert path; the picker just feeds them.
      '  <div class="sm-picker">',
      '    <div style="font-size:10px;color:var(--muted);margin-bottom:4px">Browse firmware symbols (DWARF):</div>',
      '    <input id="sm-picker-filter" type="text" placeholder="Type to filter, e.g. mrac, ekf, imu..." autocomplete="off"',
      '      style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:5px 8px;font-family:Consolas,monospace;font-size:11px"/>',
      '    <div id="sm-picker-breadcrumb" style="margin-top:4px;font-size:10px"></div>',
      '    <div id="sm-picker-results" style="margin-top:4px;max-height:220px;overflow:auto;border:1px solid var(--border);border-radius:3px;background:rgba(0,0,0,0.15)"></div>',
      '    <div id="sm-picker-status" style="margin-top:4px;font-size:10px;color:var(--muted)"></div>',
      '  </div>',

      // DWARF-name ranges input. Non-zero slots REQUIRE explicit ranges
      // (the wifi bridge raises ``ValueError("slot N requires explicit
      // ranges")`` for slots 1..3 without one). Slot 0 has a hardcoded
      // auto-subscribe layout (boot_default or dashboard) so the ranges
      // input is hidden when slot 0 is selected.
      '  <div class="sm-ranges">',
      '    <div style="font-size:10px;color:var(--muted);margin-bottom:4px">DWARF name ranges (comma- or whitespace-separated, one per slot request):</div>',
      '    <textarea id="sm-ranges" rows="3" placeholder="e.g. mrac_state.roll.Theta[0], mrac_state.roll.e, imu_data.rol" ',
      '      style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:6px 8px;font-family:Consolas,monospace;font-size:11px;resize:vertical"></textarea>',
      '    <div style="display:flex;gap:6px;margin-top:6px;align-items:center;flex-wrap:wrap">',
      '      <span style="font-size:11px;color:var(--muted)">Quick-fill:</span>',
      '      <button class="sm-slot-btn sm-preset-btn" data-preset="mrac">MRAC weights</button>',
      '      <button class="sm-slot-btn sm-preset-btn" data-preset="ekf">EKF 9-state</button>',
      '      <button class="sm-slot-btn sm-preset-btn" data-preset="imu">IMU full</button>',
      '      <button class="sm-slot-btn sm-preset-btn" data-preset="of">Optical flow</button>',
      '      <button class="sm-slot-btn sm-preset-btn" data-preset="pid">PID loops</button>',
      '    </div>',
      '  </div>',
      '  <div style="margin-top:8px;">',
      '    <button id="sm-submit-btn" class="sm-slot-btn" style="background:var(--accent);color:var(--bg);font-weight:bold;padding:6px 12px">Subscribe to Channel</button>',
      '  </div>',

      // Selection indicator ?? shows the most recently subscribed slot + when.
      '  <div id="sm-selection" class="sm-selection sm-selection-empty" style="display:none">',
      '    <span class="sm-selection-label">Currently selected slot:</span>',
      '    <span id="sm-selection-id" class="sm-selection-id">??</span>',
      '    <span class="sm-selection-meta">',
      '      subscribed <span id="sm-selection-age">??</span> ago',
      '    </span>',
      '    <button id="sm-clear-selection" class="sm-slot-btn" style="margin-left:auto">Clear</button>',
      '  </div>',

      // Result / error feedback area
      '  <div id="sm-result" class="sm-result" style="display:none"></div>',

      // Active slots table
      '  <div>',
      '    <div style="font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px">Active Slot Inventory</div>',
      '    <table class="sm-table">',
      '      <thead>',
      '        <tr><th>#</th><th>Tag</th><th>Vars</th><th>Seq</th><th>Rate</th><th>Loss</th><th>Samples</th><th>Last</th><th></th></tr>',
      '      </thead>',
      '      <tbody id="sm-slot-tbody"><tr><td colspan="9" style="color:var(--muted);text-align:center">No active slots</td></tr></tbody>',
      '    </table>',
      '  </div>',
      '</div>',
    ].join('');
  }

  function showResult(text, isOk) {
    var el = q('sm-result');
    if (!el) return;
    el.textContent = text;
    el.style.display = '';
    el.className = 'sm-result ' + (isOk === true ? 'sm-result-ok' : (isOk === false ? 'sm-result-err' : ''));
    setTimeout(function () { el.style.display = 'none'; }, 5000);
  }

  // ??? Fresh / stale key indicator ????????????????????????????????????????
  // Reads from the _healthBySlot cache populated by /health/slots.
  // Returns inline HTML appended to the "Last" cell so operators see
  // the per-key freshness breakdown at a glance ("14/18 fresh").
  function renderFreshStale(slotId) {
    var h = _healthBySlot[slotId];
    if (!h) return '';
    var fresh = h.fresh_keys != null ? h.fresh_keys : 0;
    var stale = h.stale_keys != null ? h.stale_keys : 0;
    var total = fresh + stale;
    if (total === 0) return '';
    var cls = stale === 0 ? 'fresh-ok' : (fresh === 0 ? 'fresh-bad' : 'fresh-mixed');
    return ' <span class="sm-fresh ' + cls + '" title="fresh/stale keys per slot">' +
           fresh + '/' + total + '</span>';
  }

  // --- Subscribe preview rendering ----------------------------------
  // Pure render of the _previewResult state. Hidden when no preview
  // has run yet (e.g. before bindEvents fires _schedulePreview).
  // Shows resolved/unresolved counts plus the expected wire rate so
  // the operator knows what the FC will publish BEFORE they click
  // Subscribe. Pattern from PLANNING_PROMPT ?8 Pattern 3.
  var _previewResult = null;
  function renderPreview() {
    var el = q('sm-preview');
    if (!el) return;
    if (!_previewResult) { el.style.display = 'none'; return; }
    var unresolved = _previewResult.unresolved || [];
    var resolved   = _previewResult.ranges || [];
    var slot       = _previewResult.slot;
    var divider    = _previewResult.divider;
    el.style.display = '';
    var html = '';
    html += '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">';
    html += '<span style="font-size:11px;color:var(--muted)">Preview slot ' + slot + ' — wire divider ' + divider + ':</span>';
    html += '<span class="sm-fresh fresh-ok">' + resolved.length + ' resolved</span>';
    if (unresolved.length) {
      html += '<span class="sm-fresh fresh-bad" title="' + unresolved.join(', ') + '">' +
              unresolved.length + ' unresolved</span>';
    }
    // Cadence-honest achieved Hz from the rate form; the preview response's
    // expected_rate_hz is hardwired to the 100 Hz contract, so it is not
    // used here when the form states a different cadence.
    var choice = _rateChoice();
    var achievedHz = null;
    if (choice && choice.divider === divider) {
      achievedHz = choice.achieved;
      html += '<span class="sm-fresh fresh-mixed" title="achieved ' + choice.cadence + ' Hz cadence / divider ' + divider + '">' +
              _fmtHz(choice.achieved) + ' Hz achieved @ ' + _fmtHz(choice.cadence) + ' Hz cadence</span>';
    } else if (typeof _previewResult.expected_rate_hz === 'number' &&
               _previewResult.expected_rate_hz > 0) {
      achievedHz = _previewResult.expected_rate_hz;
      html += '<span class="sm-fresh fresh-mixed">~' + _previewResult.expected_rate_hz.toFixed(1) + ' Hz expected</span>';
    }
    // Bytes/s projection against the WiFi link budget. The backend returns
    // the honest payload sum (resolved DWARF symbol sizes) and the projected
    // bps at the 100 Hz contract; we re-scale that to the cadence-honest
    // achieved rate here so it tracks whatever divider the rate form chose.
    var projectedBps = _previewResult.projected_bps;
    if (typeof projectedBps === 'number' && projectedBps > 0 && achievedHz && _previewResult.expected_rate_hz > 0) {
      projectedBps = projectedBps * (achievedHz / _previewResult.expected_rate_hz);
    }
    if (typeof projectedBps === 'number' && projectedBps >= 0) {
      html += '<span class="sm-fresh" style="background:rgba(0,0,0,0.25);color:var(--text)" title="Shared WiFi telemetry link budget. Capacity and used are shown live in the Bandwidth panel (WIFI_LINK_CAPACITY_BPS = 91304 B/s from docs/telemetry-protocol.md).">' +
              '&#8645; ' + projectedBps.toFixed(0) + ' B/s of 91,304 B/s (' + Math.min(100, projectedBps / 91304 * 100).toFixed(1) + '%)</span>';
    }
    html += '</div>';
    el.innerHTML = html;
  }

  function refreshPreview(api) {
    var selectedSlot = _activeChannelId;
    var rateChoice = _rateChoice();
    var divider = rateChoice ? rateChoice.divider : SUBSCRIBE_DIVIDER_DEFAULT;
    var ranges = selectedSlot === 0 ? [] : _readRanges();
    if (typeof api.previewSubscribe === 'function') {
      api.previewSubscribe(selectedSlot, divider, ranges).then(function (res) {
        _previewResult = res;
        renderPreview();
      }).catch(function () { _previewResult = null; renderPreview(); });
      return;
    }
    fetch('/subscribe/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slot: selectedSlot, divider: divider, ranges: ranges }),
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (res) {
      _previewResult = res;
      renderPreview();
    }).catch(function () { _previewResult = null; renderPreview(); });
  }

  // ???? Render slot table ??????????????????????????????????????????????????????????????????????????????????????????????????
  function renderSlotTable(state) {
    var tbody = q('sm-slot-tbody');
    if (!tbody) return;
    if (!state || !state.streams || Object.keys(state.streams).length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" style="color:var(--muted);text-align:center">No active slots</td></tr>';
      var cEl = q('sm-active-count');
      if (cEl) cEl.textContent = '0';
      return;
    }

    var slotIds = Object.keys(state.streams);
    if (q('sm-active-count')) q('sm-active-count').textContent = slotIds.length;

    var html = '';
    slotIds.forEach(function (slotId) {
      var s = state.streams[slotId];
      var meta = readSlotMeta(s, slotId);
      var rate = computeRate(slotId, meta);
      var varCount = countVars(s.values || {});
      var ageText = '??';
      if (_prevSlot[slotId]) {
        ageText = (((Date.now() - _prevSlot[slotId].ts) / 1000)).toFixed(1) + ' s';
      }
      var totalSamples = (meta.received || 0) + (meta.dropped || 0);
      var lossClass = meta.loss_pct > 5 ? 'loss-critical' :
                      meta.loss_pct > 1 ? 'loss-warn' : '';
      // Color the entire row by /health/slots status. The slot-status
      // classification (dead / stale / mixed / live) is derived server
      // side from fresh_keys/stale_keys counts. Forward-looking
      // pattern from PLANNING_PROMPT ?8 Pattern 2.
      var rowStatus = _healthBySlot[slotId] && _healthBySlot[slotId].status
                      ? _healthBySlot[slotId].status : '';
      var rowCls = rowStatus === 'dead'  ? 'sm-row-dead'  :
                   rowStatus === 'stale' ? 'sm-row-stale' :
                   rowStatus === 'mixed' ? 'sm-row-mixed' :
                   rowStatus === 'live'  ? 'sm-row-live'  : '';

      // Per-slot subscribe transaction state from /health/slots
      // ``request_state`` (planned -> sent -> schema_received -> streaming,
      // set by WifiBridge._slot_states). Rendered as a small badge in the
      // slot cell so the operator sees the pending/acked/live lifecycle the
      // fixed /subscribe path executes, and can tell a stuck slot from a
      // healthy one at a glance.
      var reqState = (_healthBySlot[slotId] && _healthBySlot[slotId].request_state) || 'planned';
      var stateBadge = '';
      if (reqState === 'streaming') {
        stateBadge = '<span class="sm-fresh fresh-ok" title="schema acked, frames flowing">live</span>';
      } else if (reqState === 'schema_received') {
        stateBadge = '<span class="sm-fresh fresh-mixed" title="schema acked, awaiting first framed value">acked</span>';
      } else if (reqState === 'sent') {
        stateBadge = '<span class="sm-fresh fresh-mixed" title="subscribe request sent, no schema reply yet">pending</span>';
      } else {
        stateBadge = '<span class="sm-fresh fresh-bad" title="no subscribe request in flight or no reply">error</span>';
      }

      html += '<tr class="sm-row-clickable ' + rowCls + '" data-slot="' + slotId + '">' +
        '<td><strong>Slot ' + slotId + '</strong> ' + stateBadge + '</td>' +
        '<td>' + (meta.tag != null ? meta.tag : '??') + '</td>' +
        '<td>' + varCount + '</td>' +
        '<td>' + (meta.sequence != null ? meta.sequence : '??') + '</td>' +
        '<td>' + rate.toFixed(1) + ' Hz</td>' +
        '<td class="' + lossClass + '">' + meta.loss_pct.toFixed(2) + '%</td>' +
        '<td>' + totalSamples.toLocaleString() + '</td>' +
        '<td>' + ageText + renderFreshStale(slotId) + '</td>' +
        '<td>' + (_expanded[slotId] ? '??' : '?') + '</td>' +
        '</tr>';

      if (_expanded[slotId]) {
        var vals = s.values || {};
        var keys = Object.keys(vals);
        if (keys.length === 0) {
          html += '<tr><td colspan="9" class="sm-channels-list" style="color:var(--muted);font-style:italic">(no values)</td></tr>';
        } else {
          keys.sort();
          var chanRows = keys.map(function (k) {
            var v = vals[k];
            var unit = inferUnit(k);
            return '<div class="sm-channel-row">' +
              '<span class="sm-channel-key">' + k + '</span>' +
              '<span><span class="sm-channel-val">' + formatVal(k, v) + '</span>' +
              (unit ? '<span class="sm-channel-unit">' + unit + '</span>' : '') +
              '</span></div>';
          }).join('');
          html += '<tr><td colspan="9" class="sm-channels-list">' + chanRows + '</td></tr>';
        }
      }
    });

    tbody.innerHTML = html;

    // Wire click handlers for collapse/expand
    Array.prototype.forEach.call(tbody.querySelectorAll('tr.sm-row-clickable'), function (row) {
      row.addEventListener('click', function () {
        var slotId = row.getAttribute('data-slot');
        _expanded[slotId] = !_expanded[slotId];
        renderSlotTable(_state);
      });
    });
  }

  var _rafPending = false;
  function rafPending() { return _rafPending; }
  function setRafPending(v) { _rafPending = v; }

  // ???? State handler ??????????????????????????????????????????????????????????????????????????????????????????????????????????????
  function onState(state) {
    _state = state;
    if (rafPending()) return;
    setRafPending(true);
    requestAnimationFrame(function () {
      renderSlotTable(state);
      setRafPending(false);
    });
  }

  // ???? Presets (one-click fills for the DWARF ranges textarea) ??????
  // Mirror of the manifests in ground_station/livewatch/manifests.yaml.
  // Adding a new manifest here is a one-line change.
  var _PRESETS = {
    mrac: 'mrac_state.roll.Theta[0], mrac_state.roll.Theta[1], mrac_state.roll.Theta[2], mrac_state.roll.e, mrac_state.roll.u_ad, mrac_state.pitch.Theta[0], mrac_state.pitch.Theta[1], mrac_state.pitch.e, mrac_state.pitch.u_ad, mrac_state.yaw.e, mrac_state.yaw.u_ad, mrac_state.z_rate.e, mrac_state.z_rate.u_ad',
    ekf:  's_ekf.x[0], s_ekf.x[1], s_ekf.x[2], s_ekf.x[3], s_ekf.x[4], s_ekf.x[5], s_ekf.x[6], s_ekf.x[7], s_ekf.x[8]',
    imu:  'imu_data.rol, imu_data.pit, imu_data.yaw, Gyro_X_Real, Gyro_Y_Real, Gyro_Z_Real, Gyro_X_Lpf, Gyro_Y_Lpf, Gyro_Z_Lpf, Acc_X_Real, Acc_Y_Real, Acc_Z_Real',
    of:   'ano_of.earth_x, ano_of.earth_y, ano_of.earth_x_ture, ano_of.earth_y_ture, ano_of.DISTANCE_X, ano_of.DISTANCE_Y, ano_of.of_quality, ano_of.of2_dx_fix, ano_of.of2_dy_fix, s_of_bias_x, s_of_bias_y, g_estimator_ready',
    pid:  'Ctrler.rollPID.Des, Ctrler.rollPID.FB, Ctrler.rollPID.U, Ctrler.pitchPID.Des, Ctrler.pitchPID.FB, Ctrler.pitchPID.U, Ctrler.yawPID.Des, Ctrler.yawPID.FB, Ctrler.yawPID.U, Ctrler.gyroxPID.Des, Ctrler.gyroxPID.FB, Ctrler.gyroxPID.U',
  };

  function _parseRanges(text) {
    if (!text) return [];
    return text.split(/[,\s]+/).map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function _readRanges() {
    var ta = q('sm-ranges');
    return ta ? _parseRanges(ta.value) : [];
  }

  // ???? Symbol picker (C1) ?????????????????????????????????????????????
  // Calls GET /api/symbols; the textarea stays the edit surface and the
  // picker only appends to it. Root view = base variable names with
  // prefix filtering; drill view = members/elements of one path.
  var _pickerParent = null;   // drilled-in path, null = root base-name view
  var _pickerRows = [];
  var _pickerDrillable = {};
  var _pickerTimer = null;
  var _pickerEpoch = 0;
  var _rangesChanged = null;  // hook to the debounced preview (set in bindEvents)

  function _symbolsUrl(params) {
    var parts = Object.keys(params).filter(function (k) {
      return params[k] !== '' && params[k] != null;
    }).map(function (k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
    });
    return '/api/symbols' + (parts.length ? '?' + parts.join('&') : '');
  }

  function _fetchSymbols(params) {
    return fetch(_symbolsUrl(params)).then(function (r) {
      return r.json().then(function (body) { return { status: r.status, body: body }; });
    });
  }

  function _pickerSetStatus(text) {
    var el = q('sm-picker-status');
    if (el) el.textContent = text;
  }

  function _renderPickerRows(filterText) {
    var box = q('sm-picker-results');
    if (!box) return;
    var filter = (filterText || '').toLowerCase();
    var rows = _pickerRows.filter(function (n) {
      return !filter || n.toLowerCase().indexOf(filter) !== -1;
    });
    if (!rows.length) {
      box.innerHTML = '<div class="sm-picker-result" style="color:var(--muted)">(no matching symbols)</div>';
      return;
    }
    box.innerHTML = rows.map(function (n) {
      var drill = _pickerDrillable[n];
      return '<div class="sm-picker-result">' +
        '<span class="sm-picker-name" title="' + n + '">' + n + '</span>' +
        '<button class="sm-slot-btn sm-picker-add" data-name="' + encodeURIComponent(n) + '" title="add to ranges">+</button>' +
        (drill ? '<button class="sm-slot-btn sm-picker-drill" data-name="' + encodeURIComponent(n) + '" title="browse members / elements">&#9656;</button>' : '') +
        '</div>';
    }).join('');
  }

  function _renderPickerBreadcrumb() {
    var el = q('sm-picker-breadcrumb');
    if (!el) return;
    if (!_pickerParent) {
      el.innerHTML = '<span style="color:var(--muted)">base variables (globals / file statics)</span>';
      return;
    }
    var parts = _pickerParent.split('.');
    var html = '<span class="sm-crumb" data-crumb="">base</span>';
    var acc = '';
    parts.forEach(function (p, i) {
      acc = acc ? acc + '.' + p : p;
      html += ' <span style="color:var(--muted)">&#9656;</span> ';
      if (i === parts.length - 1) {
        html += '<span style="color:var(--amber)">' + p + '</span>';
      } else {
        html += '<span class="sm-crumb" data-crumb="' + encodeURIComponent(acc) + '">' + p + '</span>';
      }
    });
    el.innerHTML = html;
  }

  function _applyPickerPayload(res, filterText) {
    var body = res.body;
    _pickerRows = body.names || [];
    _pickerDrillable = body.drillable || {};
    _renderPickerBreadcrumb();
    _renderPickerRows(filterText);
    if (body.source === 'stale_json_catalog') {
      _pickerSetStatus((body.warning || 'stale offline catalog') +
                       ' — names may not match the running build');
    } else {
      _pickerSetStatus(body.total + ' match' + (body.total === 1 ? '' : 'es') +
                       (body.truncated ? ' (showing first ' + body.count +
                        ' — refine the filter)' : ''));
    }
  }

  function _loadPickerRoot(prefix) {
    var epoch = ++_pickerEpoch;
    _fetchSymbols({ prefix: prefix, limit: 100 }).then(function (res) {
      if (epoch !== _pickerEpoch || _pickerParent !== null) return;
      if (res.status !== 200) {
        _pickerSetStatus((res.body && res.body.error) ? res.body.error : ('HTTP ' + res.status));
        return;
      }
      _applyPickerPayload(res, prefix);
    }).catch(function () { _pickerSetStatus('cannot reach /api/symbols'); });
  }

  function _drillPicker(path) {
    var epoch = ++_pickerEpoch;
    _fetchSymbols({ parent: path, limit: 100 }).then(function (res) {
      if (epoch !== _pickerEpoch) return;
      if (res.status !== 200) {
        _pickerSetStatus((res.body && res.body.error) ? res.body.error : ('HTTP ' + res.status));
        return;
      }
      _pickerParent = path;
      var filterEl = q('sm-picker-filter');
      if (filterEl) filterEl.value = '';
      _applyPickerPayload(res, '');
    }).catch(function () { _pickerSetStatus('cannot reach /api/symbols'); });
  }

  function _addRangeName(name) {
    var ta = q('sm-ranges');
    var existing = ta ? _parseRanges(ta.value) : [];
    if (existing.indexOf(name) !== -1) {
      showResult('Already in ranges: ' + name, null);
      return;
    }
    if (ta) {
      var joiner = (ta.value && !/[,\s]$/.test(ta.value)) ? ', ' : '';
      ta.value = ta.value + joiner + name;
    }
    showResult('Added to ranges: ' + name, null);
    if (_rangesChanged) _rangesChanged();
  }

  var _activeChannelId = 0;

  // ==== Event wiring ============================================================================================================================
  function bindEvents(api) {
    // Channel selection buttons
    document.querySelectorAll('.sm-channel-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        document.querySelectorAll('.sm-channel-btn').forEach(function (b) {
          b.classList.toggle('active', b === btn);
        });
        var slotId = parseInt(btn.getAttribute('data-slot'), 10);
        _activeChannelId = slotId;
        var rangesEl = q('sm-ranges');
        if (rangesEl) {
          rangesEl.style.display = (slotId === 0) ? 'none' : 'block';
        }
        _schedulePreview();
      });
    });

    // Actual subscribe button
    var submitBtn = q('sm-submit-btn');
    if (submitBtn) {
      submitBtn.addEventListener('click', function () {
        var slotId = _activeChannelId;
        var rateChoice = _rateChoice();
        var divider = rateChoice ? rateChoice.divider : SUBSCRIBE_DIVIDER_DEFAULT;
        var ranges = slotId === 0 ? [] : _readRanges();
        
        showResult('Subscribing channel ' + slotId + ' @ ' +
                   (rateChoice ? _fmtHz(rateChoice.achieved) + ' Hz' : 'divider ' + divider) +
                   ' (wire divider ' + divider +
                   (ranges.length ? ', ranges=' + ranges.length : '') + ')...', null);
                   
        submitSubscribe(api, slotId, divider, ranges).then(function (res) {
          if (res.ok) {
            showResult('OK - Channel ' + slotId + ' subscribe sent via ' + res.via, true);
            _selectedSlot = slotId;
            _selectedAt   = Date.now();
            updateSelectionBanner();
          } else {
            showResult('FAIL - Subscribe failed: ' + (res.error || 'unknown') + ' (' + res.note + ')', false);
          }
        });
      });
    }

    // Debounced preview trigger. Fires when the user edits the ranges
    // textarea, divider input, or clicks a preset. 200 ms is short
    // enough to feel live and long enough to coalesce fast typing.
    var _previewTimer = null;
    function _schedulePreview() {
      if (_previewTimer) clearTimeout(_previewTimer);
      _previewTimer = setTimeout(function () { refreshPreview(api); }, 200);
    }
    var rangesEl = q('sm-ranges');
    if (rangesEl) rangesEl.addEventListener('input', _schedulePreview);

    // Rate form (C3): desired Hz plus the assumed Send_Task cadence.
    var hzEl = q('sm-hz');
    var cadenceEl = q('sm-cadence');
    function _rateFormInput() { renderHzReadout(); _schedulePreview(); }
    if (hzEl) hzEl.addEventListener('input', _rateFormInput);
    if (cadenceEl) cadenceEl.addEventListener('input', _rateFormInput);
    document.querySelectorAll('.sm-cadence-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (cadenceEl) cadenceEl.value = btn.getAttribute('data-cadence');
        _rateFormInput();
      });
    });
    renderHzReadout();

    // Preset quick-fill buttons
    document.querySelectorAll('.sm-preset-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var name = btn.getAttribute('data-preset');
        var preset = _PRESETS[name];
        if (!preset) return;
        var ta = q('sm-ranges');
        if (ta) ta.value = preset;
        showResult('Preset loaded: ' + name + ' (' + _parseRanges(preset).length + ' ranges)', null);
        _schedulePreview();
      });
    });

    // Symbol picker (C1): filter input, delegated row/crumb clicks.
    _rangesChanged = _schedulePreview;
    var pickerFilter = q('sm-picker-filter');
    if (pickerFilter) {
      pickerFilter.addEventListener('input', function () {
        var text = pickerFilter.value;
        if (_pickerParent === null) {
          if (_pickerTimer) clearTimeout(_pickerTimer);
          _pickerTimer = setTimeout(function () { _loadPickerRoot(text); }, 200);
        } else {
          _renderPickerRows(text);
        }
      });
    }
    var pickerResults = q('sm-picker-results');
    if (pickerResults) {
      pickerResults.addEventListener('click', function (ev) {
        var t = ev.target;
        if (!t || typeof t.closest !== 'function') return;
        var addBtn = t.closest('.sm-picker-add');
        var drillBtn = addBtn ? null : t.closest('.sm-picker-drill');
        if (addBtn) {
          _addRangeName(decodeURIComponent(addBtn.getAttribute('data-name')));
        } else if (drillBtn) {
          _drillPicker(decodeURIComponent(drillBtn.getAttribute('data-name')));
        }
      });
    }
    var pickerCrumbs = q('sm-picker-breadcrumb');
    if (pickerCrumbs) {
      pickerCrumbs.addEventListener('click', function (ev) {
        var t = ev.target;
        if (!t || typeof t.closest !== 'function') return;
        var crumb = t.closest('.sm-crumb');
        if (!crumb) return;
        var path = crumb.getAttribute('data-crumb');
        if (path) {
          _drillPicker(path);
        } else {
          _pickerParent = null;
          var filterEl = q('sm-picker-filter');
          _loadPickerRoot(filterEl ? filterEl.value : '');
        }
      });
    }
    _loadPickerRoot('');

    // Refresh schema
    var refreshBtn = q('sm-refresh-schema');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        // Force a re-subscribe on slot 0 to refresh schema
        showResult('Re-subscribing slot 0...', null);
        submitSubscribe(api, 0, 1, []).then(function (res) {
          if (res.ok) {
            showResult('Schema refresh sent via ' + res.via, true);
          } else {
            showResult('Schema refresh failed: ' + (res.error || 'unknown'), false);
          }
        });
      });
    }

    // Selection banner - clear button
    var clearBtn = q('sm-clear-selection');
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        _selectedSlot = null;
        _selectedAt   = null;
        updateSelectionBanner();
      });
    }

    // Initial preview so the user sees "X resolved / Y unresolved"
    // right away on panel open (defaults to slot 0 with the dashboard
    // layout).
    _schedulePreview();
  }

  // Per-slot health cache (from /health/slots).
  // The slot table is driven from /state (every 500 ms via the shell
  // poll). /health/slots carries per-slot fresh_keys / stale_keys
  // counts that /state does not expose (the service computes them from
  // ``_key_ts`` and the freshness TTL). Polling /health/slots every 3 s
  // keeps the panel cheap while surfacing the staleness breakdown
  // inline next to each slot's "Last" column.
  var _healthBySlot = {};
  var _healthTTLns = null;
  var _healthHandle = null;

  function startHealthPoll() {
    if (_healthHandle) return;
    _pollHealth();
    _healthHandle = setInterval(_pollHealth, 3000);
  }

  function stopHealthPoll() {
    if (_healthHandle) {
      clearInterval(_healthHandle);
      _healthHandle = null;
    }
  }

  function _pollHealth() {
    fetch("/health/slots").then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (report) {
      _healthTTLns = report && report.ttl_ns ? report.ttl_ns : null;
      var slots = (report && report.slots) || {};
      _healthBySlot = {};
      Object.keys(slots).forEach(function (slotKey) {
        _healthBySlot[slotKey] = slots[slotKey];
      });
      if (_state) renderSlotTable(_state);
    }).catch(function () {
      // /health/slots unavailable (older service). Silent: panel works
      // from /state alone, just without the fresh/stale breakdown.
    });
  }

  function updateSelectionBanner() {
    var el = q('sm-selection');
    var idEl = q('sm-selection-id');
    var ageEl = q('sm-selection-age');
    if (!el || !idEl || !ageEl) return;
    if (_selectedSlot == null) {
      el.style.display = 'none';
      return;
    }
    el.style.display = '';
    idEl.textContent = '#' + _selectedSlot;
    var dt = (Date.now() - (_selectedAt || Date.now())) / 1000;
    ageEl.textContent = dt.toFixed(1) + ' s';
  }

  // ???? Per-tick selection age update ??????????????????????????????????????????????????????????????????????????????
  var _selectionTickHandle = null;
  function startSelectionTicker() {
    if (_selectionTickHandle) return;
    _selectionTickHandle = setInterval(updateSelectionBanner, 500);
  }
  function stopSelectionTicker() {
    if (_selectionTickHandle) {
      clearInterval(_selectionTickHandle);
      _selectionTickHandle = null;
    }
  }

  // ???? Export ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????
  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel('Slot Manager', function (container) {
      container.innerHTML = buildHTML();
      bindEvents(api);
      api.subscribe(onState);
      startSelectionTicker();
      startHealthPoll();
      // Initial render
      renderSlotTable(api.getState ? api.getState() : null);
    });
  };
  window.__PLUGIN_DESTROY__ = function () {
    _state = null;
    _expanded = {};
    _prevSlot = {};
    _selectedSlot = null;
    _selectedAt   = null;
    _pickerParent = null;
    _pickerRows = [];
    _pickerDrillable = {};
    if (_pickerTimer) { clearTimeout(_pickerTimer); _pickerTimer = null; }
    stopSelectionTicker();
    stopHealthPoll();
  };
  window.__registerPlugin__('Slot Manager', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
