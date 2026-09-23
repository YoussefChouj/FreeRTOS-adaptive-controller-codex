/**
 * dataflow-panel.js — Data flow block diagram (task 20260923-134045)
 *
 * Shows how telemetry moves from sensors through estimation and control to
 * actuation. Each block names the keys backing it and displays a live value.
 *
 * Liveness badges use the same rules and vocabulary as overview-panel.js:
 *   LIVE      — stream received, recent, low loss
 *   STALE     — stream received but value not updated for > 2 s
 *   NO DATA   — no slot, no keys published, or frozen past TTL
 *   NOT PUBLISHED — slot has data but this block has no key
 *
 * The EKF block is marked SHADOW: s_ekf output is NOT wired into any control
 * path. No edge is drawn from EKF to Control in the diagram.
 *
 * A block whose keys are not currently subscribed displays a "not published"
 * line naming which Slot Manager preset WOULD publish them.
 */
(function () {
  'use strict';

  /* ── Thresholds (mirrors overview-panel.js) ──────────────────────────── */
  var STALE_WARN_MS  = 2000;
  var LOSS_AMBER_PCT = 1.0;
  var LOSS_RED_PCT   = 5.0;

  var SLOT_ORDER = ['0', '1', '3', '2'];

  /* ── Data flow block model ─────────────────────────────────────────────
   * Each block carries:
   *   id       — unique identifier
   *   title    — display name shown on the block header
   *   keys     — telemetry keys backing this block
   *   slot     — which stream slot it should read from (for classification)
   *   shadow   — true if this is shadow-mode data (display only)
   *   preset   — hint text for "not published" state
   */
  var BLOCKS = [
    {
      id: 'sensors',
      title: 'Sensors',
      keys: [
        { key: 'c.gyro_x',  plain: 'Gyro X',  unit: 'rad/s',  dec: 2 },
        { key: 'c.gyro_y',  plain: 'Gyro Y',  unit: 'rad/s',  dec: 2 },
        { key: 'c.gyro_z',  plain: 'Gyro Z',  unit: 'rad/s',  dec: 2 },
        /* mg, not m/s^2: Acc_*_Real is declared in milli-g at
         * API/bmi088_driver.c:27. A level drone reads ~1012 on Z. */
        { key: 'imu.acc_x', plain: 'Acc X',   unit: 'mg',  dec: 0 },
        { key: 'imu.acc_y', plain: 'Acc Y',   unit: 'mg',  dec: 0 },
        { key: 'imu.acc_z', plain: 'Acc Z',   unit: 'mg',  dec: 0 },
        { key: 'c.altitude_cm', plain: 'Baro Alt', unit: 'cm',  dec: 0 },
      ],
      slot: '0',
      preset: 'slot-0 Frame C',
    },
    {
      id: 'attitude',
      title: 'Attitude estimate',
      keys: [
        { key: 'status.roll_deg',  plain: 'Roll',  unit: 'deg',  dec: 3 },
        { key: 'status.pitch_deg', plain: 'Pitch', unit: 'deg',  dec: 3 },
        { key: 'status.yaw_deg',   plain: 'Yaw',   unit: 'deg',  dec: 3 },
      ],
      slot: '0',
      preset: 'slot-0 Frame A',
    },
    {
      id: 'ekf',
      title: 'EKF (shadow)',
      keys: [
        { key: 'ekf.vel_x',     plain: 'Vel X',      unit: 'm/s',  dec: 3 },
        { key: 'ekf.vel_y',     plain: 'Vel Y',      unit: 'm/s',  dec: 3 },
        { key: 'ekf.vel_z',     plain: 'Vel Z',      unit: 'm/s',  dec: 3 },
        { key: 'ekf.bias_gyro_x', plain: 'Gyro bias X', unit: 'rad/s', dec: 4 },
        { key: 'ekf.bias_gyro_y', plain: 'Gyro bias Y', unit: 'rad/s', dec: 4 },
        { key: 'ekf.bias_gyro_z', plain: 'Gyro bias Z', unit: 'rad/s', dec: 4 },
      ],
      slot: '0',
      preset: 'slot-0 Frame A',
      shadow: true,
    },
    {
      id: 'outer',
      title: 'Outer PID (angle)',
      keys: [
        { key: 'pid.gyrox.U', plain: 'Roll cmd',  unit: 'cmd',  dec: 3 },
        { key: 'pid.gyroy.U', plain: 'Pitch cmd', unit: 'cmd',  dec: 3 },
        { key: 'pid.gyroz.U', plain: 'Yaw cmd',   unit: 'cmd',  dec: 3 },
      ],
      slot: '0',
      preset: 'slot-0 Frame B',
    },
    {
      id: 'inner',
      title: 'Inner PID (rate)',
      keys: [
        { key: 'pid.gyrox.FB', plain: 'Filtered roll rate', unit: 'deg/s',  dec: 1 },
        { key: 'pid.gyroy.FB', plain: 'Filtered pitch rate', unit: 'deg/s',  dec: 1 },
        { key: 'pid.gyroz.FB', plain: 'Filtered yaw rate',   unit: 'deg/s',  dec: 1 },
      ],
      slot: '0',
      preset: 'slot-0 Frame B',
    },
    {
      id: 'mrac',
      title: 'MRAC augmentation',
      keys: [
        { key: 'mrac.roll.e',     plain: 'Roll error',  unit: 'rad/s', dec: 3 },
        { key: 'mrac.roll.u_ad',  plain: 'Roll adaptive', unit: 'cmd',  dec: 3 },
        { key: 'mrac.pitch.e',    plain: 'Pitch error', unit: 'rad/s', dec: 3 },
        { key: 'mrac.pitch.u_ad', plain: 'Pitch adaptive', unit: 'cmd', dec: 3 },
        { key: 'mrac.yaw.e',      plain: 'Yaw error',   unit: 'rad/s', dec: 3 },
        { key: 'mrac.yaw.u_ad',   plain: 'Yaw adaptive', unit: 'cmd',  dec: 3 },
        { key: 'mrac.z.e',        plain: 'Z error',     unit: 'rad/s', dec: 3 },
        { key: 'mrac.z.u_ad',     plain: 'Z adaptive',  unit: 'cmd',  dec: 3 },
      ],
      slot: '0',
      preset: 'slot-0 Frame A / Frame B',
    },
    {
      id: 'motors',
      title: 'Motors',
      keys: [
        { key: 'motor.rpm_0', plain: 'Motor 1', unit: 'rpm',  dec: 0 },
        { key: 'motor.rpm_1', plain: 'Motor 2', unit: 'rpm',  dec: 0 },
        { key: 'motor.rpm_2', plain: 'Motor 3', unit: 'rpm',  dec: 0 },
        { key: 'motor.rpm_3', plain: 'Motor 4', unit: 'rpm',  dec: 0 },
      ],
      slot: '3',
      preset: 'slot-3',
    },
  ];

  /* ── Flow edges (directed connections between blocks) ──────────────────
   * Each edge: from → to
   * NOTE: No edge from EKF to Control. s_ekf is shadow mode only.
   *       No edge from Sensors to Inner PID — inner PID gets rate data via
   *       the rate filter path (pid.gyrox.FB etc.), which originates from
   *       the gyro sensor. */
  var EDGES = [
    { from: 'sensors',  to: 'attitude' },
    { from: 'attitude', to: 'outer' },
    { from: 'sensors',  to: 'inner' },
    { from: 'outer',    to: 'mrac' },
    { from: 'mrac',     to: 'motors' },
    { from: 'inner',    to: 'motors' },
  ];

  /* ── Helpers ─────────────────────────────────────────────────────────── */
  function q(id) {
    return (typeof document !== 'undefined' && document.getElementById)
      ? document.getElementById(id)
      : null;
  }

  function fmtVal(row, v) {
    var sign = (row.signed && v >= 0) ? '+' : '';
    return sign + Number(v).toFixed(row.dec) + ' ' + row.unit;
  }

  /* ── Data access ─────────────────────────────────────────────────────── */
  function findValue(state, key) {
    if (!state || !state.streams) return null;
    for (var i = 0; i < SLOT_ORDER.length; i++) {
      var s = state.streams[SLOT_ORDER[i]];
      if (s && s.values && s.values[key] != null) {
        var ts = (s._key_ts && s._key_ts[key] != null) ? s._key_ts[key]
          : (s.last_update_ns || null);
        return { val: s.values[key], ts: ts, loss: s.loss_pct || 0 };
      }
    }
    return null;
  }

  function slotOf(state, name) {
    return (state && state.streams) ? state.streams[name] : null;
  }

  /* ── Liveness classification (same rules as overview-panel.js) ─────────
   * Classifies a block: 'ok' | 'warn' | 'alarm' | 'nodata' */
  function classifyBlock(block, state, nowMs, ttlMs) {
    /* Liveness is decided by whether the keys resolve ANYWHERE, not by whether
     * block.slot arrived. Which slot carries a key is a subscribe layout, not a
     * contract: c.gyro_* and c.altitude_cm land on slot 1 under the current
     * layout, so checking block.slot ('0') alone blanked the whole sensor block
     * and hid seven keys that were live the entire time. */
    var anyKey = false;
    for (var i = 0; i < block.keys.length; i++) {
      if (findValue(state, block.keys[i].key) != null) { anyKey = true; break; }
    }
    if (!anyKey) {
      return {
        cls: 'nodata',
        label: 'NO DATA',
        sub: 'not published by this build',
      };
    }
    /* Staleness from the newest key timestamp, and loss from the slots those
     * keys actually came from - findValue reports both, so neither depends on
     * block.slot being the slot that carries them. */
    var newest = null;
    var loss = 0;
    for (var i = 0; i < block.keys.length; i++) {
      var f = findValue(state, block.keys[i].key);
      if (!f) continue;
      if (f.ts != null && (newest == null || f.ts > newest)) newest = f.ts;
      if (f.loss > loss) loss = f.loss;
    }
    if (newest != null) {
      var age = Math.max(0, nowMs - newest / 1e6);
      if (age > ttlMs) {
        return {
          cls: 'nodata', label: 'NO DATA',
          sub: 'frozen ' + fmtAge(age),
        };
      }
      if (age > STALE_WARN_MS) {
        return {
          cls: 'warn', label: 'STALE',
          sub: 'age ' + fmtAge(age),
        };
      }
    }
    if (loss > LOSS_RED_PCT) {
      return {
        cls: 'alarm', label: 'LOSS ' + loss.toFixed(1) + '%',
        sub: 'critical packet loss',
      };
    }
    if (loss > LOSS_AMBER_PCT) {
      return {
        cls: 'warn', label: 'LOSS ' + loss.toFixed(1) + '%',
        sub: 'degraded link',
      };
    }
    return { cls: 'ok', label: 'LIVE', sub: '' };
  }

  function fmtAge(ms) {
    if (ms < 1000) return Math.round(ms) + ' ms';
    if (ms < 60000) return (ms / 1000).toFixed(1) + ' s';
    return (ms / 60000).toFixed(1) + ' min';
  }

  /* ── Preset hint for "not published" blocks ──────────────────────────── */
  function getPresetHint(block) {
    var win = (typeof window !== 'undefined') ? window : null;
    var presetLookup = win && typeof win.__gs_preset_for_symbol === 'function'
      ? win.__gs_preset_for_symbol
      : null;
    var key = (block.keys[0] && block.keys[0].key) || '';
    if (!key || !presetLookup) return null;
    var carriers = presetLookup(key);
    if (carriers == null) return null;
    if (Array.isArray(carriers) && carriers.length) {
      var names = [];
      for (var i = 0; i < carriers.length; i++) {
        if (carriers[i] && carriers[i].preset &&
            names.indexOf(carriers[i].preset) === -1) {
          names.push(carriers[i].preset);
        }
      }
      return 'not published: in preset ' + names.slice(0, 2).join(', ') +
        ' · carriers ' + key;
    }
    return 'not published by this build · no preset carries ' + key;
  }

  /* ── CSS ─────────────────────────────────────────────────────────────── */
  function buildCSS() {
    return [
      '<style>',
      '.df-root { font-size: 12px; }',
      '.df-banner { font-size: 10px; color: var(--muted); margin-bottom: 10px; padding: 6px 10px; border-radius: 4px; background: var(--card); }',
      '.df-chain { display: flex; align-items: stretch; flex-wrap: wrap; gap: 0; }',
      '.df-block { flex: 1 1 180px; min-width: 180px; border: 2px solid var(--muted); border-radius: 6px;',
      '  padding: 12px 10px 8px 10px; background: var(--card); position: relative; box-sizing: border-box; }',
      '.df-block-ok     { border-color: var(--green); }',
      '.df-block-warn   { border-color: var(--amber); }',
      '.df-block-alarm  { border-color: var(--red); }',
      '.df-block-nodata { border-color: var(--muted); opacity: 0.55; }',
      '.df-block-title { font-size: 11px; font-weight: 700; margin-bottom: 2px; }',
      '.df-block-badge { position: absolute; top: 4px; right: 8px; font-size: 9px; font-weight: 700;',
      '  letter-spacing: 0.03em; text-transform: uppercase; }',
      '.df-badge-ok     { color: var(--green); }',
      '.df-badge-warn   { color: var(--amber); }',
      '.df-badge-alarm  { color: var(--red); }',
      '.df-badge-nodata { color: var(--muted); }',
      '.df-block-hint  { font-size: 9px; color: var(--muted); margin-bottom: 6px; min-height: 10px; overflow-wrap: anywhere; }',
      '.df-sub { font-size: 9px; color: var(--muted); margin-top: 4px; min-height: 10px; }',
      '.df-key-row { display: flex; justify-content: space-between; gap: 4px; padding: 1px 0; font-size: 10px; }',
      '.df-key-plain { color: var(--muted); }',
      '.df-key-val { font-family: Consolas, monospace; font-weight: 600; }',
      '.df-key-val-nodata { color: var(--muted); font-weight: 400; font-size: 9px; }',
      '.df-arrow { align-self: center; color: var(--muted); font-size: 18px; padding: 0 6px; font-weight: 700; }',
      '.df-shadow-badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 9px;',
      '  font-weight: 700; letter-spacing: 0.05em; background: rgba(136, 136, 170, 0.15); color: var(--muted); }',
      '.df-diagram { margin-top: 12px; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }',
      '.df-diagram-svg { display: block; width: 100%; height: auto; }',
      '.df-noselect { user-select: none; -webkit-user-select: none; }',
      '@media (max-width: 768px) {',
      '  .df-block { min-width: 100%; flex-basis: 100%; }',
      '  .df-arw { display: none; }',
      '}',
      '</style>',
    ].join('');
  }

  /* ── HTML ────────────────────────────────────────────────────────────── */
  function buildHTML(state) {
    var nowMs = Date.now();
    var ttlMs = ((state && state.slot_freshness_ttl_ns) || 30e9) / 1e6;

    var banner = [
      '<div class="df-banner">',
      'Data flow block diagram. Each block shows the telemetry keys backing it and a liveness badge. ',
      'EKF runs in shadow mode (display only, not in any control path). ',
      'Blocks without published keys show which preset would publish them.',
      '</div>',
    ].join('');

    /* Render blocks */
    var chain = ['<div class="df-chain">'];
    BLOCKS.forEach(function (b, idx) {
      if (idx > 0) chain.push('<div class="df-arrow df-arw">→</div>');

      var cls = classifyBlock(b, state, nowMs, ttlMs);
      var presetHint = (cls.cls === 'nodata') ? getPresetHint(b) : null;

      chain.push(
        '<div class="df-block df-block-' + cls.cls + '" id="df-block-' + b.id + '">',
        '<div class="df-block-badge df-badge-' + cls.cls + '" id="df-badge-' + b.id + '">' + cls.label + '</div>',
        '<div class="df-block-title">',
        '<span>' + b.title + '</span>',
        (b.shadow ? ' <span class="df-shadow-badge">SHADOW</span>' : ''),
        '</div>',
        '<div class="df-block-hint">',
        '<span>slot ' + b.slot + ' · ' + b.keys.map(function (k) { return k.key; }).join(', ') + '</span>',
        (presetHint ? '<br><span>' + escapeHtml(presetHint) + '</span>' : ''),
        '</div>',
        '<div class="df-noselect">',
      );

      b.keys.forEach(function (k, ri) {
        /* Always resolve the key. Suppressing the lookup when the block reads
         * NO DATA made a partially-published block report nothing at all; a key
         * that resolves prints its value regardless of the block's badge. */
        var f = findValue(state, k.key);
        var val;
        if (f == null || f.val == null || isNaN(f.val)) {
          val = '<span class="df-key-val df-key-val-nodata">NO DATA</span>';
        } else {
          val = '<span class="df-key-val">' + fmtVal(k, f.val) + '</span>';
        }
        chain.push(
          '<div class="df-key-row">',
          '<span class="df-key-plain">' + k.plain + '</span>',
          val,
          '</div>',
        );
      });

      chain.push(
        '</div>',
        '<div class="df-sub" id="df-sub-' + b.id + '">' + cls.sub + '</div>',
        '</div>',
      );
    });
    chain.push('</div>');

    /* SVG diagram */
    var svg = buildDiagram(state, nowMs, ttlMs);

    return buildCSS() + '<div class="df-root">' + banner + chain.join('') + svg + '</div>';
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ── SVG diagram ───────────────────────────────────────────────────────
   * A simplified block-and-edge diagram showing the data flow topology.
   * Blocks light up based on liveness state. */
  function buildDiagram(state, nowMs, ttlMs) {
    var W = 800, H = 360;
    var BW = 120, BH = 60, PAD = 20;

    /* Positions — four columns: Sensors | Attitude/EKF | Control | Motors */
    var positions = {
      sensors: { x: PAD, y: 30 },
      attitude: { x: PAD + BW + 80, y: 30 },
      ekf: { x: PAD + BW + 80, y: 130 },
      outer: { x: PAD + 2 * (BW + 80), y: 30 },
      inner: { x: PAD + 2 * (BW + 80), y: 130 },
      mrac: { x: PAD + 2 * (BW + 80), y: 230 },
      motors: { x: PAD + 3 * (BW + 80), y: 130 },
    };

    var blockClass = function (id) {
      var b = null;
      for (var i = 0; i < BLOCKS.length; i++) {
        if (BLOCKS[i].id === id) { b = BLOCKS[i]; break; }
      }
      if (!b) return { cls: 'nodata', stroke: 'var(--muted)', fill: 'var(--card)' };
      var c = classifyBlock(b, state, nowMs, ttlMs);
      var colors = {
        ok:     { stroke: 'var(--green)', fill: 'rgba(78, 204, 163, 0.08)' },
        warn:   { stroke: 'var(--amber)', fill: 'rgba(245, 166, 35, 0.08)' },
        alarm:  { stroke: 'var(--red)',   fill: 'rgba(233, 69, 96, 0.1)' },
        nodata: { stroke: 'var(--muted)', fill: 'var(--card)' },
      };
      return { cls: c.cls, stroke: colors[c.cls].stroke, fill: colors[c.cls].fill };
    };

    /* Edge colors: shadow edges use dashed gray */
    var edgeStyle = function (from, to) {
      var fromB = null, toB = null;
      for (var i = 0; i < BLOCKS.length; i++) {
        if (BLOCKS[i].id === from) fromB = BLOCKS[i];
        if (BLOCKS[i].id === to) toB = BLOCKS[i];
      }
      if (fromB && fromB.shadow) {
        return { stroke: 'var(--muted)', dash: 'stroke-dasharray="4,3" ' };
      }
      return { stroke: 'rgba(136, 136, 170, 0.5)', dash: '' };
    };

    var line = function (x1, y1, x2, y2, style) {
      return '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
        '" stroke="' + style.stroke + '" ' + style.dash + 'stroke-width="1.5"/>';
    };

    var arrow = function (x1, y1, x2, y2) {
      var angle = Math.atan2(y2 - y1, x2 - x1);
      var ax = x2 - 10 * Math.cos(angle);
      var ay = y2 - 10 * Math.sin(angle);
      var perpX = -10 * Math.sin(angle);
      var perpY = 10 * Math.cos(angle);
      return '<polygon points="' + x2 + ',' + y2 + ' ' +
        (ax + perpX) + ',' + (ay + perpY) + ' ' + (ax - perpX) + ',' + (ay - perpY) +
        '" fill="rgba(136, 136, 170, 0.5)"/>';
    };

    /* Compute edge paths between block centers */
    var edgePaths = EDGES.map(function (e) {
      var fp = positions[e.from], tp = positions[e.to];
      return { from: e.from, to: e.to,
        x1: fp.x + BW / 2, y1: fp.y + BH / 2,
        x2: tp.x + BW / 2, y2: tp.y + BH / 2 };
    });

    /* Draw blocks */
    var blocksHtml = '';
    for (var bid in positions) {
      var p = positions[bid];
      var bc = blockClass(bid);
      var bDef = null;
      for (var i = 0; i < BLOCKS.length; i++) {
        if (BLOCKS[i].id === bid) { bDef = BLOCKS[i]; break; }
      }
      var title = bDef ? bDef.title : bid;
      var titleStr = title + (bDef && bDef.shadow ? ' (shadow)' : '');

      blocksHtml += '<g id="df-svg-block-' + bid + '">';
      blocksHtml += '<rect x="' + p.x + '" y="' + p.y + '" width="' + BW + '" height="' + BH +
        '" rx="6" ry="6" fill="' + bc.fill + '" stroke="' + bc.stroke + '" stroke-width="1.5"/>';
      blocksHtml += '<text x="' + (p.x + BW / 2) + '" y="' + (p.y + BH / 2 + 1) +
        '" text-anchor="middle" dominant-baseline="middle" font-size="10" font-weight="600" fill="var(--text)">' +
        escapeXml(titleStr) + '</text>';
      blocksHtml += '<text x="' + (p.x + BW / 2) + '" y="' + (p.y + BH / 2 + 14) +
        '" text-anchor="middle" dominant-baseline="middle" font-size="7" fill="var(--muted)">' +
        bc.cls + '</text>';
      blocksHtml += '</g>';
    }

    /* Draw edges */
    var edgesHtml = '';
    edgePaths.forEach(function (ep) {
      var style = edgeStyle(ep.from, ep.to);
      edgesHtml += line(ep.x1, ep.y1, ep.x2, ep.y2, style);
      edgesHtml += arrow(ep.x1, ep.y1, ep.x2, ep.y2);
    });

    /* Column labels */
    var labels = [
      { x: PAD + BW / 2, y: H - 12, text: 'Sensors' },
      { x: positions.attitude.x + BW / 2, y: H - 12, text: 'Estimation' },
      { x: positions.outer.x + BW / 2, y: H - 12, text: 'Control' },
      { x: positions.motors.x + BW / 2, y: H - 12, text: 'Actuation' },
    ];
    var labelsHtml = labels.map(function (l) {
      return '<text x="' + l.x + '" y="' + l.y + '" text-anchor="middle" font-size="9" ' +
        'font-weight="700" fill="var(--muted)" text-transform="uppercase" letter-spacing="0.05em">' +
        escapeXml(l.text) + '</text>';
    }).join('');

    return '<div class="df-diagram">' +
      '<svg class="df-diagram-svg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Data flow block diagram">' +
      '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="transparent"/>' +
      edgesHtml +
      blocksHtml +
      labelsHtml +
      '</svg>' +
      '</div>';
  }

  function escapeXml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ── Render state ────────────────────────────────────────────────────── */
  var _state = null;

  function render(container) {
    container.innerHTML = buildHTML(_state);
  }

  /* ── Plugin registration (mirrors pid-gains-panel.js pattern) ────────── */
  window.__PLUGIN_INIT__ = function (api) {
    /* Without a workspace the shell defaults to 'all' (index.html:1064), which
     * put this diagram on every one of the eleven tabs. It belongs where the
     * whole signal chain is the subject. */
    api.registerPanel('Data Flow', function (container) {
      render(container);
      api.subscribe(function (state) {
        _state = state;
        render(container);
      });
    }, { workspace: 'diagnostics' });
  };
  window.__PLUGIN_DESTROY__ = function () { _state = null; };
  window.__registerPlugin__('Data Flow', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);
})();
