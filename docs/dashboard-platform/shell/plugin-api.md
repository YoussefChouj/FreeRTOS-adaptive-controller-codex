# Shell API — Dashboard Plugin Reference (v2)

The Shell API is the contract between the browser dashboard shell (`shell/index.html`) and every plugin module. This document is the **canonical reference**. For higher-level guidance (examples, patterns, testing), see [PLUGIN_DEVELOPER_GUIDE.md](../PLUGIN_DEVELOPER_GUIDE.md).

- **API version:** `v2` (workspace switching + truthful state model + control gating)
- **Stable since:** S5 (workspace), S5 (gates), S5 (truthful state)
- **Previous:** `v1` — plugins using `__registerPlugin__(name, init, destroy)` still work (workspace defaults to `'all'`)
- **Plugins live in:** `shell/plugins/*.js`
- **Loaded by:** `shell/index.html` via the `PLUGIN_FILES` array

---

## Table of contents

1. [Plugin discovery and contract](#plugin-discovery-and-contract)
2. [`shellApi` object](#shellapi-object)
3. [Methods](#methods)
   - [`getState()`](#getstate)
   - [`subscribe(callback)`](#subscribecallback)
   - [`submitCommand(cmdId, index, value)`](#submitcommandcmdid-index-value)
   - [`subscribeSlot(slot, divider, ranges)`](#subscribesltslot-divider-ranges)
   - [`getPlugins()`](#getplugins)
   - [`registerPanel(name, renderFn)`](#registerpanelname-renderfn)
4. [ServiceState schema](#servicestate-schema)
5. [StreamState schema](#streamstate-schema)
6. [Error handling patterns](#error-handling-patterns)
7. [Versioning and stability](#versioning-and-stability)
8. [Constraints](#constraints)

---

## Plugin discovery and contract

### Discovery

The shell loads plugins from the `PLUGIN_FILES` array in `index.html`:

```javascript
const PLUGIN_FILES = [
  '/plugins/status-panel.js',
  '/plugins/command-panel.js',
  // ...
];
```

For each entry the shell performs:

1. `fetch(src)` — the file must be reachable on the same origin
2. `eval(text)` — the file is evaluated in the page context
3. The file is expected to call `window.__registerPlugin__(name, init, destroy, meta?)`

There is **no hot reload** and **no directory scan** — adding or removing a plugin requires editing `PLUGIN_FILES` and restarting the service.

### Module contract (v2 — workspace + gates)

Every plugin must export, via `window.__registerPlugin__`:

```javascript
window.__registerPlugin__(name, init, destroy, meta?);
```

| Export | Type | Required | Description |
|--------|------|----------|-------------|
| `name` | `string` | yes | Display name; appears in panel headers |
| `init` | `function(shellApi)` | yes | Called once at shell startup |
| `destroy` | `function()` | recommended | Called on shell unload (close, reload) or on `init` failure |
| `meta` | `object` | no | Workspace, gates, and description (v2). Defaults to `workspace='all'` and `gates=[]` |

The `meta` argument (v2):

```typescript
interface PluginMeta {
  workspace?: string;    // primary workspace: 'overview' | 'control' | 'estimator' | 'mrac' | 'telemetry' | 'experiments' | 'paths' | 'bench' | 'replay' | 'diagnostics' | 'all'
  gates?: string[];      // required safety gates before plugin may issue commands
                          // values: 'connected' | 'disarmed' | 'fresh' | 'schema' | 'command'
  description?: string;  // human-readable description shown in tooltip
}
```

**Example — register a control panel that requires disarmed:**

```javascript
window.__registerPlugin__(
  "MRAC Tuner",
  function (api) {
    api.registerPanel("MRAC Tuner", function (container, api) {
      container.innerHTML = '<button id="mrac-apply">Apply</button>';
      document.getElementById('mrac-apply').addEventListener('click', function () {
        api.gatedCommand(0x04, 0, 1.0, ['disarmed']).then(function (r) {
          console.log('Applied!', r);
        }).catch(function (err) {
          console.error('Blocked:', err.message);
        });
      });
    });
  },
  function () { /* cleanup */ },
  { workspace: 'mrac', gates: ['disarmed', 'connected'], description: 'MRAC adaptive controller tuner' }
);
```

**Example — register a read-only telemetry panel (no gates needed):**

```javascript
window.__registerPlugin__(
  "EKF States",
  function (api) {
    api.registerPanel("EKF States", function (container, api) {
      container.innerHTML = '<div id="ekf-roll">—</div>';
      api.subscribe(function (state) {
        var s = state && state.streams && state.streams['3'];
        var roll = s && s.values && s.values['ekf.roll'];
        document.getElementById('ekf-roll').textContent =
          roll != null ? roll.toFixed(3) + ' rad' : '—';
      });
    });
  },
  function () {},
  { workspace: 'estimator' }
);
```

---

## `shellApi` object

The shell passes a single `shellApi` object to every plugin's `init(shellApi)`. The object exposes these methods; the shape is:

```typescript
interface ShellApi {
  // Core state
  getState(): ServiceState | null;
  subscribe(callback: (state: ServiceState) => void): void;

  // Commands
  submitCommand(cmdId: number, index: number, value: number): Promise<{ transaction_id: number }>;
  subscribeSlot(slot: number, divider: number, ranges: string[]): Promise<{ slot: number, divider: number, ranges: string[] }>;

  // Panel registration (v2: supports workspace meta)
  getPlugins(): string[];
  registerPanel(name: string, renderFn: (container: HTMLElement, api: ShellApi) => void, meta?: PluginMeta): void;

  // S5: Truthful state model
  getSignalState(slotData: StreamState): 'live' | 'stale' | 'degraded' | 'unknown' | 'replay' | 'unverified';
  getGates(): { connected: boolean; disarmed: boolean; fresh: boolean; schema: boolean; command: boolean };
  getActiveWorkspace(): string;
  switchWorkspace(name: string): void;

  // S5: Control gating
  gatedCommand(cmdId: number, index: number, value: number, requiredGates?: string[]): Promise<...>;
  isGated(gateName: string): boolean;    // true if gate is blocked
  isDisarmed(): boolean;                 // shorthand for !isGated('disarmed')
  getArmState(): 'armed' | 'disarmed' | null;
}
```

> **v1 → v2 migration:** The 3-argument form of `registerPanel(name, renderFn)` still works.
> The new 4-argument form `registerPanel(name, renderFn, meta)` adds workspace visibility
> and gate enforcement. Existing plugins automatically get `workspace='all'` and `gates=[]`.

The reference passed to `renderFn` is the **same** `shellApi` instance — so a panel can subscribe, submit commands, or chain further calls without re-binding.

---

## Methods

### `getState()`

Return the most recent state snapshot, or `null` if no snapshot has been received yet.

**Returns:** `ServiceState | null`

**Example:**

```javascript
export function init(api) {
  api.registerPanel("Latest State", function (container, api) {
    container.innerHTML = '<pre id="latest">no data yet</pre>';

    api.subscribe(function (state) {
      document.getElementById('latest').textContent =
        JSON.stringify(state, null, 2);
    });
  });
}
```

Use this when you need to read state **once** (e.g., in a button click handler). For continuous rendering, prefer `subscribe`.

---

### `subscribe(callback)`

Register a function called on every poll cycle. The shell polls `/state` every 500 ms; the callback fires after each successful poll with the new snapshot.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `callback` | `(state: ServiceState) => void` | Called with the latest snapshot |

**Returns:** `undefined`

**Example:**

```javascript
var lastSeen = 0;

api.subscribe(function (state) {
  if (state.last_update_ns !== lastSeen) {
    lastSeen = state.last_update_ns;
    console.log('new update:', state.last_update_ns);
  }
});
```

**Notes:**

- The callback runs on every poll — not just when state changes. Compare fields (e.g. `state.last_update_ns`) if you only want to react to changes.
- Subscribe callbacks are wrapped in try/catch by the shell; an exception in one plugin does **not** stop other plugins from receiving the same snapshot.
- There is no unsubscribe — callbacks live until the page is unloaded or `destroy()` runs.

---

### `submitCommand(cmdId, index, value)`

Submit a command to the ground-station service. Returns a Promise that resolves with `{ transaction_id }`.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `cmdId` | `number` | — | Numeric command identifier (see [COMMAND_SPEC.md](../COMMAND_SPEC.md)) |
| `index` | `number` | `0` | Command index — semantic varies by command |
| `value` | `number` | `0.0` | Command value — unit varies by command |

**Returns:** `Promise<{ transaction_id: number }>`

**Example:**

```javascript
api.submitCommand(0x0E, 0, 0)   // release SDK authority
  .then(function (result) {
    console.log('Transaction:', result.transaction_id);
  })
  .catch(function (err) {
    console.error('Submit failed:', err);
  });
```

**Error handling notes:**

| Failure | Promise | What to do |
|---------|---------|-----------|
| Network error / service down | rejects with `Error` | Show network error, retry next tick |
| HTTP 4xx (bad command) | rejects with `Error` containing the response body | Show validation error |
| Command was REJECTED by drone | **resolves** with `{transaction_id}` | The Promise does **not** reject on rejection. The drone's `0x31 REJECTED` result frame is delivered separately through `/state`. To detect it, poll `/state` for `state.last_transaction_result` or `state.command_results[]` and look up by `transaction_id`. |

The submit call only confirms that the host *received* the command and *queued* it for the drone — not that the drone *applied* it. Most operational plugins additionally poll `/state` for up to a few seconds after submission to find the matching result frame. See [`command-panel.js`](../../shell/plugins/command-panel.js) for a working example.

---

### `subscribeSlot(slot, divider, ranges)`

Subscribe the host to a telemetry stream on the drone. The shell proxies this
to `POST /subscribe` on the service, which builds the 0x21 envelope via
`WifiBridge.subscribe_slot()` and ships it over Wi-Fi.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `slot` | `number` | — | Slot id. `0..3` for legacy / typed streams, `9..12` for the dashboard typed-stream equivalents. Slot 0 falls back to the canonical dashboard layout when `ranges` is empty. Other slots require explicit `ranges`. |
| `divider` | `number` | `1` | Send-task divider. `1..255` for active streams, `0` to stop. Effective rate is `Send_Task_Hz / divider`. |
| `ranges` | `string[]` | `[]` | DWARF names (resolved against the firmware ELF) or pre-built `StreamRange` tuples. Required for slots 1–3 and 9–12. |

**Returns:** `Promise<{ slot, divider, ranges }>` resolving to the parsed JSON body on HTTP 202.

**Example:**

```javascript
// Subscribe slot 9 at divider 2 (~40 Hz if Send_Task is ~80 Hz)
api.subscribeSlot(9, 2, [])
  .then(function (res) {
    console.log('Subscribed slot', res.slot, 'divider', res.divider);
  })
  .catch(function (err) {
    console.error('Subscribe failed:', err.message);
  });

// Subscribe slot 1 with explicit DWARF names
api.subscribeSlot(1, 4, ['mrac_state.pitch.e', 'mrac_state.pitch.u_ad'])
  .then(/* ... */);

// Stop slot 9
api.subscribeSlot(9, 0, [])
  .then(/* ... */);
```

**Error handling notes:**

| Failure | Promise | Notes |
|---------|---------|-------|
| Network error / service down | rejects with `Error` | Retry next tick |
| HTTP 4xx (unknown slot, invalid divider, missing ranges) | rejects with `Error` containing response body | Show validation error |
| Drone does not honour subscribe | resolves; no further notification | The drone replies asynchronously via the schema-reply (0x08) frame; check `state.streams[slot]` for incoming data |

The slot will start populating `state.streams[slot]` once the drone acknowledges
the subscribe with a 0x08 schema reply and the first 0x09..0x0C data frame
arrives. There is no separate "subscribe result" Promise.

---

### `getPlugins()`

Return the names of all loaded plugins.

**Returns:** `string[]`

**Example:**

```javascript
console.log(api.getPlugins());
// ["Flight Status", "MRAC Controller", "EKF Estimator", ...]
```

The list is the same as the `PLUGIN_FILES` array in `index.html` (one entry per file). It does not include internal state.

---

### `registerPanel(name, renderFn, meta?)` (v2)

Register a named panel that the shell renders into the main content area, with workspace and gate metadata.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `name` | `string` | — | Display name; rendered as the panel's `card-title` |
| `renderFn` | `(container, api) => void` | — | Called once when the panel is mounted |
| `meta` | `PluginMeta` | `{}` | Workspace visibility and safety gate requirements (v2) |

**`container` argument:** an empty `<div>` inside the panel card. The plugin is responsible for filling it with DOM.

**`api` argument:** the same `shellApi` instance, so the panel can `subscribe`, `submitCommand`, etc.

**`meta` argument (v2):**

```typescript
{
  workspace?: string;    // 'overview' | 'control' | 'estimator' | 'mrac' | 'telemetry' |
                        // 'experiments' | 'paths' | 'bench' | 'replay' | 'diagnostics' | 'all'
  gates?: string[];     // required safety gates e.g. ['disarmed', 'connected']
  description?: string; // tooltip text
}
```

**DOM structure produced by the shell:**

```html
<div class="card plugin-card" id="plugin-my-panel" data-workspaces="control">
  <div class="card-header">
    <span class="card-title">My Panel</span>
    <span class="gate-badge">disarmed+connected</span>  <!-- if gates.length > 0 -->
  </div>
  <div id="plugin-body-my-panel">
    <!-- renderFn(container, api) writes here -->
  </div>
</div>
```

---

### `getSignalState(slotData)` (v2)

Compute the truthful freshness state of a signal (stream or individual variable).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `slotData` | `StreamState` | One of `state.streams[slot]` |

**Returns:** One of:

| State | Meaning |
|-------|---------|
| `live` | Received within the freshness TTL; no loss |
| `stale` | Received but older than TTL; update overdue |
| `degraded` | Received but loss > 1% (warning) or > 5% (critical) |
| `unknown` | No data ever received on this slot |
| `replay` | Data sourced from session replay (not live) |
| `unverified` | Received but not confirmed against ground truth |

**Example:**

```javascript
api.subscribe(function (state) {
  var slot0 = state && state.streams && state.streams['0'];
  var sigState = api.getSignalState(slot0);
  var el = document.getElementById('euler-state');
  if (el) el.textContent = sigState;  // 'live', 'stale', etc.
  el.className = 'signal-' + sigState; // CSS color coding
});
```

---

### `getGates()` (v2)

Return the current safety gate status. Gates are evaluated on every poll cycle.

**Returns:**

```typescript
{
  connected: boolean;  // MicoAir WiFi is reachable
  disarmed: boolean;   // Drone ARM state = 0
  fresh: boolean;      // At least one slot received data within TTL
  schema: boolean;     // telemetry_schema_id is known (0x08 reply received)
  command: boolean;    // Command gateway is available
}
```

**Example:**

```javascript
var gates = api.getGates();
if (!gates.disarmed) {
  document.getElementById('param-input').disabled = true;
  document.getElementById('param-warning').textContent = 'ARM the drone first';
}
```

---

### `gatedCommand(cmdId, index, value, requiredGates?)` (v2)

Submit a command only after all required gates pass. Rejects with a descriptive error if any gate is blocked.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `cmdId` | `number` | — | Numeric command identifier |
| `index` | `number` | `0` | Command index |
| `value` | `number` | `0.0` | Command value |
| `requiredGates` | `string[]` | `[]` | Gates that must pass before the command fires |

**Returns:** `Promise<{ transaction_id: number }>` (resolved) or `Promise.reject(Error)` (blocked)

**Example:**

```javascript
// Send MRAC parameter command — only when disarmed AND connected
api.gatedCommand(0x04, 0, 1.0, ['disarmed', 'connected'])
  .then(function (result) { console.log('Applied!', result.transaction_id); })
  .catch(function (err) {
    // Blocked: "Gate(s) not satisfied: disarmed. ARM=armed"
    console.error('Command blocked:', err.message);
  });
```

---

### `getActiveWorkspace()` (v2)

Return the currently active workspace name.

**Returns:** `string` — one of `'overview'`, `'control'`, `'estimator'`, `'mrac'`, `'telemetry'`, `'experiments'`, `'paths'`, `'bench'`, `'replay'`, `'diagnostics'`.

**Example:**

```javascript
console.log('Current workspace:', api.getActiveWorkspace());
// 'overview'
```

---

### `switchWorkspace(name)` (v2)

Switch to a named workspace. Hides panels not in the target workspace; shows those that are.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `name` | `string` | Target workspace name |

**Example:**

```javascript
// Switch to the MRAC workspace programmatically
api.switchWorkspace('mrac');
```

---

### `isGated(gateName)` (v2)

Return whether a named gate is currently blocked (`true` = blocked, `false` = passing).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `gateName` | `string` | `'connected'` \| `'disarmed'` \| `'fresh'` \| `'schema'` \| `'command'` |

**Example:**

```javascript
if (api.isGated('disarmed')) {
  showWarning('Commands blocked — drone is armed');
}
```

---

### `isDisarmed()` (v2)

Convenience: shorthand for `!isGated('disarmed')`.

**Returns:** `boolean`

---

### `getArmState()` (v2)

Return the drone's ARM state derived from live telemetry (`status.arm`).

**Returns:** `'armed'` \| `'disarmed'` \| `null` (unknown — no telemetry yet)

Register a named panel that the shell renders into the main content area.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `name` | `string` | Display name; rendered as the panel's `card-title` |
| `renderFn` | `(container, api) => void` | Called once when the panel is mounted |

**`container` argument:** an empty `<div>` inside the panel card. The plugin is responsible for filling it with DOM.

**`api` argument:** the same `shellApi` instance, so the panel can `subscribe`, `submitCommand`, etc.

**Returns:** `undefined`

**DOM structure produced by the shell:**

```html
<div class="card" id="plugin-my-panel">
  <div class="card-header">
    <span class="card-title">My Panel</span>
  </div>
  <div id="plugin-body-my-panel">
    <!-- renderFn(container, api) writes here -->
  </div>
</div>
```

**Example:**

```javascript
export const name = "Voltage Watch";

export function init(api) {
  api.registerPanel("Voltage Watch", function (container, api) {
    container.innerHTML = '<div id="vbat">—</div>';

    api.subscribe(function (state) {
      var s = state.streams && state.streams['0'];
      var vbat = s && s.values && s.values.ch11;
      document.getElementById('vbat').textContent =
        vbat != null ? vbat.toFixed(2) + ' V' : '—';
    });
  });
}

export function destroy() {
  // The shell removes the .card automatically; clear any timers/listeners here.
}
```

**Idempotency:** Each plugin should call `registerPanel` exactly once. Calling it twice from the same `init` will produce two cards.

---

## ServiceState schema (v2)

The full state object published on every poll. Top-level fields:

| Field | Type | Description |
|-------|------|-------------|
| `schema_id` | `string` | Frozen schema ID (e.g. `"r1-s1-0x9F32E2EA"`) |
| `telemetry_schema_id` | `string` | Telemetry-specific schema ID (alias of schema_id, v2) |
| `adapter_version` | `string` | Host adapter protocol version (`"v2"`) |
| `session_id` | `string` | UUID of the current ground-station session |
| `connected` | `boolean` | True if the bridge has reported health recently |
| `samples` | `integer` | Total telemetry samples ingested this session |
| `last_update_ns` | `integer` | Unix timestamp (ns) of the most recent telemetry ingest |
| `slot_freshness_ttl_ns` | `integer` | Current slot TTL in nanoseconds (default 30 s = 30e9) |
| `streams` | `object<slot, StreamState>` | Per-slot telemetry (see below) |
| `last_transaction_result` | `object?` | Most recent command result, if any |
| `command_results` | `object[]?` | Array of recent command results (drained by plugins) |

**Full example:**

```json
{
  "schema_id": "r1-s1-0x9F32E2EA",
  "telemetry_schema_id": "r1-s1-0x9F32E2EA",
  "adapter_version": "v2",
  "slot_freshness_ttl_ns": 30000000000,
  "session_id": "1f2a3b4c-5678-90ab-cdef-1234567890ab",
  "connected": true,
  "samples": 12345,
  "last_update_ns": 1726567890123456789,
  "streams": {
    "0": {
      "tag": 0, "sequence": 42, "received": 600, "dropped": 3,
      "loss_pct": 0.50,
      "values": { "ch0": 0.0123, "ch1": -0.0045, "ch11": 11.85 }
    }
  },
  "last_transaction_result": {
    "transaction_id": 2049,
    "status": "applied",
    "command_id": 14,
    "index": 0,
    "detail": "applied"
  }
}
```

---

## StreamState schema (v2)

`state.streams[slot]` for each active slot. Slot is the key (`"0"`, `"1"`, `"9"`, …).

| Field | Type | Description |
|-------|------|-------------|
| `tag` | `integer` | Slot number — `0`–`3` for legacy, `9`–`12` for typed streams |
| `sequence` | `integer` | Frame sequence number (mod 256 for legacy, larger for typed) |
| `received` | `integer` | Frames received since subscription |
| `dropped` | `integer` | Frames detected as missing (sequence gaps) |
| `loss_pct` | `number` | `dropped / (received + dropped) * 100` |
| `values` | `object<string, number>` | Key-value payload; key names per [TELEMETRY_SPEC.md](../TELEMETRY_SPEC.md) |
| `_key_ts` | `object<string, integer>` | (v2) Per-key ingest timestamp in ns — used for truthful freshness |
| `source_time_ms` | `integer` | (v2) Firmware Send_Task cycle timestamp from wire 0x09 frame |
| `crc_errors` | `integer` | (v2) CRC-16 validation errors on this slot |

**Truthful signal states (v2 — use `api.getSignalState(slotData)`):**

| State | Condition |
|-------|-----------|
| `live` | Received within TTL; no significant loss |
| `stale` | Received but older than TTL |
| `degraded` | Received with loss > 1% |
| `unknown` | No data ever received |
| `replay` | Sourced from session replay |
| `unverified` | Received but unconfirmed |

**Loss alarm thresholds (built into the shell):**

| Range | Color | Meaning |
|-------|-------|---------|
| `< 1%` | default | Normal |
| `1–5%` | amber | Degraded — investigate Wi-Fi |
| `> 5%` | red | Critical — check antenna / range |

**Stale telemetry:** if `now - state.last_update_ns > 3 × 10⁹` ns (3 s), the shell raises a red alarm in the sidebar.

---

## Error handling patterns

### Wrap callbacks in try/catch

The shell already wraps callbacks, but explicit try/catch gives better diagnostics:

```javascript
api.subscribe(function (state) {
  try {
    render(state);
  } catch (e) {
    console.error('render failed:', e);
  }
});
```

### Guard against missing state

The first snapshot may be partial, and individual streams may be absent:

```javascript
api.subscribe(function (state) {
  var s0 = state.streams && state.streams['0'];
  if (!s0 || !s0.values) return;        // graceful early-return
  var vbat = s0.values.ch11;
  if (vbat == null) return;             // key not yet published
  // ... use vbat
});
```

### Treat command rejection as data, not exception

The submit Promise resolves on `ACK` (queued). The drone-applied result is a separate event:

```javascript
function submitAndWatch(cmdId, idx, val) {
  api.submitCommand(cmdId, idx, val).then(function (tx) {
    var startNs = Date.now() * 1e6;
    var poll = setInterval(function () {
      var state = api.getState();
      var last = state && state.last_transaction_result;
      if (last && last.transaction_id === tx.transaction_id) {
        clearInterval(poll);
        if (last.frame_type === 'APPLIED') {
          console.log('applied:', last.detail);
        } else {
          console.warn('rejected:', last.reason, last.detail);
        }
      } else if ((Date.now() * 1e6 - startNs) > 5e9) {
        clearInterval(poll);
        console.log('timeout — no result feedback');
      }
    }, 250);
  }).catch(function (err) {
    console.error('network failure:', err);
  });
}
```

The above pattern (with debounce, grouping, history) is implemented in [`command-panel.js`](../../shell/plugins/command-panel.js).

### Cleanup on destroy

Always release timers, listeners, and DOM references:

```javascript
var _timer = null;

export function init(api) {
  api.registerPanel("Ticker", function (container) {
    container.innerHTML = '<div id="t">0</div>';
    _timer = setInterval(function () {
      var n = parseInt(document.getElementById('t').textContent, 10) + 1;
      document.getElementById('t').textContent = n;
    }, 1000);
  });
}

export function destroy() {
  if (_timer !== null) clearInterval(_timer);
  _timer = null;
}
```

---

## Versioning and stability

The current API is `v2`. Within v2:

- Existing method signatures from `v1` will not change.
- New optional fields may be added to `ServiceState` and `StreamState` (plugins should ignore unknown fields).
- New methods may be added to `shellApi` (existing plugins keep working).
- The `meta` argument to `registerPanel` is optional — plugins using the 3-argument form default to `workspace='all'` with no gates.

A backwards-incompatible change will bump the version. The shell will emit a `window.__SHELL_API_VERSION__` constant so plugins can detect:

```javascript
if (window.__SHELL_API_VERSION__ !== 'v2') {
  console.warn('Plugin expects v2, shell is', window.__SHELL_API_VERSION__);
}
```

For now, plugins that follow this spec will work with any v2 shell.

> **v1 → v2 migration path:** Plugins using `window.__registerPlugin__(name, init, destroy)` (3 args)
> continue to work unchanged. They get `workspace='all'` and `gates=[]` automatically.
> Plugins that need workspace filtering or gate enforcement should migrate to the 4-argument
> form: `window.__registerPlugin__(name, init, destroy, { workspace, gates })`.

---

## Constraints

- Plugins are **read-only consumers** of state. They cannot modify service state.
- Plugins **cannot be hot-reloaded**. Adding/removing/editing requires a service restart.
- Plugins must not block the main thread; keep callbacks synchronous or yield with `requestAnimationFrame`/`setTimeout`.
- All plugin code runs in the browser sandbox; no Node.js, no filesystem access.
- Plugin files must fit in a single `.js` module — no `import` from external URLs (CSP-safe).
- The shell does not enforce a panel size; design responsively using CSS variables (`--bg`, `--card`, `--green`, etc.).

---

## See also

- [PLUGIN_DEVELOPER_GUIDE.md](../PLUGIN_DEVELOPER_GUIDE.md) — plugin authoring with examples
- [ARCHITECTURE.md](../ARCHITECTURE.md) — system architecture and Agent API
- [TELEMETRY_SPEC.md](../TELEMETRY_SPEC.md) — telemetry keys and stream layout
- [COMMAND_SPEC.md](../COMMAND_SPEC.md) — command reference
- [STATE.md](../STATE.md) — project state and known gaps
