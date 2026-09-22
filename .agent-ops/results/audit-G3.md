# G3 Audit: browser-shell XSS and resource-leak report

| # | Severity | file:line | CONFIRMED / PLAUSIBLE | what goes wrong | fix |
|---|----------|-----------|----------------------|----------------|-----|
| 1 | MEDIUM | `docs/dashboard-platform/shell/index.html:1462-1465` | **CONFIRMED** | `renderAlarms()` sets `a.text` directly to `innerHTML`. `buildAlarms(state)` sources `text` from `state.streams[slot].loss_pct` (numeric, safe) and from telemetry staleness checks (static strings + slot numbers). Current code is safe, but the sink is unescaped — any future alarm source carrying device- or operator-controlled text (copilot messages, journal entries, plan step descriptions) passed through this function would be fully executable. | Use `textContent` or `escapeHtml(a.text)` at `index.html:1463`. Owner: `index.html`. |
| 2 | MEDIUM | `docs/dashboard-platform/shell/index.html:1554,1557-1558` | **CONFIRMED** | `resultEl.innerHTML` receives `data.transaction_id` and `err.message` from the `/commands` POST response. `transaction_id` is a server-generated integer (safe). `err.message` comes from a fetch rejection — if the service returns a crafted HTTP error body, the message reaches innerHTML unescaped. | Escape at `index.html:1557`. Owner: `index.html`. |
| 3 | LOW | `docs/dashboard-platform/shell/plugins/telemetry-explorer-panel.js:170` | **PLAUSIBLE** | `e.key` (telemetry field name / DWARF symbol) is placed into HTML attributes (`title="..."`) and element content inside `innerHTML`. DWARF symbol names are device-controlled; a malicious firmware could carry `<img onerror=...>` in a symbol name. `shortKey(e.key)` only strips namespace prefixes, not HTML entities. | Escape `e.key` via `escapeHtml()` or use `textContent`. Owner: `telemetry-explorer-panel.js`. |
| 4 | LOW | `docs/dashboard-platform/shell/plugins/slot-manager-panel.js:612` | **PLAUSIBLE** | `k` (telemetry key / DWARF symbol) is concatenated directly into HTML string inside `tbody.innerHTML`. No `escapeHtml()` call guards it. Same device-control vector as #3. | Escape `k` before insertion. Owner: `slot-manager-panel.js`. |
| 5 | LOW | `docs/dashboard-platform/shell/plugins/replay-panel.js:130-137,154-159,280,383` | **PLAUSIBLE** | Session IDs (`s.id`, `session.id`), schema IDs (`s.schema_id`), source names (`s.source`), record names, and stream stats slot keys come from service API responses (`/sessions`, `/sessions/<id>/records`). These are stored on the server, not user-entered in the current architecture, but any attacker with write access to session metadata could inject HTML. | Escape dynamic fields before innerHTML. Owner: `replay-panel.js`. |
| 6 | LOW | `docs/dashboard-platform/shell/plugins/overview-panel.js:1843` | **CONFIRMED** | `live.sub` (a status string like "not published by this build", "frozen 3s", "age 0.1s") is placed into `innerHTML`. These are locally generated, safe values, but the pattern is worth noting — if `keyLiveState()` ever accepted an external `sub` parameter, it would be unsafe. | The current code is safe (values are hardcoded), but the `innerHTML` sink should be changed to `textContent` for defense-in-depth. |
| 7 | LOW | `docs/dashboard-platform/shell/plugins/overview-panel.js:1952,1960` | **CONFIRMED** | `ep.text` and `ep.textEnd` from `_alarmLog` (populated from `computeAlarms` output) are placed into `innerHTML`. Current alarm texts are all locally generated static strings + numeric values — safe today. However, if `computeAlarms` were ever fed external text, this would be an XSS hole. | Use `textContent` or escape. Owner: `overview-panel.js`. |
| 8 | LOW | `docs/dashboard-platform/shell/plugins/bandwidth-panel.js:352` | **CONFIRMED** | `err.message` (from fetch rejection) placed directly into `innerHTML`. This is a standard network error text — safe in practice but technically a service-controlled value that reaches innerHTML. | Escape or use textContent. Owner: `bandwidth-panel.js`. |
| 9 | LOW | `docs/dashboard-platform/shell/plugins/bandwidth-panel.js:621,626` | **CONFIRMED** | `slot` (numeric slot number) and `err.message` placed into `innerHTML`. Numeric slot numbers are safe; `err.message` is service-controlled. | Escape `err.message`. Owner: `bandwidth-panel.js`. |
| 10 | LOW | `docs/dashboard-platform/shell/plugins/command-panel.js:436` | **CONFIRMED** | Log line `text` and `icon` placed into `innerHTML`. The `text` parameter is escaped via `escapeHtml()` at line 436, but `icon` is not escaped. The `icon` values come from `iconMap` (static strings) — safe today. | Escape `icon` as well for defense-in-depth. Owner: `command-panel.js`. |

