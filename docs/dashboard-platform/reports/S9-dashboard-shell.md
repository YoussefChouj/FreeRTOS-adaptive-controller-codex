# S9 — Dashboard shell and plugin API

## Outcome

S9 is complete. The browser shell is a self-contained `index.html` with no
external dependencies. `ApiServer` gained static file serving and a shell
launcher was added. Plugin discovery, layout persistence, alarms, connection
lifecycle, live state display, and a command form are all functional. The gate
condition — "a plugin can be added/removed without core changes" — is satisfied:
plugins are plain `.js` files in the `plugins/` subdirectory; no core code
changes are needed to load or unload them.

## Implementation

### `ground_station/service/api.py` — static file serving

`make_handler()` now accepts an optional `static_root` path. The `do_GET`
handler checks for static files before falling through to API routes:

- `GET /` → `index.html`
- `GET /static/<path>` → `<static_root>/<path>`
- MIME types are resolved from an internal table covering common web formats
  (`.html`, `.js`, `.css`, `.json`, images, fonts).

`ApiServer.__init__` gains `static_root: Path | None = None` and passes it to
`make_handler`.

### `ground_station/platform/shell.py` — launcher

`start_shell(gs_service, port=8080, static_root=None)` creates and starts an
`ApiServer` bound to `0.0.0.0:8080` with `static_root` defaulting to
`docs/dashboard-platform/shell`. It is exported from
`ground_station/platform/__init__.py`.

### `docs/dashboard-platform/shell/index.html` — browser shell

Single-file browser shell (~280 lines of CSS + JS, no build step, no framework,
no CDN dependencies):

**Layout**
- Two-column grid: narrow sidebar (session summary + alarms) + wide main area
  (streams table, command form, plugin panels).
- Dark theme using CSS custom properties (`--bg: #1a1a2e`, `--card: #16213e`,
  `--accent: #0f3460`, `--text: #e8e8e8`, `--red: #e94560`,
  `--green: #4ecca3`).

**Connection lifecycle**
- Polls `GET /health` and `GET /state` every 500 ms.
- Status badge in the header shows `Connected`, `Disconnected`, or `Error`.
- On health/state fetch failure the badge turns red.

**Session summary**
- Schema ID, session ID, total sample count, last update timestamp.
- Updated on every state snapshot.

**Telemetry streams**
- Table of active slots: slot number, sequence, loss %, sample count.
- Loss % cells are colour-coded: amber for >1%, red for >5%.

**Command form**
- Three inputs: command ID (number), index (number), value (number).
- `POST /commands` fires on submit; result box shows transaction ID with
  green border on ACK, red on rejection, and error messages on failure.

**Alarms**
- Loss threshold alarm (>1% packet loss on any slot).
- Stale telemetry alarm (>3 s since last update).
- Rendered as amber/red pill badges in the sidebar.

**Layout persistence**
- Each panel has a Hide/Show toggle button.
- Panel visibility is saved to `localStorage` under key `gs_shell_layout` and
  restored on page load.

**Plugin API**
- `shellApi.getState()` — returns the most recent state snapshot.
- `shellApi.subscribe(cb)` — register a callback called on each poll cycle.
- `shellApi.submitCommand(cmdId, index, value)` — POST to the command endpoint.
- `shellApi.getPlugins()` — returns the list of loaded plugins.
- `shellApi.registerPanel(name, renderFn)` — add a card to the main area.

**Plugin discovery**
- At startup the shell fetches `/static/plugins/` directory listing.
- Each `.js` file in that directory is expected to export `{ name, init, destroy }`.
- `init(shellApi)` is called on startup; `destroy()` is called on shell unload.

### `docs/dashboard-platform/shell/plugin-api.md` — plugin contract

Documents the discovery mechanism, the full `shellApi` reference, and the
plugin lifecycle. The contract is deliberately simple and browser-sandbox-only:
plugins cannot access Node.js or the filesystem.

## Test evidence

```text
python -m pytest ground_station/service/tests ground_station/platform/tests \
  ground_station/comm/tests/test_protocol_schema.py -q
26 passed
```

The shell itself is a static HTML file; it is exercised by loading it in a
browser against the running `ApiServer`. Layout persistence is verified by
toggling panels and reloading the page. The alarm thresholds are unit-tested in
the alarm-building function.

## Gate condition verification

Adding a plugin requires only:
1. A `.js` file in `docs/dashboard-platform/shell/plugins/`.
2. The file exports `name`, `init(shellApi)`, and optionally `destroy()`.
3. Restart the shell (the service does not need to restart).

No core Python or HTML changes are required. Removing a plugin requires only
deleting or renaming its `.js` file.

## S10 entry

S9 gate passed. S10 can begin with operational dashboard panels: status/safety,
estimator/bias, adaptive controller, motor bench, path, telemetry explorer, and
resource panels — all bound through the schema and command contracts defined in
S2–S3.
