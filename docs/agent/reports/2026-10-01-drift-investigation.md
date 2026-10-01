# 2026-10-01 drift / MRAC / logging / EKF / RPM investigation (workflow wf_2216fa13-f1d, read-only analysts)

## drift

**Bottom line:** The drift comes from a steady push that the controller has no way to cancel. A sideways force grows from about 12 to 28 cm/s² (0.7 to 1.7° of lean) during the flight. The velocity loop has no integral term, and the position integral term hits its ±2 cm/s limit within about 10 s, so position has to sit off target by e = (vFB + Uv/3 − Ui)/0.8. That formula predicts −10.7 cm and the log shows −10.73 cm. The fix: add velocity Ki 0.008 per tick (Ui limit 100 cm/s²), lower position Ki to 0.0013 while raising its limit to 5 cm/s, and let both integrate only in FLYING. In simulation this takes the steady error from −12.9/−13.8 cm to 0.1 cm and keeps a 30 cm step at 3–11% overshoot. Raising the position limit alone gives 30–45% overshoot.

### Findings
**What the code does**
- **Loop rate:** the scheduler ticks at 200 Hz (USER/main.c:421). The position (loc) and velocity (locs) loops run every 2nd tick (`cnt_loc>=2`, TASK/StabilizerTask.c:926-929), so they run at 100 Hz with dt = 10 ms. Converting per-tick gains to continuous ones: Ki_c = Ki/0.01, Kd_c = Kd·0.01.
- **Gains:** API/pid.c:30-33. Position: Kp 0.8, Ki 0.01, Kd 4. Velocity: Kp 3, **Ki 0**, Kd 6. The position Ui limit is 0.01·SumEMax 200 = **±2 cm/s**.
- **Units:** position in cm. The velocity setpoint is clamped to ±120 cm/s (1333-1351). The velocity loop outputs an acceleration (cm/s²), which becomes a lean angle via atan(a/981), clamped to ±15° = 263 cm/s² (1437-1450; global_declare.c:34-35).
- **Integration rule** (legacy mode, pid.c:153-171): the error is summed only while |E| < EMin, then clamped by SumEMax and UiMax. The check against UMax never triggers because U is already clamped to UMax. `aw_mode` is 0 for every loop, and nothing in the code sets it at runtime (robot_types.h:41).
- **When integrators run and reset:** the position and velocity PIDs run whenever the drone is armed (1032-1048), whatever the flight phase. SumE is zeroed at arming (975-981) and in Clear_Structure (pid.c:303-306). With idle enabled, GROUND_IDLE does **not** clear them (795-826), so they **do integrate on the ground**, using the raw velocity feedback, which still contains the sensor bias.

**Logs (FLYING only, active15 x, 10 s windows)**

| t | e_pos cm | Ui_p | vFB | Uv cm/s² | lean ° |
|---|---|---|---|---|---|
|0-10|−3.3|0.1|1.6|−12.4|−0.72|
|10-20|−4.3|−2 (sat)|0.2|−17.0|−0.99|
|30-40|−9.4|−2|0.0|−28.5|−1.66|
|40-50|−10.7|−2|−1.8|−26.4|−1.54|

- **Closed-form check:** the formula gives −10.7 cm for 40-50 s (measured −10.73) and −9.4 cm for 30-40 s (measured −9.35).
- **Other flights show the same pattern:** active12 x e = −10.4 cm, SumE −196 (saturated), Uv −33.8. shadow14 x e up to 6.4 cm, Uv 23.
- **Velocity sensor bias:** the logged velocity feedback averages 0.5-1.2 cm/s away from the slope of the logged position (d(xFB)/dt) over a whole flight. Raw of2 is not debiased, while the position comes from of2_fix with the bias removed. A velocity integral term cannot remove this bias; only the position integral term can.
- **Attitude:** fitted Des→FB lag is 140 ms delay + τ 0.21 s on active15, and 60 ms + τ 0.35-0.46 s on shadow14. In shadow14 roll holds a **~2° steady error** (Des +1.0…+1.3°, FB −0.7…−0.9°). Inferred cause: the angle and rate integral limits (angle Ui ≤ 2.4, gyro Ui ≤ 10).
- **Tilt to acceleration:** velocity lags tilt by 20-80 ms, gain 0.78-0.83. Optical-flow velocity noise is about 0.4 cm/s per sample.

**Phase margins** (A = active15 plant, B = shadow14)

| set | vel PM° | pos PM° |
|---|---|---|
|current, pos I not saturated|45/41|16/9 (GM 1.9 dB)|
|current, pos I saturated|45/41|72/69|
|vel Ki .008 + pos Ki .0013|38/33|61/56|
|vel Ki .015|32/25|63/34|
|vel Ki .02|27/20|B unstable|

**Simulation** (one axis, 100 Hz legacy PID, measured lag, bias −1 cm/s, disturbance ramp 12→28 cm/s²; A/B)

| set | steady e cm | rms with gusts | 30 cm step overshoot / settle |
|---|---|---|---|
|C0 current|−12.9/−13.8|11.5/12.5|7.5%/11.8%, 4.8/7.8 s|
|position limit ×5 only|−3.0/−4.3|4.0/5.7|**30%/45%**|
|vel I .008 only|−0.1/−0.2|3.4/5.1|8%/17%, B 19.8 s|
|**C6 (recommended)**|**0.10/0.09**|**3.0/4.0**|**3.1%/10.8%, 3.3/8.3 s**|
|C7 (vel Ki .005)|0.11/0.11|2.9/4.0|2.8%/9.0%|

### Recommendation
**1. PID table** (API/pid.c:30-33, recommended set C6):
```
locxPID/locyPID   PID_ROW(0.8, 0.0013, 4.0, 300, 300,   5,  50,  3850, 10)
locxsPID/locysPID PID_ROW(3.0, 0.008,  6.0, 600, 600, 100, 100, 12500, 10)
```
- **Velocity loop:** Ui limit 100 cm/s² ≈ 5.8° of lean. That leaves 9° for control even at full integral, and it covers the measured 28-34 cm/s² push about 3 times over.
- **Position loop:** Ui limit 5 cm/s covers the 1.2 cm/s sensor bias with margin.
- **Fallback:** if testing shows oscillation near 0.5 Hz, use velocity Ki 0.005 (C7).

