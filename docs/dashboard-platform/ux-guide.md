# Dashboard UX guide (WP-35)

Files: `shell/ui/tokens.css` (all values), `shell/ui/components.css` (shared classes), `shell/ui/ui-kit.js` (`window.GSUI`).
`index.html` loads all three before any plugin. No build step, no runtime dependency. Panel audit: `ux-audit-2026-10-04.md`.

## Tokens
- Colour: `--gs-bg --gs-surface --gs-surface-2 --gs-primary --gs-border --gs-text --gs-text-muted --gs-focus`.
- Status scale, used the same way everywhere (pill, banner, table row, stale mark, strip):

| Status | Colour | Means |
|---|---|---|
| `ok` | green | live and inside its limits |
| `warn` | amber | act soon: degraded, armed, waiting for the operator |
| `fail` | red | act now: link lost, trip, failed check, refused command |
| `stale` | grey | no fresh data: never received, frozen, older than its limit |
| `info` | blue | neutral activity: running, sending |

  Each has a tinted background `--gs-<status>-bg`. In JS use `GSUI.color(status)`, never a hex value.
- Spacing `--gs-space-1..5` (4 8 12 16 24 px), type `--gs-text-xs..xl` (10 11 13 15 18 px), `--gs-font`, `--gs-font-mono`,
  radius `--gs-radius-sm|md|pill`.
- Theme: `<html data-theme="dark|light">`, dark by default; the strip's Theme button calls `GSUI.setTheme`.
- Old names (`--red --green --amber --muted --bg --card --accent --text --border`) are aliases and follow the theme.

## Components (`GSUI.*` returns HTML strings or updates an element in place)
- Status pill: `pill(status, text, title, id)`, `setPill(el, status, text, title)`.
- Two-click confirm: `twoClick(btn, fn)` / `confirmClick(btn)`, for actions that add risk (grant access, bench mode, resets).
  Never `window.confirm` or `alert`: a browser can block them, and then the click does nothing.
- Data table: `table(columns, rows, {bodyId, rowClass, empty})`, `tableRows(...)` for polling updates; rows `gs-row--<status>`.
- Toast: `toast(text, status)`, bottom right, at most 4, auto-dismiss. Empty state: `empty(text, hint)`.
- Stale-data mark: `staleMark(ageMs)` / `fmtAge` / `ageStatus` (ok < 1 s <= warn < 3 s <= stale).
- Disabled with reason: `setDisabled(btn, reason, reasonEl)`. The reason goes in a `gs-reason` span next to the control,
  not only in a tooltip. An empty reason enables the control.
- Classes: `gs-btn` (+ `--primary --ok --warn --danger --ghost --big`, `is-active`), `gs-banner`, `gs-error`, `gs-note`,
  `gs-section-title`, `gs-row`, `gs-label`, `gs-input`, `gs-metric(s)`, `gs-kbd`.

## Code rules
- Requests: `GSUI.fetchJson(url, {json, timeoutMs})` gives a timeout and the server's error text.
  Polls: `GSUI.poller(fn, {intervalMs})` keeps one request in flight and backs off from the 2nd failure in a row.
- One error path: `catch (e) { showError(GSUI.report('what', e)); }` logs, shows a toast and returns the in-panel text.
  No empty `catch {}`. `GSUI.store(key[, value])` wraps localStorage and reports a failure once.
- No inline colours where a token exists; no inline `onclick=`. Bind handlers after setting `innerHTML`.
- Lint: `npx eslint@9 -c docs/dashboard-platform/eslint.config.mjs docs/dashboard-platform/shell` (dev only).
  The same rules run without npm in `ground_station/service/tests/ui_components_harness.js` (L1-L3).

## Information hierarchy
1. Always visible: the flight strip (top), the agent mode pill + STOP (bottom centre), toasts (bottom right).
2. Sidebar (collapsible): recorder, gate bar, session, alarms.
3. Workspace tabs, one home tab per panel (`PANEL_META` in index.html). Nothing flight-critical lives only in 2 or 3.

## Safety UX
- Pause / Land / Abort sit on the strip and in the Campaign panel and send the same request (`GSUI.campaignCommand`).
  They are single click and never delayed or confirmed.
- They are disabled (reason shown) only when `/api/campaign/state` says no run is active (not running or waiting_for_go).
  When that state is unknown (poll failing) they stay enabled.
- Keyboard: **P = Pause** while a campaign runs (`GSUI.shortcut`). It is ignored while typing in an input, textarea,
  select or contenteditable, inside the terminal (`.xterm`) or `[data-gs-no-shortcuts]`, with Ctrl/Alt/Meta held,
  and on auto-repeat. With no run active, P only shows a warning toast. Path panel keys R N F T 1-4 stay as they are.
- Go and other risk-adding controls list what is missing (e.g. "tick 2 checklist items") instead of just going grey.

## Adding a panel
1. Create `shell/plugins/<name>.js` as an IIFE. In init, set `var UI = window.GSUI` and call
   `api.registerPanel('<Name>', render, {workspace: '<tab>'})`, then add the file to `PLUGIN_FILES` in index.html.
2. Build the markup with `gs-*` classes and `UI.pill` / `UI.table`. Update it in place on `api.subscribe(state)`.
   Show missing data as `stale` with its age, never as a zero.
3. Every command or fetch: `UI.fetchJson` or `api.gatedCommand`, plus an in-panel error line through `UI.report`.
4. Harness: load the kit with `require('./ui_kit_loader').preloadKit(ctx)` before the plugin (see campaign_panel_harness.js),
   add a pytest wrapper, and list the file in `ui_components_harness.js` L3 once it has no hex colours or inline handlers.
