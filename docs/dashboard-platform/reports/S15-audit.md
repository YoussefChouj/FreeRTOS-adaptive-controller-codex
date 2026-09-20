# Infrastructure Audit ¡ª Telemetry & Command Pipeline

**Date:** Thursday Sep 17, 2026 (post-S14)
**Author:** Verification pass before the next implementation wave
**Drone status:** powered on, Wi-Fi (MicoAir) connected, SWD wireless debugger available
**Verdict:** **Multiple silent-data-flow bugs in the service layer + firmware gaps in the subscribe stream. Spec items vs reality is ~40% implemented; the rest is either data not flowing, mapped to wrong slot, or never produced by firmware.**

---

## 1. The single most important finding

**`core.py:ingest_decoded()` has a slot-mapping bug.** Every tag the Wi-Fi bridge produces routes to the WRONG slot, so every dashboard plugin reads from empty slots.

| Tag (from `wifi_bridge.py`) | What it means | `ingest_decoded()` routes to | Where plugins read it | Status |
|-----------------------------|---------------|------------------------------|----------------------|--------|
| `"a"` | Dashboard Frame A (sidebar) | **slot 2** | slot 0 | ? wrong slot, plugins see empty dict |
| `"b"` | Adaptive weights | slot 9 | slot 1 | ? wrong slot |
| `"id"` | Status/counter | slot 1 | (no plugin reads id keys) | ? unused |
| `"c"` | EKF / position | slot 3 | (no plugin reads c.*) | ? unused |
| `"s0"¡­"s3"` | Typed subscribe frames | `int(tag[1:])` ¡ú slot 0-3 | depends on slot | ? only works if schema was registered |

**Evidence:** `ground_station/service/core.py:97-108` shows the buggy mapping; `docs/dashboard-platform/shell/plugins/status-panel.js:265` reads `state.streams['0']`.

### Why this is silent
- The HTTP endpoint returns 200 OK.
- The shell renders without errors.
- Most plugins fall back to "¡ª" placeholders because their `getChannelVal(...)` returns `null`.
- The only panel that auto-discovers keys (`telemetry-explorer-panel.js`) works, which is why the sidebar "looks alive" but the named panels are dead.

---

## 2. Spec vs implementation matrix

### 2.1 Commands (spec: 30 commands in `COMMAND_SPEC.md`)

| Source | Status |
|--------|--------|
| `ground_station/service/gateway.py` + `api.py /commands` POST | ? Envelope format correct |
| `docs/.../plugins/command-panel.js` COMMAND_REGISTRY | ? All 30 commands declared |
| Firmware ACK/REJECTED/APPLIED result handling | ? Implemented (verified S3, S5) |
| `ServiceState.last_transaction_result` + `command_results` | ? Populated by `poll_command()` (S14) |

**Verdict:** Commands are fully implemented end-to-end.

### 2.2 Telemetry (spec: `TELEMETRY_SPEC.md` channel map)

| Stream (spec) | Source on firmware | Auto-subscribed? | Populated in service? | Plugin reads it? |
|---------------|--------------------|------------------|----------------------|------------------|
| Slot 0 `legacy_status` (8 ch, 20 Hz) | Frame A | ? via `_request_slot0_schema(layout="dashboard")` | ? misrouted to slot 2 | ? plugins read empty slot 0 |
| Slot 1 `legacy_adaptive` (var ch, 80 Hz) | Frame B | ? not auto-subscribed | ¡ª | ¡ª |
| Slot 2 `mrac_weights` (var ch, 80 Hz) | MAVLink 10001 | ? not auto-subscribed | ¡ª | ¡ª |
| Slot 3 `ekf_all` (var ch, 80 Hz) | MAVLink 10002 | ? not auto-subscribed | ¡ª | ¡ª |
| Typed slot 9 `Dashboard Frame A` | subscribe 0x09 | ? via auto-subscribe | ? in slot 2 (wrong) | ? plugins read slot 0 |
| Typed slot 10 `Inner loops` | subscribe 0x0A | ? not auto-subscribed | ¡ª | ¡ª |
| Typed slot 11 `MRAC weights` | subscribe 0x0B | ? not auto-subscribed | ¡ª | ¡ª |
| Typed slot 12 `EKF states` | subscribe 0x0C | ? not auto-subscribed | ¡ª | ¡ª |

