# Workflow B interfaces (v2, binding)

Spec: `.agent-ops/grill-autonomous-flight-loop.md`. Facts: `docs/workflow-b/facts-firmware.md`, `facts-gs.md`.
Every task codes against this file. A change here is a supervisor ruling, not a worker decision.

Every number marked PROPOSED is a starting value chosen at design time. None is measured. The operator sets
the final values before the first flight.

## 0. Conventions

- Wire units: metres, degrees, seconds, volts. World frame = the frame of `Reset_World_Origin`
  (`TASK/StabilizerTask.c:203-226`); origin is set on the ARM edge.
- Firmware internal units differ: `TWC.target_x/y` and `locx/yPID` are cm, Z is m (`facts-firmware.md`).
  The integration layer converts (m x 100 -> cm). The pure-C modules below use metres only.
- Hover point = (0, 0, `hover_z`) in the world frame. Every trajectory starts and ends there.
- Firmware style: Keil ARMCC V5, declarations at block top, no VLAs, `/* */` comments, no `malloc`.
- New firmware symbols use the prefix `wfb_` / `WFB_`.

## 1. Commands

Frame (unchanged parser, `BSP/usart5.c:419-431`): `CC DF 01 flags txid_LE(2) cmd idx 04 00 float32_LE xor`,
15 bytes, xor over bytes [2..13]. Reply: `AA BB` result frame, ACK=0 / REJECTED=1 / APPLIED=2
(`firmware/command_protocol.h:19-21`). A result frame carries no reason; the reason is `g_wfb_status.last_err`.

Integers travel as numeric floats (exact below 2^24). No payload carries raw bit patterns.

### CMD 0x1A: flight primitives

| idx | Name | Payload | Accepted when | Effect |
|---|---|---|---|---|
| 0 | TAKEOFF | ignored (send 0) | armed, motors at idle, SBUS live, prim state IDLE, no safety trip | same path as the ch7 auto-climb (`StabilizerTask.c:1166-1176`) with target (0, 0, `hover_z`); sets `gs_flight_active` |
| 1 | LAND | ignored | airborne | stop any trajectory, return to the hover point (rate-limited), settle, then the existing descent and touchdown auto-disarm. Timeout -> descend in place. RC ch5 calls the same function |
| 2 | HEARTBEAT | ignored | always | resets the heartbeat age |
| 3 | SET_HOVER_Z | hover_z in m | prim state IDLE, value in [`WFB_HOVER_Z_MIN`, `WFB_HOVER_Z_MAX`] | stores `hover_z`; boot default = the existing ch7 value 0.5 m |

### CMD 0x1B: trajectory

| idx | Name | Payload | Accepted when | Effect |
|---|---|---|---|---|
| 0 | BEGIN | point count N | traj state not EXECUTING, 2 <= N <= `WFB_TRAJ_MAX_POINTS`, N integral | clears the buffer, state LOADING |
| 1 | APPEND | next float | state LOADING, fewer than 5N floats received, value finite | appends; float order per point: x, y, z, yaw, t |
| 2 | CRC_HI | CRC32 >> 16 | state LOADING, 0 <= value <= 65535, integral | stores the high half |
| 3 | COMMIT | CRC32 & 0xFFFF | state LOADING, CRC_HI received, exactly 5N floats received | runs the checks below; pass -> READY, fail -> EMPTY |
| 4 | START | ignored | state READY, prim state HOVER (settled at the hover point) | state EXECUTING; at the last point -> DONE, vehicle holds the hover point |
| 5 | STOP | ignored | state EXECUTING | state READY; vehicle returns to the hover point (rate-limited) and holds |
| 6 | CLEAR | ignored | state not EXECUTING | state EMPTY |

