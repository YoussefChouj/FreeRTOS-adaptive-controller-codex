/**
 * record-helper.js — Operator walkthrough 2026-09-22 (items 9 & 10)
 *
 * Augments the shell's built-in Record control WITHOUT editing index.html:
 *
 *   - Item 10: describes what the note-kind (note/goal/marker) dropdown
 *     actually does. It tags an annotation with that kind into this
 *     recording's `events.jsonl` — it does NOT name the log. The recording
 *     directory name is fixed by the start label (POST /api/recording/start),
 *     never by a note.
 *   - Item 9: after a Stop, surfaces the absolute path of the saved log
 *     directory. `GET /api/recording` returns `session_abs_path` (added to the
 *     service explicitly so the operator does not have to know the service
 *     CWD). We only ever issue a read-only GET here; the Stop POST itself
 *     remains owned by the shell's own handler.
 *
 * The shell loads plugins after the DOM is present, but the record-control
 * header is static; still, we poll briefly for the anchor in case init runs
 * before the elements are mounted in some layout.
 */
(function () {
  'use strict';

  function q(id) { return typeof document !== 'undefined' ? document.getElementById(id) : null; }

  // Insert a small helper under the note-kind dropdown explaining what it does.
  function installNoteHelper() {
    var kind = q('note-kind');
    if (!kind || !kind.parentNode) return false;
    if (q('record-helper-note-kind')) return true; // already installed
    var box = document.createElement('span');
    box.id = 'record-helper-note-kind';
    box.style.cssText = 'display:block;font-size:10px;color:var(--muted);' +
      'max-width:260px;margin:2px 0 8px;line-height:1.35;';
    box.textContent =
      'Kind tags an annotation (note/goal/marker) written to this recording’s ' +
      'events.jsonl — it does not name the log (the folder name comes from the start label).';
    kind.parentNode.appendChild(box);
    return true;
  }

  // Show the saved log's absolute path after a Stop.
  function installPathDisplay() {
    if (q('record-helper-saved')) {
      // Path display already in the control; (re)attach is idempotent.
    }
    var btn = q('record-btn');
    if (!btn || btn._recordHelperBound) return;
    btn._recordHelperBound = true;
    btn.addEventListener('click', function () {
      // window.recOn is maintained by the shell's renderRecord() (index.html).
      var wasRecording = !!(window.recOn);
      var ctl = q('record-control');
      var savedEl = q('record-helper-saved');
      if (!ctl) return;
      if (!savedEl) {
        savedEl = document.createElement('span');
        savedEl.id = 'record-helper-saved';
        savedEl.style.cssText = 'display:block;font-size:10px;color:var(--green);' +
          'margin:4px 0 0;font-family:Consolas,monospace;word-break:break-all;';
        ctl.appendChild(savedEl);
      }
      if (!wasRecording) { savedEl.textContent = ''; return; }
      savedEl.textContent = 'Reading saved path…';
      // Wait for the shell's own Stop POST to round-trip, then read back the
      // (now stopped) status — still read-only.
      setTimeout(function () {
        fetch('/api/recording', { method: 'GET' })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (!data) { savedEl.textContent = ''; return; }
            // Only describe the just-finished session; never a fake 0/blank.
            if (data.recording) { savedEl.textContent = ''; return; }
            var p = data.session_abs_path || data.session_dir;
            if (!p) { savedEl.textContent = 'Saved log path not published.'; return; }
            savedEl.textContent = 'Log saved: ' + p;
          })
          .catch(function () {
            savedEl.textContent = 'Saved log path not published (status read failed).';
          });
      }, 700);
    });
  }

  function boot() {
    var tries = 0;
    function step() {
      var noteOk = installNoteHelper();
      installPathDisplay();
      if (noteOk || tries++ > 40) return; // ~2s max wait for the header anchors
      setTimeout(step, 50);
    }
    step();
  }

  window.__PLUGIN_INIT__ = function () { boot(); };
  window.__PLUGIN_DESTROY__ = function () {};
  window.__registerPlugin__('Record Helper', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);
})();