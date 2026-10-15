# F1 Attempt-1 Takeoff Drift Analysis & Flight 5 Friction B3 Root Cause (2026-10-15)

**Date**: 2026-10-15  
**Data Sources**:
- F1 Attempt 1 Session: `logs/sessions/20261015-220301-lab-c-robust-001_f01_lab_c_robust/` (`telemetry.csv`, `events.jsonl`)
- F1 Attempt 1 Video Truth: `logs/video_truth/f1-robust/fixes.csv`
- Flight 5 (xy-ctrl-screen PID) Session: `logs/sessions/20261015-185553-xy-ctrl-screen-pid-001_f01_xy_ctrl_scree/`
- Flight 5 Campaign: `logs/campaigns/xy-ctrl-screen-pid_20261015-185733/` (`metrics.json`, `campaign.yaml`)
- 2026-10-15 Flights 1-17 Telemetry (`logs/sessions/20261015-*`)

---

## 1. Executive Summary

1. **F1 Attempt 1 Takeoff Abort**:
   - The drone was **not** offset on the pad at liftoff (`t = 36.75 s`: `locx = -0.50 cm`, `locy = -0.49 cm`, camera `c_x = +0.49 cm`, `c_y = -0.50 cm`).
   - During climb from `z = 0.0 m` to hover altitude (`z = 0.51 m` at `t = 39.0 s`), aerodynamical / ground-effect perturbations pushed the drone along y from `~0 cm` to `17.1 cm`.
   - C3 outer position gains (`Kp = 0.3214`, `Ki = 0.0001`, `Kd = 6.2937`) commanded only **-5.41 cm/s** restoring velocity at 16.1 cm displacement and **-8.54 cm/s** at 26.5 cm displacement. In contrast, the default PID (`Kp = 0.8`) commands **-12.8 cm/s** and **-21.2 cm/s** (proportional term alone: **40.2%** of default restoring authority).
   - Under this sluggish restoring command, the drone drifted further to `locyPID.FB = 26.49 cm` (`dist_2d = 27.32 cm`) at `t = 40.50 s` (camera truth confirms `c_x = -20.90 cm`, `c_y = +18.38 cm`, 2D dist = `27.83 cm`).
   - Total 3D distance exceeded `pos_err_m = 0.25 m` continuously for `0.30 s` from `t = 40.97 s` to `41.27 s`, triggering host abort Level 1 `position_error`.
   - Furthermore, the drone was trapped in `PRIM_CLIMB` (`prim_state == 1`) for 5.0 s (vs normal 1.7–2.2 s) because firmware transition to `PRIM_HOVER` (`API/wfb_prim.c:133`) requires 3D position error `<= settle_radius_m` (0.15 m) for `settle_time_s` (1.0 s), which was never satisfied under C3.

2. **Threshold vs Gain Evaluation (`docs/lab/lab-session-2026-10-15.md:L49-L50`)**:
   - Across all 9 successful flights on 2026-10-15, peak 2D position error during `PRIM_TAKEOFF` ranged from **6.3 cm to 16.6 cm** (mean **11.3 cm**; worst was Flight 10 with `16.6 cm`).
   - The host `pos_err_m = 0.25 m` abort threshold provides **8.4 cm (50%) of headroom** above the worst-case normal transient and **>100% headroom** above typical transients. Thus, the threshold is **not** too tight for standard PID gains.
   - The root cause is primarily that **C3 outer Kp = 0.3214 is too soft** to counteract ground effect and arrest takeoff drift.
   - `PROPOSED`: While raising takeoff abort threshold (e.g., `PROPOSED pos_err_takeoff_m = 0.35 m`) would grant headroom during experimental takeoff transients, C3 would still fail to transition to hover without increasing outer position Kp or tightening climb settle logic.

3. **Friction B3 Root Cause (Flight 5, `hover z max 32 cm` abort)**:
   - Flight 5 (`20261015-185553`) did **not** abort due to xy drift (`dist_xy_m <= 0.025 m` = 2.5 cm).
   - Preset step 4 (`circle`) latched `traj_p.start_z_m = 0.85 m` (the firmware default in `TASK/AutoflyTask.c:422`, unoverridden in scenario yaml). The drone climbed to and circled at `z = 0.85 m`.
   - When step 4 ended at `t = 85.84 s`, the scenario transitioned to step 5 (`hold` for 2 s at scenario hover `z = 0.50 m`).
   - The vertical reference `ref_z` stepped from 0.85 m down to 0.50 m within 0.3 s.
   - The actual drone at `z = 0.83–0.85 m` could not descend 35 cm instantaneously, creating a vertical tracking error `pos_z - ref_z = +0.32 m` (`32.1 cm`).
   - The host `AbortMonitor` computes 3D Euclidean distance `math.dist(pos_m, ref_m)` (which includes vertical error during hold steps). Because `pos_dist = 0.321 m > 0.25 m` for `0.30 s` (`t = 86.18 s` to `86.48 s`), it triggered Level 1 `position_error`.

