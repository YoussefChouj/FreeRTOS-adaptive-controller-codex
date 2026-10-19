# T6a Digest: MRAC Simplex + Runtime Variant Switch — Codebase Investigation

## Q1: MRAC W-hat (Theta) Adaptive Law Updates

**Variable names**: `state->Theta[MAX_NUM_BASIS]` (per-axis adaptive weights, i.e. W-hat).
L1-style filtered copy: `state->Whatf[MAX_NUM_BASIS]` (line 198 in mrac.h).

**Update function**: `MRAC_UpdateAxis()` in `API/mrac.c:164`.
- Main weight update (gradient descent + leakage): line 319: `state->Theta[i] += MRAC_DT * y;`
- L1-style Whatf update: line 338: `state->Whatf[i] += MRAC_DT * config->gam_f * (state->Theta[i] - state->Whatf[i]);`
- Projection (bounds Theta): lines 301-304, calls `MRAC_ProjectGradient()`
- Hard freeze (skip update, line 244): if `|e| > config->e_freeze`, sets `u_ad=0` and `return`.

**Call site**: `MRAC_Control()` in `API/mrac.c:546`, called from `TASK/StabilizerTask.c:879`.
- Per-axis calls: pitch `mrac.c:590`, roll `mrac.c:595`, yaw `mrac.c:600`, Z `mrac.c:604`.

**Loop rate / task**: `Stabilizer_Task` at **200 Hz** (5 ms period).
Defined by `MRAC_DT = 0.005f` in `API/mrac.h:119`.

---

## Q2: u_ad Computation, Injection, and Saturation

**u_ad computation** (`API/mrac.c`):
- Line 344-347: `raw_u_ad = Σ Theta[i] * Phi[i]` (dot product)
- Lines 349-355: If `ENABLE_PERFORMANCE_RECOVERY==1`, LPF: `u_ad += MRAC_DT * omega_u * (raw_u_ad - u_ad)`. Else `u_ad = raw_u_ad`.
- Lines 357-363: Saturation clamp: `u_ad = ±config->u_max`

**Existing saturation/clamp**: Yes — per-axis `config->u_max` at `mrac.c:357-363`.
- Pitch/roll: `u_max = 6.73863f` (`mrac.c:428,446` = `U_MAX_PITCH/Roll`)
- Yaw: `u_max = 2.027f` (`mrac.c:468` = `U_MAX_YAW`)
- Z: `u_max = 13.47726f` (`mrac.c:485` = `U_MAX_Z`)

**Injection into PID output** (`TASK/StabilizerTask.c:888-907`):
- Line 888-914: `#if ENABLE_MRAC_OUTPUT_INJECTION == 1` guards runtime shadow-mode gate.
- Lines 896-899: `mrac_z = mrac_state.z_rate.u_ad * mrac_config_z.mrac_to_mixer`, etc.
- Lines 904-907: `Throttle_out = Ctrler.Z_ratePID.U + Throttle_th + mrac_z`, `u_gyrox = Ctrler.gyroxPID.U + mrac_roll`, etc.
- Runtime gate: `mrac_flags.output_injection_on` (CMD 0x0F idx 10, default 0 = shadow mode).

---

## Q3: Gate / Mixture Weights (Gate Sum)

**No existing gate/mixture weights**. The codebase has no gate-sum constraint, no mixture weights, and no convex-combination selector for controller variants. The `mrac_to_mixer` values are fixed scaling constants (lines 33-41 of mrac.h), not runtime-varying gates.

---

## Q4: Controller-Variant Selector and Param Write Path

**Existing selectors** (all MRAC feature flags in `MRAC_FeatureFlags_t`, mrac.h:223-238):
- `adaptation_on` (0=off, 1=on) — CMD 0x0F idx 0
- `projection_on` — CMD 0x0F idx 1
- `hard_freeze_on` — CMD 0x0F idx 3
- `output_injection_on` (shadow mode) — CMD 0x0F idx 10
- `ref_model_type` (0=passthrough, 1=1st-order, 2=2nd-order) — CMD 0x13 idx 0
- Per-axis: `axis_enable_pitch/roll/yaw` — CMD 0x0F idx 7-9

**Param write path** (`TASK/send_data.c:1416`, `Process_GroundStation_Command()`):
- Command queue: `gs_cmd_queue[16]` with `{id, index, value, transaction_id, ...}`.
- Handled in a while-loop (line 1418). Each command has `id` (uint8), `idx` (uint8), `val` (float).
- Handler for MRAC feature flags: `mrac.c:1715-1733` (CMD 0x0F idx 0-12): writes directly to `mrac_flags.*` fields.
- Handler for MRAC config: `mrac.c:1476-1501` (CMD 0x02/0x05/0x08): writes `configs[axis]->gamma[elem]`, `What_limit`, `What_tol`.
- Handler for ref model type: `mrac.c:1746-1754` (CMD 0x13 idx 0): writes `mrac_flags.ref_model_type` + snaps reference states.

**Pattern for adding new params**: Add an `else if (id == <cmd>)` block, read `idx` and `val`, write to a struct/flag. Use critical section (`taskENTER_CRITICAL`/`taskEXIT_CRITICAL`) if writing shared state during flight.

---

## Q5: Attitude State for Envelope Check

**Available at MRAC_Control / StabilizerTask time**:

| Variable | Source | Units | Type |
|---|---|---|---|
| `imu_data.rol` | `API/imu_update.h:8`, `_imu_st` in `robot_types.h:348` | rad (Mahony) | `extern _imu_st imu_data` |
| `imu_data.pit` | same | rad | |
| `imu_data.yaw` | same | rad | |
| `current_state->gyroyPID.FB` | mrac.c:556 | rad/s (converted from deg/s × 0.0174533) | pitch rate |
| `current_state->gyroxPID.FB` | mrac.c:557 | rad/s | roll rate |
| `current_state->gyrozPID.FB` | mrac.c:558 | rad/s | yaw rate |
| `current_state->gyroyPID.Des` | mrac.c:572 | rad/s | pitch rate setpoint |
| `current_state->gyroxPID.Des` | mrac.c:573 | rad/s | roll rate setpoint |

**For state envelope check** (roll/pitch angle bounds): use `imu_data.rol`, `imu_data.pit` (available as extern global, updated by Mahony filter in `IMU_Update_Mahony()`).

---

## Q6: Tier-0 Files (per AGENT_MAP_SPEC.md)

Per `docs/dashboard-platform/AGENT_MAP_SPEC.md:51`, tier-0 files touched by Simplex/variant switch:
1. **`TASK/StabilizerTask.c`** — u_ad injection, mixer output, where Simplex would insert freeze/fade
2. **`API/mrac.c`** — `MRAC_UpdateAxis()`, `MRAC_Control()`, where Simplex would freeze W-hat, evaluate triggers
3. **`API/mrac.h`** — `MRAC_FeatureFlags_t`, `MRAC_AxisState_t`, `MRAC_AxisConfig_t` definitions

---

## Q7: Proposed Minimal Design (< 40 lines)

### New struct: `SIMPLEX_State_t`

```c
typedef struct {
    uint8_t  mode;              // 0=inactive, 1=active, 2=observe_only (default 0)
    uint8_t  trip_count;        // cumulative trip counter (published to telemetry)
    uint8_t  active_axis_mask;  // bitmask: bits 0-3 = pitch/roll/yaw/z
    uint8_t  trigger_reason;    // 1=state envelope, 2=w_norm, 3=u_ad saturated, 4=other
    uint32_t trip_timestamp_ms; // tick count at last trip
    uint32_t hold_ticks;        // hysteresis hold time in ticks (200 Hz)
    uint32_t last_freeze_tick;  // last tick W-hat was frozen
    float    fade_factor;       // 1.0→0.0 fade for u_ad (updated each tick)
    float    w_norm_limit;      // ||Theta|| limit per axis (trigger threshold)
    float    envelope_roll_max; // max roll angle (rad) for state envelope
    float    envelope_pitch_max;// max pitch angle (rad) for state envelope
    float    u_ad_sat_ms;       // u_ad saturated > N ms trigger threshold
} SIMPLEX_State_t;

extern SIMPLEX_State_t simplex_state;
```

### Insertion Points

**(a) Freeze W-hat** — `API/mrac.c:244` (after `if (mrac_flags.hard_freeze_on && ...)` block, before `return`):
Add: if `simplex_state.mode == 1 && simplex_state.active_axis_mask & (1<<axis_id)`, freeze `Theta[i]` (skip update), set `simplex_state.trip_count++`.

**(b) u_ad fade factor multiply** — `API/mrac.c:352-354` (after `state->u_ad` computation):
Add: `state->u_ad *= simplex_state.fade_factor;` when simplex active.
Fade: `simplex_state.fade_factor -= 0.05f` per tick (≈100 ms at 200 Hz). When reaches 0, W-hat freeze engages.

**(c) Trigger evaluation** — `API/mrac.c:183` (start of `MRAC_UpdateAxis`, before reference model):
Add block checking:
- `fabsf(imu_data.rol) > simplex_state.envelope_roll_max`
- `fabsf(imu_data.pit) > simplex_state.envelope_pitch_max`  
- `||Theta|| > simplex_state.w_norm_limit` (compute `sqrt(Σ Θ[i]^2)`)
- `u_ad` saturated for `> simplex_state.u_ad_sat_ms` (track `is_finite(u_ad)` and saturated flag)

**(d) Variant switch** — `TASK/StabilizerTask.c:888` (before `#if ENABLE_MRAC_OUTPUT_INJECTION` block):
Add: if `simplex_state.mode == 2` (observe_only), set `mrac_flags.output_injection_on = 0` for this tick, log would-be trip. If `simplex_state.mode == 1`, set `fade_factor`.

**(e) Trip counter telemetry** — `TASK/send_data.c` (append to existing MRAC telemetry frame, e.g. frame 0x03 or new field in frame 0x06):
Publish `simplex_state.trip_count`, `simplex_state.active_axis_mask`, `simplex_state.mode`, `simplex_state.fade_factor`.

### Default Values (all disabled = inert)
- `mode = 0` (inactive)
- `w_norm_limit = 999.0f` (never triggers)
- `envelope_roll_max = 3.14f` (180°, effectively no bound)
- `envelope_pitch_max = 3.14f`
- `u_ad_sat_ms = 999999`
- `hold_ticks = 0`
- `fade_factor = 1.0f`

### Runtime param writes (same pattern as existing CMD 0x0F)
Add to `Process_GroundStation_Command()` at `send_data.c:1755`:
- `CMD 0x14` simplex config: idx 0=mode, 1=w_norm_limit, 2=envelope_roll_max, 3=envelope_pitch_max, 4=u_ad_sat_ms, 5=hold_ticks
- `CMD 0x15` simplex control: idx 0=trigger trip, idx 1=reset counter
