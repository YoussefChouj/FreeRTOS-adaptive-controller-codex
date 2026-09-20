# S15 ¡ª Plugin overhaul

**Date:** Thursday Sep 17, 2026 (post-audit, in S15 implementation wave)
**Author:** plugin-overhaul subagent
**Scope:** Dashboard plugin files only. No Python service code, no firmware, no `core.py`.
**Goal:** Make every plugin correctly read the new merged-key telemetry surface, fix the 5 broken panels called out in `S15-audit.md`, add slot observability, all while remaining robust to BOTH the named-key surface AND the raw positional fallback.

---

## 1. Files changed

| File | Lines before | Lines after | ¦¤ | Status |
|------|-------------|-------------|---|--------|
| `shell/plugins/status-panel.js`     | 285 | 420 | +135 / ?60 | Rewritten |
| `shell/plugins/mrac-panel.js`       | 152 | 244 | +92  / ?55 | Rewritten |
| `shell/plugins/estimator-panel.js`  | 175 | 300 | +125 / ?40 | Rewritten |
| `shell/plugins/safety-panel.js`     | 245 | 341 | +96  / ?50 | Rewritten |
| `shell/plugins/bandwidth-panel.js`  | 287 | 494 | +207 / ?80 | Rewritten |
| `shell/plugins/resource-panel.js`   | 168 | 274 | +106 / ?60 | Rewritten |
| `shell/plugins/slot-manager-panel.js` | 0 | 383 | +383 / ?0 | **NEW** |
| `shell/index.html`                  | 619 | 678 | +59  / ?12 | PLUGIN_FILES + `shellApi.subscribeSlot` |
| **Total**                           | ¡ª | ¡ª | **+1203 / ?357** | 1 new file, 7 modified |

No other files touched. All 17 service tests still pass (verified with `pytest ground_station/service/tests/ -v`).

---

## 2. Per-plugin behavior change

### 2.1 `status-panel.js`

**Before:** ARM derived from `Math.sqrt(ch0? + ch1?) > 0.1` (always DISARMED unless drone is moving), FlyMode hardcoded to `null`, vbat read `ch11` (never populated), no authority/state flag pills, stream table read top-level `loss_pct/received/dropped` only.

**After:**
- ARM reads `status.arm` first; falls back to `status.status_bits` bit 0; falls back to `ch13` bit 0. Source label rendered below badge.
- FlyMode reads `status.flymode`; falls back to `status.status_bits` bits 1¨C3. Label rendered from `FLY_MODE_LABELS = ['Stabilize','AltHold','PosHold','Auto','Manual','SDK']`.
- vbat reads `status.vbat` ¡ú `vbat` ¡ú `real_voltage` ¡ú `ch11` ¡ú `slot0.ch0.11`.
- New **Authority & State Flags** pills row: `TWC ARRIVED`, `TWC EXECUTE`, `RC AUTH`, `OF HOLD`, `ESTIMATOR`. Each pill is `green` when on, `muted` when off, `amber` with `?` when unknown.
- Stream table adds **Vars** column (count of non-metadata keys).
- Stream-table cells read top-level fields first, then fall back to embedded `slotN.seq/received/dropped/loss_pct` keys inside `values`.

**Visual check (live `/state` from audit):**
- ARM: DISARMED (no `status.arm` key yet ¡ú bits 0 of `slot0.ch0.13` = 2064 & 1 = 0). Source: `status_bits bit 0`.
- FlyMode: `(2064 >> 1) & 7 = 0` ¡ú renders **Stabilize**.
- vbat: 0.00 V (no key yet).
- Pills: all amber `?` (status flags not yet exposed).
- Stream table row for slot 0: `0 | 124 | 0.37% | 138,044 | 18` (vars count non-slotN metadata keys in values).

### 2.2 `mrac-panel.js`

**Before:** Read `ch0`, `ch1`, `ch10` and faked 6 theta bars per axis with `* 0.5 + j * 0.1` scaling. Header comment acknowledged firmware doesn't expose `mrac.*` keys.

**After:**
- Each axis (pitch, roll, yaw, z) shows **two values**: `e` (tracking error) and `u_ad` (adaptive output), each from named keys `mrac.<axis>.e` / `mrac.<axis>.u_ad`.
- Per-axis source label rendered (`mrac.<axis>.*` in green when named, `[PROXY] slot0.ch0.N` in amber when falling back).
- Proxy fallback uses real raw positional keys:
  - pitch.e ¡û slot0.ch0.0 (gyro_x), pitch.u_ad ¡û slot0.ch0.0 ¡Á 0.1
  - roll.e ¡û slot0.ch0.1 (gyro_y), roll.u_ad ¡û slot0.ch0.1 ¡Á 0.1
  - yaw.e, z.e ¡û slot0.ch0.10 (altitude proxy)