COMMIT checks, in this order (first failure sets `last_err`):
1. CRC32 (IEEE 802.3, reflected poly 0xEDB88320, init 0xFFFFFFFF, final xor 0xFFFFFFFF) over the 20 x N
   bytes of the buffer as stored little-endian. Host equivalent: `zlib.crc32(struct.pack('<%df' % (5*N), ...))`.
2. `t[0] == 0` and t strictly increasing.
3. Every point inside the envelope: |x| <= 1.3 m, |y| <= 1.7 m, `WFB_TRAJ_Z_MIN` <= z <= 1.4 m (0.3 m inside the fence and ceiling), and
   |yaw_deg| <= 180 (a generator wraps headings to +/-180 deg before upload).
4. First and last point within `WFB_TRAJ_ENDPOINT_TOL` of the hover point (uses the current `hover_z`).
5. Every segment speed <= `WFB_TRAJ_V_MAX`.

Between samples the executor interpolates linearly in time. Yaw interpolates on the shortest arc.

Upload integrity: point count + CRC. On a COMMIT reject the uploader re-sends from BEGIN (3 attempts, then
fail). If the integration finds the txid reachable at the handler, a repeated txid equal to the last applied
APPEND is answered APPLIED without a second append.

CMD 0x1C and 0x1D stay unused.

### Constants (one `*_ROW` table per module, `docs/firmware-table-pattern.md`)

| Name | Value | Status |
|---|---|---|
| `WFB_TRAJ_MAX_POINTS` | 600 (12,000 B in CCM) | PROPOSED |
| `WFB_TRAJ_Z_MIN` | 0.3 m | PROPOSED |
| `WFB_TRAJ_V_MAX` | 1.0 m/s | PROPOSED |
| `WFB_TRAJ_ENDPOINT_TOL` | 0.10 m | PROPOSED |
| `WFB_HOVER_Z_MIN` / `_MAX` | 0.3 / 1.4 m (max = ceiling - soft margin) | PROPOSED |
| Fence | +/-1.6 m x, +/-2.0 m y around the ground-centre origin | operator, 2026-10-03 launch grill |
| Ceiling | 1.7 m | operator, 2026-10-03 launch grill |
| Soft boundary (trajectory x_abs / y_abs / z_max) | 1.3 / 1.7 / 1.4 m = fence and ceiling - 0.3 m | PROPOSED |
| Fence push-back | hold 2.0 s, over 0.3 m, target 0.3 m inside | PROPOSED |
| Airborne cap | 120 s | spec Q5 |
| Heartbeat timeout | 1.0 s (GS sends at 5 Hz) | PROPOSED |
| Low-voltage backstop | 14.0 V held 3 s | PROPOSED, operator sets |
| Crash tilt | 60 deg held 0.2 s | PROPOSED, operator sets |
| Return leg XY rate | 0.3 m/s | PROPOSED |
| Settle | within 0.15 m for 1.0 s | PROPOSED (0.15 m = existing arrival distance) |
| Return timeout | 10 s | PROPOSED |

## 2. Firmware status: `g_wfb_status`

One global `wfb_status_t g_wfb_status` placed in section `MRAC_CCM`, read by DWARF name through the existing
subscribe stream (float32 values, `facts-gs.md:12`). Every field is `float` so the stream needs no new type.

| Field | Meaning |
|---|---|
| `prim_state` | `wfb_prim_state_t` value |
| `traj_state` | `wfb_traj_state_t` value |
| `traj_n` | committed point count |
| `traj_rx` | floats received since BEGIN |
| `traj_crc_hi`, `traj_crc_lo` | halves of the CRC the firmware computed at COMMIT |
| `traj_t` | seconds since START |
| `last_err` | `wfb_err_t` of the last rejected 0x1A/0x1B command; 0 after an applied one |
| `safety_trip` | `wfb_trip_t`, latched until disarm |
| `hb_age` | seconds since the last HEARTBEAT |
| `gs_flight_active` | 1 from TAKEOFF accepted until disarm |
| `hover_z` | current hover height, m |
| `airborne_t` | seconds airborne this flight |
| `fence_push` | `WFB_PUSH_*` bits being pushed back (1 x, 2 y, 4 z), 0 = none |

