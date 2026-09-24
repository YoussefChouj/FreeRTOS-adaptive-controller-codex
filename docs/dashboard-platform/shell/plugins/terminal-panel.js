/* Terminal panel plugin -- xterm.js WebSocket PTY client.

Loaded from the "Terminal" tab.  Uses xterm.js + xterm-addon-fit from
cdn.jsdelivr.net/npm (injected via <link> / <script> tags on first mount).
The token is read from sessionStorage (set by the operator UI); if missing
the panel shows a "Connect" button that prompts the user.

Events from the agent layer:
  - ``ui`` event with ``action: "switch_tab"`` and ``args.tab == "terminal"``
    auto-focuses this tab.
  - ``ui`` event with ``action: "highlight"`` highlights this panel.
*/

(function () {
  "use strict";

  /* -- xterm.js loader --------------------------------------------------- */
  var xtermLoaded = false;
  var fitLoaded = false;
  var xtermResolve = null;
  var xtermReject = null;
  var xtermPromise = new Promise(function (res, rej) {
    xtermResolve = res;
    xtermReject = rej;
  });

  function appendScript(s) {
    var parent = document.head || document.body || document.documentElement;
    if (parent) {
      parent.appendChild(s);
    } else {
      xtermReject(new Error("no document to load xterm.js into"));
    }
  }

  function loadXterm() {
    if (xtermLoaded && fitLoaded) {
      xtermResolve(window.Terminal);
      return xtermPromise;
    }
    if (!window.__gs_terminal_loaded) {
      window.__gs_terminal_loaded = true;
      /* xterm.js */
      var s1 = document.createElement("script");
      s1.src =
        "https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js";
      s1.onload = function () {
        xtermLoaded = true;
        /* xterm-addon-fit */
        var s2 = document.createElement("script");
        s2.src =
          "https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js";
        s2.onload = function () {
          fitLoaded = true;
          xtermResolve(window.Terminal);
        };
        s2.onerror = function () {
          xtermReject(new Error("xterm-addon-fit failed to load"));
        };
        appendScript(s2);
      };
      s1.onerror = function () {
        xtermReject(new Error("xterm.js failed to load from CDN"));
      };
      appendScript(s1);
    }
    return xtermPromise;
  }

  /* -- Panel state ------------------------------------------------------- */
  var term = null;
  var fitAddon = null;
  var ws = null;
  var terminalEl = null;
  var statusEl = null;
  var tokenEl = null;
  var connectBtn = null;
  var inputRow = null;

  /* Read the token from sessionStorage (set via the operator UI or manual
   * paste).  The terminal module writes it to .agent_state/terminal-token. */
  function getToken() {
    try {
      return sessionStorage.getItem("terminal_token") || "";
    } catch (_) {
      return "";
    }
  }

  function setToken(val) {
    try {
      sessionStorage.setItem("terminal_token", val);
    } catch (_) {
      /* sessionStorage unavailable -- silently ignore */
    }
  }

  /* -- Connect / disconnect ---------------------------------------------- */
  function connect() {
    var token = getToken();
    if (!token) {
      setStatus("no-token", "Token required -- enter it above then click Connect.");
      return;
    }
    setStatus("connecting", "Connecting...");
    try {
      ws = new WebSocket(
        buildWsUrl(token)
      );
    } catch (e) {
      setStatus("error", "WebSocket error: " + e.message);
      return;
    }
    ws.onopen = function () {
      setStatus("connected", "Connected");
      if (term) term.write("\x1b[32mConnected.\x1b[0m\r\n");
    };
    ws.onmessage = function (evt) {
      if (term && typeof evt.data === "string") {
        term.write(evt.data);
      }
    };
    ws.onclose = function () {
      setStatus("disconnected", "Disconnected");
      ws = null;
      if (term) term.write("\r\n\x1b[31mDisconnected.\x1b[0m\r\n");
    };
    ws.onerror = function () {
      setStatus("error", "Connection error");
    };
  }

  function disconnect() {
    if (ws) {
      ws.close();
      ws = null;
    }
    setStatus("disconnected", "Disconnected");
  }

  function buildWsUrl(token) {
    /* Use the same host/port as the current page.  The path is absolute. */
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + location.host + "/api/terminal/ws?token=" + encodeURIComponent(token);
  }

  /* -- Status display ---------------------------------------------------- */
  function setStatus(type, text) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.className = "term-status term-status-" + type;
  }

  /* -- Resize handling --------------------------------------------------- */
  function sendResize() {
    if (!ws || !term) return;
    ws.send(JSON.stringify({
      type: "resize",
      rows: term.rows,
      cols: term.cols,
    }));
  }

  /* -- Panel rendering --------------------------------------------------- */
  function render(container) {
    if (terminalEl) return; /* already rendered */

    /* Create container */
    terminalEl = document.createElement("div");
    terminalEl.id = "plugin-body-terminal";
    terminalEl.setAttribute("data-testid", "panel-terminal");
    terminalEl.style.cssText =
      "display:flex;flex-direction:column;height:100%;overflow:hidden;";

    /* Input row: token + connect button */
    inputRow = document.createElement("div");
    inputRow.style.cssText =
      "display:flex;gap:6px;padding:6px 8px;background:var(--bg2);border-bottom:1px solid var(--border);flex-shrink:0;";

    var tokenLabel = document.createElement("label");
    tokenLabel.textContent = "Token:";
    tokenLabel.style.cssText = "font-size:11px;color:var(--muted);white-space:nowrap;";

    tokenEl = document.createElement("input");
    tokenEl.type = "password";
    tokenEl.placeholder = "terminal-token";
    tokenEl.style.cssText =
      "flex:1;padding:3px 6px;border:1px solid var(--border);border-radius:3px;font-size:11px;background:var(--bg);color:var(--fg);";
    /* Populate from sessionStorage on first render */
    var saved = getToken();
    if (saved) tokenEl.value = saved;

    connectBtn = document.createElement("button");
    connectBtn.textContent = "Connect";
    connectBtn.style.cssText =
      "padding:3px 10px;border:1px solid var(--border);border-radius:3px;background:var(--btn-bg);color:var(--fg);font-size:11px;cursor:pointer;";
    connectBtn.addEventListener("click", function () {
      setToken(tokenEl.value);
      if (ws) {
        disconnect();
      }
      connect();
    });

    var disconnectBtn = document.createElement("button");
    disconnectBtn.textContent = "Disconnect";
    disconnectBtn.style.cssText =
      "padding:3px 10px;border:1px solid var(--border);border-radius:3px;background:var(--btn-bg);color:var(--fg);font-size:11px;cursor:pointer;";
    disconnectBtn.addEventListener("click", disconnect);

    inputRow.appendChild(tokenLabel);
    inputRow.appendChild(tokenEl);
    inputRow.appendChild(connectBtn);
    inputRow.appendChild(disconnectBtn);

    /* Status bar */
    statusEl = document.createElement("div");
    statusEl.className = "term-status term-status-idle";
    statusEl.style.cssText =
      "font-size:10px;padding:2px 8px;color:var(--muted);background:var(--bg2);border-bottom:1px solid var(--border);flex-shrink:0;";
    setStatus("idle", "idle -- enter token and connect");

    /* Terminal container */
    var termContainer = document.createElement("div");
    termContainer.style.cssText =
      "flex:1;overflow:hidden;padding:4px;";

    terminalEl.appendChild(inputRow);
    terminalEl.appendChild(statusEl);
    terminalEl.appendChild(termContainer);

    /* Load xterm and init */
    loadXterm().then(function (Terminal) {
      term = new Terminal({
        cursorBlink: true,
        fontSize: 12,
        fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
        theme: {
          background: "#1e1e1e",
          foreground: "#d4d4d4",
          cursor: "#aeafad",
        },
      });
      try {
        fitAddon = new window.FitAddon.FitAddon();
        term.loadAddon(fitAddon);
      } catch (e) {
        /* fit addon unavailable */
      }
      term.open(termContainer);

      /* Forward user input to WebSocket */
      term.onData(function (data) {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(data);
        }
      });

      /* Resize */
      if (fitAddon) {
        try {
          fitAddon.fit();
        } catch (_) {}
      }
      sendResize();

      var ro = new ResizeObserver(function () {
        if (fitAddon) {
          try { fitAddon.fit(); } catch (_) {}
        }
        sendResize();
      });
      ro.observe(termContainer);
    }).catch(function (e) {
      setStatus("error", "xterm.js unavailable: " + e.message);
    });

    /* Append to plugin card body */
    var body = container || document.getElementById("plugin-body-terminal");
    if (body) {
      body.innerHTML = "";
      body.appendChild(terminalEl);
    }
  }

  /* -- Registration ------------------------------------------------------ */
  window.__PLUGIN_INIT__ = function (api) {
    api.registerPanel("Terminal", render, {
      workspace: "terminal",
      gates: [],
      description: "Terminal PTY session via WebSocket (xterm.js)",
    });
  };

  window.__PLUGIN_DESTROY__ = function () {};

  if (typeof window !== "undefined" && window.__registerPlugin__) {
    window.__registerPlugin__("Terminal", window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__,
      { workspace: "terminal", description: "Terminal PTY session via WebSocket (xterm.js)" });
  }
})();