**Verdict:** Only **1 of 8** spec'd streams is being subscribed, and that one is routed to the wrong slot.

### 2.3 Named telemetry keys produced by `wifi_bridge._slot0_to_sidebar()`

The bridge already publishes these named keys (good ¡ª see `wifi_bridge.py:530-575`):

| Key | Spec source | Reaches plugin? |
|-----|-------------|-----------------|
| `status.roll_deg`, `status.pitch_deg`, `status.yaw_deg` | imu_data.{rol,pit,yaw} | ? wrong slot |
| `status.arm` | `DroneStatus.ARM_Status` | ? wrong slot |
| `status.flymode` | `DroneStatus.FlyMode` | ? wrong slot |
| `status.sbus`, `status.twc_execute`, `status.twc_arrived`, `status.rc_authority`, `status.of_hold`, `status.estimator_ready` | status flags | ? wrong slot |
| `status.vbat` | `real_voltage` | ? wrong slot |
| `mrac.pitch.e`, `mrac.pitch.u_ad`, `mrac.roll.e`, `mrac.roll.u_ad`, `mrac.yaw.e`, `mrac.yaw.u_ad`, `mrac.z.e`, `mrac.z.u_ad` | `mrac_state.*.e`, `mrac_state.*.u_ad` | ? wrong slot |

### 2.4 Keys the spec requires but the firmware doesn't publish over Wi-Fi

| Missing key | Spec'd by | Why missing | Fix |
|-------------|-----------|-------------|-----|
| `ch0` ¡­ `ch14` | `TELEMETRY_SPEC.md` channel map | Wifi bridge publishes DWARF names, not `ch*` indices | Decide: re-map sidebar to `ch*` indices, OR update plugins to use named keys |
| `ekf.pos_x/y/z`, `ekf.vel_x/y/z`, `ekf.bias_*` | TELEMETRY_SPEC.md | Subscribe stream doesn't include these vars | Add EKF symbols to DASHBOARD_FRAME_A_VARS in `boot_default_layout.py` |
| `estimator.filter_status`, `estimator.cov_*` | TELEMETRY_SPEC.md | Same as above | Same fix |
| `mrac.theta_0¡­theta_5` (per axis) | TELEMETRY_SPEC.md, COMMAND_SPEC.md MRAC Gamma | Subscribe stream emits `mrac_state.<axis>.e` / `u_ad` only, not full theta vector | Add `mrac_state.<axis>.theta_N` to subscribe list |
| `rtos.scheduler_tick_count`, `rtos.heap_free_bytes`, `rtos.usart3_*`, `rtos.cmd_queue_*`, `system.*` | ARCHITECTURE.md security & obs | `rtos_observability.c` not in Keil project; not streamed | Add to Keil uVision source group; stream over Wi-Fi via second slot |
| `safety.gs_max_*` | COMMAND_SPEC.md | Safety parameters are stored in firmware, not streamed back | Add typed parameter readback (firmware change) |

---

## 3. Plugin-by-plugin audit