**2. Integrate only in FLYING** (StabilizerTask.c:1032-1048, copying the Z_posPID save/restore pattern at 902-921):
- In GROUND_IDLE or LANDED: set SumE = Ui = 0 for all four PIDs before calling ComputePID.
- In LANDING: save SumE/Ui, call ComputePID, then restore them (freeze).
- Without this gate, a 2 cm/s bias held for 10 s on the ground winds the velocity integrator to SumE = 2000. That is 16 cm/s² ≈ 0.9° of lean at takeoff (inferred).

**3. Anti-windup:** keep legacy mode. In this code only EMin and SumEMax = UiMax/Ki actually limit the integral; the UMax check is a no-op, and setting aw_mode would need new init code. Optionally set the velocity UMax to 263 so it matches the 15° lean limit (no effect on behaviour).

**4. Why it works**
- **Steady state, current code:** with P only, the velocity loop's acceleration is Uv = 3·(Vdes − vFB). To hold against a push a, it needs Vdes = vFB + a/3, and the position loop only produces that with error e = (Vdes − Ui_p)/0.8. With a = 28 cm/s² that is 9.3 cm/s of setpoint, about 10 cm of error.
- **With a velocity integral:** Ui_v → a and e → 0.
- **Why the old fix alone fails:** position Ki_c = 1.0/s puts the PI zero at 1.25 rad/s, above the 0.8 rad/s position crossover, so phase margin drops to 9-16°. Raising the limit without cutting Ki gives 30-45% overshoot.
- **Rule used:** place the PI zero at Ki_c = Kp·ωc/N, with N = 5-8 (costs about atan(1/N) = 7-11° of phase margin).
  - Velocity: 3·2.0/7.5 ≈ 0.8/s, so per-tick Ki = 0.008.
  - Position: 0.8·0.8/5 ≈ 0.13/s, so per-tick Ki = 0.0013.

**5. Analogy: cruise control on a hill.** Throttle = K·(set speed − speed) holds 100 km/h on the flat. On a hill the car settles at 95 km/h, because it needs that 5 km/h error to make the extra throttle. An integral term keeps adding throttle until the error is zero. Here the "hill" is a 1.7° lean held all the time (an off-centre battery or weight, or a trimmed-in IMU tilt). The planned asymmetric-load demo creates exactly this push.

### Risks
- **Lag fits may be biased:** they come from flight data with the controller running, so they can be off. The OF latency (20-80 ms) is inferred. Plant B's gain margin is thin (about 2 dB in the sweep), so check in flight starting with C7.
- **Simulation limits:** the push is modelled as one constant force per axis. Real yaw rotation and wind are not modelled.
- **PID-vs-MRAC demo baseline:** the shadow (PID-only) flight held a ~2° roll error, inferred to come from the angle and rate integral limits. With an off-centre load the PID baseline may be limited by the attitude integrators rather than the position loop. Check the angle-loop logs before the demo.
- **Lean limit can change at runtime:** gs_max_pitch_deg/gs_max_roll_deg are settable (send_data.c:1606-1608). Below about 8° the 100 cm/s² velocity Ui limit becomes a large share of the available lean.
- **Unlogged terms:** the Up/Ui/Ud terms and the velocity SumE are not logged. Add the velocity SumE to slot3 so the integral can be checked in flight.
- **Not tested on hardware:** all of the above is from simulation and phase-margin analysis.

## mrac

**Bottom line:** Today MRAC learns all the time: on the ground, before takeoff and after disarm. In shadow mode its weights wind up to the projection bounds, and turning injection on adds that wound-up u_ad to the motors in a single tick. That costs 5.6-7.5 deg rms roll error in the first second (active15, active8). In active12 injection was already on at liftoff, which gave a 678-unit motor spread. Fix: reset the weights when injection turns on, ramp it in with a 2.5 s smoothstep (scaling both u_ad and the learning step), allow learning only when flight_phase is FLYING or LANDING plus a 1 s hold, and on disarm zero the ramp and reset.

### Findings
**Code (read this session)**
- **Learning is never gated by phase or arm.**
  - mrac.c:368 `if (mrac_flags.adaptation_on && do_adaptation)` has no flight_phase or arm check.
  - MRAC_Control (mrac.c:687) runs every tick (StabilizerTask.c:1095).
  - adaptation_on defaults to 1 (mrac.c:636).
- **Injection is a hard step.** controller.c:13-29 returns `u_ad*mrac_to_mixer*mrac_simplex.fade` as soon as `output_injection_on` is set.
- **CMD 0x0F flag writes are plain assignments** (send_data.c:1745-1786: idx 0 adaptation, idx 10 injection). Nothing resets Theta when injection turns on.
- **MRAC_Reset** (mrac.c:656-678) zeroes Theta and Whatf and snaps xm=x and x_prev. It runs only at init and on a controller switch while disarmed (controller.c:44-54).
- **Simplex fade** (mrac.c:458-523) is a 100 ms linear ramp toward 0 when tripped or variant==1, otherwise toward 1. It defaults to 1 and is unrelated to injection.
- **Phase gates that exist today**:
  - the yaw-integral idle hold (StabilizerTask.c:1076-1084);
  - Wfb `airborne` = FLYING or LANDING, with the disarm edge taken from s_was_armed (StabilizerTask.c:237-250).
  - `flight_phase` is declared extern at flight_fsm.h:24.
- **Panel bug:** the "MRAC On/Off" quick buttons (command-panel.js:227-228) send 0x0F **idx 1** (projection_on), not 10 or 0.

**Logged signals**
- Slot 0 (25 Hz) carries e, u_ad, Theta[0..5] for 4 axes, and adaptation_on.
- Slot 1 carries flight_phase, x, r, u_nom and ARM.
- Slot 2 carries injection, adaptation, motors and PID terms.
- No log records fade or tripped.
- **active15 slot0.csv has 0 rows** (header only). The cause was not investigated.