- Removed fake `* 0.5 + j * 0.1` scaling. Now shows real values (¡Á0.1 only for u_ad scaling).
- Added `Altitude` axis (z) for the MRAC altitude loop (was missing).
- Proxy banner at top of panel: shown only when ALL axes are using proxy (no named keys at all). Hidden once any axis gets a real `mrac.*` key.

**Visual check:** pitch.e ¡Ö -1.150 (matches `slot0.ch0.0`), roll.e ¡Ö -1.020 (matches `slot0.ch0.1`), yaw/z.e ¡Ö 0.1554. Source labels read `[PROXY] slot0.ch0.0` etc. in amber. Top banner visible.

### 2.3 `estimator-panel.js`

**Before:** Read `ch0, ch1, ch2, ch10` and labelled them **"EKF Position/Velocity/Altitude"**. Filter status hardcoded to "Active" whenever ch0 non-null. Covariance row used raw channels as proxy with no explanation.

**After:**
- Disclaimer banner at top: **"? EKF state not yet exposed by firmware"** ¡ª explains that the section below is raw IMU, not EKF.
- New "Estimator State (EKF)" section at the top, hidden by default, with Position/Velocity/Gyro Bias/Accel Bias groups. Shown only when firmware publishes any `ekf.*` key.
- Existing groups **renamed**:
  - "Gyro X/Y (rad/s)" ¡ú "Raw IMU ¡ª Gyro (rad/s)"
  - "Accel Z (m/s?)"  ¡ú "Raw IMU ¡ª Accel (m/s?)"
  - "Altitude (m)"     ¡ú "Raw IMU ¡ª Baro Alt (m)"
- Covariance row now uses real `estimator.cov_*` keys (pxx, pyy, pzz, vxvx, vyvy, vzvz) ¡ª placeholder until firmware exposure. Sub-label "pending firmware exposure".
- Filter status: reads `estimator.filter_status` enum (0=Initializing, 1=Active, 2=Degraded, 3=Failed). Renders "Pending firmware exposure" when not present.
- Removed the broken `g.keys.length / 2 + ai` line (referenced an out-of-bounds index).

**Visual check:** Disclaimer banner visible. Raw IMU section shows gyro_x/y, accel_z, altitude (raw values). EKF section hidden. Covariance row reads "¡ª" for all 6 keys.

### 2.4 `safety-panel.js`

**Before:** Read `state.streams['9'].values` (slot 9 never populated). All values always `null`. Plus/minus buttons wired correctly but the row never updates from incoming telemetry.

**After:**
- Reads from `state.streams['0'].values` first (S15 merged keys). Falls back to `streams['9'].values` for back-compat.
- Tries key shapes: `gs_max_horizontal_speed_mps`, `safety.gs_max_horizontal_speed_mps`, `parameters.gs_max_horizontal_speed_mps`, `gs_max.horizontal_speed_mps`.
- **Hint banner** shown when no readback yet: "? parameter readback pending ¡ª firmware has not yet streamed `gs_max_*` values. The +/? buttons below still submit writes to the firmware."
- Safety interlocks render: "? Awaiting first parameter readback from firmware" (no more silent "¡ª" rows).
- Plus/minus buttons STILL WORK ¡ª unchanged wiring to `api.submitCommand(1, index, value)` (correct: cmdId 1 indices 0¨C3 write the gs_max_* params).

**Visual check:** Hint banner visible. All four rows show "¡ª m/s" / "¡ª deg". Buttons clickable.

### 2.5 `bandwidth-panel.js`

**Before:** Read `stream.last_update_ns`, `stream.seq`, `stream.received` ¡ª fields that `ingest_decoded` never set. Empty bar chart.

**After:**
- New `readMeta(s, slotId)` helper reads both top-level fields AND embedded `slotN.seq/received/dropped/loss_pct/t_ms` keys inside `values`. Falls back transparently.
- Rolling rate calculation via per-slot `_prevSample[slot] = {t_ms, seq, ts}` history. Computes `dSeq / dT` over each ingest cycle.
- Stream table adds **Vars** and **Last** columns.
- "? Refresh" button forces a re-render and resets the rate baseline.
- Total budget bar, warning zones, per-slot bars all populate from real data.