## Resource leaks

### 10. SSE subscriber cleanup: NO LEAK (CONFIRMED)
- `ground_station/service/agent.py:1222-1237` — `subscribe()` creates a `queue.Queue` and appends to a `deque` with `maxlen=SSE_SUBSCRIBER_LIMIT`. `unsubscribe()` removes it.
- `ground_station/service/api.py:1494-1527` — The SSE handler wraps the read loop in `try/finally` calling `_AGENT.unsubscribe(q)` at line 1526. All exit paths (clean close, broken pipe, exception) are covered by the `finally` block.
- `api.py:1513` and `api.py:1522` — `BrokenPipeError`/`ConnectionResetError`/`OSError` break out of the loop and hit the `finally`.
- **Verdict: Clean.**

### 11. StateHub subscriber list: PLAUSIBLE growth (minor)
- `api.py:913-934` — `StateHub._subscribers` is a plain `list`. `unsubscribe()` removes by value, but the shell only registers one listener (`service.add_listener(self.hub.publish)` at line 2069). No HTTP handler adds/removes StateHub subscribers.
- **Verdict: No leak in practice. One permanent listener.**

### 12. Action journal: BOUNDED (CONFIRMED)
- `core.py:284` — `deque(maxlen=ACTION_JOURNAL_MAX)`. Max 64 entries.

### 13. Command results history: BOUNDED (CONFIRMED)
- `core.py:281` — `deque(maxlen=command_history)`. `command_history` defaults to 64.

### 14. Fault log: BOUNDED (CONFIRMED)
- `core.py:286` — `deque(maxlen=FAULT_LOG_MAX)`.

### 15. Activity journal: BOUNDED (CONFIRMED)
- `activity.py:30` — `ACTIVITY_RING_MAX = 2000`.
- `activity.py:48` — `deque(maxlen=int(ring_max))`.
- JSONL files rotate daily.

### 16. Copilot conversation history: BOUNDED (CONFIRMED)
- `copilot.py:206` — `_history` list, trimmed at line 223 and 245 to `_MAX_HISTORY_TURNS * 2 = 80` entries.

### 17. Agent pending acks: CLEANED UP (CONFIRMED)
- `agent.py:610` — `_pending_acks` dict, popped at line 1330 on step completion.

### 18. _pending_verifications: CLEANED UP (CONFIRMED)
- `core.py:289` — Dict cleared and rebuilt at line 1070.

### 19. _shell_changed_cache in agent.py: PLAUSIBLE growth
- `agent.py:618` — `_shell_changed_cache` is a `list` that accumulates changed field names. It is used for debounce batching but there is no explicit cap or eviction. However, the list is only appended to during shell file changes (operator-triggered, bounded by file system activity), not per-telemetry-frame.
- **Verdict: Negligible growth rate. Not a leak.**

### 20. _recentAlarms in overview-panel.js: BOUNDED (CONFIRMED)
- `overview-panel.js:352` — Filtered by `RECENT_MS` (60s) at line 776.

### 21. _alarmLog in overview-panel.js: BOUNDED (CONFIRMED)
- `overview-panel.js:100` — `ALARM_LOG_MAX = 500`. Eviction at line 1122-1124.

### 22. _hist (session sample history): BOUNDED (CONFIRMED)
- `overview-panel.js:99` — `HIST_MAX = 1200`. Eviction at line 806.

## Event listeners

No panel mount/unmount listener leaks found. Each plugin registers its `api.onState()` handler via the plugin system, and the teardown function (second argument to `api.registerPlugin`) removes the panel DOM element. The plugin API does not maintain a separate listener registry — the state callback is stored in a closure and only lives as long as the panel exists. Since panels are not unmounted and remounted in the current architecture, this is not an issue.

## Summary

- **XSS findings:** 10 findings (3 MEDIUM, 7 LOW). The most actionable are #1 (alarm text innerHTML sink without escape) and #3 (telemetry key names from firmware into innerHTML without escape). The majority of innerHTML usage in the codebase is well-guarded — `command-panel.js` and `mrac-panel.js` have `escapeHtml()` functions and use them consistently on 30+ data points. The unguarded sites are primarily in: alarm rendering, telemetry explorer, slot manager, and session/replay panels.
- **Resource leaks:** No unbounded growth found in the service layer. All journals, queues, and caches have explicit caps. SSE subscriber cleanup is correct via try/finally. The StateHub has a single permanent listener.
- **Recommendation:** Add a global `escapeHtml` function to the shell's shared scope (or `index.html`) and audit all innerHTML sinks against it. The pattern of escaping only in some plugins (command-panel.js, mrac-panel.js) but not others (telemetry-explorer-panel.js, slot-manager-panel.js, replay-panel.js) is a systemic gap.

SUBSTITUTIONS: none