**Edges (s from log start)**

| log | FLYING | inj on | disarm |
|---|---|---|---|
| active15 | 21.1 | 26.86 | 97.81 |
| active8 | 12.08 | 18.51 | 75.72 |
| active12 | 16.62 | on from start | 32.89 |
| shadow14 | 18.6 | never | 66.22 |

**Transient after injection turns on** (Des−FB, deg rms)

| log | roll 0-1 s (max) | pitch 0-1 s (max) | roll 1-2 / 2-3 s |
|---|---|---|---|
| active15 (pre 1.34) | 5.56 (7.75) | 1.78 | 2.75 / 1.02 |
| active8 (pre 0.80) | 7.47 (10.03) | 7.09 (10.03) | 2.14 / 1.00 |
| active12 at liftoff | 6.48 (9.26) | 8.28 (11.38) | 0.53 |
| shadow14, no injection | 1.34 | 0.28 | flat |

- **active8 when injection turned on:**
  - u_ad×mixer was roll 166, pitch 132, yaw 173.
  - Theta bias was roll 0.138, pitch 0.139, yaw 0.0896, which is at the yaw projection bound (0.09).
  - The roll PID U swung from +34 to −46 to fight it.
- **Closed-loop unwind:**
  - 1/e time is 0.45 s (active8) and 0.25-0.29 s (active12).
  - The settled Theta0 is about 0.016-0.03 (17-33 mixer units), about 5x below the shadow wind-up.
- **Ground learning:**
  - While armed on the ground, |e| > deadzone in 69-99% of roll/pitch samples, so Theta0 hits ±0.149 before liftoff.
  - It keeps learning after disarm (active12 z Theta0 went 0.136→0.740 in 3.7 s).
  - The weights carry over from one log to the next (pitch −0.148 at t=0 in active12 and shadow14).
- **Ground motor spread** before liftoff:
  - active12, injecting: 678.
  - Others: 234 (active8), 89 (shadow14), 84 (active15).

### Recommendation
**Design**
1. **Reset the shadow weights when injection turns on.** Do not keep them. They are open-loop wind-up at the bound, not a prior. Relearning in closed loop takes about 0.3-0.5 s (measured).
2. **Ramp in with α.** Use α = smoothstep(p) = 3p²−2p³ with p = t/T_up and **T_up = 2.5 s**. That is more than 5× the 0.45 s unwind and matches the 2-3 s settling.
3. **What α scales:**
   - It multiplies the injected u_ad, on top of the simplex fade.
   - It also multiplies the adaptation gradient, so Theta cannot wind up to make up for the reduced authority (inferred).
4. **Injection off:** ramp α down linearly over **T_dn = 0.5 s**, then freeze learning.
5. **Disarm edge, or phase LANDED / GROUND_IDLE:**
   - set α=0 immediately and gate learning off;
   - call a weights-only reset on the disarm edge, so the next flight starts from zero.
6. **Learning gate:** `learn = armed && (phase==FLYING || phase==LANDING) && t_since_FLYING >= 1.0 s`. Phase becomes FLYING only at h > 0.2 m, so takeoff ground effect is excluded. Shadow mode uses the same gate, so the shadow logs become meaningful.
7. **Injection never ramps in before FLYING.** If injection is on at arm (the active12 case), α stays at 0 until the gate opens, then ramps up.

**Code plan**
- **API/mrac.h, near :280** — add `float inj_alpha; uint8_t learn_gate; uint16_t fly_ticks;` (new struct `mrac_inj`), plus `#define MRAC_INJ_T_UP 2.5f`, `MRAC_INJ_T_DN 0.5f`, `MRAC_LEARN_HOLD_S 1.0f`.
- **API/mrac.c, new `MRAC_GateStep(uint8_t armed, uint8_t phase)`**, called first in MRAC_Control (:687):
  - edge-detect injection and arm;
  - on the injection rising edge, call a new `MRAC_ResetWeights()`. It is the Theta/Whatf part of MRAC_Reset (:656-678) plus the xm/x_prev snap.
  - advance α;
  - compute learn_gate.
- **API/mrac.c:368** — change the condition to `&& mrac_inj.learn_gate`, and scale the Theta increment by `mrac_inj.inj_alpha` when injection is on (by 1 in shadow mode).
- **API/controller.c:25** — `return u * mrac_simplex.fade * mrac_inj.inj_alpha;`
- **TASK/StabilizerTask.c:1095** — pass `DroneStatus.ARM_Status==Armed` and `flight_phase`. Alternatively, set two globals just before the call, to keep the mrac.c host stubs simple.
- **Leave the simplex fade (mrac.c:458-523) unchanged.**
- **Logging:** add inj_alpha, learn_gate, fade and tripped to slot 0.
- **command-panel.js:227-228:** change idx 1 to 10, and add separate buttons for adaptation (idx 0).

**Host tests** (existing)
- **MRAC equivalence:** `python API/tests/run_mrac_equiv.py` builds `API/tests/test_mrac_equiv.c` with gcc, using `-I stubs -lm` (run_mrac_equiv.py:77-82). It must stay bit-equal when gate=1 and α=1.
- **Controller:** `tests/firmware_host/test_controller.c` builds with `gcc -std=c99 -Wall -Werror -Istubs -I../../API test_controller.c ../../API/controller.c -o t && ./t` (its line 3). Add cases:
  - (a) when injection turns on, the per-tick change in the output is at most 1.5·u/(T_up·200);
  - (b) α=0 while phase ≠ FLYING;
  - (c) α reaches 0 within 100 ticks after injection turns off;
  - (d) α=0 immediately on disarm.
- **Sigma prior:** `API/tests/test_mrac_sigma_prior.c`. Add a gate case: Theta stays unchanged for 1000 ticks with phase=GROUND_IDLE and e = 0.5.
- **Flight check:** compare a re-flown active8-style log against these targets. They are inferred, not measured.
  - roll rms 0-1 s after injection turns on < 2 deg (was 7.47);
  - the ground spread stays under 100 when injection is pre-enabled.