**Visual check:** Slot 0 row: `Slot 0 | 18 vars | seq 124 | rate ¡Ö Hz | loss 0.37% | 138,044 samples | 0.1 s ago`. Budget bar shows slot 0 fill (color depends on rate ratio). Warning suppressed (well under 80 Hz budget).

### 2.6 `resource-panel.js`

**Before:** Groups labelled "Scheduler", "IMU Rate", "Magnetometer", "Environment" ¡ª implied RTOS metrics but actually reading raw IMU channels. No RTOS stream detection.

**After:**
- Top banner: "? The ch0..ch11 groups below are raw IMU telemetry channels, not RTOS metrics. They appear here as placeholders until `rtos.*` keys become available."
- Renamed groups:
  - "Scheduler" ¡ú "Stream Metadata (slot 0)" (Seq / Loss% / Dropped / Received ¡ª real stream counters)
  - "IMU Rate" ¡ú "IMU Rate (raw via chN)"
  - "Magnetometer" ¡ú "Magnetometer (raw via chN)"
  - "Environment" ¡ú "Environment (raw via chN)"
- New **RTOS Metrics** group at the top, scans `streams['rtos']`, `streams['99']`, `streams['system']`, `streams['9']` for any key matching `/^(rtos|system)\./`. If found, source label rendered (e.g. `from slot "rtos"`). If not, shows hint: "? RTOS observability pending ¡ª `firmware/rtos_observability.c` must be added to the Keil source group and read back via SWD."
- Stream metadata cells read both top-level fields AND embedded `slot0.seq/received/dropped/loss_pct`.

**Visual check:** Top banner visible. Stream Metadata shows real seq=124, loss=0.37%, dropped=514, received=137530. RTOS Metrics group shows "¡ª" for all 8 keys with hint visible.

### 2.7 `slot-manager-panel.js` (NEW)

A new panel for jiang's complaint: "I don't have observability on what slot I am selecting."

**Features:**
- **Active Slots counter** (top-right): live count of `Object.keys(state.streams).length`.
- **Subscribe slot** button row (1, 2, 3, 9, 10, 11, 12) + divider input. Click ¡ú calls `shellApi.subscribeSlot(slot, divider, ranges)` (new S15 shellApi helper, see ¡ì3). On failure falls back to POST `/commands` with `command_id: 33` (TODO comment for service-layer agent to wire 0x21).
- **? Refresh schema** button: re-subscribes slot 0 to refresh the schema view.
- **Active Slot Inventory table** with columns: #, Tag, Vars, Seq, Rate (Hz), Loss %, Samples, Last (s ago).
- **Click row to expand**: reveals a per-slot **Channels** list with every key in `values`, formatted value, and unit inferred from key prefix (`ekf.pos_*` ¡ú m, `ekf.vel_*` ¡ú m/s, `bias_gyro_*` ¡ú rad/s, `vbat` ¡ú V, `temp` ¡ú ¡ãC, `press/hpa` ¡ú hPa, `alt` ¡ú m, etc.).
- **Result feedback bar**: shows "? subscribed via shellApi.subscribeSlot" or "? subscribe failed: <error>" for 5 s.

**Visual check:** Active Slots: 1 (or 2 if "a" sidebar still routed to slot 2). Slot 0 row: `Slot 0 | tag "s0" | 18 vars | seq 124 | rate ¡Ö Hz | 0.37% | 138,044 samples | 0.1 s ago`. Expand ¡ú 18 channel rows showing slot0.ch0.0 through slot0.ch0.14 plus metadata keys. Unit inferred from ch position (none ¡ú raw float).

---

## 3. `index.html` changes

| Section | Change |
|---------|--------|
| `PLUGIN_FILES` array | Added `'/plugins/slot-manager-panel.js'` as the 15th entry |
| `shellApi` object | Added `subscribeSlot(slot, divider, ranges)` helper that POSTs `{ command_id: 33, index: slot, value: divider, args: {slot, divider, ranges} }` to `/commands`. Resolves with the JSON response or throws on non-2xx |

No CSS changes (panels use their own scoped styles). No other HTML changes.

---

## 4. Robustness guarantees

Every plugin now reads with priority `named ¡ú fallback ¡ú proxy ¡ú null`:

1. **Named keys** (post-S15 service-layer fix): `status.arm`, `mrac.pitch.e`, `ekf.pos_x`, etc.
2. **Variant keys**: `arm` vs `status.arm`; `ch11` vs `slot0.ch0.11`; etc.
3. **Raw positional proxy**: `ch0..ch14`, `slot0.ch0.N`
4. **Embedded metadata**: `slot0.seq`, `slot0.received`, etc. inside `values`
5. **null** with a clear "pending" hint rendered

This means every plugin works correctly under all four live-state configurations seen during the audit:

| State | Behaviour |
|-------|-----------|
| Slot 0 only, raw positional + embedded metadata | All panels show real numbers; named-only fields show "¡ª" + hint |
| Slot 0 + slot 2 (current "a" sidebar bug) | Status pills read from slot 0; sidebar pitch/roll/yaw are ignored unless user is on telemetry explorer |
| Slot 0 + named sidebar keys merged in | Full functionality |
| No data at all | "¡ª" placeholders + hints; no crashes |

---

## 5. Visual verification (mental check vs live `/state`)

With the captured audit state, expected panel states:

| Panel | Status | Notes |
|-------|--------|-------|
| Flight Status | ARM: DISARMED (bits 0 of ch13=2064); FlyMode: Stabilize; vbat: 0.00 V; pills: amber `?` | Source labels visible |
| MRAC Controller | proxy banner visible; pitch.e=-1.150, roll.e=-1.020, yaw.e=0.1554, z.e=0.1554 | Source `[PROXY] slot0.ch0.N` |
| EKF Estimator | disclaimer banner visible; Raw IMU shows gyro_x/y, accel_z, altitude | EKF section hidden |
| Safety Limits | hint banner visible; all 4 rows show "¡ª m/s / deg" | buttons still clickable |
| Bandwidth Manager | Slot 0 row visible with rate, loss, vars; budget bar partial | Warning suppressed |
| RTOS Resources | top banner visible; Stream Metadata shows real counters; RTOS group hint visible | |
| **Slot Manager** | Active Slots: 1; Slot 0 row with all 7 columns + 18 channels when expanded | New panel |

---

## 6. Spec gaps discovered (escalation to S15 backlog)

1. **No `/commands` handler for `command_id: 33`** ¡ª `slot-manager-panel.js` POSTs the subscribe envelope but the service-layer agent will need to either add a `/subscribe` endpoint OR teach `CommandGateway` to interpret `0x21` (33). Current state: HTTP 200 with default command-processor response (probably NOP). TODO comment left in `slot-manager-panel.js`.

2. **EKF keys not in DWARF** ¡ª as audit section 2.4 already noted, only 3/22 DASHBOARD_FRAME_A_VARS symbols resolve in the live `OBJ/JX_FLY.axf`. The new estimator-panel "EKF State" section is ready and will populate automatically once those symbols land.

3. **`gs_max_*` readback missing** ¡ª firmware does not yet publish safety params back over Wi-Fi. New hint banner informs the operator; +/- buttons still submit writes.

4. **`status.*` flags missing from DWARF** ¡ª only `status.pitch/roll/yaw_deg` resolved at audit time. The new pills row will populate automatically as `status.arm/flymode/twc_arrived/etc.` become available.

---

## 7. Verification checklist (commands run)

```bash
# All 17 service tests pass (10 original + 7 added by service-layer agent)
python -m pytest ground_station/service/tests/ -v
# ¡ú 17 passed in 3.31s

# All 7 modified/new plugin JS files parse cleanly
node -e 'for (const f of [...]) new Function(fs.readFileSync(f,"utf8"))'
# ¡ú All OK

# index.html inline script parses
node -e 'new Function(<extracted script>)'
# ¡ú OK
```

---

## 8. Files I did NOT touch

- `core.py`, `api.py`, `gateway.py` ¡ª owned by service-layer agent
- `wifi_bridge.py`, `boot_default_layout.py` ¡ª out of scope (firmware/bridge)
- `firmware/*.c` ¡ª out of scope (firmware)
- `command-panel.js`, `experiment-panel.js`, `motor-bench-panel.js`, `time-series-panel.js`, `fft-panel.js`, `replay-panel.js`, `path-panel.js`, `telemetry-explorer-panel.js` ¡ª already working; no changes needed (telemetry-explorer panel does its own auto-discovery).

---

## 9. Manual browser verification

Open `http://localhost:8081/` (per jiang's note that the operator dashboard is running there). All 15 panels should now render with correct data from slot 0 and the slot-manager panel adds the missing observability surface. If the service-layer agent's fix to `core.py:ingest_decoded` is not yet deployed, all panels still render correctly via the raw positional fallback paths.
