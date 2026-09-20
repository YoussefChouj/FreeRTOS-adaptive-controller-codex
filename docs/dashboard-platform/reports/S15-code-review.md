# S15 Code Review (standards + spec compliance)

**Reviewer:** code-review agent  
**Date:** 2026-09-17  
**Scope:** S15 implementation wave — service layer, RTOS bridge, plugins, firmware observability

---

## Verdict

**BLOCK** — one critical spec contract violation, two documentation-state mismatches, one divider range issue.

---

## Spec compliance findings

### BLOCK — `shellApi.subscribeSlot` not in `index.html` (never wired)

The `slot-manager-panel.js` calls `shellApi.subscribeSlot(...)` (line 52 of slot-manager-panel.js) as the primary subscribe path, and the fallback `submitSubscribeFallback` POSTs `{command_id: 33, ...}` to `/commands`. But `shell/index.html` does not define `shellApi.subscribeSlot`. The shellApi object (index.html:472–488) only has `getState`, `subscribe`, `submitCommand`, `getPlugins`, `registerPanel`. This means every click of a slot subscribe button in `slot-manager-panel.js` will throw `TypeError: api.subscribeSlot is not a function`.

Additionally, `core.py` does not handle `command_id: 33` — the service has no subscribe-wrapper endpoint. The fallback path also fails silently (the `/commands` POST returns a transaction_id but nothing subscribes to anything).

**STATE.md S15 gate result** claims: *"Added `shellApi.subscribeSlot(slot, divider, ranges)` helper that POSTs `{command_id: 33, ...}` to `/commands`"*. This is not in the actual file.

---

### BLOCK — PLUGIN_FILES missing all S14/S15 plugins

`index.html:400–407` PLUGIN_FILES only lists 6 plugins:

```
status-panel, mrac-panel, estimator-panel,
resource-panel, safety-panel, telemetry-explorer-panel
```

Missing from PLUGIN_FILES (untracked new files confirmed in git status):

| Missing plugin | File status |
|---|---|
| Slot Manager (new S15) | untracked new |
| Command Panel | untracked new |
| Experiment Panel | untracked new |
| Motor Bench | untracked new |
| Time Series | untracked new |
| FFT Spectrum | untracked new |
| Bandwidth Manager | untracked new |
| Session Replay | untracked new |
| Path Planning | untracked new |

STATE.md "Complete plugin registry" says **15** plugins; exactly 9 are absent at runtime. The `slot-manager-panel.js` (the headline S15 deliverable for "slot observability") is one of them.

---

### WARN — `safety-panel.js` uses `cmdId: 1` but spec says "Ground Station Safety Limits (0x09)"

`safety-panel.js:20` defines `cmdId: 1` for all four safety parameters, and line 218 confirms `api.submitCommand(param.cmdId, ...)`. Per `COMMAND_SPEC.md` §"Ground Station Safety Limits (0x09)", the command ID should be `0x09` (decimal **9**), not 1. The four indices (0–3) and value ranges in the plugin match the spec correctly; only the command ID is wrong.

This means the +/- step buttons send PID Gain commands (0x01) instead of Ground Station Safety Limit commands (0x09). The drone will execute PID gain writes instead of safety limit updates, which could be dangerous.

---

### WARN — `slot-manager-panel.js` divider bounded to max=32, spec allows ≤255

`slot-manager-panel.js:66` HTML input: `<input id="sm-divider" type="number" min="1" max="32" ...>`. Per `COMMAND_SPEC.md`, divider is a uint8 (0–255). The `livewatch/stream.py:285` also validates `divider` is in 0..255. Users cannot request dividers above 32 from the UI.

---

### WARN — `core.py` `_streams` dict uses `tag` string on update, spec says `tag: int`

`core.py:137` sets `self._streams[slot] = {"tag": tag, ...}`. The incoming `tag` is a string (`"a"`, `"s0"`, etc.) from `ingest_decoded`. The spec (`ARCHITECTURE.md` "State publication") says `tag: int`, and `plugin-api.md` StreamState schema also lists `tag: integer`. The shell's sidebar and plugins read `stream.tag` expecting a number but receive a string. No runtime error since JS is loosely typed, but the schema contract is violated.

---

### WARN — STATE.md claims 15 plugins in registry, actual count is 6

`STATE.md:71` "15 operational plugins implemented". The PLUGIN_FILES in index.html (the only plugin discovery mechanism per `plugin-api.md`) has 6. The 9 missing files are untracked new files — they exist on disk but are not wired into the shell.

---

## Style/convention findings

### WARN — `var` vs `let/const` mixed in plugins