| Plugin | Spec data source | What it reads | Problem | Severity |
|--------|-----------------|---------------|---------|----------|
| `status-panel.js` | slot 1 (legacy_status), slot 9 (typed) | `streams['0'].values.chN` (gyro proxy for ARM, ch11 for vbat) | Reads slot 0 (empty); ARM derived from gyro magnitude | High ¡ª status panel is broken |
| `mrac-panel.js` | `mrac.*` keys | `streams['0'].values.chN` as theta proxy | Shows scaled gyro, not real MRAC | High |
| `estimator-panel.js` | `ekf.*`, `estimator.*` | `streams['0'].values.chN` as EKF proxy | Shows gyro/altitude, not EKF state | High |
| `safety-panel.js` | `safety.*` keys | `streams['9']` (never populated) | Shows "¡ª" for all params | High |
| `resource-panel.js` | `rtos.*`, `system.*` | `streams['0'].values.chN` as IMU/baro proxy | Reads ch0-ch11 as raw IMU; RTOS metrics never appear | Medium ¡ª partially works because chN data exists in legacy_status |
| `telemetry-explorer-panel.js` | all streams | all keys | ? Auto-discovers, works | ? Working |
| `command-panel.js` | service state | `state.last_transaction_result`, `state.command_results` | ? Wired in S14 | ? Working |
| `experiment-panel.js` | `/experiments` API | HTTP | ? | ? Working |
| `motor-bench-panel.js` | `motor.rpm_*` | (not yet checked) | ? Needs review | Low ¡ª bench mode is offline |
| `time-series-panel.js` | all streams | (not yet checked) | ? Needs review | Low |
| `fft-panel.js` | all streams | (not yet checked) | ? Needs review | Low |
| `bandwidth-panel.js` | `streams.*` | `stream.last_update_ns`, `stream.received`, `stream.dropped` | ? `ingest_decoded` never sets these fields | High ¡ª bandwidth chart can't compute rate |
| `replay-panel.js` | `/sessions` API | HTTP | ? | ? Working |
| `path-panel.js` | `ekf.pos_x/y` | (not yet checked) | ? Needs review | Medium |

### Detailed plugin issues (read code, line-cited)

**`status-panel.js`:**
- L265: `var stream0 = state.streams && state.streams['0'];` ¡ª empty when only slot 2 is populated
- L268: `var estimatedArm = gyroMag > 0.1 ? 1 : 0;` ¡ª proxy that always reads "DISARMED" unless drone is moving
- L274: `updateFlyMode(null);` ¡ª explicitly passes null, never shows FlyMode label
- L277-279: `getChannelVal(stream0, 11)` reads `ch11` which doesn't exist in sidebar payload (only `status.vbat` exists)

**`mrac-panel.js`:**
- Header comment L1-9 says "Full MRAC theta parameters require firmware to expose mrac.* keys" ¡ª the firmware already does! But the plugin doesn't read them.
- L86-100: Reads `ch0`, `ch1`, `ch10` as proxy and `* 0.5 + j * 0.1` to fake 6 theta bars. Should be reading `mrac.<axis>.e` / `u_ad`.

**`estimator-panel.js`:**
- L19: comment "EKF state fields need firmware to expose; showing gyro/accel as proxy"
- Reads `ch0`, `ch1`, `ch2`, `ch10` and labels them "Gyro X/Y", "Accel Z", "Altitude"
- Filter status hardcoded to "Active" whenever `ch0` is non-null

**`safety-panel.js`:**
- L218: `var slot9 = state.streams['9'];` ¡ª slot 9 is never populated; `vals[param.key]` is always null

**`bandwidth-panel.js`:**
- L52-55: Reads `stream.last_update_ns` and `stream.seq`/`stream.received` ¡ª these fields don't exist on the dict that `ingest_decoded` stores (`{tag, values}` only)

**`resource-panel.js`:**
- Reads `ch0`¨C`ch11` as IMU proxy in three groups (Scheduler, IMU Rate, Magnetometer, Environment)
- Comments acknowledge RTOS metrics "come via SWD/RTT, not WiFi telemetry" ¡ª but SWD path is not wired to plugin

---

## 4. Live verification -- what `/state` returns when drone is connected

**Captured 2026-09-17 14:36 UTC+8** (operator dashboard running on `localhost:8081`):

