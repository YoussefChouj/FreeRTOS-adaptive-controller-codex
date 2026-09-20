# S15 Completion Report — Dashboard Service Layer Fixes

**Date:** Thursday Sep 17, 2026  
**Author:** S15 completion pass  
**Scope:** ground-station Python + JS layer; firmware excluded  

---

## Summary of findings

All eight files were already fixed in the codebase (prior S15 work). The Python compile-check passes cleanly. The only remaining open items are firmware-side DWARF symbol gaps documented as `# TODO(firmware)` comments in `boot_default_layout.py`.

---

## File-by-file audit

### 1. `ground_station/service/api.py`

**Status:** ✅ Already correct

- `POST /commands` at L97–108: routes `command_id` → `service.submit_command()` → FC 0xCC 0xDD. This is correct for PID gains and other standard commands.
- `POST /subscribe` at L207–228: dedicated endpoint for the 0x21 SUBSCRIBE envelope. Body is `{slot, divider, ranges}` → `service.bridge.subscribe_slot()`. Correctly falls back with 503 if `service.bridge` is None.

```213:27-213:37:ground_station/service/api.py
            # POST /subscribe — typed 0x21 envelope (slot subscription).
            # The /commands endpoint routes command_id 33 (0x21) through the
            # generic submit_command path, which sends a 0xCC 0xDD frame the
            # firmware does not recognise. The 0x21 envelope is built and
            # shipped by WifiBridge.subscribe_slot() instead. Slot 0 is
            # wired today (via _request_slot0_schema); slots 1-3 are TODO.
            elif self.path == "/subscribe":
```

### 2. `ground_station/comm/wifi_bridge.py`

**Status:** ✅ Already correct

- `subscribe_slot(slot, divider, ranges, transport)` at L531–612: public method matching the `api.py` `/subscribe` contract. Slot 0 falls back to `_request_slot0_schema(layout="dashboard")`. Slots 1–3 require explicit `ranges`. Divider 0 = stop. Validates slot 0–3 and divider 0–255. Backward-compatible: `_request_slot0_schema` is unchanged and still works.
- `_slot0_to_sidebar(names, values)` at L1039–1109: maps raw DWARF names to sidebar keys. Already extended with MRAC theta (24 entries, 4 axes × 6 theta elements) and EKF bias/velocity (9 entries). Status flags and MRAC e/u_ad were already correct.

```531:531-531:531:ground_station/comm/wifi_bridge.py
    def subscribe_slot(
```

### 3. `docs/dashboard-platform/shell/index.html`

**Status:** ✅ Already correct

- `subscribeSlot` at L494–510: POSTs `{slot, divider, ranges}` to `/subscribe` (not `/commands`). Comment correctly documents the 0x21 wire distinction.

```494:505:docs/dashboard-platform/shell/index.html
    subscribeSlot: function (slot, divider, ranges) {
      var body = {
        slot:    slot,
        divider: divider || 1,
        ranges:  ranges || []
      };
      return fetch('/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
```

### 4. `docs/dashboard-platform/shell/plugins/safety-panel.js`

**Status:** ✅ Already correct

- Header comment (L3–15): documents `cmdId 9` correctly.
- All four `SAFETY_PARAMS` entries (L26, L39, L52, L65): `cmdId: 9` in each.
- Comment at L14–15 correctly states: *"cmdId was previously 1 (PID Gain) — that wrote a PID Kp instead of a safety limit, which silently corrupted the rate controller (see COMMAND_SPEC.md L185)."*

### 5. `ground_station/comm/boot_default_layout.py::DASHBOARD_FRAME_A_VARS`

**Status:** ⚠️ Symbol availability unverified

The tuple is already extended with MRAC theta and EKF state symbols. Verified symbol status against `ground_station/livewatch/manifests.yaml`:

| DWARF path | In manifests? | Size | Status |
|---|---|---|---|
| `mrac_state.<axis>.Theta[0]`–`[2]` | Yes (3 per axis) | 4 B | ✅ Verified in manifests.yaml:888–158 |
| `mrac_state.<axis>.Theta[3]`–`[5]` | **No** | 4 B | ⚠️ TODO(firmware): MAX_NUM_BASIS may be 3 in current firmware; increase to 6 or add aliases |
| `s_ekf.x[0]`–`x[8]` | Partial (`x[3]`–`x[5]` confirmed) | 4 B | ⚠️ TODO(firmware): expose `x[0]`–`x[2]` (velocity) and `x[6]`–`x[8]` (gyro bias) |
| `s_ekf.active` | Yes (manifests.yaml:424) | 4 B | ✅ |
| `s_ekf.P[0]`–`P[8]` | Yes (manifests.yaml:196–204) | 4 B | ✅ |

The existing TODO comments at the bottom of `DASHBOARD_FRAME_A_VARS` are accurate and correctly placed.

### 6. `ground_station/comm/wifi_bridge.py::_slot0_to_sidebar`