### Risks
- **Ramp scaling is not validated.** Scaling the gradient by α slows early relearning. Over the 2.5 s ramp the PID carries the load, which is today's baseline behaviour. Not validated in flight.
- **Reset every flight loses useful learning.** If the asymmetric-load demo needs a persistent bias, resetting each flight throws away a learned offset. The measured relearning time (0.3-0.5 s) suggests that is acceptable (inferred).
- **FLYING/LANDING edges are height-based.** The phase depends on the OF/EKF height (of2_h > 0.2 m). A bad height estimate could keep learning off or flicker the gate. Add hysteresis via fly_ticks.
- **Not checked:**
  - the cause of the empty slot 0 in active15 (the logger or the subscription), which may hide other missing streams;
  - whether `run_mrac_equiv.py` stubs `flight_phase`/`DroneStatus`;
  - whether `sil_gate/` (cited in test_mrac_sigma_prior.c:22) still exists. A glob found no sil_gate MRAC tests.
- **Panel buttons:** after the idx fix, operators lose the projection toggle they may have been using unknowingly.
- **Transient attribution is partial.** The yaw Theta was at its projection bound (0.0896 against 0.09), so part of the transient may come from yaw/z coupling, not only roll/pitch.

## logging

**Bottom line:** Slot 0 is empty because the FC rebooted (battery swap) and lost all its stream subscriptions, which live only in RAM. The ground station re-sends the 4 slot requests without waiting for a reply or retrying, and one of them got lost. The bridge watchdog only re-sends when no slot at all has streamed for 3 s, so with slots 1-3 working the lost slot is never asked for again. /api/streams still shows it as "streaming", so nobody notices. This happened 3 times in about 10 reboots (active6 slot 0, pidonly7 slot 3, active15 slot 0), and slot 0 is still dead in the live session right now. The sweep also found two real firmware bugs that make the thrust-estimator columns useless (`empirical[]` and `imu_total` are constant), plus a 0xFFFFFFFF sentinel in `of_alt_cm`.

### Findings
**How the slot gets lost (code + live evidence)**
1. After a reboot the FC has no subscriptions. The bridge resends 4 requests back-to-back and checks none of them:
   - `StreamsManager._replay` (streams.py:663-678)
   - or `_request_slot0_schema` (wifi_bridge.py:497-547)
   - `_request_stream_schema` (wifi_bridge.py:549-635) prints "Sent" and never retries.
2. A request can be dropped. The firmware's 0x21 mailbox holds only one request and silently discards a new one while the last is pending (BSP/usart5.c:312-339). Inferred: the FC can also still be booting, or UDP can lose the packet. The slot-0 request is 498 B against a 512 B RX buffer (usart5.h:12), which is 97% full (inferred margin hazard).
3. The watchdog is link-wide. `_check_resubscribe` returns early if *any* slot was decoded in the last 3 s (wifi_bridge.py:436; `_last_stream_rx` is set at :1319). Once slots 1-3 stream, slot 0 is never re-requested.
4. Slot state never goes backwards (wifi_bridge.py:2258), so /api/streams keeps reporting "streaming". The logger meta still lists all configured vars, so the CSV gets a header and no rows.

**Live proof (GET only, 20:26:55)**
- Slot 0 `received` stayed frozen at 31736 (crc_errors 0) for 6.9 s. Over the same window the other slots grew: slot 1 +685, slot 2 +343, slot 3 +342.
- The view-model slots dict holds only {1,2,3}, while /api/streams reports slot 0 `"state":"streaming"`.
- The FC booted at about 19:48:25, the same boot as active15, so slot 0 has now been dead for 38+ min.

**Reboots seen in the 8 logs**
| Boot (approx.) | Log | Result |
|---|---|---|
| 18:07:13 | active6 | slot 0 lost |
| 18:51:27 | pidonly7 | slot 3 lost |
| 18:58 / 19:29 / 19:32 | active8, shadow10, active12 | all slots OK |
| mid-log, 38 s outage | shadow13 | recovered; 209 empty rows written to slot 0 |
| 19:48:25 | active15 | slot 0 lost |

The losses hit different slots each time, so this is a lost-request race, not anything about what slot 0 contains (inferred). The empty shadow13 rows come from fallback raw JustFloat "a" frames being mapped to slot 0 (telemetry_adapter.py:157-162).

Ruled out: the logger code, link bandwidth (about 40 of 87.5 kB/s used), firmware round-robin starvation, and a schema mismatch (that would not freeze the counter).

**Other dead or bad variables (8 newest logs; pidonly7 is only 6 s on the ground, so its constants are ignored)**
| Slot | Variable | What's wrong | Cause |
|---|---|---|---|
| s0 / s3 / s1 | whole slot | 0 rows in active15, active6 and pidonly7 | the race above |
| thrust | `g_thrust_est.empirical[0..3]` | constant 12.8 / 12.8 / ~12.9 / 12.9 in all logs | **bug:** motor CCR 2000-4000 (pwm.h:13,15) is looked up in a 1100-2000 µs table (thrust_estimators.c:23-31,79-82), so it always clamps to the top |
| thrust | `g_thrust_est.imu_total` | constant 9.69719 in all logs | **bug:** `imu_data.a_acc` (robot_types.h:344) is never written anywhere; its only reader is StabilizerTask.c:1167. So acc_z=0 and the result is just mass×g (thrust_estimators.c:98-99) |
| s1 | `ano_of.of_alt_cm` | 4.29497e9 (0xFFFFFFFF) in 20.6% of active15 rows and 8.0% of shadow14 rows; median 108 / 96 cm | invalid-reading sentinel, not masked |
| s2 | `rpm_dbg_period_cyc[0..3]` | median about 1.92e6 cycles; spikes 1.07e7-7.19e7 in all logs | inferred: missed edges or timeouts |
| s0 | `mrac_state.{yaw,z_rate}.Theta[3]` | constant 0 | by design: phi[3]=0 for Z and YAW (mrac.c:168-169) |
| s0 | `s_ekf.x[6..8]` | constant 0 in all flights | inferred: these states are not estimated; worth checking |
| s3 | `s_ekf_of.x[0..5]`, `s_of_bias_x/y`, `ekf_of_health/fallback` | constant | config: `g_of_bias_mode`=0, and EKF_OF runs only in mode 2 (StabilizerTask.c:429-442) |

