const fs = require('fs');
let code = fs.readFileSync('docs/dashboard-platform/shell/plugins/campaign-panel.js', 'utf8');

const escapeHtmlFunc = `
  function escapeHtml(unsafe) {
    if (unsafe === undefined || unsafe === null) return '';
    return String(unsafe)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function q(id) {`;
code = code.replace("  function q(id) {", escapeHtmlFunc);

const renderStateReplace = `      var rows = (_state.flights || []).map(function(f) {
        return '<tr>' +
          '<td>' + escapeHtml(f.flight_id) + '</td>' +
          '<td>' + escapeHtml(f.pack_id) + '</td>' +
          '<td>' + escapeHtml(f.experiment) + '</td>' +
          '<td>' + escapeHtml(f.j) + '</td>' +
          '<td>' + escapeHtml(f.decision) + '</td>' +
          '<td>' + escapeHtml(f.abort_level) + '/' + escapeHtml(f.abort_reason) + '</td>' +
          '<td>' + (f.hover_only ? 'yes' : 'no') + '</td>' +
          '</tr>';
      });`;
code = code.replace(/      var rows = \(_state\.flights[^]+?      }\);/, renderStateReplace);

const fetchStateReplace = `  function fetchState() {
    fetch('/api/campaign/state')
      .then(function (r) {
        if (!r.ok) {
          return r.json().catch(function() { return { error: 'HTTP ' + r.status }; })
            .then(function(data) {
              showError(data.error || ('HTTP ' + r.status));
              throw new Error('__HANDLED__');
            });
        }
        return r.json();
      })
      .then(function (data) {
        showError('');
        _state = data;
        renderState();
      })
      .catch(function (e) {
        if (e.message !== '__HANDLED__') {
          showError(e.message);
        }
      });
  }`;
code = code.replace(/  function fetchState\(\) {[\s\S]+?  }/, fetchStateReplace);

const handleAllowArmReplace = `  function handleAllowArmChange(e) {
    var next = e.target.checked;
    var prev = !next;
    var target = e.target;
    if (next) {
      if (!window.confirm('Allow agent arm?')) {
        target.checked = false;
        return;
      }
    }

    fetch('/api/agent/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ allow_agent_arm: next, source: 'operator' })
    })
    .then(function(r) {
      if (!r.ok) {
        target.checked = prev;
        return r.json().catch(function() { return { error: 'HTTP ' + r.status }; })
          .then(function(data) { showError(data.error || ('HTTP ' + r.status)); });
      } else {
        showError('');
      }
    })
    .catch(function(err) {
      target.checked = prev;
      showError(err.message);
    });
  }`;
code = code.replace(/  function handleAllowArmChange\(e\) {[\s\S]+?  }/, handleAllowArmReplace);

const handleGoStart = `  function handleGo() {
    var goBtn = q('cp-go-btn');
    if (goBtn) goBtn.disabled = true;

    var pathInput = q('cp-path');`;
code = code.replace(/  function handleGo\(\) {\n    var pathInput = q\('cp-path'\);/, handleGoStart);

const handleGoError1 = `      if (!r.ok) {
        checkGoReady();
        return r.json().catch(function() { return { error: 'HTTP ' + r.status }; })`;
code = code.replace(/      if \(\!r\.ok\) {\n        return r\.json/, handleGoError1);

const handleGoError2 = `    .catch(function(e) {
      checkGoReady();
      showError(e.message);
    });`;
code = code.replace(/    \.catch\(function\(e\) {\n      showError\(e\.message\);\n    }\);/, handleGoError2);

const pluginInit = `      if (_pollingTimer) {
        clearInterval(_pollingTimer);
      }
      fetchState();
      _pollingTimer = setInterval(fetchState, 1000);`;
code = code.replace(/      fetchState\(\);\n      _pollingTimer = setInterval\(fetchState, 1000\);/, pluginInit);


fs.writeFileSync('docs/dashboard-platform/shell/plugins/campaign-panel.js', code);