## 3. Pure-C firmware modules

No hardware headers, no globals, no FreeRTOS calls. Caller owns all state. Headers: `<stdint.h>`, `<string.h>`,
`<math.h>` only. Host tests in `tests/firmware_host/test_wfb_<name>.c`, built with

`gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement -Wvla -I API tests/firmware_host/test_wfb_<name>.c API/wfb_<name>.c -lm -o <scratch>/test_wfb_<name>`

A test prints `PASS <n>` and returns 0, or prints the failing check and returns 1. No binaries are committed.

### `API/wfb_types.h`

```c
typedef enum { WFB_ERR_NONE = 0, WFB_ERR_STATE, WFB_ERR_RANGE, WFB_ERR_COUNT, WFB_ERR_CRC,
               WFB_ERR_TIME, WFB_ERR_BOUNDS, WFB_ERR_ENDPOINT, WFB_ERR_SPEED, WFB_ERR_SAFETY } wfb_err_t;
typedef struct { float x_m, y_m, z_m, yaw_deg, t_s; } wfb_traj_point_t;   /* 20 bytes */
```

### `API/wfb_traj.h`

```c
typedef enum { WFB_TRAJ_EMPTY = 0, WFB_TRAJ_LOADING, WFB_TRAJ_READY, WFB_TRAJ_EXECUTING, WFB_TRAJ_DONE } wfb_traj_state_t;
typedef struct { float x_abs_m, y_abs_m, z_min_m, z_max_m, v_max_mps, endpoint_tol_m; } wfb_traj_limits_t;
typedef struct { wfb_traj_point_t *buf; uint16_t cap, n, seg; uint32_t rx; uint16_t crc_hi;
                 uint8_t crc_hi_set, state; uint32_t crc_calc; } wfb_traj_t;

void      wfb_traj_init(wfb_traj_t *tr, wfb_traj_point_t *buf, uint16_t cap);
wfb_err_t wfb_traj_begin(wfb_traj_t *tr, float n_points);
wfb_err_t wfb_traj_append(wfb_traj_t *tr, float v);
wfb_err_t wfb_traj_crc_hi(wfb_traj_t *tr, float hi);
wfb_err_t wfb_traj_commit(wfb_traj_t *tr, float crc_lo, const wfb_traj_limits_t *lim, float hover_z_m);
wfb_err_t wfb_traj_start(wfb_traj_t *tr);
wfb_err_t wfb_traj_stop(wfb_traj_t *tr);
wfb_err_t wfb_traj_clear(wfb_traj_t *tr);
int       wfb_traj_sample(wfb_traj_t *tr, float t_s, wfb_traj_point_t *out); /* 1 = running, 0 = finished (out = last point, state DONE) */
uint32_t  wfb_crc32(const uint8_t *data, uint32_t len);
void      wfb_traj_default_limits(wfb_traj_limits_t *out);   /* the section 1 constants */
#define WFB_TRAJ_MAX_POINTS 600u
```

Defined behaviour (settled during the build, binding for the integration and for the host `validate`):
- `commit` outside LOADING returns `WFB_ERR_STATE` and changes nothing. A `commit` that fails while LOADING
  leaves the state EMPTY. Order inside `commit`: state, CRC_HI present, float count, `crc_lo` range, then
  the five COMMIT checks of section 1.
- `crc_hi` and `crc_lo` reject 65536, -1, non-integers and NaN with `WFB_ERR_RANGE`.
- Every limit comparison is written in negated form (`!(v <= limit)`), so a NaN value or a NaN limit fails
  the check instead of passing it.