Expected constants, no action needed: `s_authority`, `TWC_*`, `sbus_lost`, `adaptation_on`, `g_ctrl_select`, the `gs_*` limits, and `of_quality` (about 255).

### Recommendation
**Do now (operator):** after every battery swap or FC reboot, re-apply the preset. Before arming, confirm that every slot's `received` counter is increasing. The live session needs this right now, because slot 0 is still dead.

**Code fixes (ground station, `ground_station/comm/wifi_bridge.py`)**
1. **Watch each slot separately.** Track `_last_slot_rx[slot]` in the 0x09-0x0C dispatch (:1311-1340). In `_check_resubscribe`, any configured slot with no frames for more than `max(3 s, 5/rate)` gets re-requested on its own, at most every 5 s. Also set `_slot_states[slot]="stale"` so it stops showing "streaming" (:2258).
2. **Make `_replay` wait and retry** (streams.py:663-678). Reuse the `_await_schemas` logic from `_do_apply` (:612-661): send one slot, wait up to 500 ms for its 0x08 reply (or an "E:" error), retry up to 3 times, and leave at least 150 ms between slots. The spacing respects the one-request mailbox (usart5.c:312-339).
3. **Merge contiguous ranges** in `_request_stream_schema` (:549-635), the way `subscribe_slot` already does. This takes the slot-0 request from 498 B down to roughly half (inferred), well clear of the 512 B buffer.
4. **Fix the logger (StreamLogger, streams.py:230-256).** Write per-slot row counts and status into meta.json. Raise a fault if a slot has 0 rows after 2 s. Drop rows where every value is empty (the legacy "a" fallback rows).
5. **Count errors instead of swallowing them**, both in `_note_recorder` (core.py:380-391) and in the decode path (wifi_bridge.py:2133-2192).

**Pre-flight gate (blocks takeoff on an empty slot)**
- Where: in `/api/streams/log/start` and in the workflow-B arm precheck.
- Check: sample `received` per slot twice, 2 s apart. Every configured slot must grow by at least 0.8 × rate × 2.
- On failure: re-request the slot (fix 2), check again once, then refuse to arm with "slot N silent".
- Add a fake_drone test that drops one 0x21 request.

**Firmware fixes (separate WP, need review and a flash)**
- `ThrustEst_Update`: convert CCR to µs (`pwm*0.5`) before the table lookup. This is inferred from the 2000-4000 CCR range versus the 1000-2000 µs table. Verify the timer tick first.
- Write `imu_data.a_acc` (gravity-removed, earth-frame acceleration) where the attitude update runs. Without this, `imu_total`, and any "EKF with accel input", is fed zeros.
- Mask `of_alt_cm==0xFFFFFFFF` in the stream or in analysis. In analysis, also reject `rpm_dbg_period_cyc` values above 5× the median.

### Risks
- **Unproven drop path.** Which of the three drop paths lost the request (mailbox drop, FC still booting, UDP loss) is inferred, not proven. All three are fixed the same way (wait for the reply, then retry), so the fix holds either way. To confirm, a firmware counter of dropped 0x21 requests would help.
- **Pre-reboot capture not checked.** I could not see the FC's E: replies at the active6 and active15 reboots, so the request may instead have arrived and been rejected. A rejection is still covered by the retry plus the per-slot watchdog.
- **CCR-to-µs factor is a guess.** Converting CCR to µs as pwm/2 is inferred from the ranges (Motor_PWM_ZERO 2000, MAX 4000). Confirm the TIM prescaler before changing it, or the thrust table will be wrong in a different way.
- **What fills `a_acc` is open.** It must be earth-frame and gravity-removed to match thrust_estimators.c:98-99. Using body-frame raw accel would double-count g.
- **`s_ekf.x[6..8]` meaning not checked.** A quick grep for the state meaning found nothing. Read the EKF header before treating these zeros as a bug.
- **Small sample.** The 3 losses in about 10 reboots are measured, but that is too few reboots to estimate a reliable failure rate.
- **Gate needs exceptions.** The pre-flight gate should be skippable for presets where a slot is intentionally rate 0.

## ekf

**Bottom line:** The ~0.6 lag-1 innovation autocorrelation is not a defect in the accel model. It comes from the OF signal itself: an OF-only random walk scores 0.78-0.94 at NIS 0.2-0.6, and the old 6-state scores 0.91-0.97. No model I tried meets both "autocorr < 0.3" and "NIS 0.5-2". The real defect is the accel input: the body-axis Acc_X/Y explain about 0% of OF velocity change, and using them through Lin_Acc makes velocity-change prediction 4-34x worse than not using them. The gravity-tilt term g·sin(attitude) alone explains 34-63%. Shadow is safe after a small (~20-line) code change. Active (mode 2) is not ready: switch the input to tilt-only, set R to 1e-4, fix the rebase sign and tighten the health gate first. "Just filter the accel" does not work, because the bad content is below 2 Hz.

### Findings
Data: 7 logs, flight window only (takeoff+2 s to landing). Scripts are in `C:/Users/Acer/AppData/Local/Temp/claude/C--Users-Acer-Desktop-UAV-lab-FreeRTOS-adaptive-controller-codex/14960682-4ba6-4f37-9b25-44a2d771e384/scratchpad/wf1/ekf/` (`kf.py`, `exp1.py`-`exp5.py`).

**1. Innovation autocorrelation is a property of the OF signal**

| model | lag-1 autocorr (flight) | NIS | innovation rms (cm/s) |
|---|---|---|---|
| OF-only, q 1e-3 | 0.78-0.94 | 0.19-0.64 | 1.2-2.3 |
| OF-only, q 1e-1 | -0.27..+0.51 | 0.01 | 0.6-0.9 |
| old 6-state | 0.91-0.97 | 0.52-1.28 | 1.9-3.0 |
| new, CHOSEN params | 0.39-0.61 | 0.86-1.31 | 3.0-4.2 |
| new, R 1e-4 | 0.14-0.38 | 1.6-2.2 | 2.5-3.6 |