```json
{
  "schema_id": "r1-s1-9F32E2EA",
  "session_id": "08f86184a5b94df0a4bf25e1a7392b6b",
  "samples": 137553,
  "last_update_ns": 1789628158920388400,
  "streams": {
    "0": {
      "tag": "s0",
      "values": {
        "slot0.ch0.0": -1.150, "slot0.ch0.1": -1.020, "slot0.ch0.2": 47.045,
        "slot0.ch0.3": 0.0359, "slot0.ch0.4": -0.1536, "slot0.ch0.5": -0.1007,
        "slot0.ch0.6": 0.153, "slot0.ch0.7": -0.0142, "slot0.ch0.8": 0.0444,
        "slot0.ch0.9": 0.0146, "slot0.ch0.10": 0.1554, "slot0.ch0.11": 0.0,
        "slot0.ch0.12": 0.0, "slot0.ch0.13": 2064.0, "slot0.ch0.14": 0.0,
        "slot0.dropped": 514, "slot0.loss_pct": 0.372,
        "slot0.received": 137530, "slot0.seq": 124, "slot0.t_ms": 2581504
      }
    },
    "2": {
      "tag": "a",
      "values": {
        "status.pitch_deg": -1.035, "status.roll_deg": -0.793, "status.yaw_deg": 16.602
      }
    }
  }
}
```

### Updated understanding after live capture

The audit's slot-mapping theory was **partially correct** but missed nuance:

1. **Telemetry IS flowing** -- 137,553 samples since session start, seq 124, 0.37% loss. The wire is healthy.
2. **Two concurrent paths exist:**
   - `streams["0"]` is the **raw subscribe frame** with positional names (`slot0.ch0.0` to `slot0.ch0.14`) and rich **stream metadata embedded as values**: `slot0.dropped`, `slot0.loss_pct`, `slot0.received`, `slot0.seq`, `slot0.t_ms`. This is `MultiStreamDecoder.feed()` output.
   - `streams["2"]` is the **sidebar-mapped named payload** from `wifi_bridge._slot0_to_sidebar()`. Only 3 keys resolved: `status.pitch/roll/yaw_deg`. This is `WifiBridge._publish_telem("a", sidebar_payload)` output.
3. **The slot-mapping "bug"** in `ingest_decoded` (`"a"` -> slot 2 instead of 0) is real but only affects the named-key subset. The raw subscribe frame already goes to slot 0.
4. **Plugin failure root cause is multi-faceted:**
   - Status/MRAC/EKF panels expect named keys in `streams['0']` but get raw `slot0.ch0.N` keys -> silent fallback to "-"
   - Safety panel expects `streams['9']` to exist -> no such slot
   - Bandwidth panel expects `stream.last_update_ns/received/dropped` as top-level stream fields -> they're embedded INSIDE `values` as `slot0.dropped/received/loss_pct/seq/t_ms`
5. **NEW finding: DWARF symbol resolution gap.** The bridge requests 22 vars via `DASHBOARD_FRAME_A_VARS` but only 3 (`imu_data.rol/pit/yaw`) resolve to actual addresses in the live `OBJ/JX_FLY.axf`. The other 19 (ARM_Status, FlyMode, real_voltage, mrac_state.*, etc.) are absent from the ELF -- likely renamed/removed by a firmware refactor since `boot_default_layout.py` was last updated. **Need to re-audit the ELF symbol table** before any fix will produce more named keys.

### What the live data actually tells us about slot semantics

The spec says slot 0 is `legacy_status` (8 channels). The live subscribe frame has 15 channels (`ch0.0` to `ch0.14`), which matches the **expanded dashboard layout** (22 vars requested, 15 channels worth of floats actually streamed). So the wire is doing what was designed -- but the symbols didn't match.

### Concrete implications for the fix agents

