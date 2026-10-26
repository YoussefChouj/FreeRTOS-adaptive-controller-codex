# Run sheet: Z / climb-rate complementary filter bring-up (`z-vz-cf`, tests A1–A3 & F1–F2)

Spec: `.agent-ops/briefs/z-vz-complementary-filter.md`.
Target code: `Alt_UpdateFB()` in `TASK/StabilizerTask.c` (`ALT_VZ_MODE_FD = 0` legacy per-tick ToF finite difference vs. `ALT_VZ_MODE_CF = 1` world-up accelerometer + ToF complementary filter).
Every threshold and initial gain below (`k1 = 0.21`, `k2 = 0.45`, `k3 = 0.0` or `0.1`, `AccMax = 20.0 m/s^2`) is **PROPOSED** unless explicitly marked **MEASURED**. Stop at the first failed gate.

```
host U1-U6 + S1 sim ─> bench A1 (ToF frame rate) ─> bench A2 (a_up sign & bias) ─> bench A3 (CF vs FD dvz)
                                                                                          │ pass
                                     flight F2 (lab_z_vz_steps in CF) <─ flight F1 (60 s hover FD vs CF)
```

---

## 1. Preconditions & Roles (Who does what)

| Phase | Drone state | Operator role | Agent / supervisor role | Safety & authorization gates |
|---|---|---|---|---|
| **0. Host pre-gate** (`U1`–`U6`, `S1`) | Off / not required | Reviews `S1` pole and noise-sweep results | Runs host unit tests and closed-loop Z sim (`.agent-ops/research/z-vz-cf/sim.py`) | Do **not** flash or fly gains that `S1` has not verified closed-loop |
| **1. Bench bring-up** (`A1`–`A3`) | **Disarmed, propellers OFF**, on bench | Present at bench; holds airframe level/still or moves it vertically by hand (~0.2–0.8 m) | Starts 200 Hz telemetry log; switches `g_alt_vz_mode` via `VAR_WRITE` (`0x2D`) **while disarmed** | Agent **NEVER** arms on the bench; props remain off throughout `A1`–`A3` |
| **2. Reflash gate** (before `F1`) | **Disarmed**, on bench/pad | Reviews diff and approves flash in chat | Supervisor on `main` runs `python -m ground_station.flashtool.rebuild_and_flash --yes` then `python -m ground_station.livewatch verify` | Never flash while armed; stop on non-zero exit |
| **3. Flight tests** (`F1`–`F2`) | On flight pad, resting pack `>= 15.8 V` | Opens battery session (`allow_agent_arm` on, says `"go"`, RC transmitter ON with `ch10` hard-kill active); approves any disarmed mode/gain write in chat | Writes `g_alt_vz_mode` **only while disarmed** after operator chat OK; verifies readback; runs 200 Hz capture / `lab_z_vz_steps.fly` | Mode/gain writes blocked while armed; session ends at 30% battery or any abort; each pack needs a fresh `"go"` |

---

## 2. Variables to Log (200 Hz / 5 ms tick)

All bench (`A1`–`A3`) and flight (`F1`–`F2`) captures **must** log at **200 Hz** (`SUBSCRIBE_ONLY` 5 ms control tick) so per-tick climb-rate steps (`dvz`), thrust kicks (`dU`), and ToF frame arrivals are captured without aliasing:

| Group | Variables | Purpose |
|---|---|---|
| **ToF & legacy FD path** | `ano_of.alt_update_cnt`, `of_alt_cm`, `of2_raw_h`, `of2_h`, `of2_h_v`, `of2_h_f2_v` | Frame arrival counter, raw/gated height, and legacy FD climb-rate (`of2_h_f2_v` computed in both modes for direct comparison) |
| **Mode selector** | `g_alt_vz_mode` | `0` = `ALT_VZ_MODE_FD` (default), `1` = `ALT_VZ_MODE_CF` |
| **Complementary filter (`alt_cf`)** | `alt_cf.h`, `alt_cf.vz`, `alt_cf.b_az`, `alt_cf.e`, `alt_cf.a_up`, `alt_cf.n_corr` | CF height (m), climb rate (m/s), accel bias estimate (m/s²), ToF residual `h_tof - h` (m), world-up linear accel (m/s²), accepted-sample correction count |
| **Z PID loops** | `Ctrler.Z_posPID.Des`, `Ctrler.Z_posPID.FB`, `Ctrler.Z_ratePID.Des`, `Ctrler.Z_ratePID.FB`, `Ctrler.Z_ratePID.U` | Outer height setpoint/feedback, inner climb-rate setpoint/feedback, and Z rate-PID thrust output `U` |

---

## 3. Bench Procedure (`A1`–`A3`) — Disarmed, Props Off

### A1: ToF frame rate vs. value-change rate