- After removing a 1 Hz zero-phase fit, the OF residual has sd 0.5-1.1 cm/s. R=6.16e-4 assumes 2.5 cm/s, so R is too large.
- OF is integer cm/s. 31-41% of consecutive samples repeat exactly; this is quantization at hover, not stale frames. Dropping those samples made position deviate 20-201 cm rms from FIXED, which shows how fast the lin-acc model drifts without OF.

**2. What the accel actually contains** (exp2/exp3). I checked whether ∫a over a 0.5 s window predicts the OF velocity change over the same window:

| input | best-fit gain k | residual / ΔOF variance (1.0 = no help) |
|---|---|---|
| Lin_Acc (body + gravity) | 0.02-0.24 | 0.64-1.00 |
| body Acc_X/Y only | -0.12..+0.15 | 0.91-1.00 |
| gravity-tilt only | 0.32-0.74 | 0.27-0.70 |

- With gain forced to 1, Lin_Acc makes the prediction 4.3-34x worse.
- The body axes carry 25-43 mg of content below 2 Hz. The tilt term carries only 11-20 mg.
- Rotor drag is not usable at hover speeds: raw accel vs OF velocity correlation r is -0.31..+0.17, with inconsistent sign.

**3. Experiments (c)-(e)**
- **(c) LPF on Lin_Acc at 2/5/10 Hz:** innovation rms falls from 3.8 to 2.1-3.2 cm/s, but autocorr rises to 0.85-0.92. The gain stays at 0.02-0.24, so filtering leaves the problem in place.
- **(d) Latency:** OF lags the tilt-accel by +33..+197 ms (median about 55 ms, r 0.72-0.92). Delaying the input by 60-120 ms changes KF metrics by ≤0.05 and residual variance by ≤0.09. Latency is real but secondary.
- **(e) 200 Hz replay vs native rate:** autocorr changes by +0.05..+0.10 and NIS barely moves. Interpolating the log adds no information, so the firmware's 200 Hz noise averaging is still untested (inferred).

**4. Tilt-only input, q_acc 3e-3, R 1e-4:**
- innovation rms 0.78-1.25 cm/s
- NIS 0.26-0.55
- autocorr -0.07..+0.64
- ba -8..-25 mg (about 0.5-1.4° of attitude bias)

**5. Firmware (wp/8)**
- **No shadow.** The else branch sets `s_ekf_of_inited = 0U` (`TASK/StabilizerTask.c:514`), so the KF runs only in mode 2 and re-initializes from P0 each time it is entered.
- **Mode 2 feeds only position:** `x[0]`/`x[3]` deltas go to locx/yPID (`:487-511`). The velocity loop uses raw `of2_dx/dy` or `g_ekf_gate` (`:605-606`), never the KF velocity.
- **Accel input is `Lin_Acc_X/Y_body`** (`:449`), i.e. `Acc_X_Real - 1000*vecxZ` (`API/imu_update.c:202-203`). Per finding 2, this is the noisy input.
- **Rebase sign bug.** `Of_RebaseKfBias` does `x[2] += dbx` (`:206-207`), but the new measurement model is z = v + bof (`API/ekf_of.c:198`). It should be `-=`; as written, each rebase adds a 2Δ bias error. Measured impact here is about 0: |s_of_bias| ≤ 0.05 cm/s in all 7 logs. It is a latent bug for CMD 0x17 snaps.
- **Health gate:** the 2 m/s innovation threshold (`:125`, `:469-472`) is about 50x the measured innovation rms of 3-4 cm/s, so it would almost never trip (inferred).
- **OF frame counter:** `of_update_cnt` counts type-1 frames, but `of2_*` comes from type-2 frames, which are already fused with the module's own IMU (`API/Ano_OF.c:132-142`).

### Recommendation
**Shadow mode** (wp/8 `TASK/StabilizerTask.c`, ~20 lines)
1. In `Update_Data`, take the predict/ZUPT/update block (`:435-476`) out of `else if (g_of_bias_mode == 2U)`. Run it whenever `g_ekf_of_shadow || mode == 2`. On a bad innovation, only switch to mode 0 when `mode == 2`; in shadow, just set health to 0.
2. Delete `s_ekf_of_inited = 0U` (`:514`).
3. Drop the `g_of_bias_mode == 2U &&` condition at `:205`, `:230`, `:1021` and `:1042`, so rebase, reset and zero-velocity apply whenever the KF is inited.
4. Change `+=` to `-=` at `:206-207`.
5. Keep the position feed (`:487-511`) gated on mode 2.
6. Log `x[6..7]` and the per-axis innovation in slot3 (`ground_station/comm/boot_default_layout.py`).

CPU cost (inferred, not measured): about 300 MAC for predict plus ≤3 scalar 4-state updates per 5 ms tick. That is roughly 1-2k cycles, about 10 µs at 168 MHz (~0.2%). This is already the cost of flying in mode 2.

**Model changes before any active flight**
- **Accel input:** in `:449`, use the tilt term only, `-1000.0f*vecxZ` and `-1000.0f*vecyZ`, not `Lin_Acc_*`.
- **Parameters:** q_acc 3e-3, R_of 1e-4, q_bof 1e-7, q_ba 1e-6, R_zupt 1e-4. These are the CHOSEN q_bof/q_ba with the WP-8 defaults rejected.
- **Health gate:** set the threshold to about 0.15 m/s. Trip it only on persistence (inferred: roughly 5 × innovation sd sustained for 0.5 s).
- **Acceptance criteria:** drop "autocorr < 0.3"; no estimator meets it. Use instead:
  - NIS 0.3-2
  - residual/ΔOF variance below 0.7 on every log
  - bias drift in flight under 1 cm/s
  - a shadow flight where KF velocity leads OF by 30-150 ms

