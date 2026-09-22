/* Phase 1B plugin F — Hot Reload & Code Reload Banner.
 * Two responsibilities, both reacting to SSE `shell_updated` events:
 *  1. per-plugin hot reload: when the changed files are only plugins/<x>.js,
 *     ask the shell to re-mount just that plugin (see index.html
 *     remountPluginFile) without a full page reload — this plugin tracks which
 *     plugin files were hot-reloaded so it can surface the info.
 *  2. a "what changed" card listing the changed files plus the summary the
 *     agent provided via its `say` command (surfaced through SSE `message` /
 *     `activity` events with kind 'code_change' or a message whose text
 *     describes the edit).
 *
 * Maintains a card list of recent changes. Publishes {handle, get, hot(cargo)}
 * under window.__gs_ui_state__.codeReload.
 */
(function () {
  'use strict';
  var KEY = 'codeReload';
  var changes = [];      // {at, files[], summary}
  var hotReloaded = {};  // map src -> at (plugin files hot-replaced in place)
  var handle = null;

  function view() {
    return {
      changes: changes.slice(-20),
      hotReloaded: Object.keys(hotReloaded).map(function (src) {
        return { src: src, at: hotReloaded[src] };
      }),
    };
  }

  function recordChange(files, summary) {
    changes.push({ at: Date.now() / 1000, files: files.slice(), summary: summary || null });
    if (changes.length > 200) changes.splice(0, changes.length - 200);
  }

  function isPluginFile(file) { return /^plugins\/.*\.js$/.test(file) || /^\/plugins\/.*\.js$/.test(file); }

  window.__registerPlugin__('Code Reload', function (api) {
    var container;
    try {
      api.registerPanel('What changed', function (body) {
        body.setAttribute('data-testid', 'code-reload-card');
        container = body;
      });
    } catch (e) { console.warn('[codeReload] panel skip:', e && e.message); }

    handle = function (evt) {
      var d = evt && evt.data ? evt.data : {};
      if (evt.event === 'shell_updated' && Array.isArray(d.files)) {
        var pluginOnly = d.files.filter(isPluginFile);
        var shared = d.files.filter(function (f) { return !isPluginFile(f); });
        // per-plugin hot reload: only plugin files changed → no full reload.
        if (d.files.length && pluginOnly.length === d.files.length) {
          pluginOnly.forEach(function (f) { hotReloaded[f] = d.at || Date.now() / 1000; });
          recordChange(pluginOnly, 'hot-reloaded in place (plugin only)');
        } else {
          // index.html or shared shell changed → full reload banner.
          recordChange(d.files, d.summary || 'shell changed — reload required to pick up shared files');
        }
      } else if (evt.event === 'message') {
        // The agent's `say` summary often accompanies a code edit.
        var text = d.text;
        if (text && /(\bfiles?\b|code|edit|wrote|updated)/i.test(text)) {
          recordChange([], text);
        }
      } else if (evt.event === 'activity') {
        var kind = d.kind, data = d.data || {};
        if (kind === 'code_change' || kind === 'shell_updated') {
          recordChange((data.files || []).slice(), data.summary || data.text || null);
        }
      }
      render();
    };
    api.onAgentEvent(handle);

    // hot(src) — ask the shell to hot-reload a single plugin file.
    function hot(src) {
      if (!src || !/plugins\/.*\.js$/.test(src)) return Promise.reject(new Error('not a plugin file: ' + src));
      if (typeof api === 'object' && window.__gs_hot_reload_plugin__) {
        return window.__gs_hot_reload_plugin__(src);
      }
      // Fallback: trigger a remount through the shell API if exposed.
      var gs = typeof window !== 'undefined' ? window : null;
      if (gs && gs.__gs_hot_reload_plugin__) return gs.__gs_hot_reload_plugin__(src);
      console.warn('[codeReload] no hot-reload hook available for', src);
      return Promise.resolve({ skipped: true });
    }

    function render() {
      if (!container) return;
      container.innerHTML = '';
      if (!changes.length) {
        container.innerHTML = '<div style="opacity:.5">No recent code changes.</div>';
        return;
      }
      changes.slice(-10).forEach(function (c) {
        var row = document.createElement('div');
        row.style.cssText = 'padding:6px 0;border-bottom:1px solid rgba(230,233,239,.08);';
        var head = document.createElement('div');
        head.textContent = c.files.length ? '✦ ' + c.files.join(', ') : '✦ code change';
        row.appendChild(head);
        if (c.summary) {
          var sum = document.createElement('div');
          sum.textContent = c.summary;
          sum.style.cssText = 'opacity:.7;font-size:11px;';
          row.appendChild(sum);
        }
        container.appendChild(row);
      });
    }

    window.__gs_ui_state__ = window.__gs_ui_state__ || {};
    window.__gs_ui_state__[KEY] = { handle: handle, get: view, hot: hot, recordChange: recordChange };
  }, function () {}, { workspace: 'all', description: 'Per-plugin hot reload + what-changed card' });
})();