# Demo Protocol: PID vs PID+MRAC Comparison

## 1. Purpose and Design
- **Purpose**: Demonstrate and compare flight performance between pure PID and PID+MRAC controllers.
- **Design**: 4 conditions (hover, hover with off-centre load, circle, figure8). Each condition is flown first with pure PID, then with PID+MRAC. Held equal: same battery pack, same preset `flight_test_adaptive`.

## 1b. Read first: corrections from a source check (2026-10-01; flashed 2478734)
- **MRAC injection is off at boot.** `CMD 0x1F` val 1 selects MRAC, but `mrac_flags.output_injection_on = 0`
  (API/mrac.c:648) keeps MRAC in shadow: the motors still get pure PID (API/controller.c:17). For an MRAC run also
  send `CMD 0x0F` idx 10 val 1; val 0 returns to shadow.
- **Before the first injected run**, lower MRAC authority: gamma `CMD 0x20+axis` or What_limit `CMD 0x24+axis`,
  idx = element (TASK/send_data.c:1538). Then re-check RMS(u_ad)/RMS(u_nom) < 0.5 in shadow (flight16 had
  1.09-2.19, docs/analysis/flight16-tuning-input.md:56-58), and set Simplex mode 1 (`CMD 0x19` idx 0 val 1).
- **Circle yaw: fixed in f8b9348.** Before it, the heading error read 360 deg from 1.5 laps and the yaw loop
  saturated; now ComputeYawPID wraps fully and the circle heading stays in [-180, 180). Any lap count is fine.
  Never set duration (idx 5) to 0: 0 runs until stop.
- **Units** (AutoflyTask.c:89,103-123; dt 0.005 s per 200 Hz call): center_x/y, radius and amplitude in cm,
  center_z in m, angular_speed in rad/s, duration in s.
- **Start point (2478734)**: every path starts where the drone is. The start command moves the centre so the first
  point is the current position, x, y and altitude (AutoflyTask_Start*, TASK/AutoflyTask.c), so center_x/y/z
  (idx 0-2) are overwritten and need not be sent. The circle centre ends up one radius in -x of the start point, the
  Bernoulli figure8 centre one amplitude in -x, the Gerono figure8 centre at the start point. Heading: the
  sinusoid and figure8 hold the current heading, and the circle turns one lap per lap from it.
- **Recording** (section 4) uses 8081 `POST /api/recording/start`: start 8081 and close VOFA (both use UDP 14550).
- **Image**: 2478734 was built, flashed and checked (`livewatch verify`: 0 mismatches) on 2026-10-01. Skip step 2
  unless the firmware changes again.

## 2. Morning Flash Step
```powershell
ah lock hw
UV4 -b -t JX_FLY -j0 JX_FLY.uvprojx
python -m ground_station.flashtool.rebuild_and_flash --force --yes
python -m ground_station.livewatch verify
```
A pass looks like: cold-boot init by default, and `livewatch verify` returning success without a stale ELF warning.

## 3. Props-off Bench Checklist
- [ ] **Controller Toggle**: Send `CMD 0x1F` (CTRL_SELECT) index `0` value `0` (PID) or `1` (MRAC) while disarmed.
  - *Observation*: `g_ctrl_select` (streamed in `flight_test_outer`, preset slot 2) shows the new value. While
    armed the request stays pending until disarm; an unavailable controller is refused and the request reverts
    (API/controller.c:47-56, ground_station/livewatch/manifests.yaml:1161).
- [ ] **MRAC Telemetry**:
  - *Observation*: Verify telemetry streams for preset `flight_test_adaptive` (dashboard_frame_a, inner_loops, flight_test_outer, flight_test_position).
- [ ] **Path preconditions**: 0x0A/0x0B/0x0C/0x11 are rejected (code 6) unless `FlyMode == FlyMode_SDK`
  (TASK/send_data.c:1421-1423). The FSM sets SDK in the armed and disarmed states and DangerousStop in EMERGENCY
  (API/flight_fsm.c:12-22), so a rejected path command on the bench means the FSM is in EMERGENCY.
- [ ] **Circle Path (Ground)**: first set `CMD 0x0C` idx 3-5 = radius, angular_speed, duration (idx 0-2, the
  centre, are overwritten at start; TASK/send_data.c:1660-1675); then index `6` value `1` to start, value `0` to stop.
  - *Observation*: Path activates, `circle_path.active` becomes 1.
- [ ] **Figure8 Path (Ground)**: first set `CMD 0x11` idx 3-5 = amplitude, angular_speed, duration and idx 6 =
  type (0 or 1, clamped to 1) (TASK/send_data.c:1690-1709; idx 0-2 are overwritten at start); then index `7`
  value `1` to start, value `0` to stop.
  - *Observation*: Path activates, `figure8_path.active` becomes 1.