`slot-manager-panel.js:39`: `var SUBSCRIBE_DIVIDER_DEFAULT = 1` (ES5 style). All other plugin files consistently use `var` for state, function declarations for module-like patterns. No `let/const` anywhere. This is consistent with the existing plugin style and PLUGIN_DEVELOPER_GUIDE.md examples. No change needed.

### WARN — `rtos_bridge.py` default `interval_hz=5.0` conflicts with `firmware/README.md`

`rtos_bridge.py:66` sets `DEFAULT_INTERVAL_HZ: float = 5.0`. `firmware/README.md` "Suggested host poll interval" section recommends **100 ms (10 Hz)** as the right default: "matches Send_Task's 100 Hz cadence in MIXED mode without thrashing the SWD link." The `--rtos-interval` CLI default is 5.0 Hz, which is slower than the recommended 10 Hz and 10× slower than Send_Task's cadence. Not a hard error but inconsistent with the firmware author's stated intent.

### WARN — `rtos_bridge.py:226–233` hardcoded `1000 // 5` for `source_time_ms`

```python
ground_station/platform/rtos_bridge.py:226
"source_time_ms": int((sequence or 0) * (1000 // 5)) if sequence else 0,
```

`1000 // 5 = 200`. This is a placeholder; it converts a sample counter into fake milliseconds at 5 Hz, producing `sequence * 200 ms`. The value is meaningless for RTOS metrics. This is acknowledged in a comment but produces garbage `source_time_ms` in the injected stream.

---

## Bug-shaped findings (technically wrong, not just style)

### WARN — `wifi_bridge.py` `_pending_schema_ranges` never cleared after schema reply

`_pending_schema_ranges` is set in `_request_slot0_schema` (wifi_bridge.py:492) but never removed after the 0x08 reply arrives. On a second reconnect/re-subscribe cycle, `_pending_schema_ranges[0]` still holds the old ranges, and `by_addr` will match the new reply's addresses against stale names. This is a subtle re-connect bug: on a fresh session, the second schema reply may attach first-session names to second-session ranges if addresses repeat.

Fix: clear `self._pending_schema_ranges[slot]` inside `_handle_schema_frame` after consuming it (after line ~1709).

### WARN — `wifi_bridge.py` CRC length check inconsistency

`_parse_one` was fixed to include 2-byte CRC in total_len for variable-length frames (wifi_bridge.py:954: `total_len = 6 + payload_len + 2`). However, the MAVLink check at the top of `_parse_one` (line 942: `total = 8 + mav_len + 2`) and the Frame A check (68 bytes) do not share the same CRC-inclusive length. MAVLink has a 2-byte CRC at the end, Frame A 68-byte total may or may not include CRC. No evidence this causes bugs in practice, but the inconsistency between MAVLink path and custom frame path is worth noting.

### WARN — `bandwidth-panel.js` `calculateRate` can produce negative rate on sequence rollover

`bandwidth-panel.js:57`: `var dSeq = meta.sequence - prev.seq`. For typed streams with 32-bit sequence numbers, `dSeq` could be negative if sequence wraps. The early-return guard `if (dT <= 0 || dSeq <= 0)` correctly handles this. For legacy 8-bit sequence (0–255), same guard applies. ✅

### WARN — `slot-manager-panel.js` rate calculation ignores `source_time_ms`

`slot-manager-panel.js:137`: `computeRate` uses `meta.sequence` only (no time source). If two subscribe frames have the same sequence (firmware bug or re-subscription), `dSeq` = 0 and rate = 0. The bandwidth-panel.js uses `t_ms` for its rate calculation (bandwidth-panel.js:57: `var dT = (meta.t_ms - prev.t_ms) / 1000.0`), which is better. The slot-manager panel has no time-based fallback.

---

## Diff hygiene findings

### WARN — `[S15]` print instrumentation not behind a flag in `wifi_bridge.py`

`wifi_bridge.py:508–517` prints `[S15]` diagnostic lines on every subscribe request and schema reply. In CI (no drone attached), these print on every failed connection attempt. Per the review requirement "should be gated behind a flag", a `debug` or `verbose` flag would be better than always-on print.

### WARN — No dead imports detected

`core.py`: clean imports. `__main__.py`: clean. `rtos_bridge.py`: `TYPE_CHECKING` guard for `GroundStationService` import, avoiding circular import at type-check time. ✅

### WARN — No commented-out code blocks in reviewed files

No large commented-out blocks found in `core.py`, `wifi_bridge.py`, `rtos_bridge.py`, or any plugin. ✅

### WARN — `rtos_bridge.py` is 226 lines, spec says ~150 line cap