1. **Setup**: Drone disarmed on the bench, propellers off, ToF beam pointing at clear floor/bench surface.
2. **Capture**: Start 200 Hz logging of the Section 2 variables:
   - **Segment 1 (30 s still)**: Operator leaves the drone stationary on the bench for 30 s.
   - **Segment 2 (30 s hand motion)**: Operator moves the drone smoothly up and down by hand between ~0.2 m and ~0.8 m above the surface for 30 s.
3. **Compute**:
   - **Height frame rate (Hz)**: `Delta(ano_of.alt_update_cnt) / elapsed_s` in both still and moving segments.
   - **Value-change rate (Hz)**: rate of ticks where `|of2_h[k] - of2_h[k-1]| > 2 mm` (2026-10-24 flight logs measured 20 Hz mode run = 10 ticks while moving, 93% repeat ticks overall — **MEASURED**).
   - **Correction count rate**: `Delta(alt_cf.n_corr) / elapsed_s`.
   - **Bench `a_up` noise**: standard deviation of `alt_cf.a_up` during the 30 s still window (reference: 2026-10-26 flight logs `Acc_Z_Real` >5 Hz std was `0.73–0.94 m/s^2` — **MEASURED**).
4. **Pass line (PROPOSED)**:
   - `ano_of.alt_update_cnt` increments steadily at **`>= 20 Hz`**.
   - `alt_cf.n_corr` increments on **every accepted ToF frame** (`ano_of.alt_update_cnt` step passing range/median-3/jump gates), including stationary ticks where `of_alt_cm` / `of2_h` repeats the same value.

### A2: World-up linear acceleration sign & stationary bias

1. **Setup**: Drone disarmed, propellers off, placed level on the bench.
2. **Capture (200 Hz)**:
   - **Still (30 s)**: Hold/leave drone level and stationary for 30 s.
   - **Upward lift**: Operator lifts the drone briskly upward by hand by **`~0.5 m`** and holds briefly.
3. **Compute**:
   - Stationary mean and std of `alt_cf.a_up` over the 30 s still window.
   - Sign of the initial `alt_cf.a_up` pulse at lift onset and sign of `alt_cf.vz` during the upward travel.
4. **Pass line (PROPOSED)**:
   - Stationary 30 s mean `alt_cf.a_up` is within **`[-0.1, +0.1] m/s^2`**.
   - On upward hand lift, `alt_cf.a_up` goes **positive first**, and `alt_cf.vz` stays **positive** while lifting upward.
   - *Fail action*: If `alt_cf.a_up` goes negative first on an upward lift, stop, flip `ALT_CF_ACC_SIGN` in firmware, rebuild/reflash, and re-run `A2`.

### A3: CF `vz` vs. FD `vz` on hand motion

1. **Setup**: Drone disarmed, propellers off. While disarmed, write `g_alt_vz_mode = 1` (`ALT_VZ_MODE_CF`) via `VAR_WRITE` (`0x2D`) and confirm readback (`g_alt_vz_mode == 1`, `alt_cf.h` initialized to `of2_h`, `alt_cf.vz == 0`).
2. **Capture (200 Hz)**: Operator moves the drone smoothly up and down by hand (~0.2–0.8 m) for **30 s** (may use the 30 s hand-motion window from `A1` if recorded with `alt_cf` active). Both `alt_cf.vz` and legacy `of2_h_f2_v` are logged on the same ticks.
3. **Compute**:
   - Per-tick climb-rate change `|dvz[k]| = |vz[k] - vz[k-1]|` for both `alt_cf.vz` and `of2_h_f2_v`.
   - Share of ToF repeat ticks (`of2_h` unchanged) where `|dvz| > 0.05 m/s` for `alt_cf.vz`.
   - Overall and change-tick p90 of `|dvz|` for `alt_cf.vz` vs. `of2_h_f2_v` (2026-10-24 session `20261024-213634` FD baseline p90 at change ticks = **`0.60 m/s`**, **MEASURED**).
4. **Pass line (PROPOSED)**:
   - In CF (`alt_cf.vz`), the repeat-tick share of `|dvz| > 0.05 m/s` is near zero (**`< 1%`**).
   - Per-tick `|dvz|` p90 for `alt_cf.vz` is **at least 5x below** FD `of2_h_f2_v`.

---

## 4. Flight Procedure (`F1`–`F2`) — After Bench Pass & Review

Pre-flight checklist for every flight below:
- Bench gates `A1`, `A2`, `A3` all passed.
- Resting battery voltage **`>= 15.8 V`**.
- RC transmitter ON, `ch10` hard-kill switch verified, operator opens battery session with `"go"`.
- Any `g_alt_vz_mode` write is performed **only while disarmed** and **only after operator approval in chat**, with readback verified before arming.

### F1: Hover A/B comparison — Mode FD (`z-vz-hover-fd`) vs. Mode CF (`z-vz-hover-cf`)

1. **Flight 1 (`z-vz-hover-fd`, `g_alt_vz_mode = 0`)**:
   - While disarmed on the pad, verify `g_alt_vz_mode == 0` (`ALT_VZ_MODE_FD`).
   - With 200 Hz logging of the Section 2 variables active, take off and hover for **60 s** at nominal PID gains (`Z_posPID` and `Z_ratePID` untouched). Land and disarm.
