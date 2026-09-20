# S15 Security Review

Reviewer: security review agent (subagent of jiang's S15 wave).
Scope: changes shipped by Agent A (Python service layer), Agent B (browser
plugin code), and Agent C (firmware `rtos_observability.{c,h}`). Only files
named in the S15 task brief are reviewed. Tests, docs, and build artifacts are
out of scope.

## Verdict

**WARN** ！ no newly-introduced authentication bypass, RCE, or buffer
overflow. Three findings deserve attention before merge:

1. The new `shellApi.subscribeSlot` (index.html L327-L342) and
   `slot-manager-panel.js:submitSubscribeFallback` (L72-L92) claim the
   Python service treats `command_id >= 0x21` as a "subscribe envelope".
   It does not ！ `api.py:do_POST /commands` (L218-L227) forwards any
   `command_id` verbatim through `service.submit_command` ★
   `gateway.submit` ★ `bridge.send_transaction`, which builds a `0xCC 0xDD`
   transaction frame and ships it to the FC. Misleading documentation;
   no actual whitelist or `0x21`-aware dispatch exists. (W1)
2. `_request_slot0_schema` runs unconditionally on `bridge.start(...)` with
   no retry / exponential backoff. If the FC never returns a `0x08`
   schema (the documented "subscribe-only" caveat applies), every retry
   path or restart will keep firing `0x21` requests. Currently the bridge
   only calls it once per start, but combined with the launcher auto-restart
   in `service/__main__.py` it becomes a quiet DoS vector against the
   air-side link. (W2)
3. `inject_external_stream` is documented as a "public hook for non-WiFi
   sources" with no size, type, or key bound on `values`. A misbehaving
   caller can grow `streams[slot]['values']` without limit and force
   `/state` payloads to balloon. (W3)

No BLOCK-level issues ！ the platform still trusts the localhost browser
for command authority, which is a pre-existing design decision. No new
auth bypass, no `eval` of attacker input (the `eval(text)` at
`index.html:L262` is pre-existing and unchanged in scope).

## Critical findings (must fix before merge)

None.

## Warnings (should fix)

### W1 ！ `command_id >= 0x21` "subscribe envelope" claim is false
**Files**: `docs/dashboard-platform/shell/index.html` L322-L342
(`shellApi.subscribeSlot`); `docs/dashboard-platform/shell/plugins/slot-manager-panel.js`
L72-L92 (`submitSubscribeFallback`); `ground_station/service/api.py` L218-L227.

The shell sends:
```js
{ command_id: 33, index: slot, value: divider, args: { slot, divider, ranges } }
```
with the comment *"The Python service accepts command_id >= 0x21 as the
subscribe envelope."* The service does no such thing ！ it just forwards
it to `bridge.send_transaction`, which builds a standard
`0xCC 0xDD <cmd_id> <idx> <value_f32>` frame. On the FC, the
command handler (`Process_GroundStation_Command`) will see `cmd_id == 33`
and either ignore it (no match in the switch) or, worse, treat it as a
write to feature flag index `slot`, which is not what the dashboard
intended.

The `args` payload (including the full `ranges` array) is silently
dropped at `submit_command` (L304-L307 of `core.py` only reads
`command_id`, `index`, `value`, `flags`).

Minimum fix: either (a) wire a real `0x21` subscribe path in
`core.py` that recognises `command_id == 33`, or (b) change the comment
and the panel to say the dashboard's subscribe is currently a no-op
and require the operator to use `bridge.subscribe_preset()` directly
until the path lands.

### W2 ！ Auto-subscribe has no rate limit / backoff
**File**: `ground_station/comm/wifi_bridge.py` L407-L520
(`_request_slot0_schema`); invoked from `start()` at L355-L357, which
is called from `ground_station/service/__main__.py` L97 (`service.start(...)`).

`_request_slot0_schema` is called once on every bridge start. It
sends a `0x21` request, prints diagnostics, and returns. There is no
retry, but also no backoff if the operator restarts the service in a
loop. The MicoAir link is a half-duplex radio ！ repeated restart +
auto-subscribe storms it. The `--no-auto-subscribe` flag exists, but
the default path will fire `0x21` on every start.

Minimum fix: rate-limit `_request_slot0_schema` to once per
process-lifetime (e.g. with a `self._auto_subscribed` flag set on
first attempt), so a restart loop doesn't spam the FC.

### W3 ！ `inject_external_stream` has no bounds on `values`
**File**: `ground_station/service/core.py` L246-L286.

```python
def inject_external_stream(self, slot: int, values: dict[str, Any],
                           *, sequence: int | None = None) -> None:
    ...
    existing["values"].update(values)
```
The docstring calls this a "public hook for non-WiFi sources". The
function accepts an arbitrary dict and merges it into the live snapshot.
A buggy caller (or a future adversarial one, e.g. an HTTP endpoint
that ends up wrapping it) can grow `streams[slot]['values']` to
megabytes. `/state` snapshots the full dict and the shell polls every
500 ms (`index.html:L475`), so a single bad sample will dominate
JSON serialisation cost.

Minimum fix: enforce a cap (e.g. max 256 keys per slot, max 8 KB per
value) and log+drop on violation.

### W4 ！ `/commands` POST accepts arbitrary `command_id`, `index`, `value`, `flags`
**File**: `ground_station/service/api.py` L218-L227.

```python
txid = service.submit_command(int(body["command_id"]),
                              int(body.get("index", 0)),
                              float(body.get("value", 0.0)),
                              int(body.get("flags", 0)))
```
No range check on `command_id` (FC supports 0x01..0x18), no range check
on `index` (uint8), no NaN/Inf guard on `value`, no whitelist of which
commands a localhost browser is allowed to fire. Pre-existing design ！
the service trusts localhost. S15 widens the surface by adding
`shellApi.subscribeSlot`, so it is worth re-stating the assumption.

Minimum fix: add a per-command whitelist + index/value range check at
the API boundary. At minimum, drop a `LOG.warning` for any `command_id
>= 0x20` (which the FC's `0xCC 0xDD` parser should never see ！ only
the `0xCC 0xDE` 0x21 path uses those).

### W5 ！ `_pending_schema_ranges` keyed by slot is never cleaned up on subscribe failure
**File**: `ground_station/comm/wifi_bridge.py` L241-L244 (allocation),
L1687-L1700 (read), `_request_slot0_schema` (write).

`self._pending_schema_ranges[slot]` is written under `self._stream_lock`
on every `0x21` request and read under the same lock when the `0x08`
reply arrives. If the `0x08` never comes (e.g. firmware in
SUBSCRIBE_ONLY mode, documented caveat in `wifi_bridge.py:L1938`), the
entry is retained until the next subscribe for that slot overwrites
it. The dict is bounded by `MAX_STREAM_RANGES * len(slots)` so memory
is fine, but it is a state-leak of stale DWARF addresses from
previous attempts. Low risk, no fix needed unless an attacker can
trigger subscribe churn.

## Observations (informational)

### O1 ！ Agent C's note that `PlatformObservability_Tick` is "never called" is stale
**File**: `firmware/rtos_observability.c` L28 (definition);
`TASK/send_data.c` L637 (caller).

`grep -n PlatformObservability_Tick` shows the function is now wired:
```
TASK/send_data.c:637:    PlatformObservability_Tick((uint16_t)((gs_cmd_head + 16U - gs_cmd_tail) % 16U),
firmware/rtos_observability.c:28:void PlatformObservability_Tick(uint16_t queue_depth, uint16_t dma_busy)
firmware/rtos_observability.h:26:void PlatformObservability_Tick(uint16_t queue_depth, uint16_t dma_busy);
```
The STATE.md "Known gaps" entry #6 (`docs/dashboard-platform/STATE.md:L181`)
should be removed in the same S15 merge ！ the wire-up is done.

### O2 ！ `volatile` on the new globals is correct
`firmware/rtos_observability.c` L21-L27 declares all seven globals
`volatile`. Counters, DMA flags, and ring head/tail are read from a
different context (the SWD bridge, or the ISR) so volatile is
required. `xPortGetFreeHeapSize()` is itself a volatile-friendly read
of `xStart.pxEnd`. No issue.

### O3 ！ `uint16` ring arithmetic is safe
`firmware/rtos_observability.c` L41-L47:
```c
uint16_t head16 = (uint16_t)gs_cmd_head;
uint16_t tail16 = (uint16_t)gs_cmd_tail;
platform_obs_cmd_queue_depth = (head16 >= tail16)
                               ? (uint16_t)(head16 - tail16)
                               : (uint16_t)(GS_CMD_QUEUE_LEN - tail16 + head16);
```
The ternary guards against subtraction underflow. Both operands are
promoted to uint16, the constant `GS_CMD_QUEUE_LEN = 16` is `int`, and
the result is masked back to uint16. No wrap risk.

### O4 ！ `UA3TxDrops` / `gs_cmd_head` / `gs_cmd_tail` extern linkage is OK
`UA3TxDrops` is defined at `BSP/usart3.c:209`. `gs_cmd_head/tail` are
defined at `BSP/usart4.c:97`. Both are `extern volatile` in
`firmware/gs_command.h` (L19-L20). Link will resolve.

### O5 ！ `inject_external_stream` slot key is a string (`"rtos"`) by design
`ground_station/platform/rtos_bridge.py:L42` defines `RTOS_SLOT_KEY = "rtos"`.
`snapshot()` in `core.py:L344` does `{str(slot): ...}` so an int
Wi-Fi slot and the string `"rtos"` key coexist without collision.
`resource-panel.js:L93` reads `streams['rtos']`. All three sites
agree.

### O6 ！ Path parsing in `api.py` is fragile but not exploitable
`parts = self.path.split("/")` (api.py L91, L96, L181, L186, L213)
treats a leading `//foo` as `["", "", "foo"]`, which would mis-parse
`parts[2]` as the empty string for `//sessions/<id>/records`. Not a
security issue (the empty string fails the existence check), but it
is a robustness wart and a minor information-disclosure risk (the
handler still returns 404 with an error body for the wrong path).

### O7 ！ `[S15]` instrumentation prints DWARF addresses and symbol names
**File**: `ground_station/comm/wifi_bridge.py` L493-L507, L1722-L1742.

The new `[S15]` print lines log the first 64 hex bytes of every `0x21`
request and the full `(address, size, count, name)` table of every
`0x08` reply, on every bridge start. These are not security-sensitive
(no keys, no credentials, no PII), but they do leak the firmware's
memory layout. Gate behind `--debug` or `GROUND_STATION_DEBUG_NAMES=1`
(which already exists at `wifi_bridge.py:L1847`) for production.

### O8 ！ `rtos_bridge.py` logging is bounded
**File**: `ground_station/platform/rtos_bridge.py`.

`LOG.info(...)` is used for "started" / "stopped" / "disabled" events
(four messages per bridge lifetime). `LOG.debug(...)` is used for
per-sample errors (silent unless DEBUG). No high-rate writes to
stderr. ?

### O9 ！ `plugin api.subscribe(callback)` has no bound
**File**: `docs/dashboard-platform/shell/index.html` L311
(`shellApi.subscribe`).

```js
subscribe: function (cb) { onStateChangeCallbacks.push(cb); }
```
Push-only, no cap. Pre-existing. `slot-manager-panel.js` registers
exactly one callback in `bindEvents` via `api.subscribe(onState)`
(L213). No regression in S15 ！ flagging because the brief asked.

### O10 ！ `__proto__` poisoning is not a concern
`existing_values.update(telemetry)` in `core.py:L172` would happily
overwrite a `__proto__` key if telemetry contained one, but Python
dicts are not prototypes ！ JS-style prototype pollution does not
apply. No fix needed.

### O11 ！ `slot-manager-panel.js` SUBSCRIBABLE_SLOTS mixes legacy and new slots
**File**: `docs/dashboard-platform/shell/plugins/slot-manager-panel.js` L19.

`SUBSCRIBABLE_SLOTS = [1, 2, 3, 9, 10, 11, 12]` ！ the 9..12 range is
the legacy typed-readback slot bank. Per `wifi_bridge._slot0_to_sidebar`
the legacy slot 9 was never populated (the S15 audit fixed the routing).
Subscribing to slot 9 is therefore expected to fail or produce garbage.
Not a security issue, but the panel will appear broken on first run.

## Verification performed

```
$ python -m py_compile ground_station/service/core.py ground_station/service/__main__.py \
                       ground_station/comm/wifi_bridge.py ground_station/platform/rtos_bridge.py \
                       ground_station/service/api.py
(silent ！ all five files parse)

$ python -c "from ground_station.platform.rtos_bridge import RtosBridge, build_bridge, RTOS_SYMBOLS, RTOS_SLOT_KEY; ..."
Import OK; symbols: ('platform_obs_send_ticks', 'platform_obs_queue_depth', 'platform_obs_dma_busy', 'xTickCount')
Slot key: rtos

$ grep -rn 'command_id == 33' ground_station/
(no matches ！ the service does NOT special-case 0x21; confirmed W1)

$ grep -rn 'PlatformObservability_Tick' firmware/ TASK/
TASK/send_data.c:637:    PlatformObservability_Tick(...)
firmware/rtos_observability.c:28:void PlatformObservability_Tick(...)
firmware/rtos_observability.h:26:void PlatformObservability_Tick(...);
(confirmed O1: the function IS wired, despite the STATE.md note)
```

## Checklist results

- [ ] Injection validation ！ **PASS** with caveat. `core.py:ingest_decoded` and
      `inject_external_stream` accept arbitrary dicts but are called only
      from internal threads (`wifi_bridge._rx_loop`, `rtos_bridge._run`).
      See W3 for the public-API recommendation.
- [ ] Command whitelisting ！ **WARN**. `api.py:do_POST /commands` forwards
      any `command_id`, `index`, `value`, `flags` to the FC. Pre-existing
      design choice. See W1 and W4.
- [ ] Resource bounds ！ **WARN**. `inject_external_stream.values` has no
      cap (W3). `wifi_bridge._pending_schema_ranges` is unbounded but
      self-clears on overwrite (W5). `_streams[slot]["values"]` grows only
      via WiFi frames (12 floats max) or the SWD bridge (4 keys).
- [ ] Concurrency ！ **PASS**. All `ServiceState` mutations in
      `ingest_decoded`, `inject_external_stream`, and `snapshot` are under
      `_state_lock`. `_pending_schema_ranges` reads/writes are under
      `_stream_lock`. No double-release paths.
- [ ] Logging ！ **PASS with caveat**. `rtos_bridge.py` is bounded. See
      O7 for `[S15]` instrumentation that should be debug-gated.
- [ ] Firmware extern reachability ！ **PASS**. `UA3TxDrops` defined
      `BSP/usart3.c:209`. `gs_cmd_head/tail` defined `BSP/usart4.c:97`.
      `xPortGetFreeHeapSize()` from `heap_4.c` reachable via `FreeRTOS.h`.
- [ ] Other ！ see W1-W5, O1-O11.

## Files reviewed (with line ranges)

- `ground_station/service/core.py` ！ L21-L40 (state dataclass), L42-L73
  (constructor), L75-L82 (start/stop), L84-L101 (ingest), L120-L186
  (ingest_decoded + merge), L188-L198 (tag★slot map), L202-L223
  (metadata extraction), L246-L286 (inject_external_stream),
  L304-L307 (submit_command), L344-L351 (snapshot).
- `ground_station/service/__main__.py` ！ full file (L1-L168), focus on
  L41-L65 (argparse), L88-L101 (RTOS bridge wiring), L121-L123 (start).
- `ground_station/service/api.py` ！ full file (L1-L256), focus on
  L82-L99 (static serving), L120-L177 (path parsing), L218-L227
  (`/commands` POST).
- `ground_station/comm/wifi_bridge.py` ！ focus on L241-L244
  (`_pending_schema_ranges`), L355-L357 (`_request_slot0_schema`
  invocation), L407-L520 (request builder), L493-L507 (`[S15]` prints),
  L1687-L1742 (schema-frame decode + `[S15]` prints), L1847 (`DEBUG_NAMES`).
- `ground_station/platform/rtos_bridge.py` ！ full file (L1-L226),
  focus on L65-L77 (`DEFAULT_INTERVAL_HZ`), L93-L97 (build_bridge),
  L104-L139 (start/stop), L141-L196 (_run).
- `firmware/rtos_observability.h` ！ full file (L1-L27).
- `firmware/rtos_observability.c` ！ full file (L1-L48), focus on L9-L12
  (externs), L21-L27 (globals), L28-L48 (Tick).
- `docs/dashboard-platform/shell/index.html` ！ focus on L259-L268
  (`eval(text)`), L311 (`api.subscribe`), L322-L342 (`shellApi.subscribeSlot`),
  L466-L475 (polling).
- `docs/dashboard-platform/shell/plugins/slot-manager-panel.js` ！
  L19 (`SUBSCRIBABLE_SLOTS`), L72-L92 (`submitSubscribe`/`Fallback`),
  L93-L112 (`readSlotMeta`), L208-L226 (`bindEvents`).
- `docs/dashboard-platform/shell/plugins/{status,mrac,estimator,safety,bandwidth,resource}-panel.js`
  ！ spot-checked each: no `eval`, no fetch to non-`/commands`/`/state`
  URLs, no `Function(...)` constructor, no direct DOM injection from
  unsanitised state (only safe textContent / innerHTML escapes via
  numeric formatting). The new `readNamed` / `readMeta` helpers in
  `mrac-panel.js:L42-L67` and `bandwidth-panel.js:L43-L62` do not
  write user-controlled strings into `innerHTML`. PASS.

## Recommendations (minimum fix summary)

1. W1: rewrite the `shellApi.subscribeSlot` comment to reflect that
   `0x21` is not a valid `0xCC 0xDD` command and the subscribe path is
   a placeholder until `core.py` learns to dispatch `command_id == 33`.
2. W2: add a `self._auto_subscribed` flag to `WifiBridge` so
   `_request_slot0_schema` only fires once per process lifetime.
3. W3: enforce `len(values) <= 256` and `max(value_size) <= 8192` in
   `inject_external_stream`; log + drop on violation.
4. W4: add a `[0x01, 0x18]` whitelist + index/value sanity check at
   `api.py:do_POST /commands` L218-L227.
5. O1: remove the obsolete "SWD reader wire-up" item from
   `docs/dashboard-platform/STATE.md:L181` ！ the wire-up is in
   place at `TASK/send_data.c:L637`.