**Active (mode 2)**
- Fly it only after one shadow flight confirms the replay numbers on the 200 Hz firmware stream.
- Then consider feeding the KF velocity to `locxsPID.FB` (`:605-606`). That is the only place the ~55 ms lead can help.

**"Just filter the accel": no.**
- A 2 Hz LPF cuts Lin_Acc sd from 114-176 mg to 29-46 mg, but what remains is still not motion (gain k 0.02-0.24).
- The useful information is in the attitude, not the accelerometer.
- Drift note (inferred): with q_bof 1e-7 the KF holds bias constant just like FIXED. The EKF therefore will not fix the hover drift; the position I-term saturation does.

### Risks
- **No ground truth.** "Position vs FIXED" (OF integration) measures disagreement with FIXED, not drift. No replay can say which estimator drifts less.
- **Attitude source:** the attitude is `Ctrler.roll/pitchPID.FB` from slot2, interpolated onto slot1's accel timestamps. The firmware uses `vecxZ` from the same tick, so its body-accel junk could be partly aliasing in the 50/100 Hz logs (inferred). The tilt result still holds; the size of the Lin_Acc penalty in firmware may be smaller.
- **Tilt gain:** k for the tilt term is 0.32-0.74, not 1. Possible causes are regression dilution from attitude error, OF scale, or drag (not separated). A unit-gain tilt input may still overshoot; consider 0.6× or estimating the gain.
- **OF is already fused:** of2_fix is IMU-fused inside the module, so the module's inertial data is counted twice (inferred).
- **Rebase bug impact:** about 0 in these logs only because s_of_bias stayed ≤0.05 cm/s. A real CMD 0x17 snap will hit it.
- **Tests unverified:** golden C-vs-Python tests are skipped on the laptop, so ekf_of.c is not checked against the Python model.
- **CPU cost** is inferred, not measured.

## thrust_rpm

**Bottom line:** Thrust estimation is telemetry only (it never feeds control), and all three estimators are logging wrong values: one has a unit bug, one reads an input that is never written, one uses a placeholder k_T that is 2.2x too high, and the RPM-to-motor pairing is wrong. The RPM hardware itself works on ch0-2: hover Σω² stayed within ±0.4% across 6 flights while hover PWM shifted about -70 ticks/V, so k_T can be calibrated from data we already have. RPM will not fix the horizontal drift: the accelerometer measures horizontal wind force directly, and the drift is a position/velocity integrator problem. RPM is most useful for (1) measuring mass, CG and payload torque as ground truth for the asymmetric-payload demo and (2) battery-sag-compensated thrust feed-forward for Z hold and dense trajectories.

### Findings
**Code status (thrust_estimators.c, the 103-line file)**
- Telemetry only, at 200 Hz after the mixer (StabilizerTask.c:1150-1167). The header says "no control feedback" (thrust_estimators.h:14).
- **empirical[] is broken.** The lookup table is in µs (1100-2000, thrust_estimators.c:23-33), but it is passed `mymotor` in 0.5 µs ticks (2000-4000; pwm.c:59-60). Every reading saturates at the top table entry: 12.8/12.8/12.9/12.9 N, min = max, in active15 and shadow14.
- **imu_total is broken.** Its input `imu_data.a_acc[_Z]` is never written anywhere: the only hits are the declaration (robot_types.h:344) and the call (StabilizerTask.c:1167). The logged value is a constant 9.697 N (= 0.9885·g, std 0.000).
- **blade_element[] uses placeholder k_T = 1.5e-5/1.6e-5** (thrust_estimators.c:38). The summed thrust is 21.5 N in active15 and 19.2 N in shadow14, against m·g = 9.70 N.
- **Pairing is wrong.** rpm[i] is paired with motor i+1 (StabilizerTask.c:1157-1165), but rpm.h:19-20 says a channel is a pin number, not a motor number.
- **Mass is unresolved.** The code has 0.9885 kg (thrust_estimators.c:17) and the sim plant has 1.2961 kg (sim/bench/plant.py:19). Neither is confirmed for "battery type 2".
- What is logged: raw `rpm_dbg_period_cyc/edges[0..3]` at 50 Hz (slot2) and `g_thrust_est.*` at 100 Hz (slot1). The averaged `rpm_dbg_rpm` (rpm.c:37) is not logged.

**Data check (FLYING phase, trimmed 3 s / 1 s)**

| tag | Vbat | PWM avg | RPM median ch0/1/2/3 | Σω² [1e6] | k_T @0.9885 / @1.2961 |
|---|---|---|---|---|---|
| active15 | 15.61 | 3002 | 5343/5951/5381/6106 | 1.428 | 6.79e-6 / 8.91e-6 |
| shadow13 | 16.08 | 2954 | 5150/6163/5103/6272 | 1.424 | 6.81e-6 / 8.93e-6 |
| active12 | 14.50 | 3065 | 5072/6221/4975/6397 | 1.427 | 6.80e-6 / 8.91e-6 |
| shadow10 | 14.64 | 3054 | 5115/6202/4945/6393 | 1.425 | 6.80e-6 / 8.92e-6 |
| active8 | 15.00 | 3039 | 5171/6117/5061/6289 | 1.418 | 6.84e-6 / 8.97e-6 |
| active6 | 15.08 | 3036 | 5085/6084/5067/6438 | 1.426 | 6.80e-6 / 8.92e-6 |

