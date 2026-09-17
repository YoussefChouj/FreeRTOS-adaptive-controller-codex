# Plugin API — Dashboard Shell

Plugins extend the browser shell with custom panels, visualizations, and integrations. They are loaded at shell startup from the `plugins/` subdirectory of the shell directory.

## Plugin discovery

The shell scans the `plugins/` directory at startup and loads every `.js` file it finds. Each file must export a module with the following shape:

```js
// plugins/my-plugin.js
export const name = "My Plugin";

export function init(shellApi) {
  // Register a panel, subscribe to state, etc.
}

export function destroy() {
  // Optional cleanup when the shell is closed or reloading.
}
```

Plugins are **static** — they are discovered and loaded once at shell startup. Adding, removing, or modifying a plugin requires a restart of the core service to take effect.

## `shellApi` reference

The shell passes a single `shellApi` object to every plugin's `init(shellApi)` function.

### `shellApi.getState()`

Returns the most recent state snapshot object, or `null` if no state has been received yet.

```js
var state = shellApi.getState();
// { schema_id, session_id, connected, samples, last_update_ns, streams }
```

### `shellApi.subscribe(callback)`

Register a function that is called every time the shell receives a new state snapshot. The callback receives the state object.

```js
shellApi.subscribe(function(state) {
  console.log("New state:", state);
});
```

### `shellApi.submitCommand(cmdId, index, value)`

Submit a command to the ground-station service. Returns a Promise that resolves with the response.

```js
shellApi.submitCommand(0x1234, 0, 1.0).then(function(result) {
  console.log("Transaction:", result.transaction_id);
}).catch(function(err) {
  console.error("Command failed:", err);
});
```

**Parameters**

| Name | Type | Description |
|------|------|-------------|
| `cmdId` | `number` | Numeric command identifier |
| `index` | `number` | Command index (default `0`) |
| `value` | `number` | Command value (default `0.0`) |

### `shellApi.getPlugins()`

Returns an array of all loaded plugin descriptors. Each entry has `{ name, renderFn }` after the plugin calls `registerPanel`.

### `shellApi.registerPanel(name, renderFn)`

Register a named panel that the shell renders in the main content area.

```js
shellApi.registerPanel("Altitude Chart", function(container, api) {
  container.innerHTML = '<canvas id="alt-chart"></canvas>';
  api.subscribe(function(state) {
    // Update the chart
  });
});
```

The `renderFn` is called with `(containerElement, shellApi)` when the panel is first mounted.

## Example plugin

```js
// plugins/packet-monitor.js
export const name = "Packet Monitor";

export function init(api) {
  var count = 0;
  api.registerPanel("Packet Monitor", function(el) {
    el.innerHTML = '<div id="pkt-count">0 packets</div>';
    api.subscribe(function(state) {
      count++;
      document.getElementById("pkt-count").textContent = count + " packets";
    });
  });
}

export function destroy() {
  // Nothing to clean up.
}
```

## Constraints

- Plugins are **read-only consumers** of state. They cannot modify service state.
- Plugins **cannot** be hot-reloaded. Changes require a service restart.
- Plugins must not block the main thread; keep callbacks synchronous or yield with `requestAnimationFrame`/`setTimeout` where needed.
- All plugin code runs in the browser sandbox; it has no access to Node.js or the filesystem.