- `sample` in EMPTY, LOADING or READY returns 0 and does not write `*out`.
- The yaw wrap in `sample` has no loop; it relies on check 3 keeping stored yaw inside +/-180 deg.
- `begin` clears `cap` x 20 bytes (12 kB at 600 points); the integration calls it from the command handler,
  not from the 200 Hz loop.

### `API/wfb_safety.h`

```c
typedef enum { WFB_ACT_NONE = 0, WFB_ACT_LAND_VIA_HOVER, WFB_ACT_LAND_IN_PLACE, WFB_ACT_KILL } wfb_action_t;
typedef enum { WFB_TRIP_NONE = 0, WFB_TRIP_HEARTBEAT, WFB_TRIP_LOW_V, WFB_TRIP_AIRBORNE_CAP,
               WFB_TRIP_FENCE, WFB_TRIP_CEILING, WFB_TRIP_TILT } wfb_trip_t;
typedef struct { float fence_x_m, fence_y_m, ceiling_m, low_v, low_v_hold_s, tilt_deg, tilt_hold_s,
                 airborne_cap_s, hb_timeout_s, fence_hold_s, fence_over_m, soft_margin_m; } wfb_safety_limits_t;
typedef struct { float x_m, y_m, z_m, roll_deg, pitch_deg, vbat_v, hb_age_s, dt_s;
                 uint8_t airborne, gs_flight_active; } wfb_safety_in_t;
typedef struct { float low_v_t, tilt_t, airborne_t, fence_t; uint8_t trip, action, push; } wfb_safety_t;
#define WFB_PUSH_X 1u
#define WFB_PUSH_Y 2u
#define WFB_PUSH_Z 4u

void         wfb_safety_init(wfb_safety_t *s);
wfb_action_t wfb_safety_step(wfb_safety_t *s, const wfb_safety_limits_t *lim, const wfb_safety_in_t *in);
void         wfb_safety_default_limits(wfb_safety_limits_t *out);   /* the section 1 constants */
void         wfb_safety_push_sp(const wfb_safety_t *s, const wfb_safety_limits_t *lim, const wfb_safety_in_t *in,
                                float *x_sp_m, float *y_sp_m, float *z_sp_m);  /* pushed axes -> soft boundary */
```