**Status:** ✅ Already correct

All MRAC theta and EKF mapping keys are present. No changes needed.

### 7. `docs/dashboard-platform/shell/plugins/mrac-panel.js`

**Status:** ✅ Already correct

- `readTheta(values, axis)` at L74–83: reads `mrac.<axis>.theta_0` through `mrac.<axis>.theta_5` (6 elements).
- `onState` at L155–195: checks `named` source and falls back to gyro proxy with `[PROXY]` badge.

```74:74:docs/dashboard-platform/shell/plugins/mrac-panel.js
  function readTheta(values, axis) {
    return THETA_N.map(function (n) {
      var key = 'mrac.' + axis + '.theta_' + n;
      return {
        value:  (values && values[key] != null) ? values[key] : null,
        source: (values && values[key] != null) ? 'named' : 'none',
      };
    });
  }
```

### 8. `docs/dashboard-platform/shell/plugins/estimator-panel.js`

**Status:** ✅ Already correct

- `EKF_GROUPS` at L27–55: reads `ekf.vel_x/y/z`, `ekf.bias_accel_x/y/z`, `ekf.bias_gyro_x/y/z`.
- `ekf.pos_x/y/z` keys in Position group return "—" until firmware exposes them (correct behavior).
- Disclaimer banner correctly hidden when EKF data arrives.

---

## Compile check result

```
python -m py_compile ground_station/service/api.py ground_station/comm/wifi_bridge.py ground_station/comm/boot_default_layout.py
→ EXIT:0 (no errors)
```

---

## Firmware TODO items for jiang

The following changes require firmware/Keil action (no firmware files were modified per the task constraint):

### High priority

| # | Symbol | Action needed | File(s) to touch |
|---|---|---|---|
| 1 | `mrac_state.<pitch/roll/yaw/z_rate>.Theta[3..5]` | Ensure `MAX_NUM_BASIS = 6` in `mrac_adaptive_layer.c` and that DWARF exposes all 6 elements. Currently `manifests.yaml` only lists `Theta[0..2]` | `firmware/mrac_adaptive_layer.c`, `firmware/API/subscribe.c` |
| 2 | `s_ekf.x[0..2]` (velocity) | Add to DWARF: `s_ekf.x[0]` = vx, `x[1]` = vy, `x[2]` = vz. 9-state EKF currently exposes `x[3..5]` but not velocity. | `firmware/API/ekf.c` or `firmware/TASK/send_data.c` |
| 3 | `s_ekf.x[6..8]` (gyro bias) | Add to DWARF: `s_ekf.x[6..8]` = b_g_x/y/z. Currently not exposed. | `firmware/API/ekf.c` or `firmware/TASK/send_data.c` |
| 4 | `ekf.pos_x/y/z` | Add a 12-state position-aware EKF variant or three scalar aliases. The 9-state model has no position state. | `firmware/API/ekf.c` |
| 5 | `estimator.filter_status` | Expose `Ekf9_t.active` as a scalar DWARF alias `estimator.filter_status`. Currently accessible as `s_ekf.active` in manifests but not aliased. | `firmware/TASK/send_data.c` |

### Lower priority (covariance diagonal)

| # | Symbol | Action needed | File(s) to touch |
|---|---|---|---|
| 6 | `estimator.cov_pxx/pyy/pzz/vxvx/vyvy/vzvz` | Expose 6 diagonal elements of `Ekf9_t.P[81]` as scalar aliases. `s_ekf.P[0..8]` are in manifests but the panel wants named covariance keys. | `firmware/TASK/send_data.c` |

### Slot 1–3 explicit subscription (lower priority)

Slots 1–3 in `WifiBridge.subscribe_slot` require explicit `ranges`; there is no per-slot default. When jiang is ready to define layouts for those slots, the API will be ready.

---

## Verification checklist

| Item | Status |
|---|---|
| `api.py` `/subscribe` endpoint present | ✅ |
| `WifiBridge.subscribe_slot` method present | ✅ |
| Slot 0 → `_request_slot0_schema(layout="dashboard")` | ✅ |
| Slot 1–3 → explicit `ranges` (raises if absent) | ✅ |
| `index.html` `subscribeSlot` → `/subscribe` | ✅ |
| `safety-panel.js` `cmdId: 9` (all 4 entries) | ✅ |
| `mrac-panel.js` reads `mrac.<axis>.theta_0..5` | ✅ |
| `estimator-panel.js` reads `ekf.*` keys | ✅ |
| `_slot0_to_sidebar` has all MRAC theta + EKF mappings | ✅ |
| `DASHBOARD_FRAME_A_VARS` extended with theta + EKF | ✅ |
| Python compile check | ✅ EXIT 0 |
| `_TAG_TO_SLOT` in `core.py` unchanged | ✅ |
| Backward compat: `_request_slot0_schema` unchanged | ✅ |
