# Plugin Developer Guide

This guide covers how to build dashboard plugins for the UAV Ground Station shell. Plugins are self-contained browser JavaScript modules that render custom panels, visualizations, and integrations.

## Table of contents

1. [Plugin format](#plugin-format)
2. [Plugin lifecycle](#plugin-lifecycle)
3. [Shell API reference](#shell-api-reference)
4. [Examples](#examples)
5. [State subscription patterns](#state-subscription-patterns)
6. [Canvas charting best practices](#canvas-charting-best-practices)
7. [Testing strategies](#testing-strategies)

---

## Plugin format

Plugins are plain JavaScript files placed in `docs/dashboard-platform/shell/plugins/`. The shell loads them at startup.

### Minimal plugin structure

```javascript
// plugins/my-plugin.js

export const name = "My Plugin";

export function init(shellApi) {
  // Register panels, subscribe to state, etc.
}

export function destroy() {
  // Optional cleanup
}
```

### Adding a new plugin

1. Create a new `.js` file in `docs/dashboard-platform/shell/plugins/`
2. Export `name`, `init(shellApi)`, and optionally `destroy()`
3. Restart the ground station service (shell restart is automatic with service restart)
4. The plugin appears in the dashboard without any core code changes

### Removing a plugin

Delete or rename the `.js` file and restart the service.

---

## Plugin lifecycle

### Initialization

```
Shell startup
    ↓
Load plugin JS files
    ↓
Call init(shellApi) for each plugin
    ↓
Plugin registers panels via registerPanel()
    ↓
Shell begins polling (500ms interval)
    ↓
Plugin callbacks receive state updates
```

### Destruction

Called when:
- Shell is closed/reloaded
- Service is stopped
- Plugin throws an uncaught error during `init()`

```javascript
export function destroy() {
  // Clear intervals
  clearInterval(myInterval);
  
  // Remove event listeners
  window.removeEventListener('resize', myHandler);
  
  // Cancel pending requests
  myAbortController.abort();
  
  // Null out DOM references
  myDOMElement = null;
}
```

### Error handling

```javascript
export function init(api) {
  try {
    api.registerPanel("My Panel", function(container, api) {
      // Safe rendering code
      api.subscribe(function(state) {
        try {
          updateDisplay(state);
        } catch (e) {
          console.error('Update failed:', e);
        }
      });
    });
  } catch (e) {
    console.error('Plugin init failed:', e);
  }
}
```

---

## Shell API reference

The shell passes a single `shellApi` object to every plugin's `init(shellApi)` function.

### `shellApi.getState()`

Returns the most recent state snapshot object, or `null` if no state has been received yet.

```javascript
var state = shellApi.getState();
// {
//   schema_id: "r1-s1-0x9F32E2EA",
//   session_id: "uuid-string",
//   connected: true,
//   samples: 1234,
//   last_update_ns: 1726567890123456789,
//   streams: {
//     "0": { tag: 0, sequence: 42, received: 100, dropped: 2, loss_pct: 1.96, values: {...} },
//     "1": { tag: 1, sequence: 88, received: 400, dropped: 0, loss_pct: 0, values: {...} }
//   }
// }
```

### `shellApi.subscribe(callback)`

Register a function called every time the shell receives a new state snapshot.

```javascript
shellApi.subscribe(function(state) {
  console.log("New state:", state.schema_id, "samples:", state.samples);
});
```

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `callback` | `function(ServiceState)` | Function called with the new state |

**Returns:** `undefined`

### `shellApi.submitCommand(cmdId, index, value)`

Submit a command to the ground station service. Returns a Promise.

```javascript
shellApi.submitCommand(0x01, 0, 1.5).then(function(result) {
  console.log("Transaction:", result.transaction_id);
}).catch(function(err) {
  console.error("Command failed:", err);
});
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `cmdId` | `number` | — | Numeric command identifier (see COMMAND_SPEC.md) |
| `index` | `number` | `0` | Command index (semantic varies by command) |
| `value` | `number` | `0.0` | Command value (unit varies by command) |

**Returns:** `Promise<{transaction_id: number}>`

### `shellApi.subscribeSlot(slot, divider, ranges)`

Subscribe the host to a telemetry stream on the drone. Proxies `POST /subscribe`
to the service, which builds the 0x21 envelope via `WifiBridge.subscribe_slot()`
and ships it over Wi-Fi.

```javascript
// Slot 0 — falls back to the canonical dashboard layout when ranges is empty.
shellApi.subscribeSlot(0, 4, []).then(function(res) {
  console.log("Subscribed slot", res.slot, "divider", res.divider);
}).catch(function(err) {
  console.error("Subscribe failed:", err.message);
});

// Slot 1 with explicit DWARF names.
shellApi.subscribeSlot(1, 4, ["mrac_state.pitch.e", "mrac_state.pitch.u_ad"]);

// Stop a slot.
shellApi.subscribeSlot(9, 0, []);
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `slot` | `number` | — | Slot id. `0..3` or `9..12`. Slot 0 falls back to the dashboard layout when `ranges` is empty. Other slots require explicit `ranges`. |
| `divider` | `number` | `1` | Send-task divider. `1..255` for active streams, `0` to stop. Effective rate is `Send_Task_Hz / divider`. |
| `ranges` | `string[]` | `[]` | DWARF names (resolved against the firmware ELF) or pre-built `StreamRange` tuples. Required for slots 1–3 and 9–12. |

**Returns:** `Promise<{slot, divider, ranges}>` resolving to the parsed JSON body on HTTP 202.

The slot starts populating `state.streams[slot]` once the drone acknowledges the
subscribe with a 0x08 schema reply and the first 0x09..0x0C data frame arrives.

### `shellApi.getPlugins()`

Returns an array of all loaded plugin names.

```javascript
var names = shellApi.getPlugins();
// ["Flight Status", "MRAC Controller", "EKF Estimator", ...]
```

### `shellApi.registerPanel(name, renderFn)`

Register a named panel that the shell renders in the main content area.

```javascript
shellApi.registerPanel("Altitude Chart", function(container, api) {
  container.innerHTML = '<canvas id="alt-chart"></canvas>';
  
  var ctx = document.getElementById('alt-chart').getContext('2d');
  
  api.subscribe(function(state) {
    var altitude = state.streams['0'] && state.streams['0'].values.ch10;
    updateChart(ctx, altitude);
  });
});
```

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `name` | `string` | Display name for the panel |
| `renderFn` | `function(containerElement, shellApi)` | Called when panel is mounted |

**Returns:** `undefined`

---

## Examples

### Example 1: Basic status display

```javascript
// plugins/example-basic.js

export const name = "Basic Status";

export function init(api) {
  api.registerPanel("Basic Status", function(container, api) {
    container.innerHTML = [
      '<div id="basic-arm">ARM: —</div>',
      '<div id="basic-vbat">Battery: —</div>'
    ].join('');
    
    api.subscribe(function(state) {
      var stream0 = state.streams && state.streams['0'];
      var values = stream0 && stream0.values || {};
      
      // ARM status (ch0 gyro magnitude proxy)
      var gyroMag = Math.sqrt(
        Math.pow(values.ch0 || 0, 2) + Math.pow(values.ch1 || 0, 2)
      );
      var armed = gyroMag > 0.1;
      document.getElementById('basic-arm').textContent = 
        'ARM: ' + (armed ? 'ARMED' : 'DISARMED');
      
      // Battery voltage (ch11)
      document.getElementById('basic-vbat').textContent = 
        'Battery: ' + (values.ch11 != null ? values.ch11.toFixed(2) + ' V' : '—');
    });
  });
}

export function destroy() {
  // Nothing to clean up
}
```

### Example 2: Parameter step buttons

```javascript
// plugins/example-step-buttons.js

export const name = "Step Buttons";

var PARAM_ID = 0x01;  // PID gain command
var STEP = 0.1;

export function init(api) {
  api.registerPanel("Step Buttons", function(container, api) {
    container.innerHTML = [
      '<div style="margin-bottom:8px">PID Kp (axis 0)</div>',
      '<div style="display:flex;gap:8px;align-items:center">',
        '<button id="step-dec">−</button>',
        '<span id="step-value">—</span>',
        '<button id="step-inc">+</button>',
      '</div>',
      '<div id="step-status" style="margin-top:8px;font-size:11px;color:var(--muted)">—</div>'
    ].join('');
    
    var currentValue = null;
    var pending = false;
    
    function updateDisplay() {
      document.getElementById('step-value').textContent = 
        currentValue != null ? currentValue.toFixed(3) : '—';
      document.getElementById('step-status').textContent = 
        pending ? 'Sending...' : (currentValue != null ? 'Ready' : 'No data');
    }
    
    function step(delta) {
      if (currentValue == null || pending) return;
      var newValue = Math.max(0, Math.min(200, currentValue + delta));
      pending = true;
      updateDisplay();
      
      api.submitCommand(PARAM_ID, 0, newValue).then(function(result) {
        currentValue = newValue;
        pending = false;
        updateDisplay();
      }).catch(function(err) {
        pending = false;
        updateDisplay();
        console.error('Command failed:', err);
      });
    }
    
    document.getElementById('step-dec').addEventListener('click', function() {
      step(-STEP);
    });
    
    document.getElementById('step-inc').addEventListener('click', function() {
      step(STEP);
    });
    
    api.subscribe(function(state) {
      var stream0 = state.streams && state.streams['0'];
      if (stream0 && stream0.values) {
        // Read current Kp from ch0 (proxy - actual implementation varies)
        if (currentValue == null) {
          currentValue = stream0.values.ch0 || 1.0;
          updateDisplay();
        }
      }
    });
  });
}

export function destroy() {
  // Remove event listeners in production
}
```

### Example 3: SVG line chart with history

```javascript
// plugins/example-chart.js

export const name = "Line Chart";

var MAX_POINTS = 100;
var CHANNEL = 'ch0';  // gyro_x

export function init(api) {
  api.registerPanel("Line Chart", function(container, api) {
    container.innerHTML = [
      '<svg id="line-chart" viewBox="0 0 300 100" style="width:100%;max-width:400px">',
        '<line x1="0" y1="50" x2="300" y2="50" stroke="rgba(255,255,255,0.1)" stroke-width="1"/>',
        '<polyline id="chart-line" fill="none" stroke="var(--green)" stroke-width="2"/>',
      '</svg>',
      '<div style="font-size:11px;color:var(--muted);margin-top:4px" id="chart-label">—</div>'
    ].join('');
    
    var history = [];
    var W = 300, H = 100, PAD = 2;
    
    function updateChart() {
      if (history.length < 2) return;
      
      var min = Math.min.apply(null, history);
      var max = Math.max.apply(null, history);
      var range = max - min || 0.01;
      
      var points = history.map(function(v, i) {
        var x = (i / (MAX_POINTS - 1)) * W;
        var y = H - PAD - ((v - min) / range) * (H - 2 * PAD);
        return x.toFixed(1) + ',' + y.toFixed(1);
      }).join(' ');
      
      document.getElementById('chart-line').setAttribute('points', points);
      document.getElementById('chart-label').textContent = 
        'min=' + min.toFixed(4) + ' max=' + max.toFixed(4) + ' range=' + range.toFixed(4);
    }
    
    api.subscribe(function(state) {
      var stream0 = state.streams && state.streams['0'];
      var value = stream0 && stream0.values && stream0.values[CHANNEL];
      
      if (value != null) {
        history.push(value);
        if (history.length > MAX_POINTS) history.shift();
        updateChart();
      }
    });
  });
}

export function destroy() {
  // Nothing to clean up
}
```

---

## State subscription patterns

### Debouncing expensive updates

For plugins with expensive rendering (charts, tables), debounce updates:

```javascript
var DEBOUNCE_MS = 100;
var pending = false;

function debouncedUpdate(state) {
  if (pending) return;
  pending = true;
  setTimeout(function() {
    doExpensiveRender(state);
    pending = false;
  }, DEBOUNCE_MS);
}

api.subscribe(debouncedUpdate);
```

### Filtering specific streams

```javascript
api.subscribe(function(state) {
  // Only process stream 1 (inner loops)
  var stream1 = state.streams && state.streams['1'];
  if (!stream1 || !stream1.values) return;
  
  // Process stream1.values
  var pitch = stream1.values.ch0;
  var roll = stream1.values.ch1;
});
```

### Loss detection

```javascript
api.subscribe(function(state) {
  Object.keys(state.streams || {}).forEach(function(slot) {
    var s = state.streams[slot];
    if (s.loss_pct > 5) {
      showCriticalLossAlert(slot, s.loss_pct);
    } else if (s.loss_pct > 1) {
      showWarningLossAlert(slot, s.loss_pct);
    }
  });
});
```

### Stale telemetry detection

```javascript
var STALE_THRESHOLD_NS = 3e9;  // 3 seconds

api.subscribe(function(state) {
  var age = Date.now() * 1e6 - (state.last_update_ns || 0);
  if (age > STALE_THRESHOLD_NS) {
    showStaleTelemetryWarning();
  }
});
```

---

## Canvas charting best practices

### Simple 2D canvas chart

```javascript
function renderCanvasChart(canvas, data, options) {
  options = options || {};
  var width = canvas.width;
  var height = canvas.height;
  var ctx = canvas.getContext('2d');
  
  // Clear
  ctx.clearRect(0, 0, width, height);
  
  // Draw grid
  ctx.strokeStyle = 'rgba(255,255,255,0.1)';
  ctx.lineWidth = 1;
  for (var y = 0; y < height; y += 20) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
  
  // Draw data
  if (data.length < 2) return;
  
  var min = Math.min.apply(null, data);
  var max = Math.max.apply(null, data);
  var range = max - min || 0.01;
  
  ctx.strokeStyle = options.color || 'var(--green)';
  ctx.lineWidth = options.lineWidth || 2;
  ctx.beginPath();
  
  data.forEach(function(v, i) {
    var x = (i / (data.length - 1)) * width;
    var y = height - ((v - min) / range) * (height - 10) - 5;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  
  ctx.stroke();
  
  // Draw labels
  ctx.fillStyle = 'rgba(255,255,255,0.5)';
  ctx.font = '10px Consolas, monospace';
  ctx.fillText('max: ' + max.toFixed(3), 5, 12);
  ctx.fillText('min: ' + min.toFixed(3), 5, 24);
}
```

### RequestAnimationFrame for smooth updates

```javascript
var RAF_ID = null;
var needsRedraw = false;

function startAnimation() {
  function frame() {
    if (needsRedraw) {
      renderChart();
      needsRedraw = false;
    }
    RAF_ID = requestAnimationFrame(frame);
  }
  RAF_ID = requestAnimationFrame(frame);
}

function stopAnimation() {
  if (RAF_ID !== null) {
    cancelAnimationFrame(RAF_ID);
    RAF_ID = null;
  }
}

api.subscribe(function(state) {
  updateData(state);
  needsRedraw = true;
});

startAnimation();

export function destroy() {
  stopAnimation();
}
```

---

## Testing strategies

### Manual testing

1. Start the ground station service
2. Open `http://localhost:8081` in browser
3. Verify plugin panel appears in the main content area
4. Check console for errors: `F12` → Console tab
5. Verify state updates: observe values changing when drone sends telemetry

### Plugin unit testing pattern

```javascript
// Test helper: mock shellApi
function createMockApi() {
  var state = null;
  var callbacks = [];
  return {
    getState: function() { return state; },
    subscribe: function(cb) { callbacks.push(cb); },
    _setState: function(s) {
      state = s;
      callbacks.forEach(function(cb) { cb(s); });
    },
    submitCommand: function() { return Promise.resolve({transaction_id: 1}); },
    subscribeSlot: function() { return Promise.resolve({slot: 0, divider: 1, ranges: []}); },
    getPlugins: function() { return []; },
    registerPanel: function() {}
  };
}

// Test: plugin reads ARM status
function test_arm_status() {
  var api = createMockApi();
  var panel = createPanel(api);  // your plugin's panel creation
  
  // Simulate disarmed state
  api._setState({
    streams: { '0': { values: { ch0: 0.01, ch1: 0.01 } } }
  });
  
  var armText = document.getElementById('arm-status').textContent;
  console.assert(armText.includes('DISARMED'), 'Should show DISARMED');
  
  // Simulate armed state
  api._setState({
    streams: { '0': { values: { ch0: 0.5, ch1: 0.3 } } }
  });
  
  armText = document.getElementById('arm-status').textContent;
  console.assert(armText.includes('ARMED'), 'Should show ARMED');
}
```

### Console debugging tips

```javascript
// Log all state updates
api.subscribe(function(state) {
  console.log('State update:', JSON.stringify(state, null, 2));
});

// Inspect stream values
api.subscribe(function(state) {
  Object.keys(state.streams || {}).forEach(function(slot) {
    var s = state.streams[slot];
    console.log('Slot', slot, 'loss:', s.loss_pct.toFixed(2) + '%', 
                'seq:', s.sequence, 'keys:', Object.keys(s.values || {}));
  });
});
```

---

## See also

- [ARCHITECTURE.md](ARCHITECTURE.md) — System overview and data flow
- [TELEMETRY_SPEC.md](TELEMETRY_SPEC.md) — Stream and channel mapping
- [COMMAND_SPEC.md](COMMAND_SPEC.md) — Command reference
- [shell/plugin-api.md](shell/plugin-api.md) — Official plugin API documentation