- shadow14 is excluded because 42% of its ch3 samples are bad. pidonly7 has no FLYING phase.
- **Battery sag:** a least-squares fit over 7 flights gives hover PWM ≈ **-70 ticks/V**, while Σω² does not move.
- **ch0-2 are clean:** no frozen edges. Outliers (more than 15% off the 1 s median) are 0-0.06% of samples in active15 and 0.8-2.2% in shadow14. Sample-to-sample std is 71-118 RPM. Best PWM→RPM correlation lag is 40-60 ms.
- **ch3 is faulty.** 13-42% of samples are outliers (2.5% in active6). They cluster at 0.5x and 0.67x the true rate (missed marks) and 1.5-2x (extra edges). In active15 the edge-count RPM is 5715 against a period median of 6106. This is a sensor/mark problem (inferred).
- **Mapping:** regressing RPM on the yaw mixer term gives ch0 and ch2 negative in all 4 flights tested, ch1 positive in all 4. So ch0/ch2 are the CCW pair (M3/M4) and ch1/ch3 the CW pair (M1/M2; ch3 by elimination). Which channel is which corner could not be resolved, because roll and pitch commands are correlated at -0.5 to -0.84.
- **Yaw-trim asymmetry:** the CW pair carries 56-61% of ω², driven by the yaw command (uz = 99-164 ticks). That costs thrust headroom on M1/M2.
- **Steady roll/pitch trim:** PWM differences are ux ≈ 33-39 and uy ≈ -14 to -18 ticks. That works out to roughly a 5-7 mm equivalent CG offset (inferred; assumes dT/dPWM ≈ 3e-3 N/tick and 0.141 m arm).

### Recommendation
**Uses of RPM, ranked for this operator**
1. **Mass/CG/payload-torque identification.** Gives ground truth for the asymmetric-load demo, and later an instant trim at takeoff.
2. **Battery-sag-compensated collective thrust.** Improves Z hold and the thrust feed-forward for dense waypoint tracking.
3. Vertical disturbance observer: F_ext,z = m·f_z − k_T·Σω².
   - Horizontal drift does **not** need RPM. In the body frame, f_xy = R^T F_ext,xy / m, so the accelerometer already measures wind force. The catch is accel bias: 10 mg reads the same as 0.1 N of wind.
   - So for drift: add the velocity-loop I-term, raise the position I-limit, and use the accel-input EKF.
4. Measured thrust as the MRAC actuator signal instead of PWM. Later.
5. Motor and sensor health monitoring.

**#1: the math**
- T_i = k_T·ω_i², with ω = π·RPM/30. Calibrate at hover: k_T = m·g/Σω².
- Mass: m̂ = k_T·Σω²/(g + a_z).
- Torques: τ_roll = a·Σ s_y,i·T_i and τ_pitch = a·Σ s_x,i·T_i, where a = 0.1414 m (plant.py:21, unverified) and s are the corner signs.
- CG offset: Δy = τ_roll/(m̂g), Δx = −τ_pitch/(m̂g).
- For the payload, use loaded minus unloaded. This cancels per-motor k_T bias.
- Example (inferred): 100 g at 10 cm gives 0.098 N·m. That is about 0.17 N per motor, roughly 3.5% RPM (about 190 RPM), which a 1 s average resolves easily.

**#1: implementation**
- `thrust_estimators.c`:
  - Use a single calibrated k_T.
  - Express the lookup table in ticks.
  - Add a `motor_of_ch[4]` remap.
  - Drop `imu_total`, or feed it `Lin_Acc_Z_body` (see StabilizerTask.c:533).
  - Add `sum_w2`, `tau_roll`, `tau_pitch` and `mass_hat`, each low-pass filtered with a 1 s time constant.
- StabilizerTask.c:1155-1167: pass the remapped channels.
- rpm.c:236-246: replace the 4-revolution mean with a median of 5 (stops single missed edges). If ch3 stays at 13-42% bad, median-of-5 fails about 1.8% of the time at 13% and about 35% at 42% (inferred).
- Ground station: add `hover_thrust_id()` to `rpm_signals.py`, plus a flightlab plot of PID I-term and MRAC u_ad against the RPM-measured payload torque. This plot is the demo's headline metric.

**#2: the math and plan**
- Hover feed-forward: Throttle_th(V) = 3002 + 70·(15.61 − V_f), where V_f = LPF(real_voltage, 2 s), capped at ±150.
- It replaces the fixed 3050 at StabilizerTask.c:1104. The old law commented out at :1099 used 105.5 ticks/V for a heavier configuration.
- Then fit ω_i = α(V)·(PWM_i − β) per pair from existing logs.
- Trajectory feed-forward: PWM_i = β + √(T_i/k_T)/α(V).
- Online RPM feedback is not advised: the 4-revolution average adds about 22 ms of delay, and ch3 is unreliable.

**Measure first**
1. Weigh the drone with battery type 2 to settle 0.9885 vs 1.2961 kg; this sets k_T at 6.80e-6 or 8.92e-6.
2. Map channels to corners: spin one motor at a time (operator-run) and see which `rpm_dbg_edges` counter ticks.
3. Fix the ch3 sensor (alignment and mark contrast).
4. Optional: hover with a known 50 g mass on each arm in turn to get per-motor k_T. This matters because the CW/CCW props may differ.

### Risks
- **Mass sets k_T.** k_T scales directly with the unverified mass. The constant Σω² only shows that k_T is the same across flights, not what its value is.
- **Per-motor k_T unknown.** The 56-61% CW thrust share may partly come from different CW and CCW prop coefficients rather than yaw trim, so CG figures that assume one shared k_T are biased. Payload deltas (loaded minus unloaded) remove most of this.
- **Corner mapping and ch3 block the CG estimate.** The geometry (0.1414 m arm, X layout) comes from the sim, not a measurement.
- **The -70 ticks/V comes from comparing separate flights.** Other flight-to-flight differences (shadow vs active, room airflow, battery state) are mixed into it. A slope within a single flight was not separable.
- **The model is simplified.** T = k_T·ω² ignores ground effect, inflow from room airflow, and climb rate. Those errors grow in dense trajectories.
- **Roll/pitch mapping is weakly identified.** Correlations are 0.2-0.7 and R² is at most 0.51. The yaw-pair assignment is consistent across all 4 flights tested.
- **Dynamic lag and rate are approximate.** The 40-60 ms lag figure is quantised to the 50 Hz log rate, and the firmware RPM is a 4-revolution average.
- **Inferred, not measured:** dT/dPWM ≈ 3e-3 N/tick, the 5-7 mm equivalent CG, the 100 g payload example, and the median-of-5 failure rates.
- Every number above was measured this session on active15, shadow14, shadow13, active12, shadow10, active8 and active6, except the items marked inferred. Scripts are in scratchpad/wf1/rpm/.