- **Fix 1 (slot routing)** -- change `"a"` -> slot 0 in `core.py:ingest_decoded`. This puts the 3 named keys into `streams['0']` alongside the raw positional data. Plugins can then use both.
- **Fix 2 (plugin reads)** -- teach plugins to fall back to raw positional `slot0.chN` keys when named keys are missing. So even when DWARF resolution fails for 19 vars, panels keep working.
- **Fix 3 (DWARF audit)** -- regenerate `DASHBOARD_FRAME_A_VARS` from the CURRENT `OBJ/JX_FLY.axf` symbol table so all 22 vars resolve.
- **Fix 4 (stream metadata extraction)** -- bandwidth panel should read `slot0.loss_pct`, `slot0.received`, `slot0.dropped` from `values`, not from top-level fields. After this fix the bandwidth panel works immediately.
- **Fix 5 (streams[9] for safety)** -- populate `streams[9]` from a typed parameter readback OR move safety panel to read from `streams[0].values.gs_max_*` after DWARF symbols are added.

---

## 5. Multi-slot system status

- **Spec allows 4 slots (0-3) for legacy, plus typed slots 9-12**
- **Auto-subscribe only requests slot 0** (the "dashboard" layout in `boot_default_layout.py`)
- **No way to subscribe additional slots at runtime** from the dashboard UI
- **No way to view all active slots' metadata** (rate, loss, names) ¡ª bandwidth panel is supposed to do this but is broken

The slot "system" exists in firmware and bridge code, but the dashboard has **zero UI to select, view, or manage slots**. This matches the user's complaint: "I don't have observability on what slot I am selecting."

---

## 6. SWD wireless debugger status

- `firmware/rtos_observability.c` exists and exposes `platform_obs_send_ticks`, `platform_obs_queue_depth`, `platform_obs_dma_busy`
- But STATE.md line "Known gaps" #1 says: **"`rtos_observability.c` must be added to the Keil `.uvprojx` source group to expose `s_rtos_obs.*` metrics over SWD"**
- `ground_station/livewatch/reader.py` exposes `LiveReader` to read DWARF symbols via SWD ¡ª but no plugin uses it
- RTOS metrics (`rtos.*`, `system.*`) thus unavailable to any panel

---

## 7. Implementation backlog (proposed, for user review)

To match spec, fix in priority order:

### P0 ¡ª Pure data-flow bugs (no firmware change needed)
1. **`core.py:ingest_decoded()` slot mapping** ¡ª `"a"` ¡ú slot 0, `"b"` ¡ú slot 1, `"c"` ¡ú slot 3 (and stream `s0`/`s1`/`s2`/`s3` map to the typed slot id, not 0+id)
2. **`core.py:ingest_decoded()` enrichment** ¡ª set `last_update_ns`, `received`, `dropped`, `loss_pct`, `sequence`, `source_time_ms` on each stream entry
3. **`status-panel.js`** ¡ª read `state.streams['2'].values.status.arm` instead of gyro proxy; read `status.flymode` instead of null
4. **`mrac-panel.js`** ¡ª read `mrac.<axis>.e` / `u_ad` from named sidebar keys
5. **`estimator-panel.js`** ¡ª read `ekf.*` / `estimator.*` (requires firmware OR re-tag sidebar keys)
6. **`safety-panel.js`** ¡ª read from `streams['0']` after P0.1 fix; OR (better) wire to firmware's parameter readback
7. **`bandwidth-panel.js`** ¡ª compute rate from `received`/`last_update_ns` after P0.2 fix

### P1 ¡ª Spec coverage (firmware changes + dashboard)
8. **`boot_default_layout.py:DASHBOARD_FRAME_A_VARS`** ¡ª add EKF state symbols so `ekf.pos_*`, `ekf.vel_*`, `ekf.bias_*` are streamed
9. **`boot_default_layout.py`** ¡ª add full MRAC theta vector (currently only `.e` and `.u_ad` per axis)
10. **Slot subscription UI** ¡ª dashboard panel to view/select active slots, request new slots via bridge.subscribe_preset()
11. **Slot observability** ¡ª bandwidth-panel: show which slot has which schema, rate, loss %, var count