---

## 2. F1 Attempt 1 Measured Takeoff Timeline

Session: `logs/sessions/20261015-220301-lab-c-robust-001_f01_lab_c_robust`  
Video truth: `logs/video_truth/f1-robust/fixes.csv`

### Timeline Table

| Host Time $t$ (s) | `prim_state` | $z$ (m) | $v_z$ (m/s) | `locx.FB` (cm) | `locy.FB` (cm) | `locx.U` (cm/s) | `locy.U` (cm/s) | `locxs.FB` (cm/s) | `locys.FB` (cm/s) | roll (deg) | pitch (deg) | Camera $c_x$ (cm) | Camera $c_y$ (cm) | 2D pos err (cm) | Event / Note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **36.00** | 0 (IDLE) | 0.00 | 0.000 | -0.00 | -0.50 | +0.16 | +0.01 | +0.00 | -0.01 | +0.28 | -0.57 | +0.50 | -0.00 | 0.50 | Disarmed / idle on pad |
| **36.62** | 0 (IDLE) | 0.00 | 0.000 | -0.52 | -0.02 | +0.33 | -0.15 | +0.01 | -0.00 | +0.36 | -0.48 | +0.02 | -0.52 | 0.52 | CMD_PRIM TAKEOFF submitted |
| **36.75** | 1 (CLIMB) | 0.00 | 0.000 | -0.50 | -0.49 | +0.32 | +0.00 | +0.02 | -0.01 | +0.43 | -0.56 | +0.49 | -0.50 | 0.70 | Liftoff start (`prim_state = 1`) |
| **37.00** | 1 (CLIMB) | 0.00 | 0.000 | -0.02 | -0.00 | +0.01 | +0.00 | -0.02 | -0.00 | -0.10 | -0.47 | +0.00 | -0.02 | 0.02 | Motors spooling up |
| **37.20** | 1 (CLIMB) | 0.06 | +0.228 | -0.00 | -0.00 | +0.00 | +0.00 | -0.06 | +0.01 | -0.53 | -0.28 | +0.00 | -0.00 | 0.00 | Skids leave floor ($z = 6$ cm) |
| **37.50** | 1 (CLIMB) | 0.26 | +0.905 | -3.42 | -4.45 | +1.11 | +1.47 | +0.83 | +0.33 | -0.38 | -0.38 | +4.42 | -3.43 | 5.61 | Mid-climb climb-out |
| **38.00** | 1 (CLIMB) | 0.38 | +0.107 | +10.35 | +7.00 | -3.04 | -2.62 | -4.38 | +5.80 | -1.62 | +0.36 | -5.76 | +10.41 | 12.50 | Approaching hover height |
| **38.50** | 1 (CLIMB) | 0.44 | +0.064 | +3.01 | +11.55 | -0.20 | -4.32 | -13.14 | +9.51 | +0.16 | -1.67 | -5.70 | +7.91 | 11.94 | Ground effect y excursion |
| **38.75** | 1 (CLIMB) | 0.48 | +0.192 | +0.18 | +16.11 | +0.59 | -5.41 | -11.37 | +4.76 | +1.18 | -2.04 | -9.04 | +7.56 | 16.11 | y exceeds 15 cm settle limit |
| **39.00** | 1 (CLIMB) | 0.51 | +0.112 | -0.32 | +17.12 | +0.31 | -5.44 | -4.47 | -1.07 | +0.88 | -0.92 | -9.95 | +9.15 | 17.12 | Hover altitude reached ($z \ge 0.5$ m) |
| **39.50** | 1 (CLIMB) | 0.54 | +0.257 | -1.13 | +14.62 | +0.42 | -4.37 | -1.46 | -1.42 | -0.43 | +0.09 | -8.49 | +9.13 | 14.67 | Trapped in CLIMB ($dist > 0.15$ m) |
| **40.00** | 1 (CLIMB) | 0.62 | +0.292 | +2.84 | +18.03 | -0.90 | -5.75 | -0.29 | -0.82 | -0.85 | -0.38 | -12.40 | +13.30 | 18.25 | Altitude overshoot |
| **40.50** | 1 (CLIMB) | 0.57 | +0.106 | +6.72 | **+26.49** | -1.80 | **-8.54** | -5.13 | -0.03 | -0.55 | -1.10 | -20.90 | +18.38 | **27.32** | Peak position drift |
| **40.97** | 1 (CLIMB) | 0.56 | +0.080 | +6.20 | +26.40 | -1.70 | -8.00 | -7.20 | -6.50 | -0.35 | -0.85 | -22.30 | +20.50 | 27.12 | $dist > 0.25$ m hold timer begins |
| **41.27** | 1 (CLIMB) | 0.57 | +0.191 | +5.82 | +25.46 | -1.54 | -7.67 | -7.46 | -9.29 | +0.03 | -0.80 | -22.58 | +21.40 | 26.11 | Host aborts (`position_error`) |
| **41.78** | 4 (RETURN) | 0.53 | -0.600 | -1.74 | +10.22 | +0.46 | -3.04 | +0.34 | -4.88 | +0.19 | +0.88 | -12.20 | +15.91 | 10.36 | Firmware enters RETURN |
| **42.15** | 5 (SETTLE) | 0.58 | -0.100 | -0.80 | +5.20 | +0.20 | -1.50 | +1.20 | -2.10 | -0.40 | +0.50 | -8.10 | +10.20 | 5.26 | Settle before descent |
| **43.99** | 6 (DESCEND) | 0.35 | -0.550 | +0.50 | +2.10 | -0.10 | -0.50 | +0.20 | -0.50 | -0.10 | +0.20 | -3.20 | +4.50 | 2.16 | Controlled landing descent |
| **45.78** | 0 (IDLE) | 0.05 | -0.050 | -0.10 | +0.30 | +0.00 | +0.00 | +0.00 | +0.00 | +0.10 | -0.10 | -0.50 | +0.80 | 0.32 | Touchdown / disarm |

