/**
 * replay-panel.js — Session Replay Panel
 *
 * Provides session management and telemetry replay functionality:
 * - Lists all recorded sessions from GET /sessions
 * - Displays session details (ID, start time, sample count, schema)
 * - Browses individual records via GET /sessions/<id>/records
 * - Exports sessions to CSV via POST /sessions/<id>/export
 * - Plays a session into the live panels via POST /replay/<id>/play
 * - Timeline scrubber with playback controls
 * - Telemetry statistics per stream
 * - Filter by stream/slot
 *
 * Uses window.__registerPlugin__ pattern (IIFE, dark theme).
 */
(function () {
  'use strict';

  // ── State ───────────────────────────────────────────────────────────────
  var _sessions = [];
  var _selectedSession = null;
  var _records = [];
  var _filteredRecords = [];
  var _playbackIndex = 0;
  var _boundResize = null;
  var _playbackTimer = null;
  var _playbackSpeed = 1; // records per second
  var _filterSlot = 'all';

  // ── DOM refs ────────────────────────────────────────────────────────────
  var _elSessionList = null;
  var _elSessionDetail = null;
  var _elRecordList = null;
  var _elTimeline = null;
  var _elScrubber = null;
  var _elPlaybackInfo = null;
  var _elStreamStats = null;
  var _elFilter = null;
  var _elPlayBtn = null;
  var _elExportBtn = null;

  // ── Helpers ────────────────────────────────────────────────────────────
  function q(id) { return document.getElementById(id); }

  function fmtTime(ns) {
    if (ns == null) return '—';
    var d = new Date(Math.floor(ns / 1e6));
    return d.toLocaleString();
  }

  function fmtDuration(startNs, endNs) {
    if (!startNs || !endNs) return '—';
    var ms = Math.floor((endNs - startNs) / 1e6);
    if (ms < 1000) return ms + 'ms';
    if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
    return (ms / 60000).toFixed(1) + 'm';
  }

  function fmtNum(v, dec) {
    if (v == null) return '—';
    dec = dec === undefined ? 3 : dec;
    return parseFloat(v).toFixed(dec);
  }

  function stripSlotPrefix(key) {
    // No-op for the current telemetry spec (e.g., 'ch5', 'ekf.pos_x'):
    // keys do not carry a 'slotN.chN.N' prefix. Kept for back-compat with
    // any older records that might still use 'slot0.ch0.5'-style keys.
    var parts = key.split('.');
    if (parts.length >= 3 && /^slot\d+$/.test(parts[0]) && /^ch\d+$/.test(parts[1])) {
      return parts.slice(2).join('.');
    }
    return key;
  }

  // ── API calls ───────────────────────────────────────────────────────────
  function fetchSessions() {
    return fetch('/sessions')
      .then(function (r) { return r.json(); })
      .catch(function (err) {
        console.error('[replay] fetchSessions error:', err);
        return [];
      });
  }

  function fetchSessionDetail(sessionId) {
    return fetch('/sessions/' + sessionId)
      .then(function (r) { return r.json(); })
      .catch(function (err) {
        console.error('[replay] fetchSessionDetail error:', err);
        return null;
      });
  }

  // Page size for the record browser. A flight session holds hundreds of
  // thousands of records; fetching them all is hundreds of MB and freezes
  // the tab, so the panel asks for a window and says when it truncated.
  var RECORD_PAGE = 2000;

  function fetchSessionRecords(sessionId) {
    return fetch('/sessions/' + sessionId + '/records?limit=' + RECORD_PAGE)
      .then(function (r) { return r.json(); })
      .catch(function (err) {
        console.error('[replay] fetchSessionRecords error:', err);
        return { records: [] };
      });
  }

  function exportSession(sessionId, outputPath) {
    return fetch('/sessions/' + sessionId + '/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ output_path: outputPath })
    }).then(function (r) { return r.json(); });
  }

  function playSession(sessionId) {
    return fetch('/replay/' + sessionId + '/play', { method: 'POST' })
      .then(function (r) { return r.json(); });
  }

  // ── Render: Session List ─────────────────────────────────────────────────
  function renderSessionList(sessions) {
    if (!sessions || sessions.length === 0) {
      _elSessionList.innerHTML = '<div class="rp-empty">No sessions recorded</div>';
      return;
    }
    var rows = sessions.map(function (s) {
      var cls = _selectedSession && _selectedSession.id === s.id ? 'rp-session-item rp-selected' : 'rp-session-item';
      return '<div class="' + cls + '" data-session-id="' + s.id + '">' +
        '<div class="rp-session-id">Session #' + s.id + '</div>' +
        '<div class="rp-session-meta">Schema: ' + (s.schema_id || '—') + '</div>' +
        '<div class="rp-session-meta">Started: ' + fmtTime(s.started_ns) + '</div>' +
        '<div class="rp-session-meta">Source: ' + (s.source || 'unknown') + '</div>' +
        '</div>';
    }).join('');
    _elSessionList.innerHTML = rows;

    // Attach click handlers
    _elSessionList.querySelectorAll('.rp-session-item').forEach(function (el) {
      el.addEventListener('click', function () {
        var sessionId = el.getAttribute('data-session-id');
        selectSession(sessionId);
      });
    });
  }

  // ── Render: Session Detail ───────────────────────────────────────────────
  function renderSessionDetail(session) {
    if (!session) {
      _elSessionDetail.innerHTML = '<div class="rp-empty">Select a session</div>';
      return;
    }
    _elSessionDetail.innerHTML = [
      '<div class="rp-detail-grid">',
      '<div class="rp-detail-item"><span class="rp-detail-label">Session ID</span><span class="rp-detail-value">' + session.id + '</span></div>',
      '<div class="rp-detail-item"><span class="rp-detail-label">Schema ID</span><span class="rp-detail-value">' + (session.schema_id || '—') + '</span></div>',
      '<div class="rp-detail-item"><span class="rp-detail-label">Started</span><span class="rp-detail-value">' + fmtTime(session.started_ns) + '</span></div>',
      '<div class="rp-detail-item"><span class="rp-detail-label">Ended</span><span class="rp-detail-value">' + fmtTime(session.ended_ns) + '</span></div>',
      '<div class="rp-detail-item"><span class="rp-detail-label">Duration</span><span class="rp-detail-value">' + fmtDuration(session.started_ns, session.ended_ns) + '</span></div>',
      '<div class="rp-detail-item"><span class="rp-detail-label">Record Count</span><span class="rp-detail-value">' + (session.record_count != null ? session.record_count : '—') + '</span></div>',
      '</div>',
      '<div class="rp-detail-actions">',
      '<button id="rp-export-btn" class="rp-btn rp-btn-primary">Export CSV</button>',
      '<button id="rp-load-records-btn" class="rp-btn">Load Records</button>',
      '<button id="rp-bus-play-btn" class="rp-btn" data-testid="replay-play" title="Push stored telemetry onto the live bus (nothing is sent to the drone)">Play to Live View</button>',
      '</div>',
      '<div id="rp-export-status" class="rp-export-status"></div>'
    ].join('');

    // Export button
    q('rp-export-btn').addEventListener('click', function () {
      var outputPath = 'session_' + session.id + '_export.csv';
      var statusEl = q('rp-export-status');
      statusEl.textContent = 'Exporting…';
      exportSession(session.id, outputPath)
        .then(function (result) {
          statusEl.textContent = 'Exported: ' + (result.exported || outputPath);
          statusEl.style.color = 'var(--green)';
        })
        .catch(function (err) {
          statusEl.textContent = 'Export failed: ' + err.message;
          statusEl.style.color = 'var(--red)';
        });
    });

    // Play button: POST /replay/<id>/play feeds the other panels.
    q('rp-bus-play-btn').addEventListener('click', function () {
      var statusEl = q('rp-export-status');
      statusEl.textContent = 'Replaying…';
      playSession(session.id)
        .then(function (result) {
          if (result.error) { throw new Error(result.error); }
          statusEl.textContent = 'Replayed ' + result.replayed + ' records to live view';
          statusEl.style.color = 'var(--green)';
        })
        .catch(function (err) {
          statusEl.textContent = 'Replay failed: ' + err.message;
          statusEl.style.color = 'var(--red)';
        });
    });

    // Load records button
    q('rp-load-records-btn').addEventListener('click', function () {
      loadRecordsForSession(session.id);
    });
  }

  // ── Render: Record List / Playback ───────────────────────────────────────
  function renderRecordList(records) {
    _records = records || [];
    applyFilter();
  }

  function applyFilter() {
    if (_filterSlot === 'all') {
      _filteredRecords = _records;
    } else {
      _filteredRecords = _records.filter(function (r) {
        if (!r) return false;
        // Prefer record.slot if present; otherwise fall back to checking
        // values keys for legacy 'slotN.chN.N' prefix.
        if (r.slot != null) return String(r.slot) === String(_filterSlot);
        if (!r.values) return false;
        var keys = Object.keys(r.values);
        return keys.some(function (k) { return k.startsWith('slot' + _filterSlot + '.'); });
      });
    }
    _playbackIndex = 0;
    renderPlayback();
    renderTimeline();
  }

  function renderPlayback() {
    if (_filteredRecords.length === 0) {
      _elRecordList.innerHTML = '<div class="rp-empty">No records to display</div>';
      if (_elPlaybackInfo) _elPlaybackInfo.textContent = 'No playback data';
      return;
    }

    var record = _filteredRecords[_playbackIndex];
    if (!record) return;

    // Build display from record values
    var values = record.values || {};
    var keys = Object.keys(values);

    var html = '<div class="rp-record-header">Record #' + _playbackIndex + ' / ' + (_filteredRecords.length - 1) + '</div>';
    html += '<div class="rp-record-values">';

    // Group by slot. Prefer record.slot when present; otherwise derive a
    // group label from each key's namespace prefix (e.g. 'ekf.pos_x' → 'ekf').
    var slots = {};
    var recordSlot = record.slot != null ? String(record.slot) : null;
    keys.forEach(function (k) {
      var slot;
      if (recordSlot != null) {
        slot = recordSlot;
      } else {
        slot = (k.indexOf('.') !== -1) ? k.split('.')[0] : 'ch';
      }
      if (!slots[slot]) slots[slot] = [];
      slots[slot].push({ key: k, value: values[k] });
    });

    Object.keys(slots).sort().forEach(function (slot) {
      html += '<div class="rp-slot-group">';
      html += '<div class="rp-slot-label">' + slot + '</div>';
      html += '<div class="rp-slot-values">';
      slots[slot].forEach(function (item) {
        html += '<div class="rp-value-item">';
        html += '<span class="rp-value-key">' + stripSlotPrefix(item.key) + '</span>';
        html += '<span class="rp-value-num">' + fmtNum(item.value, 4) + '</span>';
        html += '</div>';
      });
      html += '</div></div>';
    });

    html += '</div>';
    _elRecordList.innerHTML = html;

    // Update playback info
    if (_elPlaybackInfo) {
      var ts = record.timestamp_ns || record.time_ns || null;
      _elPlaybackInfo.textContent = 'Index: ' + _playbackIndex + ' / ' + (_filteredRecords.length - 1) +
        ' | Time: ' + fmtTime(ts);
    }

    // Update scrubber
    if (_elScrubber) {
      var pct = _filteredRecords.length > 1 ? (_playbackIndex / (_filteredRecords.length - 1)) * 100 : 0;
      _elScrubber.value = pct;
    }
  }

  function renderTimeline() {
    if (!_elTimeline || _filteredRecords.length === 0) return;

    var W = _elTimeline.offsetWidth || 600;
    var H = 60;
    var canvas = _elTimeline.querySelector('canvas');
    if (!canvas) return;

    canvas.width = W;
    canvas.height = H;
    var ctx = canvas.getContext('2d');

    // Background
    ctx.fillStyle = '#0a0a1a';
    ctx.fillRect(0, 0, W, H);

    // Draw mini-timeline dots
    var step = Math.max(1, Math.floor(_filteredRecords.length / W));
    var maxY = 0;

    // Quick pass to find max value
    for (var i = 0; i < _filteredRecords.length; i += step) {
      var r = _filteredRecords[i];
      if (r && r.values) {
        var vals = Object.values(r.values);
        vals.forEach(function (v) {
          if (Math.abs(v) > maxY) maxY = Math.abs(v);
        });
      }
    }
    maxY = maxY || 1;

    ctx.fillStyle = 'rgba(78, 204, 163, 0.6)';
    for (var i = 0; i < _filteredRecords.length; i += step) {
      var r = _filteredRecords[i];
      if (r && r.values) {
        var vals = Object.values(r.values);
        var avg = vals.reduce(function (s, v) { return s + v; }, 0) / vals.length;
        var y = H - ((avg / maxY + 1) / 2 * H);
        ctx.fillRect(i / step, y, 1, 2);
      }
    }

    // Playhead
    if (_filteredRecords.length > 1) {
      var px = (_playbackIndex / (_filteredRecords.length - 1)) * W;
      ctx.fillStyle = '#e94560';
      ctx.fillRect(px - 1, 0, 2, H);
    }
  }

  // ── Stream Statistics ───────────────────────────────────────────────────
  function renderStreamStats(records) {
    if (!records || records.length === 0) {
      _elStreamStats.innerHTML = '<div class="rp-empty">No data</div>';
      return;
    }

    var stats = {};
    var totalRecords = records.length;

    // Count slot appearances and compute loss estimate.
    // Prefer record.slot when present; otherwise derive from key namespace.
    records.forEach(function (r) {
      if (!r || !r.values) return;
      var recordSlot = r.slot != null ? String(r.slot) : null;
      Object.keys(r.values).forEach(function (k) {
        var slot;
        if (recordSlot != null) {
          slot = recordSlot;
        } else {
          slot = (k.indexOf('.') !== -1) ? k.split('.')[0] : 'ch';
        }
        if (!stats[slot]) stats[slot] = { count: 0, keys: new Set() };
        stats[slot].count++;
        stats[slot].keys.add(k);
      });
    });

    var html = '<table class="rp-stats-table"><thead><tr><th>Slot</th><th>Records</th><th>Coverage</th><th>Keys</th></tr></thead><tbody>';
    Object.keys(stats).sort().forEach(function (slot) {
      var s = stats[slot];
      var pct = ((s.count / totalRecords) * 100).toFixed(1);
      var cls = parseFloat(pct) < 50 ? 'rp-loss-warn' : '';
      html += '<tr class="' + cls + '"><td>' + slot + '</td><td>' + s.count + '</td><td>' + pct + '%</td><td>' + s.keys.size + '</td></tr>';
    });
    html += '</tbody></table>';
    _elStreamStats.innerHTML = html;
  }

  // ── Playback Controls ───────────────────────────────────────────────────
  function startPlayback() {
    if (_filteredRecords.length === 0) return;
    if (_playbackTimer) clearInterval(_playbackTimer);

    var intervalMs = Math.max(10, 1000 / _playbackSpeed);
    _playbackTimer = setInterval(function () {
      _playbackIndex++;
      if (_playbackIndex >= _filteredRecords.length) {
        _playbackIndex = 0; // Loop
      }
      renderPlayback();
      renderTimeline();
    }, intervalMs);

    _elPlayBtn.textContent = '⏸ Pause';
  }

  function stopPlayback() {
    if (_playbackTimer) {
      clearInterval(_playbackTimer);
      _playbackTimer = null;
    }
    _elPlayBtn.textContent = '▶ Play';
  }

  function togglePlayback() {
    if (_playbackTimer) {
      stopPlayback();
    } else {
      startPlayback();
    }
  }

  function seekTo(pct) {
    if (_filteredRecords.length === 0) return;
    _playbackIndex = Math.floor(pct / 100 * (_filteredRecords.length - 1));
    _playbackIndex = Math.max(0, Math.min(_playbackIndex, _filteredRecords.length - 1));
    renderPlayback();
    renderTimeline();
  }

  // ── Session Selection ───────────────────────────────────────────────────
  function selectSession(sessionId) {
    stopPlayback();
    fetchSessionDetail(sessionId).then(function (detail) {
      _selectedSession = detail;
      renderSessionDetail(detail);
      renderSessionList(_sessions);
    });
  }

  function loadRecordsForSession(sessionId) {
    _elRecordList.innerHTML = '<div class="rp-empty">Loading records…</div>';
    fetchSessionRecords(sessionId).then(function (result) {
      var records = result.records || [];
      renderRecordList(records);
      renderStreamStats(records);
      renderTimeline();
      if (result.truncated) {
        var note = document.createElement('div');
        note.className = 'rp-empty';
        note.textContent = 'Showing the first ' + records.length +
          ' records of this session. Export it for the full set.';
        _elRecordList.appendChild(note);
      }
    });
  }

  // ── Build HTML ──────────────────────────────────────────────────────────
  function buildHTML() {
    return [
      '<style>',
      /* Scoped styles */
      '.rp-container { display: grid; grid-template-columns: 280px 1fr; gap: 12px; height: 100%; }',
      '.rp-sidebar { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; }',
      '.rp-main { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; }',
      '.rp-session-list { flex: 0 1 auto; max-height: 45%; overflow-y: auto; }',
      '.rp-session-item { padding: 8px 10px; border-radius: 4px; cursor: pointer; margin-bottom: 4px;',
      '  background: var(--bg); border: 1px solid transparent; }',
      '.rp-session-item:hover { border-color: var(--accent); }',
      '.rp-session-item.rp-selected { border-color: var(--green); background: rgba(78,204,163,0.1); }',
      '.rp-session-id { font-weight: 600; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
      '.rp-session-meta { font-size: 10px; color: var(--muted); margin-top: 2px; }',
      '.rp-detail-card { padding: 10px; }',
      '.rp-detail-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 8px; margin-bottom: 10px; }',
      '.rp-detail-item { display: flex; flex-direction: column; gap: 2px; }',
      '.rp-detail-label { font-size: 10px; color: var(--muted); }',
      '.rp-detail-value { font-size: 12px; font-family: Consolas, monospace; overflow-wrap: anywhere; }',
      '.rp-detail-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }',
      '.rp-btn { padding: 5px 12px; border-radius: 4px; border: 1px solid var(--border); background: var(--bg); color: var(--text);',
      '  cursor: pointer; font-size: 12px; font-weight: 600; white-space: nowrap; }',
      '.rp-btn-primary { background: var(--accent); border-color: var(--accent); }',
      '.rp-btn:hover { opacity: 0.85; }',
      '.rp-export-status { font-size: 11px; color: var(--muted); }',
      '.rp-empty { color: var(--muted); font-size: 12px; text-align: center; padding: 16px; }',
      '.rp-playback-bar { display: flex; align-items: center; gap: 10px; padding: 8px 0; }',
      '.rp-play-btn { min-width: 36px; height: 36px; padding: 0 12px; border-radius: 18px; white-space: nowrap; background: var(--accent); color: white;',
      '  border: none; cursor: pointer; font-size: 14px; }',
      '.rp-speed-btn { padding: 4px 8px; border-radius: 4px; background: var(--bg); color: var(--muted);',
      '  border: 1px solid var(--border); cursor: pointer; font-size: 11px; }',
      '.rp-speed-btn.rp-active { background: var(--accent); color: var(--text); border-color: var(--accent); }',
      '.rp-scrubber { flex: 1; -webkit-appearance: none; height: 6px; border-radius: 3px;',
      '  background: var(--bg); cursor: pointer; }',
      '.rp-scrubber::-webkit-slider-thumb { -webkit-appearance: none; width: 14px; height: 14px;',
      '  border-radius: 50%; background: var(--green); cursor: pointer; }',
      '.rp-playback-info { font-size: 11px; color: var(--muted); min-width: 180px; text-align: right; }',
      '.rp-timeline { width: 100%; border-radius: 4px; overflow: hidden; margin: 4px 0; }',
      '.rp-timeline canvas { display: block; width: 100%; }',
      '.rp-record-display { flex: 1; overflow-y: auto; background: var(--bg); border-radius: 4px; padding: 10px; }',
      '.rp-record-header { font-size: 11px; color: var(--muted); margin-bottom: 8px; padding-bottom: 6px;',
      '  border-bottom: 1px solid var(--border); }',
      '.rp-record-values { display: flex; flex-wrap: wrap; gap: 8px; }',
      '.rp-slot-group { background: rgba(255,255,255,0.03); border-radius: 4px; padding: 6px 8px; min-width: 120px; }',
      '.rp-slot-label { font-size: 10px; font-weight: 600; color: var(--amber); margin-bottom: 4px; }',
      '.rp-slot-values { display: flex; flex-direction: column; gap: 2px; }',
      '.rp-value-item { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; }',
      '.rp-value-key { color: var(--muted); font-family: Consolas, monospace; }',
      '.rp-value-num { color: var(--green); font-family: Consolas, monospace; }',
      '.rp-stats-card { max-height: 150px; overflow-y: auto; }',
      '.rp-stats-table { width: 100%; border-collapse: collapse; font-size: 11px; }',
      '.rp-stats-table th { text-align: left; font-size: 10px; color: var(--muted); padding: 4px 6px;',
      '  border-bottom: 1px solid var(--border); }',
      '.rp-stats-table td { padding: 4px 6px; font-family: Consolas, monospace; }',
      '.rp-loss-warn td { color: var(--amber); }',
      '.rp-filter-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }',
      '.rp-filter-label { font-size: 11px; color: var(--muted); }',
      '.rp-filter-select { background: var(--bg); color: var(--text); border: 1px solid var(--border);',
      '  border-radius: 4px; padding: 4px 8px; font-size: 12px; }',
      '.rp-refresh-btn { padding: 4px 10px; background: var(--accent); color: var(--text); border: none;',
      '  border-radius: 4px; cursor: pointer; font-size: 11px; }',
      '</style>',

      '<div class="rp-container">',

      /* Left sidebar: session list + detail */
      '<div class="rp-sidebar">',
      '<div class="rp-section-label" style="font-size:11px;color:var(--muted);margin-bottom:4px">Sessions</div>',
      '<div id="rp-session-list" class="rp-session-list"></div>',
      '<div id="rp-session-detail" class="rp-detail-card"></div>',
      '</div>',

      /* Right: playback + records */
      '<div class="rp-main">',
      '<div id="rp-stream-stats" class="rp-stats-card"></div>',

      '<div class="rp-filter-row">',
      '<span class="rp-filter-label">Filter:</span>',
      '<select id="rp-filter-select" class="rp-filter-select">',
      '<option value="all">All Slots</option>',
      '</select>',
      '<button id="rp-refresh-btn" class="rp-refresh-btn">&#8635; Refresh</button>',
      '</div>',

      '<div class="rp-playback-bar">',
      '<button id="rp-play-btn" class="rp-play-btn">&#9654;</button>',
      '<input type="range" id="rp-scrubber" class="rp-scrubber" min="0" max="100" value="0">',
      '<div class="rp-speed-row">',
      '<button class="rp-speed-btn rp-active" data-speed="1">1x</button>',
      '<button class="rp-speed-btn" data-speed="2">2x</button>',
      '<button class="rp-speed-btn" data-speed="5">5x</button>',
      '<button class="rp-speed-btn" data-speed="10">10x</button>',
      '</div>',
      '<span id="rp-playback-info" class="rp-playback-info"></span>',
      '</div>',

      '<div id="rp-timeline" class="rp-timeline"><canvas></canvas></div>',
      '<div id="rp-record-list" class="rp-record-display"></div>',
      '</div>',

      '</div>'
    ].join('');
  }

  // ── Init ────────────────────────────────────────────────────────────────
  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel('Session Replay', function (container) {
      container.innerHTML = buildHTML();

      _elSessionList = q('rp-session-list');
      _elSessionDetail = q('rp-session-detail');
      _elRecordList = q('rp-record-list');
      _elTimeline = q('rp-timeline');
      _elScrubber = q('rp-scrubber');
      _elPlaybackInfo = q('rp-playback-info');
      _elStreamStats = q('rp-stream-stats');
      _elFilter = q('rp-filter-select');
      _elPlayBtn = q('rp-play-btn');
      _elExportBtn = q('rp-export-btn');

      // Load sessions on init
      fetchSessions().then(function (sessions) {
        _sessions = sessions;
        renderSessionList(sessions);
      });

      // Play/pause button
      _elPlayBtn.addEventListener('click', togglePlayback);

      // Scrubber
      _elScrubber.addEventListener('input', function () {
        seekTo(parseFloat(this.value));
      });

      // Speed buttons
      document.querySelectorAll('.rp-speed-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
          document.querySelectorAll('.rp-speed-btn').forEach(function (b) { b.classList.remove('rp-active'); });
          btn.classList.add('rp-active');
          _playbackSpeed = parseInt(btn.getAttribute('data-speed'), 10) || 1;
          if (_playbackTimer) {
            stopPlayback();
            startPlayback();
          }
        });
      });

      // Refresh button
      q('rp-refresh-btn').addEventListener('click', function () {
        fetchSessions().then(function (sessions) {
          _sessions = sessions;
          renderSessionList(sessions);
        });
      });

      // Filter select
      _elFilter.addEventListener('change', function () {
        _filterSlot = this.value;
        applyFilter();
      });

      // Window resize → redraw timeline (captured so destroy can remove it)
      var _onResize = function () {
        if (_filteredRecords.length > 0) renderTimeline();
      };
      window.addEventListener('resize', _onResize);
      _boundResize = _onResize;

      // Initial state
      _elSessionDetail.innerHTML = '<div class="rp-empty">Select a session to view details</div>';
      _elRecordList.innerHTML = '<div class="rp-empty">Load records to begin replay</div>';
      _elPlaybackInfo.textContent = 'No playback data';
    });
  };

  window.__PLUGIN_DESTROY__ = function () {
    if (_boundResize) {
      window.removeEventListener('resize', _boundResize);
      _boundResize = null;
    }
    stopPlayback();
    _sessions = [];
    _selectedSession = null;
    _records = [];
    _filteredRecords = [];
    _playbackIndex = 0;
  };

  window.__registerPlugin__('Session Replay', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);

})();