### P2 ¡ª RTOS observability via SWD
12. **`firmware/JX_FLY.uvprojx`** ¡ª add `rtos_observability.c` to source group
13. **`ground_station/platform/rtos_bridge.py`** (new) ¡ª wire `LiveReader` to periodically poll `s_rtos_obs.*` and inject into `ServiceState`
14. **`resource-panel.js`** ¡ª display `rtos.*` keys from the SWD-injected stream
15. **`systemmonitor` task in firmware** ¡ª also expose scheduler tick, heap free, queue depth via the same path

### P3 ¡ª Documentation enrichment
16. **`STATE.md`** ¡ª update with infrastructure audit findings; bump "Known gaps" section
17. **`TELEMETRY_SPEC.md`** ¡ª reconcile channel-index (`chN`) vs DWARF-named (`status.vbat`) key conventions; pick one and update plugins + spec
18. **`sessions/S15-infrastructure-fixes.md`** ¡ª session brief for this wave
19. **`reports/S15-audit.md`** ¡ª this file (already written)
20. **`INDEX.md`** + **`README.md`** ¡ª bump plugin registry & known gaps sections

### P4 ¡ª Review & adjudication
21. Run `/review` + `/code-review` + `/review-bugbot` on every changed file
22. Manual live verification with `curl /state` after fixes
23. Update hardware validation evidence table in `STATE.md`

---

## 8. Recommended scope for next implementation wave

**Recommended:** Spawn 3-4 parallel subagents covering P0¨CP2 above (data-flow + firmware SWD + slot UI), then 1 docs subagent, then 3 reviews.

**Minimum viable for jiang's complaint ("data not flowing, no slot observability"):** P0 + P1.10 + P1.11. Estimated ~600 lines Python + ~1500 lines JS.

**Stretch (full spec compliance):** all of P0¨CP2.

---

## 9. Files I inspected

| File | Lines read | Why |
|------|-----------|-----|
| `docs/dashboard-platform/STATE.md` | 151 | Project state & known gaps |
| `docs/dashboard-platform/ARCHITECTURE.md` | full | Data flow contract |
| `docs/dashboard-platform/TELEMETRY_SPEC.md` | full | Channel map |
| `docs/dashboard-platform/COMMAND_SPEC.md` | full | Command contract |
| `ground_station/service/core.py` | full | Slot mapping bug origin |
| `ground_station/service/api.py` | full | HTTP endpoint surface |
| `ground_station/comm/wifi_bridge.py` | 870 of 87684 | Sidebar mapping, auto-subscribe flow |
| `ground_station/service/__main__.py` | full | Startup wiring |
| `ground_station/platform/shell.py` | full | HTTP shell launcher |
| `ground_station/livewatch/stream.py` | first 80 | Stream decoder API |
| `ground_station/livewatch/reader.py` | first 60 | SWD reader API |
| `firmware/rtos_observability.c` | full | RTOS metrics source |
| `docs/dashboard-platform/shell/index.html` | full | Shell + plugin loader |
| `docs/dashboard-platform/shell/plugins/status-panel.js` | full | ARM/FlyMode bug |
| `docs/dashboard-platform/shell/plugins/mrac-panel.js` | full | Theta proxy bug |
| `docs/dashboard-platform/shell/plugins/estimator-panel.js` | full | EKF proxy bug |
| `docs/dashboard-platform/shell/plugins/safety-panel.js` | full | Slot 9 dependency |
| `docs/dashboard-platform/shell/plugins/bandwidth-panel.js` | first 100 | Missing-fields bug |
| `docs/dashboard-platform/shell/plugins/resource-panel.js` | first 80 | RTOS missing |
| `docs/dashboard-platform/shell/plugins/telemetry-explorer-panel.js` | first 80 | ? working |
| `docs/dashboard-platform/shell/plugins/command-panel.js` | first 60 | ? working |