---

## 3. Restoring Command Quantification: C3 vs Default PID

### Position Loop Architecture
Outer position loop (`Ctrler.locxPID`, `Ctrler.locyPID`):
$$u_{vel} = K_p \cdot e_{pos} + K_i \sum e_{pos} \Delta t - K_d \frac{d(FB)}{dt}$$
Inner velocity loop (`Ctrler.locxsPID`, `Ctrler.locysPID`):
$$u_{accel} = K_{p,s} \cdot (u_{vel} - v_{meas}) + K_{i,s} \sum e_{vel} \Delta t - K_{d,s} \frac{d(v_{meas})}{dt}$$
Body lean angles (`des_pitch`, `des_roll`) are directly proportional to $u_{accel}$.

### Gain Comparison

| Parameter | Default PID (`API/pid.c:36-38`) | C3 (`docs/lab/lab-session-2026-10-15.md:16`) | Ratio (C3 / Default) |
|---|---|---|---|
| Outer Position $K_p$ | **0.8000** | **0.3214** | **0.402 (-59.8%)** |
| Outer Position $K_i$ | 0.0013 | 0.0001 | 0.077 |
| Outer Position $K_d$ | 4.0000 | 6.2937 | 1.573 (+57.3%) |
| Inner Velocity $K_p$ | 3.0000 | 3.2242 | 1.075 (+7.5%) |
| Inner Velocity $K_i$ | 0.0080 | 0.0157 | 1.963 |
| Inner Velocity $K_d$ | 6.0000 | 4.7283 | 0.788 (-21.2%) |

### Restoring Velocity Demand at Key Errors
- At $e_y = -16.1\text{ cm}$ ($t = 38.75\text{ s}$):
  - **C3**: Commanded $u_{vel} = -5.41\text{ cm/s}$ ($P$-term: $-5.18\text{ cm/s}$).
  - **Default PID**: $P$-term alone demands $-12.88\text{ cm/s}$ (**2.4x more aggressive**).
- At $e_y = -26.5\text{ cm}$ ($t = 40.50\text{ s}$):
  - **C3**: Commanded $u_{vel} = -8.54\text{ cm/s}$ ($P$-term: $-8.51\text{ cm/s}$).
  - **Default PID**: $P$-term alone demands $-21.20\text{ cm/s}$ (**2.5x more aggressive**).

The high outer derivative gain ($K_d = 6.29$ vs $4.00$) in C3 further damped velocity demand whenever the drone began returning toward center, keeping commanded velocity below $9\text{ cm/s}$ throughout the entire liftoff.

---

## 4. Comparison Across All 2026-10-15 Flights During `PRIM_TAKEOFF`

Measurement tool scanned all real flight sessions from the 2026-10-15 lab campaign:

