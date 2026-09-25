/**
 * flight-test-panel.js — One-click flight-test pipeline UI plugin
 *
 * Augments the record control with:
 *   - "Analyse" checkbox
 *   - Controller selector (pid / mrac)
 *   - Payload selector (symmetric / asymmetric)
 *   - Free-text notes field
 *   - Status line showing analysis state (pending/running/done/failed)
 *     and output folder path after stop.
 *
 * Follows the plugin-api.md convention: __registerPlugin__(name, init, destroy).
 * Uses the same DOM-inspection approach as record-helper.js.
 */
(function () {
  'use strict';

  function q(id) {
    return typeof document !== 'undefined' ? document.getElementById(id) : null;
  }

  function _buildUI(ctl) {
    if (q('flight-test-panel')) return true; // already installed

    var panel = document.createElement('div');
    panel.id = 'flight-test-panel';
    panel.style.cssText = 'display:block;font-size:11px;margin:4px 0 4px;' +
      'padding:6px 8px;background:var(--card,#fff);border:1px solid var(--border,#ddd);' +
      'border-radius:4px;line-height:1.5;';

    // Preset hint label
    var presetHint = document.createElement('div');
    presetHint.style.cssText = 'font-size:10px;color:var(--muted,#888);margin-bottom:4px;';
    presetHint.textContent = 'Preset: flight_test_adaptive';
    panel.appendChild(presetHint);

    // Analyse checkbox
    var checkRow = document.createElement('div');
    checkRow.style.cssText = 'margin-bottom:4px;';
    var checkLabel = document.createElement('label');
    checkLabel.style.cssText = 'cursor:pointer;display:inline-flex;align-items:center;gap:4px;';
    var analyseCheck = document.createElement('input');
    analyseCheck.type = 'checkbox';
    analyseCheck.id = 'flight-test-analyse';
    analyseCheck.checked = false;
    checkLabel.appendChild(analyseCheck);
    checkLabel.appendChild(document.createTextNode(' Analyse'));
    checkRow.appendChild(checkLabel);

    // Controller selector
    var ctrlRow = document.createElement('div');
    ctrlRow.style.cssText = 'margin-bottom:4px;';
    var ctrlLabel = document.createElement('span');
    ctrlLabel.style.cssText = 'margin-right:6px;color:var(--muted,#666);';
    ctrlLabel.textContent = 'Controller:';
    var ctrlSelect = document.createElement('select');
    ctrlSelect.id = 'flight-test-controller';
    ctrlSelect.style.cssText = 'font-size:11px;padding:1px 4px;';
    ctrlSelect.innerHTML = '<option value="pid">pid</option>' +
      '<option value="mrac">mrac</option>' +
      '<option value="unknown">unknown</option>';
    ctrlSelect.value = 'pid';
    ctrlRow.appendChild(ctrlLabel);
    ctrlRow.appendChild(ctrlSelect);

    // Payload selector
    var payloadRow = document.createElement('div');
    payloadRow.style.cssText = 'margin-bottom:4px;';
    var payloadLabel = document.createElement('span');
    payloadLabel.style.cssText = 'margin-right:6px;color:var(--muted,#666);';
    payloadLabel.textContent = 'Payload:';
    var payloadSelect = document.createElement('select');
    payloadSelect.id = 'flight-test-payload';
    payloadSelect.style.cssText = 'font-size:11px;padding:1px 4px;';
    payloadSelect.innerHTML = '<option value="symmetric">symmetric</option>' +
      '<option value="asymmetric">asymmetric</option>' +
      '<option value="unknown">unknown</option>';
    payloadSelect.value = 'symmetric';
    payloadRow.appendChild(payloadLabel);
    payloadRow.appendChild(payloadSelect);

    // Notes field
    var notesRow = document.createElement('div');
    notesRow.style.cssText = 'margin-bottom:4px;';
    var notesLabel = document.createElement('span');
    notesLabel.style.cssText = 'display:block;margin-bottom:2px;color:var(--muted,#666);';
    notesLabel.textContent = 'Notes:';
    var notesInput = document.createElement('input');
    notesInput.type = 'text';
    notesInput.id = 'flight-test-notes';
    notesInput.placeholder = 'Optional notes...';
    notesInput.style.cssText = 'width:100%;font-size:11px;padding:2px 4px;' +
      'box-sizing:border-box;';
    notesRow.appendChild(notesLabel);
    notesRow.appendChild(notesInput);

    // Status line (shown after stop)
    var statusEl = document.createElement('span');
    statusEl.id = 'flight-test-status';
    statusEl.style.cssText = 'display:block;font-size:10px;color:var(--muted,#888);' +
      'font-family:Consolas,monospace;word-break:break-all;min-height:14px;';

    panel.appendChild(checkRow);
    panel.appendChild(ctrlRow);
    panel.appendChild(payloadRow);
    panel.appendChild(notesRow);
    panel.appendChild(statusEl);
    ctl.appendChild(panel);
    return true;
  }

  function _pollStatus() {
    fetch('/api/recording', { method: 'GET' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data) return;
        if (data.recording) {
          // Still recording — clear status
          var el = q('flight-test-status');
          if (el) el.textContent = '';
          return;
        }
        // Recording stopped — check for analysis info
        var el = q('flight-test-status');
        if (!el) return;
        var lines = [];
        if (data.analysis_status) {
          lines.push('Analysis: ' + data.analysis_status);
        }
        if (data.analysis_output_path) {
          lines.push('Output: ' + data.analysis_output_path);
        }
        if (data.flight_test_dir) {
          lines.push('Folder: ' + data.flight_test_dir);
        }
        if (data.analysis_error) {
          el.style.color = '#d32f2f';
          lines.push('Error: ' + data.analysis_error);
        } else {
          el.style.color = '';
        }
        el.textContent = lines.length ? lines.join('  |  ') : '';
      })
      .catch(function () {
        // Ignore fetch errors
      });
  }

  function boot() {
    var tries = 0;
    function step() {
      var ctl = q('record-control');
      if (!ctl) {
        if (tries++ > 40) return; // ~2s max wait
        setTimeout(step, 50);
        return;
      }
      _buildUI(ctl);
      // Attach click handler: on start-recording, expose flight-test fields
      // so the shell includes them in /api/recording/start body.
      var btn = q('record-btn');
      if (btn && !btn._flightTestBound) {
        btn._flightTestBound = true;
        btn.addEventListener('click', function () {
          // Determine if we are starting or stopping by checking current state.
          fetch('/api/recording', { method: 'GET' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              if (data && data.recording) {
                // Was recording — will stop; poll status after.
                setTimeout(_pollStatus, 1000);
                return;
              }
              // Starting a new recording — attach flight-test fields to the
              // body so the shell's fetch picks them up via
              // window.__flight_test_start_body__.
              var check = q('flight-test-analyse');
              var ctrlSel = q('flight-test-controller');
              var payloadSel = q('flight-test-payload');
              var notesInp = q('flight-test-notes');
              window.__flight_test_start_body__ = {
                analyse: check ? check.checked : false,
                controller: ctrlSel ? (ctrlSel.value || '') : '',
                payload: payloadSel ? (payloadSel.value || '') : '',
                notes: notesInp ? (notesInp.value || '').trim() : '',
              };
            })
            .catch(function () {});
        });
      }
    }
    step();
  }

  window.__PLUGIN_INIT__ = function () { boot(); };
  window.__PLUGIN_DESTROY__ = function () {};
  window.__registerPlugin__('Flight Test Panel',
    window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);
})();