`rtos_bridge.py` is 226 lines. The `platform/` folder spec (mentioned in rtos_bridge.py:19) says "caps this module at ~150 lines." This is advisory, not enforced, but worth noting. The module is well-structured and readable; the overage is mostly docstring and error-handling. Not a blocker.

---

## Test coverage assessment

### APPROVE — `test_service.py` S15 tests comprehensively cover the data-flow fixes

7 new tests (S15 section, lines ~295–476):

| Test | Covers |
|---|---|
| `test_ingest_decoded_routes_a_to_slot_0` | Fix 1: "a" → slot 0 |
| `test_ingest_decoded_routes_b_c_id_to_expected_slots` | Fix 1: b/c/id/s0..s3 routing |
| `test_ingest_decoded_merges_sidebar_into_existing_slot` | Fix 3: sidebar merge, raw coexist |
| `test_stream_metadata_present_on_every_path` | Fix 2: top-level metadata always populated |
| `test_inject_external_stream_publishes_to_snapshot` | Fix 5: RTOS bridge injection |
| `test_inject_external_stream_no_op_when_service_not_started` | Defensive: early no-op |
| `test_state_stream_metadata_visible_over_http` | End-to-end: /state JSON has all fields |

The tests are well-named, atomic, and use synthetic payloads. The S14 tests (lines 124–295) also cover command result fields, HTTP endpoints, and the compare API.

### APPROVE — `test_wifi_bridge_stream.py::TestSchemaHandlerPreservesNames` covers the name-re-attach bug

Three tests:
- `test_schema_reattaches_names_from_pending_request` — happy path (pre-populated `_pending_schema_ranges`)
- `test_schema_without_pending_request_returns_anonymous_ranges` — fallback when no pending request
- `test_schema_partial_pending_match_only_named_what_matches` — partial address match only

These directly test the S15 Fix 4 (`_pending_schema_ranges` re-attach) with wire-exact frame construction.

### WARN — No JS plugin tests exist

Per review scope, no JavaScript test files exist. The STATE.md says "All 17 service tests pass (10 original + 7 added by service-layer agent) ... All 7 plugin JS files parse cleanly via `node new Function(code)`." The parse check is not a unit test — it only validates syntax, not behavior.

---

## Documentation gaps

### BLOCK — `PLUGIN_DEVELOPER_GUIDE.md` never mentions `shellApi.subscribeSlot`

The guide is the canonical reference for plugin authors. `shellApi.subscribeSlot` is referenced in `STATE.md S15 gate result` and in `slot-manager-panel.js` (as the preferred subscribe path), but the guide lists only the 5 original methods. Plugin authors who follow the guide will not know this API exists.

### BLOCK — `plugin-api.md` does not mention `shellApi.subscribeSlot`

The canonical shell API reference (`shell/plugin-api.md`) has no `subscribeSlot` entry. The `ShellApi` interface definition (lines 45–54) lists only 5 methods. The method `command_id: 33` subscribe wrapper is also absent.

### WARN — STATE.md plugin registry says 15 plugins, PLUGIN_FILES has 6

`STATE.md:71` "15 operational plugins implemented in `docs/dashboard-platform/shell/plugins/`". Only 6 are in the active PLUGIN_FILES. The other 9 exist as untracked files but are not loaded at runtime.

### WARN — STATE.md "S15 gate result" section is aspirational in two places

1. "Added `shellApi.subscribeSlot(slot, divider, ranges)` helper" — not in index.html
2. "slot-manager-panel.js added to PLUGIN_FILES" — not done
3. "All 17 service tests pass" — 10 original + 7 new = 17, ✅

### WARN — `firmware/README.md` says "wire-up required" but `rtos_observability.c` tick not wired

The README says: *"PlatformObservability_Tick(queue_depth, dma_busy) is the existing tick handler. It now also updates the three S15 counters. **Caller wiring is required**"*. `rtos_observability.c` was modified by the S15 agent but the actual call site in `USER/main.c::Send_Task` was not modified. The `platform_obs_*` symbols will exist in the ELF with value 0 unless the tick function is wired. This is documented as a known gap in `STATE.md` "Known gaps" item #6 — but the S15 gate result says the RTOS bridge is shipped, which implies the observability surface is live. Without the tick wiring, all RTOS metrics will be zero.

---

## Files reviewed with verdict per file