2. **Disarmed mode switch**:
   - With the drone disarmed on the pad and operator chat confirmation, write `g_alt_vz_mode = 1` (`ALT_VZ_MODE_CF`) via `VAR_WRITE` (`0x2D`).
   - Verify readback: `g_alt_vz_mode == 1`, `alt_cf.h` matches current `of2_h`, `alt_cf.vz == 0`.
3. **Flight 2 (`z-vz-hover-cf`, `g_alt_vz_mode = 1`)**:
   - On the same pack (if resting `>= 15.8 V`) or a matched fresh pack, at the **exact same PID gains**, take off and hover for **60 s** with 200 Hz logging. Land and disarm.
4. **Compute (steady hover window of both flights)**:
   - Per-tick thrust-command kick `|dU|` p90, where `U = Ctrler.Z_ratePID.U` and `dU[k] = U[k] - U[k-1]` (2026-10-24 FD baseline: `|dU|` median `86` / p90 `241` on height-change ticks vs. `4` / `12` on repeat ticks — **MEASURED**).
   - Altitude tracking error RMS: `rms(Ctrler.Z_posPID.Des - Ctrler.Z_posPID.FB)`.
   - Dominant Z oscillation frequency (Hz) and peak amplitude from the FFT of `Ctrler.Z_posPID.FB`.
   - In-flight `alt_cf.a_up` noise std (>5 Hz) to confirm `S1` noise regime (`~1 m/s^2` expected vs. `3 m/s^2` degradation threshold).
5. **Pass line (PROPOSED)**:
   - Per-tick `|dU|` p90 in mode CF (`g_alt_vz_mode = 1`) is **at least 3x lower** than in mode FD (`g_alt_vz_mode = 0`).
   - Z position RMS error in mode CF is **not worse** than mode FD (`<= 1.0x` FD within run-to-run noise margin).

### F2: Climb-rate step campaign in Mode CF (`lab_z_vz_steps.fly` / `z-vz-steps`)

1. **Setup**: Run only after `F1` passes. Drone disarmed on pad, pack resting `>= 15.8 V`. Confirm `g_alt_vz_mode == 1` (`ALT_VZ_MODE_CF`) after operator chat OK.
2. **Execute (`z-vz-steps`)**:
   - Dry-run first on the ground: `.\fly lab_z_vz_steps --dry-run` (script: `ground_station/service/campaigns/lab_z_vz_steps.fly`).
   - Ensure the Section 2 `alt_cf` and Z-PID signals are included at **200 Hz**.
   - On operator `"go"`, fly `.\fly lab_z_vz_steps --once`: takeoff to `1.0 m`, 8 s hold, z-only steps `1.0 -> 1.4 -> 0.6 m` at `v_cruise_mps = 0.2 m/s` (3 s dwell each), then `1.4 -> 0.6 m` at `v_cruise_mps = 0.4 m/s` (3 s dwell each), then land.
3. **Compute**:
   - Step-response overshoot (m), settling time (s), and climb-rate tracking error `rms(Ctrler.Z_ratePID.Des - Ctrler.Z_ratePID.FB)` across the `±0.2 m/s` and `±0.4 m/s` legs.
   - Check `Ctrler.Z_posPID.FB` and `Ctrler.Z_ratePID.U` for any new oscillation during cruise or dwell holds.
4. **Pass line (PROPOSED)**:
   - **No new oscillation** in hover holds or step transitions.
   - Step tracking (overshoot, settling time, and `vz` RMS error) is **not worse** than the mode FD baseline.

---

## 5. Branches & Fallbacks

| Event | Action |
|---|---|
| `A1` shows `alt_cf.n_corr` only increments on value change, not frame count | Firmware bug in new-sample trigger (`ano_of.alt_update_cnt`); fix and re-run `U1`–`U6` before `A2` |
| `A2` upward lift drives `alt_cf.a_up` negative first | Stop; flip `ALT_CF_ACC_SIGN`, rebuild, reflash, and repeat `A2` |
| `A2` still mean `|alt_cf.a_up| > 0.1 m/s^2` | Enable bias state (`k3 > 0`, e.g. `k3 = 0.1` **PROPOSED** after `U4`/`S1` check) so `alt_cf.b_az` removes stationary offset |
| `F1` in-flight `alt_cf.a_up` noise std `>= 3 m/s^2` or Z RMS worse than FD | Land, disarm, revert `g_alt_vz_mode = 0`, re-run `S1` with the measured in-flight `a_up` spectrum to lower `(k1, k2)` bandwidth |
| Any oscillation or unexpected climb/descent in `F1` or `F2` | Operator takes RC control or triggers `ch10` kill / lands; once disarmed, set `g_alt_vz_mode = 0` (`ALT_VZ_MODE_FD`) and verify readback |