Rules. Trip -> action: TILT -> KILL; FENCE, CEILING -> LAND_IN_PLACE; HEARTBEAT, LOW_V, AIRBORNE_CAP ->
LAND_VIA_HOVER. The action is latched and only escalates (KILL > LAND_IN_PLACE > LAND_VIA_HOVER) until
`wfb_safety_init`. `trip` keeps the first cause of the current action level. Nothing trips while `airborne == 0`.
HEARTBEAT and AIRBORNE_CAP apply only while `gs_flight_active == 1` (an RC-only flight is never landed by them).
TILT, FENCE, CEILING and LOW_V apply to every flight.
Fence push-back (2026-10-03): outside the fence or ceiling on a GS flight with action NONE, `push` holds the
`WFB_PUSH_*` bits of the axes out, the glue overrides those setpoints with `wfb_safety_push_sp` (fence or ceiling
minus `soft_margin_m`, on the drone's side) and freezes the trajectory clock, and `fence_t` counts. FENCE/CEILING
latch LAND_IN_PLACE only when still out after `fence_hold_s`, more than `fence_over_m` beyond, or with
`gs_flight_active == 0` (RC flight or pilot takeover: no GS setpoint to push with). Back inside clears `fence_t`
and `push`; a recovered push-back is not a trip.
Comparisons are in negated form, so a non-finite input trips its check. When several checks trip in the same
step, `trip` is the first in this order: TILT, FENCE, CEILING, LOW_V, HEARTBEAT, AIRBORNE_CAP.

### `API/wfb_prim.h`

```c
typedef enum { WFB_PRIM_IDLE = 0, WFB_PRIM_CLIMB, WFB_PRIM_HOVER, WFB_PRIM_TRAJ, WFB_PRIM_RETURN,
               WFB_PRIM_SETTLE, WFB_PRIM_DESCEND } wfb_prim_state_t;
typedef struct { float hover_z_m, xy_rate_mps, settle_radius_m, settle_time_s, return_timeout_s; } wfb_prim_cfg_t;
typedef struct { float x_m, y_m, z_m, dt_s; } wfb_prim_in_t;
typedef struct { float x_sp_m, y_sp_m, z_sp_m; uint8_t descend; } wfb_prim_out_t;
typedef struct { uint8_t state, land_after_return; float x_sp_m, y_sp_m, settle_t, return_t; } wfb_prim_t;

void      wfb_prim_init(wfb_prim_t *p);
wfb_err_t wfb_prim_takeoff(wfb_prim_t *p);                 /* IDLE -> CLIMB */
wfb_err_t wfb_prim_traj_begin(wfb_prim_t *p);              /* HOVER -> TRAJ */
wfb_err_t wfb_prim_traj_end(wfb_prim_t *p, const wfb_prim_in_t *in);  /* TRAJ -> RETURN, hold after */
wfb_err_t wfb_prim_land(wfb_prim_t *p, const wfb_prim_in_t *in);      /* any airborne state -> RETURN, land after */
wfb_err_t wfb_prim_land_in_place(wfb_prim_t *p);           /* any airborne state -> DESCEND */
void      wfb_prim_disarmed(wfb_prim_t *p);                /* -> IDLE */
void      wfb_prim_step(wfb_prim_t *p, const wfb_prim_cfg_t *cfg, const wfb_prim_in_t *in, wfb_prim_out_t *out);
void      wfb_prim_default_cfg(wfb_prim_cfg_t *out);       /* the section 1 constants, hover_z 0.5 m */
```

A module may add private fields to its state struct (`wfb_prim_t` carries `uint8_t descend_frozen`). Signatures,
enum values and the fields listed here do not change.

Behaviour. CLIMB: setpoint (0, 0, hover_z); -> HOVER after the settle condition holds. RETURN: the XY setpoint
starts at the current position and moves toward (0, 0) at `xy_rate_mps` (the firmware has no XY ramp,
`StabilizerTask.c:1271`), Z setpoint = hover_z; when the setpoint reaches (0, 0) -> SETTLE. SETTLE: settle
condition met -> DESCEND if `land_after_return`, else HOVER. RETURN + SETTLE together longer than
`return_timeout_s` -> DESCEND when `land_after_return`, else stay in SETTLE. DESCEND: `out->descend = 1`, XY
setpoint frozen; the existing landing code (`StabilizerTask.c:1226-1242`, 694-735) does the rest. In TRAJ the
module outputs nothing; the integration feeds `wfb_traj_sample` to the setpoints.

Defined behaviour. `land` and `land_in_place` are idempotent: `return_t` restarts only when landing is newly
added; from DESCEND both return `WFB_ERR_NONE`; `land` from IDLE returns `WFB_ERR_STATE`. A non-finite position
estimate never becomes a setpoint. The integration passes a constant `dt_s` (a NaN `dt_s` would stall the
settle and return timers) and converts the metre setpoints to cm for `TWC.target_x/y`.

## 4. Ground-station interfaces (Python)

All under `ground_station/`. Tests beside each module in `tests/test_<module>.py`. No module opens a socket
or a serial port at import. Transport is injected: `send(frame: bytes) -> int` returns the result code, so
every module is testable with `service/fake_drone.py`.

| Module | Public interface |
|---|---|
| `platform/wfb_commands.py` | `encode(cmd, idx, value, txid) -> bytes`; `class WfbClient(send)`: `arm()`, `idle()`, `takeoff()`, `land()`, `heartbeat()`, `set_hover_z(z)`, `traj_begin(n)`, `traj_append(v)`, `traj_commit(crc)`, `traj_start()`, `traj_stop()`, `traj_clear()`, `kill()`; each returns `bool` (APPLIED) |
| `service/trajectory_pipeline.py` | `TrajPoint(x, y, z, yaw_deg, t)`; `TrajLimits`; `Profile(v_cruise_mps, a_max_mps2, ds_m, hover_z_m, yaw_deg)`; `generate(shape, params, profile, limits) -> list[TrajPoint]` (shape -> tilt rotation -> fixed arc-length resample -> timing profile); `validate(points, limits, hover_z) -> list[str]` (the COMMIT checks on float32 values, tags COUNT, RANGE, TIME, BOUNDS, ENDPOINT, SPEED, every problem listed); `crc32(points) -> int` |
| `platform/trajectory_upload.py` | `upload(points, client, attempts=3) -> UploadResult(ok, attempts, error)` |
| `analysis/battery_model.py` | `PackRegistry.load(path)`; `predict_soc(pack_id, resting_v) -> float`; `record_flight(pack_id, soc_before, soc_after)`; `expected_drop(pack_id) -> float`; `next_flight_allowed(pack_id, resting_v, cooldown_s) -> (bool, reason)` (rest time met and predicted SoC minus the expected drop >= 30 %); `sag_critical(pack_id, loaded_v_filtered) -> bool`; packs in `analysis/packs.yaml` |
| `livewatch/campaign_capture.py` | `CAMPAIGN_SET` (locked variable list, needed + optional); `probe_max_rate(stream) -> float` (highest rate with zero loss); `write_manifest(session_dir, ...)` |
| `analysis/workflow_b_adapter.py` | `flight_rows(session_dir) -> list[dict]` (keys as `bench.run_rows`: at least `rmse`, `sat`); `score(rows) -> float` = `bench.objective(rows)` by import (`sys.path` insert of `sim/bench`); never a copy of the formula |
| `analysis/controller_descriptor.py` | `load(path) -> Descriptor(name, knobs, shadow_outputs)`; knob = `{symbol, cmd_id, idx, default, lo, hi, scale}`; files in `ground_station/analysis/controllers/<name>.yaml` |
| `analysis/tuner.py` | `class Tuner(descriptor, seed)`: `propose(history) -> dict[str, float]`, `record(params, J, valid)`; never reads a controller name |
| `service/abort_monitor.py` | `class AbortMonitor(limits)`: `step(sample) -> AbortDecision(level, reason)`; level 0 none, 1 stop and land, 3 campaign stop |
| `service/fake_drone.py` | `class FakeDrone`: `send(frame) -> int`, `status() -> dict` (the `g_wfb_status` fields), `step(dt)`; point-mass kinematics; same accept/reject rules as section 1 |
| `service/campaign_schema.py` | `load_campaign(path) -> Campaign`; raises `CampaignError` with every problem listed |
| `service/campaign_runner.py` | `class CampaignRunner(client, capture, analysis, tuner, battery, monitor, clock, decide=None)`: `open_battery(pack_id, checklist)`, `run_flight() -> FlightResult`, `pause()`, `land()`, `abort()`, `state` |
| `flashtool/code_gate.py` | `run_gate(change) -> GateResult(steps)` (the nine Q10c steps; refuses protected files) |
| `service/adb_recorder.py` | `class AdbRecorder(adb="adb")`: `start(session_dir)`, `stop() -> Path | None`; a missing `adb` is a warning, never a flight blocker |

Operator actions on the panel and over MCP: **Pause** = finish the current flight and do not start the next;
**Land** = `traj_stop()` then `land()`; **Abort** = `kill()` (motor kill, CMD 0x0D). The agent uses `land()`
for abort level 1. `kill()` stays an operator action; the firmware crash detect covers the fast case.

Arming by the agent needs all of: `allow_agent_arm` on (operator toggle), an open battery session with a
per-battery go, and live SBUS. The runner checks these and refuses otherwise.