| File | Verdict | Key finding |
|---|---|---|
| `ground_station/service/core.py` | **APPROVE** | All S15 fixes correct; stream metadata fully populated; `inject_external_stream` consistent with `ingest_decoded`; merge preserves both sidebar and raw keys |
| `ground_station/service/__main__.py` | **APPROVE** | Flag naming consistent (`--rtos-bridge`, `--rtos-interval`); optional bridge cleanly started/stopped; daemon thread lifecycle correct |
| `ground_station/comm/wifi_bridge.py` | **WARN** | `_pending_schema_ranges` re-attach correct; `[S15]` prints not behind a flag; `_pending_schema_ranges` never cleared on reconnect |
| `ground_station/platform/rtos_bridge.py` | **WARN** | Follows `LiveReader` pattern; SWD-unavailable handled silently; `DEFAULT_INTERVAL_HZ=5.0` conflicts with firmware/README (100 ms / 10 Hz); `source_time_ms` is placeholder math |
| `firmware/rtos_observability.c` | **WARN** | `volatile` correct; queue-depth math correct; `PlatformObservability_Tick` needs `static` or internal linkage; tick not wired to Send_Task (known gap) |
| `firmware/rtos_observability.h` | **APPROVE** | Header guard correct; `extern volatile` for all 7 symbols; Doxygen-style comments present |
| `docs/dashboard-platform/shell/index.html` | **BLOCK** | `slot-manager-panel.js` not in PLUGIN_FILES; `shellApi.subscribeSlot` not defined; 9 of 15 plugins not loaded |
| `docs/dashboard-platform/shell/plugins/status-panel.js` | **APPROVE** | Read priority: named → fallback → proxy → null; ARM from `status.arm`→status_bits→ch13 chain correct; `destroy()` clears state |
| `docs/dashboard-platform/shell/plugins/mrac-panel.js` | **APPROVE** | `mrac.<axis>.e`/`u_ad` read correctly; proxy fallback transparent with badge; `destroy()` clears state |
| `docs/dashboard-platform/shell/plugins/estimator-panel.js` | **APPROVE** | EKF groups hidden until keys appear; honest disclaimer banner; proxy groups correctly labeled; `destroy()` clears state |
| `docs/dashboard-platform/shell/plugins/safety-panel.js` | **BLOCK** | Uses `cmdId: 1` (PID Gain) instead of `cmdId: 9` (Ground Station Safety Limits); all 4 params will write wrong command ID |
| `docs/dashboard-platform/shell/plugins/bandwidth-panel.js` | **APPROVE** | Reads top-level first, embedded fallback; `countVars` regex correct; RAF throttling present; refresh button no leaky setInterval |
| `docs/dashboard-platform/shell/plugins/resource-panel.js` | **APPROVE** | `findRtosStream` scans multiple slot candidates; honest disclaimer banner for RTOS pending; `destroy()` clears state |
| `docs/dashboard-platform/shell/plugins/slot-manager-panel.js` | **BLOCK** | Calls `shellApi.subscribeSlot` which doesn't exist; `command_id: 33` fallback to `/commands` not wired in service; divider max=32 < spec 255 |
| `ground_station/service/tests/test_service.py` | **APPROVE** | 7 S15 tests atomic and well-named; all data-flow fixes covered; `streams["0"]` string key correct |
| `ground_station/comm/tests/test_wifi_bridge_stream.py` | **APPROVE** | `TestSchemaHandlerPreservesNames` covers the actual name re-attach bug; wire-exact frame construction |

---

## Minimum-needed fixes (for APPROVE)

1. **index.html**: add `slot-manager-panel.js` to `PLUGIN_FILES` array; define `shellApi.subscribeSlot(slot, divider, ranges)` that POSTs `{command_id: 33, ...}` to `/commands`
2. **safety-panel.js**: change all `cmdId: 1` to `cmdId: 9` for the four safety limit parameters
3. **slot-manager-panel.js**: change `max="32"` to `max="255"` on the divider input
4. **STATE.md**: correct plugin count from 15 to 6 (or add all 9 missing files to PLUGIN_FILES if they are meant to be live); remove aspirational `shellApi.subscribeSlot` claim; add known-gap note that `PlatformObservability_Tick` tick wiring in firmware is pending
5. **PLUGIN_DEVELOPER_GUIDE.md**: add `shellApi.subscribeSlot(slot, divider, ranges)` entry to the Shell API reference section
6. **plugin-api.md**: add `shellApi.subscribeSlot(slot, divider, ranges)` to the `ShellApi` interface and method list
7. **wifi_bridge.py**: clear `_pending_schema_ranges[slot]` after consuming in `_handle_schema_frame`
8. **firmware/rtos_observability.c**: add `static` to `PlatformObservability_Tick` or document that it must be called from `USER/main.c::Send_Task`