- [ ] **Simplex Fade Behaviour**: Send Simplex mode `1` (enforce) via `CMD 0x19` index `0` value `1`.
  - *Observation*: On trigger (roll > roll_max, pitch > pitch_max, w_norm > w_norm_max, u_ad sat > sat_ticks_max), gradient updates freeze and fade ramps to 0 over ~100ms, suppressing MRAC injection. Resumes when triggers clear for `hold_ticks` (default 200 ticks).

## 4. Per-Condition Run Card

| Setup | Start Recording | Switch Controller | Start/Stop Path | Duration | Watch |
|---|---|---|---|---|---|
| Hover | `POST /api/recording/start` | `CMD 0x1F` idx 0 val 0 | N/A | N/A | Stability, `loops.rate_roll.e_rms` |
| Hover | `POST /api/recording/start` | `CMD 0x1F` idx 0 val 1 | N/A | N/A | MRAC adaptation, `u_ad` |
| Hover (load) | `POST /api/recording/start` | `CMD 0x1F` idx 0 val 0 | N/A | N/A | Stability, drift |
| Hover (load) | `POST /api/recording/start` | `CMD 0x1F` idx 0 val 1 | N/A | N/A | MRAC adaptation |
| Circle | `POST /api/recording/start` | `CMD 0x1F` idx 0 val 0 | `CMD 0x0C` idx 6 val 1 / 0 | idx 5; 0 = until stop | Path tracking error |
| Circle | `POST /api/recording/start` | `CMD 0x1F` idx 0 val 1 | `CMD 0x0C` idx 6 val 1 / 0 | idx 5; 0 = until stop | Path tracking error |
| Figure8 | `POST /api/recording/start` | `CMD 0x1F` idx 0 val 0 | `CMD 0x11` idx 7 val 1 / 0 | idx 5; 0 = until stop | Path tracking error |
| Figure8 | `POST /api/recording/start` | `CMD 0x1F` idx 0 val 1 | `CMD 0x11` idx 7 val 1 / 0 | idx 5; 0 = until stop | Path tracking error |

Circle: the yaw setpoint is the start heading plus the path angle (AutoflyTask_RunCircle), so the airframe yaws a
full turn per lap. A path with duration > 0 stops itself when `t_elapsed >= duration`. Units: section 1b.

## 5. Abort Criteria and How to Abort
- **RC Ch10 Hard Kill**: Use transmitter channel 10 to instantly kill motors.
- **RC Ch9 Loss**: `DANGEROUS_STOP` latch if channel 9 <= 500 (RC loss).
- **Simplex Trips**: Mode 1 trips automatically on excessive roll (`roll_max`), pitch (`pitch_max`), MRAC weight norm (`w_norm_max`), or prolonged `u_ad` saturation (`sat_ticks_max`), safely falling back to PID.

## 6. Analysis
After each condition pair, run:
```powershell
python -m ground_station.analysis.flightlab analyze <stem_PID>
python -m ground_station.analysis.flightlab analyze <stem_MRAC>
python -m ground_station.analysis.flightlab compare <stem_PID> <stem_MRAC>
```
**Metrics to answer "did MRAC help":**
- Position hold: `position.drift_rms`, `position.drift_max`, `position.alt_e_rms`
  (ground_station/analysis/flightlab/plugins/position.py:61-66).
- Per loop: `loops.<loop>.steady.e_rms` (ledger column `e_rms_steady_<loop>`, ledger.py:15,48). Loops:
  rate_roll, rate_pitch, rate_yaw, att_roll, att_pitch, att_yaw, alt_pos, alt_rate, pos_x, pos_y
  (ground_station/analysis/flightlab/config/loops.yaml:16-25).
- `analyze` takes a VOFA stem (e.g. `flight17`) or a path to `<stem>.meta.json` (flightlab/__main__.py:16).

## 7. Open Questions
- UNKNOWN: hover segment duration and the off-centre load mass and position (no source; operator decides).

<!--
Sources:
docs/research-platform/CONTROLLER_INTERFACE.md:22-33 (CTRL_SELECT CMD 0x1F)
docs/research-platform/SIMPLEX.md:24-54 (Simplex fade behaviour)
API/controller.c:48-52 (CTRL_SELECT applies when disarmed)
TASK/send_data.c:1660-1721 (CMD 0x0C, 0x11 params/indexes)
TASK/AutoflyTask.c:116-126 (circle_path duration handling), 187-210 (figure8)
ground_station/platform/firmware_contract.py:311-398, 495-510 (0x0C, 0x11, 0x1F definitions)
ground_station/livewatch/multi_slot_presets.yaml:111-125 (flight_test_adaptive)
docs/analysis/flightlab-spec.md:144-149 (metrics, rules)
docs/dashboard-platform/AGENT_GUIDE.md:149-160 (POST /api/recording/start)
docs/RUNBOOK.md:7-18 (start service)
AGENTS.md (abort criteria RC ch10, flashing command)
-->