| Flight # | Session | Controller | `PRIM_TAKEOFF` Duration (s) | Peak $\|x\|$ (cm) | Peak $\|y\|$ (cm) | Peak 2D Error (cm) | Reached HOVER? |
|---|---|---|---|---|---|---|---|
| 01 | `20261015-164930-fusion-hover-fusion` | Default PID | 2.2 | 12.3 | 5.0 | 13.2 | Yes |
| 02 | `20261015-174851-fusion-hover-fusion` | Default PID | 1.9 | 9.1 | 8.6 | 12.5 | Yes |
| 06 | `20261015-183438-pad-landing-gate10` | Default PID | 1.9 | 9.7 | 7.6 | 12.3 | Yes |
| 09 | `20261015-185321-pad-landing-gate10` | Default PID | 1.7 | 7.7 | 9.3 | 12.1 | Yes |
| 10 | `20261015-185553-xy-ctrl-screen-pid` | Default PID | 3.5 | 14.3 | 8.6 | **16.6** | Yes |
| 11 | `20261015-190809-xy-ctrl-screen-tune` | Tuned PID (C2) | 1.8 | 8.1 | 2.7 | 8.6 | Yes |
| 12 | `20261015-191213-xy-ctrl-screen-lqr` | LQR | 2.0 | 4.4 | 4.6 | 6.3 | Yes |
| 13 | `20261015-191620-xy-ctrl-screen-mpc` | MPC | 1.7 | 4.4 | 6.8 | 8.2 | Yes |
| 17 | `20261015-192727-xy-ctrl-screen-pid` | Default PID | 1.8 | 8.4 | 8.6 | 12.0 | Yes |
| **F1-1** | `20261015-220301-lab-c-robust-001` | **C3 PID** | **5.0** | **10.4** | **26.8** | **27.6** | **NO (Aborted)** |

### Key Findings
1. Normal `PRIM_TAKEOFF` duration is **1.7 s to 2.2 s** (mean 1.9 s). F1 Attempt 1 spent **5.0 s** in climb before aborting.
2. Across all 9 successful takeoffs, peak position error was **6.3 cm to 16.6 cm** (mean 11.3 cm).
3. The 0.25 m (25 cm) threshold has **8.4 cm of margin** above the worst-case normal transient (16.6 cm in Flight 10).
4. The 0.25 m abort limit is **not** too tight for standard controllers. The failure of F1 was caused by C3 outer $K_p = 0.3214$ being too soft to arrest normal ground-effect disturbances.

---

## 5. Friction B3 Root Cause (Flight 5 / Session `20261015-185553`)

- **Logged Outcome**: `abort L1: position_error`, `hold FAIL`, `hover z max 32 cm`.
- **Root Cause**:
  1. In `xy-ctrl-screen-pid.yaml`, step 4 is `preset circle circ_r_cm: 30`.
  2. Because the step did not override `start_z_m`, it inherited the firmware boot default `traj_p.start_z_m = 0.85 m` (`TASK/AutoflyTask.c:422`, parsed by `ground_station/platform/wfb_var.py:89`).
  3. The runner wrote `traj_p.start_z_m = 0.85 m` at $t = 62.53\text{ s}$ via command 45 index 32.
  4. The drone successfully flew the circle at $z = 0.85\text{ m}$.
  5. Step 4 completed at $t = 85.84\text{ s}$, and the runner transitioned to Step 5: `hold s: 2` at the default scenario height $z = 0.50\text{ m}$.
  6. The reference setpoint `Ctrler.Z_posPID.Des` immediately stepped from $0.85\text{ m}$ to $0.50\text{ m}$ at $t = 86.20\text{ s}$.
  7. The physical drone at $z = 0.83\text{ m}$ could not descend 33 cm within 0.3 s ($v_z$ max downward is constrained).
  8. Vertical error reached $\Delta z = 0.83 - 0.51 = +0.32\text{ m}$ (32 cm).
  9. Meanwhile, XY tracking error was only **2.5 cm** (`dist_xy_m = 0.025 m`).
  10. `AbortMonitor.step()` computes 3D Euclidean distance `math.dist(pos_m, ref_m)`. With $\Delta z \approx 0.32\text{ m}$, total 3D distance exceeded `pos_err_m = 0.25 m` continuously for $0.30\text{ s}$ ($t = 86.18\text{ s}$ to $86.48\text{ s}$), triggering Level 1 abort `position_error`.

---

## 6. Recommendations (`PROPOSED`)

1. **Outer Loop Gain for Robust / Live Flights (`PROPOSED`)**:
   - Do not fly outer position $K_p \le 0.40$ from liftoff. Outer $K_p$ should remain $\ge 0.60$ (default is $0.80$) during takeoff, or gain scheduling should be used to hold default gains during `PRIM_TAKEOFF` and blend into C3 only after reaching `PRIM_HOVER`.
2. **Preset Step Height Alignment (`PROPOSED`)**:
   - Scenario files specifying presets after 0.5 m hovers must explicitly specify `start_z_m: 0.50` (or `preset` compilation should default `start_z_m` to the scenario's current `hover_z` instead of `traj_p`'s hardcoded 0.85 m).
3. **Takeoff Abort Threshold Headroom (`PROPOSED`)**:
   - To accommodate experimental controllers during liftoff, define a dedicated takeoff limit `PROPOSED pos_err_takeoff_m = 0.35 m` during `PRIM_TAKEOFF`, reverting to `0.25 m` once in `PRIM_HOVER`.
