# Live camera fusion, explained from zero (2026-10-12)

Written after `r5_live_2`, the first real live-fusion flight. Every number is either **measured** (with the log
named) or labelled **PROPOSED** / **illustrative**. Firmware described: JX_FLY_26e0a177 (EXT_POS).

## Bottom line

| Question | Answer |
|---|---|
| Does the EKF receive the camera? | **No.** The camera never enters the EKF. A separate offset `c` is learned from the camera and added to `earth_x/y` *after* the EKF. |
| Why does hover still wander? | The correction is right on average, but it enters the position loop in ways the loop misreads: (1) every correction step looks like real motion to the P and D terms, (2) fixes are 0.14 s late, (3) the camera frame is rotated 12-18 deg from the EKF frame, (4) re-anchor bursts move the feedback by up to 50 cm in 0.5 s. |
| Was the gain (0.9) too high? | **No.** Measured camera noise is 1-2 cm, so the best constant gain is 0.81-0.84. Gain 0.3 would add more lag than it removes noise. That idea is dropped. |
| What would an expert do? | Fuse the camera **inside** the EKF as a delayed position measurement (PX4 EKF2 / ArduPilot EKF3 / ETH MSF all do this). Then velocity and OF bias get corrected too, and the controller sees one smooth estimate. Hide any remaining jumps from the controller (PX4 publishes "reset deltas" for this). Estimate the frame yaw instead of guessing it. |

**Analogy.** The EKF is a blindfolded walker counting steps. The camera is a friend across the room calling out
"you are *here*". Today the friend's calls never change how the walker counts. Instead, someone keeps sliding the
pin on the walker's map. The pin ends up in the right place, but every slide feels to the walker like a sudden
stumble. And because the friend calls 0.14 s late and holds the map 15 deg rotated, some slides go the wrong way.
The expert fix is to let the walker *hear* the friend, so the step counting itself improves (that is "fusing
inside the EKF").

---

## 1. Words you need

| Word | Meaning here |
|---|---|
| **State** | The numbers the filter believes about the drone. Here: position, velocity, and sensor biases. |
| **EKF** | Extended Kalman filter. It predicts the state forward in time, then corrects it with each measurement, weighting each correction by how much it trusts the prediction versus the sensor. |
| **OF** | Optical flow. A downward camera chip that measures how fast the floor slides by, which gives velocity. Integrating velocity gives position, and that drifts. |
| **Earth frame** | x/y fixed to the room (after take-off). `ano_of.earth_x/y`, in cm. |
| **Heading frame** | x/y that turn with the drone's yaw. The EKF works in this frame. |
| **Yaw** | The drone's heading angle. It rotates heading-frame motion into the earth frame. |
| **Fix** | One camera position report, `z = (zx, zy)` in cm, earth frame. About 25 per second. |
| **Innovation `e`** | Measurement minus prediction: "how far off were we?" |
| **Gate** | A size limit on `e`. A larger `e` is treated as suspicious and rejected. |
| **Gain `K`** | The fraction of `e` we believe per fix (0 = ignore it, 1 = jump fully to the camera). |
| **Latency / delay** | The time between the photon hitting the phone camera and the fix reaching the drone. |
| **PID** | The controller. `E = Des - FB` (setpoint minus feedback) becomes a tilt command `U = Up + Ui + Ud`. |
| **Des / FB** | `locxPID.Des` is where we want to be. `locxPID.FB` is where we think we are (cm). |

---

## 2. The whole chain, end to end

```
phone camera (looking at the floor/drone)
  -> scrcpy window on the laptop            (screen mirror)
  -> mss screen grab                        (live_tracker.py reads the window pixels)
  -> CoralTracker: find the drone's ring    -> pixel (u, v)
  -> ring_xy: pixel + height -> floor x/y   (camera pose R, t from the ArUco floor markers; height from telemetry z)
  -> JumpGate: drop single-frame jumps
  -> PadOrigin: subtract the pad position frozen at first take-off (pad-relative)
  -> FixScheduler: about 25 fixes/s
  -> EarthAlign: rotate by --yaw-deg (+ mirror/scale) into the EKF earth frame
  -> encode 12-bit/axis (0.5 cm quantum), UDP :47800
  -> stream_log relay -> WiFi (ESP) -> USART3 -> firmware g_ext_pos_post {x_cm, y_cm, t_meas_ms, seq}
  -> Ext_Pos_Tick() every 5 ms control tick
```

The firmware dates each fix `t_meas = arrival - (frame age + g_ext_pos_delay_ms)`. The tracker sets
`g_ext_pos_delay_ms` with `--delay-ms`. In `r5_live_2` it was not set (default 0), so the camera and
link latency were **not** compensated. Section 5.3 covers what that costs.

---

## 3. What the drone does without the camera

### 3.1 The EKF (it only sees IMU + optical flow)

The state is 8 floats, in metres, in the heading frame:

```
x = [pos_x, vel_x, bof_x, pos_y, vel_y, bof_y, ba_x, ba_y]
      position, velocity, OF bias (per axis), accelerometer bias
```

Every Kalman filter has the same two steps:

**Predict** (each tick, dt = 5 ms). Use physics to move the belief forward:

```
x⁻ = F x + B u          (pos += vel*dt, vel += (accel - ba)*dt, biases stay)
P⁻ = F P Fᵀ + Q         (uncertainty grows by the process noise Q)
```

**Update** (when an OF reading arrives). Compare the sensor with what the state predicts it should read:

```
e = z_of - H x⁻         (innovation; the OF reads roughly velocity + OF bias)
S = H P⁻ Hᵀ + R         (expected size of e: state uncertainty + sensor noise R)
K = P⁻ Hᵀ S⁻¹           (Kalman gain: trust the sensor more when P⁻ is large or R small)
x = x⁻ + K e
P = (I - K H) P⁻
```

The key point for later: `K` is a column with one entry per state. A single OF reading corrects velocity *and*
biases *and* position, through the correlations stored in `P`. **The camera is not one of the `z`s here.**

### 3.2 From EKF position to the controller (mode 2, every 5 ms)

`TASK/StabilizerTask.c:915-941`, abridged:

```c
float ekf_dx = (s_ekf_of.x[0] - s_ekf_prev_px) * CM_PER_M;   /* EKF step this tick, cm */
float ekf_dy = (s_ekf_of.x[3] - s_ekf_prev_py) * CM_PER_M;
...
ekf_dx *= g_of_scale;                                         /* OF scale calibration */
ekf_dy *= g_of_scale;
ano_of.earth_x += ekf_dx * Cos_Yaw_01 + ekf_dy * Sin_Yaw_01;  /* rotate heading -> earth, accumulate */
ano_of.earth_y += ekf_dy * Cos_Yaw_01 - ekf_dx * Sin_Yaw_01;
ano_of.earth_x_ture =  ano_of.earth_y;                        /* axis swap for the PID */
ano_of.earth_y_ture = -ano_of.earth_x;
Ctrler.locxPID.FB = ano_of.earth_x_ture;
Ctrler.locyPID.FB = ano_of.earth_y_ture;
```

As formulas, with yaw ψ and scale s:

```
Δx_e = s·100·(Δpos_x·cos ψ + Δpos_y·sin ψ)
Δy_e = s·100·(Δpos_y·cos ψ - Δpos_x·sin ψ)
earth += Δ_e   (each tick)
locxPID.FB =  earth_y
locyPID.FB = -earth_x
```

**Worked example (illustrative numbers).** In one tick the EKF moves `pos_x` by 0.0020 m and `pos_y` by 0, with
s = 1.04 and ψ = 30 deg. Then `Δx_e = 1.04·100·0.0020·cos30 = 0.180 cm` and `Δy_e = -1.04·100·0.0020·sin30 = -0.104 cm`.
`earth_x` grows by 0.180 and `earth_y` shrinks by 0.104, so `locxPID.FB` (= earth_y) drops by 0.104 cm.

Note that `earth` is a running **sum of increments**. Any error in a step (OF scale, bias, a yaw error at that
moment) stays in the sum forever. That is why it drifts. In `r5_live_2` the raw EKF path and the camera
disagreed by up to about 2 m (c_x swung -486 to +32 cm, measured).

### 3.3 The position PID

`API/pid.c`: `E = Des - FB`, `Up = Kp·E`, `Ui = Ki·ΣE`, `Ud = Kd·(E - PreE)`, each clamped. The locx/locy row is
`Kp 0.8, Ki 0.0013, Kd 4.0, UMax 300, UpMax 300, UiMax 5, UdMax 50, SumEMax 3850, EMin 10`. `Pos_Compute()`
calls it once per control tick. `(E - PreE)` is the change in error over one tick. Des is fixed in hover, so this
equals `-ΔFB`: the D term is a **velocity brake computed from changes in FB**. Remember that for section 5.5.

---

## 4. Where the camera enters: after the EKF, as an offset

The idea: keep a correction `c` (cm, earth frame) and use `earth = raw EKF path + c`. Each fix nudges `c` so that
`raw + c` matches the camera. The EKF is never touched (comment at `StabilizerTask.c:871`: *the EKF is not touched*).

There are two copies of the correction:
- `c`: the **target**, updated once per fix;
- `a`: the part **already applied** to `earth_x/y`, which ramps toward `c` at most 0.5 cm per tick.

So `raw path p = earth - a`.

### 4.1 Every 5 ms: remember the raw path (`Ext_Pos_Tick`)

```c
ext_pos_ring_tick(&s_ext_pos_ring, now_ms, ano_of.earth_x - g_ext_pos.a_x, ano_of.earth_y - g_ext_pos.a_y);
```

The ring keeps `p(t)` every 50 ms for 32 slots (1.55 s), so a late fix can be compared with where we were
*when the photo was taken*, not where we are now. `ext_pos_ring_at()` interpolates linearly between slots:

```
p(t_meas) = p[a] + w·(p[b] - p[a]),   w = (t_meas - t[a]) / (t[b] - t[a])
```

If `t_meas` is older than the ring, the result is `EXT_POS_FIX_OLD` (counted in `n_old`, ignored).

### 4.2 When a fix arrives: innovation and gate (`ext_pos_fix`, `API/ekf_of.c:361`)

```c
s->e_x = zx - (px + s->c_x);
s->e_y = zy - (py + s->c_y);
if (s->e_x * s->e_x + s->e_y * s->e_y > EXT_POS_GATE_CM * EXT_POS_GATE_CM) {   /* 40 cm */
    s->n_rej++;  s->rej_run++;   return EXT_POS_FIX_GATED;
}
s->n_ok++;  s->rej_run = 0;  s->last_ok_ms = now_ms;  s->stale = 0;
return EXT_POS_FIX_OK;
```

```
e = z - (p(t_meas) + c)                 innovation, cm
accept  if |e| ≤ 40 cm                   gate (fixed radius)
```

### 4.3 The step (`ext_pos_step`, `API/ekf_of.c:396`)

```c
if (res == EXT_POS_FIX_OLD || (res == EXT_POS_FIX_GATED && s->rej_run < EXT_POS_REJ_N)) return 0;  /* REJ_N = 3 */
if (res == EXT_POS_FIX_GATED) s->n_anchor++;          /* 3rd+ gated fix in a row = re-anchor */
*dx = EXT_POS_GAIN * s->e_x;  *dy = EXT_POS_GAIN * s->e_y;                                        /* 0.9 */
d = sqrtf(*dx * *dx + *dy * *dy);
if (d > EXT_POS_SLEW_CM) { *dx *= EXT_POS_SLEW_CM / d; *dy *= EXT_POS_SLEW_CM / d; }              /* 10 cm */
s->c_x += *dx;  s->c_y += *dy;
```

```
Δc = sat₁₀(0.9·e)      sat₁₀(v) = v·min(1, 10/|v|)     (clamp the vector length to 10 cm)
c ← c + Δc
```

The rule for gated fixes is "3 strikes, then believe it". One or two big innovations are treated as camera glitches.
From the third gated fix in a row, the camera is trusted again, 10 cm per fix, until a fix falls back inside the gate.

### 4.4 Every 5 ms: spread the step into `earth` (`ext_pos_spread`, `API/ekf_of.c:422`)

```c
const float m = EXT_POS_SLEW_CM / (float)EXT_POS_SPREAD_N;      /* 10/20 = 0.5 cm per tick */
float ex = s->c_x - s->a_x, ey = s->c_y - s->a_y;
if (|(ex,ey)| > m) scale (ex,ey) down to length m;
s->a_x += ex;  s->a_y += ey;
/* caller: */  ano_of.earth_x += ex;  ano_of.earth_y += ey;
```

```
Δa = sat₀.₅(c - a),   a ← a + Δa,   earth ← earth + Δa
```

0.5 cm per 5 ms is 1 m/s. The spread exists so a 10 cm step does not hit the PID in a single tick. It still
reaches FB, though, just as a 1 m/s ramp.

### 4.5 Stale

`ext_pos_stale_tick()` sets `stale = 1` when no fix has been accepted for 3 s. **Nothing else happens.** `c` just
holds its last value, and the drone continues on OF only, with no alarm on the RC side. (`stale` was 0 for the whole
of `r5_live_2`, measured.)

### 4.6 What kind of filter is this?

Ignore the clamps and assume `p` has no error between fixes. Then `c` follows a constant-gain recursion:

```
c_k = c_{k-1} + K·(y_k - c_{k-1}),     y_k = z_k - p(t_k)    (the offset the camera reports)
```

This is a first-order low-pass filter, or "alpha filter", on the offset `y = camera - raw path`. Its time constant
per fix interval Δt is:

```
τ = -Δt / ln(1 - K)          K = 0.9, Δt = 40 ms  ->  τ = 0.017 s
                             K = 0.3, Δt = 40 ms  ->  τ = 0.11 s
```

It is what a Kalman filter becomes in steady state when the offset is modelled as a random walk:

```
P⁻ = (q + sqrt(q² + 4qr)) / 2,    K = P⁻ / (P⁻ + r)
q = offset random-walk variance per fix,  r = camera noise variance
```

In words: the faster the offset wanders compared with the camera noise, the closer K gets to 1.

---

## 5. Worked examples

Axes: everything below is `earth_x`, and `locyPID.FB = -earth_x`.

### 5.1 A normal fix (illustrative numbers)

Values at the photo time: `p(t_meas) = 120.0`, `c = -30.0` (so we believe x = 90.0). The camera says
`z = 93.0`.

| Step | Calculation | Result |
|---|---|---|
| innovation | `e = 93.0 - (120.0 + (-30.0))` | 3.0 cm |
| gate | 3.0 ≤ 40 | accepted, rej_run = 0 |
| step | `0.9 · 3.0 = 2.7` ≤ 10 | c = -27.3 |
| spread | a: -30.0 → -29.5 → -29.0 → -28.5 → -28.0 → -27.5 → -27.3 | 6 ticks = 30 ms |
| controller | earth_x +2.7, so `locyPID.FB` -2.7 | E changes by +2.7 cm; the drone moves 2.7 cm toward where the camera says it should be |

This is the intended behaviour: the camera says we are 3 cm further than we thought, so the position loop
corrects by about 3 cm.

### 5.2 A gated burst, then a re-anchor (shape of the measured bursts)

OF loses scale during a fast move, and the raw path falls 88 cm behind the camera (88 cm was the measured p50
|e| at anchors in `r5_live_2`). Fixes come every 40 ms.

| t (ms) | Fix | e before (cm) | Result | Δc (cm) | c - a still to spread (cm) |
|---|---|---|---|---|---|
| 0 | 1 | 88 | gated, rej_run 1 | 0 | 0 |
| 40 | 2 | 88 | gated, rej_run 2 | 0 | 0 |
| 80 | 3 | 88 | **re-anchor** (rej_run 3) | +10 | 10 |
| 120 | 4 | 78 | re-anchor | +10 | 20 - 4 = 16 |
| 160 | 5 | 68 | re-anchor | +10 | 22 |
| 200 | 6 | 58 | re-anchor | +10 | 28 |
| 240 | 7 | 48 | re-anchor | +10 | 34 |
| 280 | 8 | 38 | accepted (inside gate) | +10 (clamped 0.9·38 = 34) | 40 |
| 320 | 9 | 28 | accepted | +10 | 46 |
| ... | ... | ... | `c` converges in about 0.4 s | | `a` needs about 0.9 s at 1 m/s to apply 88 cm |

(e here is measured against `c`, which already includes steps that are not yet applied. That is why e falls by
10 per fix even though `a` lags.)

What the drone feels: for about 0.9 s, FB slides 88 cm at 1 m/s. The PID sees "I am moving 88 cm away from Des"
and flies the drone 88 cm the other way. If the camera was right, this is the correct recovery from OF drift.
If the camera was wrong (a tracker jump that lasted ≥ 3 fixes) or rotated (5.4), the drone flies up to a metre
for nothing. Measured in `r5_live_2`: 106 anchors, 92 of them in bursts shorter than 1 s, for example
132.7-133.2 s. To the operator this looks like a "random wander".

### 5.3 Delay

In `r5_live_2`, a fix described where the drone was about **0.14 s before** the time the firmware used (measured
lag between the camera and the raw path; coherence only 0.30, so treat it as approximate). For a drone moving at
speed v:

```
error per fix = v · Δ = 0.30 m/s · 0.14 s = 4.2 cm   (illustrative v), pointing backwards along the motion
```

The correction pulls FB backwards ("you were behind"), so the loop is told the drone moved less than it did.
That is a lagged feedback loop, and lag in a feedback loop turns into overshoot and oscillation. The fix
needs no reflash: send the measured value with `--delay-ms`. `ext_pos_fix` then compares the camera with
`p(t_meas)` from the ring at the right time. The ring holds 1.55 s, so 0.14 s is well inside it.

### 5.4 Yaw misalignment

The tracker rotates camera x/y into the EKF earth frame by `--yaw-deg` (100 deg in live_2). If the true angle
differs by δ, a real displacement of length L shows up rotated by δ:

```
cross-track error = L · sin δ,   along-track error = L · (1 - cos δ)
δ = 15 deg, L = 50 cm  ->  13 cm sideways, 1.7 cm along   (sin 15° ≈ 0.26)
```

So when the drone moves 50 cm forward, the camera reports that it *also* slid 13 cm sideways. The correction
then pushes FB sideways, and the drone drifts sideways to "fix" it. Measured: the residual yaw in the first
40 s of `r5_live_2` was -12 to -18 deg. On `r5_shadow_3` the fitted angle wandered between 73 and 115 deg from one
window to the next. That means the EKF earth frame itself rotates during flight (yaw-estimate drift), so no single
`--yaw-deg` is right for a whole flight.

### 5.5 The fake-velocity D kick

The D term is `Ud = Kd·(E - PreE)`, with Kd = 4.0. During a spread, FB moves 0.5 cm per tick on top of the real motion:

| Source of ΔFB per tick | ΔFB (cm/tick) | Speed it implies | Ud = 4.0 · ΔE |
|---|---|---|---|
| real hover drift at 0.25 m/s (illustrative) | 0.125 | 0.25 m/s | 0.5 |
| correction spread (fixed by the firmware) | 0.5 | **1 m/s** | **2.0** |

The P term sees a 10 cm step as `Kp·10 = 8` extra units, delivered over 0.1 s. The D term then fights a 1 m/s
"motion" that never happened. That is a brake pulse for every correction, 4× the real hover motion in this
example. With fixes at 25 Hz, those pulses never stop. This is ranked cause #1.

### 5.6 Why gain 0.3 would not help (measured inputs, computed)

The constant-gain tradeoff for a fix interval Δt = 40 ms:

```
lag error   = v·Δt·(1-K)/K               (offset drifting at speed v)
noise left  = σ·sqrt(K/(2-K))            (camera noise σ passed through)
pick K that minimises lag² + noise²       (ground_station/analysis/live_tracker.py offset_noise())
```

Measured on `r5_live_2` with `live_tracker fit` (commit 73aa5ed4):

| Axis | Camera σ | Offset drift p90 | Best K | Lag at K = 0.9 | Noise at K = 0.9 | Lag at K = 0.3 | Noise at K = 0.3 |
|---|---|---|---|---|---|---|---|
| x | 1.7 cm | 70 cm/s | 0.84 | 0.31 cm | 1.54 cm | 6.5 cm | 0.71 cm |
| y | 0.8 cm | 28 cm/s | 0.81 | 0.12 cm | 0.72 cm | 2.6 cm | 0.34 cm |

The offset wanders fast (it is OF drift, which depends on motion), while the camera is quiet. So high gain is right,
and the wander is not camera noise.

---

## 6. Why this design is half a filter

| What it does | What it cannot do |
|---|---|
| Pins position to the camera on average (in-loop \|e\| p50 1.9, p90 9.2 cm, measured; the camera is not independent truth here) | Correct **velocity**. The EKF keeps its wrong velocity and OF bias, so the raw path keeps drifting at the same rate and `c` has to keep chasing it (swing of 5 m in live_2). |
| Uses the ring to compare at the photo time | Compensate delay that it is not told about (`delay_ms` = 0 in live_2) |
| Gates outliers | Size the gate by uncertainty. 40 cm is too big 40 ms after a good fix and too small after 2 s of OF-only flight. |
| Ramps corrections | Hide them from the controller. P and D both read them as motion. |
| Has a stale flag | Do anything about stale. There is no alarm, no hold and no re-acquire rule. |

---

## 7. State of the art: how the big autopilots do it

### 7.1 PX4 EKF2 (external vision / motion capture)

- Vision position is a **measurement inside the EKF**, next to GPS, flow and baro.
- **Delayed fusion horizon.** EKF2 runs its fusion at a point in the past, buffers every sensor in a FIFO, and
  fuses each one at the time it was taken. A complementary "output predictor" then propagates the result forward
  to "now" with buffered IMU data, and that output is what the controllers use ([PX4 ECL EKF guide][px4-ekf]).
- **`EKF2_EV_DELAY`** is the vision lag relative to the IMU. The docs suggest tuning it empirically: pick
  the value that gives the smallest innovations during dynamic moves ([PX4 external position][px4-ev]).
  Forum users still report about 0.2 s of residual lag in some setups ([PX4 forum][px4-evd]).
- **Reset bookkeeping.** `vehicle_local_position` carries `xy_reset_counter` and `delta_xy`: when the estimate
  jumps, consumers learn by how much, so they can shift their own references instead of chasing the jump
  ([VehicleLocalPosition msg][px4-vlp]). (The search did not confirm the controller-side code. The fields'
  stated purpose is enough for our design.)

### 7.2 ArduPilot EKF3 (ExtNav / VISO)

- Selected with `VISO_TYPE` and the `EK3_SRC1_*` source parameters. `VISO_DELAY_MS` is the delay (one user setup
  uses 50 ms, which is not a recommendation). `VISO_POS_M_NSE` is a minimum position noise fed to the EKF
  ([ArduPilot forum, developer list][ap-viso]).
- One practitioner measured the delay by lining up accelerometer peaks with peaks in the ExtNav velocity
  ([ArduPilot forum: ExtNav delay][ap-delay]). The same trick works for us with deliberate stick pulses.
- Field reports flag a weak spot we share: a vision-only outage that does not raise a failsafe ([failsafe thread][ap-fs]).

### 7.3 ETH MSF (Lynen et al., IROS 2013)

- A modular EKF for MAVs that is built for sensors with different rates, delays and outages. It estimates
  **calibration states** along with the pose ([paper][msf-paper], [ethzasl_msf][msf-wiki]).
- Two of its ideas matter here. First, keep a buffer of past states and apply a delayed measurement at its own
  time stamp. Second, estimate the vision-to-world transform (yaw, offset, scale) as filter states, so a rotated
  or drifting camera frame is learned instead of hand-set. (These details come from the MSF/SSF design
  literature. The search confirmed the paper, its framework and the calibration states, not the buffer code.)

### 7.4 Crazyflie (Bitcraze)

- The EKF fuses external position from mocap or Loco, each update weighted by a set standard deviation
  ([state estimation docs][cf-est], [blog][cf-blog]). Same pattern: the measurement goes into the EKF.

### 7.5 The textbook ways to handle a late measurement

| Method | Idea | Who |
|---|---|---|
| Ignore | Fuse as if fresh | (our `delay_ms` = 0 case) |
| Compare in the past, apply now | Innovation against the buffered past state, update applied to the current state | **our ring**, Larsen-style |
| Re-propagate | Roll back to the stored state, update, re-run the predictions | MSF |
| Delayed horizon + output predictor | The whole filter lives in the past, a light predictor brings it to now | PX4 EKF2 |

For position fixes at 25 Hz with 0.1-0.2 s of delay, "compare in the past, apply now" is accurate enough. We
already have the ring for it.

---

## 8. What an expert would do here (recommended design)

### 8.1 The real fix: the camera as an EKF measurement (PROPOSED, needs a reflash)

Add a second update to `ekf_of` alongside the OF update. The measurement is the camera position, compared
with the EKF position at the photo time:

```
e   = z_cam - pos(t_meas)                      (from a ring of EKF pos, same as now)
e_h = Rᵀ(ψ) e                                  (earth -> heading frame; H picks pos_x, pos_y)
H   = rows selecting x[0] (pos_x) and x[3] (pos_y)
S   = H P Hᵀ + R_cam                           (2x2)
gate: eᵀ S⁻¹ e ≤ 9.21                          (chi-square, 2 dof, 99%: replaces the fixed 40 cm)
K   = P Hᵀ S⁻¹                                 (8x2)
x  += K e_h,    P = (I - K H) P
```

**Worked example (illustrative numbers, not measured).** Say the position variance is `P_pp = 25 cm²` (5 cm sd), the
position-velocity covariance is `P_pv = 20 cm²/s`, and `R_cam = 4 cm²` (2 cm sd, close to the measured 1.7). A fix
with `e = 4 cm` gives:

```
K_pos = P_pp / (P_pp + R) = 25 / 29 = 0.86   -> position moves 3.4 cm     (same size as today's 0.9)
K_vel = P_pv / (P_pp + R) = 20 / 29 = 0.69/s -> velocity moves 2.8 cm/s   (today: 0, never corrected)
```

The second line is the whole point. Each camera fix also corrects velocity, and through the OF update it corrects
the OF bias as well. The raw drift that `c` had to chase in live_2 shrinks at its source. The controller gets one
continuous, physically consistent estimate: position and velocity agree, so `ΔFB` is real motion and D brakes
only on real motion.

### 8.2 Hide every remaining jump from the controller (PX4's reset-delta idea)

Whenever the estimate is shifted on purpose, shift the controller's memory by the same amount. If FB moves by `d`
on a correction tick:

```
PreE ← PreE - d      (so E - PreE, the D input, excludes the shift)
```

Optionally, `Des ← Des + d` as well, if the operator should not see the drone fly back. Keep Des alone if the
goal is to return to the true pad position. Sign and axis care: `locxPID.FB = earth_y` and
`locyPID.FB = -earth_x`. This is a 5-line change and it removes ranked cause #1 even before 8.1.

### 8.3 Measure the delay, then send it (no reflash)

Do deliberate stick pulses in shadow mode. Cross-correlate the camera velocity with the raw EKF velocity
(ArduPilot's accelerometer-peak trick), and pick the delay with the smallest innovations (PX4's advice). Send it
with `--delay-ms`.

### 8.4 Learn the frame yaw instead of guessing it

- Short term (no reflash): fit yaw over a sliding window (`live_tracker fit --window-s`) and update `EarthAlign`
  live.
- Better: put an ArUco marker on the drone, so the camera measures the drone's **heading** directly. Then frame yaw is
  measured, not fitted.
- Expert (MSF-style, reflash): add a frame-yaw state to the EKF.

### 8.5 Re-anchor and stale policy (PROPOSED, reflash)

- Re-anchor slower than normal fixes. One option is a smaller clamp per fix during `rej_run ≥ 3` and a slower
  spread. Another is to require a consistent innovation (same direction, ±10 cm) over the run before believing it.
  Both cap how far a bad burst can throw the drone.
- Stale: send the flag up the telemetry and to an LED. After a stale period, require N good fixes before
  re-anchoring, so the first fix after an outage cannot yank the drone.
- Log `locxPID.Des`, `locyPID.Des` and the RC channels in `r5_extpos_frames.md`, so the next review can separate
  operator input from estimator motion.

### 8.6 Order of work

| # | Change | Reflash | Fixes cause | How to verify |
|---|---|---|---|---|
| 1 | Measure delay, `--delay-ms` | no | delay | Innovation sd drops in shadow with stick pulses |
| 2 | Windowed or live yaw in the tracker | no | yaw | Residual yaw within ±3 deg per 20 s window |
| 3 | PreE shift on correction ticks (8.2) | yes | D/P kick | Ud trace has no 1 m/s pulses at fixes |
| 4 | Slower re-anchor + stale alarm + Des/RC logging | yes | bursts, outage | Anchor bursts < 30 cm; stale visible |
| 5 | Camera inside `ekf_of` (8.1), host-tested with `ext_pos_replay` on live_2/shadow_3 first | yes | root (velocity/bias drift) | Raw-path drift rate drops; \|c\| stays small |
| 6 | Frame yaw state or heading marker | yes / hardware | yaw | Fit yaw stable across windows |

**Next flight:** a hands-off hover A/B (shadow then live, same battery), after items 1-4.

---

## Sources

- [px4-ekf]: PX4 docs, Using the ECL EKF (fusion time horizon, output predictor) — https://docs.px4.io/v1.15/en/advanced_config/tuning_the_ecl_ekf
- [px4-ev]: PX4 docs, Using Vision or Motion Capture Systems for Position Estimation (EKF2_EV_DELAY) — https://docs.px4.io/main/en/ros/external_position_estimation
- [px4-evd]: PX4 forum, EKF2_EV_DELAY configuration — https://discuss.px4.io/t/ekf2-ev-delay-configuration/9740
- [px4-vlp]: PX4 uORB VehicleLocalPosition (xy_reset_counter, delta_xy) — https://docs.px4.io/main/en/msg_docs/VehicleLocalPosition
- [ap-viso]: ArduPilot forum, vision position estimate thread (VISO_POS_M_NSE, VISO_DELAY_MS) — https://discuss.ardupilot.org/t/vision-position-estimate-not-appearing-in-qgroundcontrol/23978?page=4
- [ap-delay]: ArduPilot forum, calculate the delay of the External Navigation System — https://discuss.ardupilot.org/t/calculate-the-delay-of-the-external-navigation-system-as-ekf3-inside-the-log/83095
- [ap-fs]: ArduPilot forum, EKF failsafe not triggering with VISO-only — https://discuss.ardupilot.org/t/bug-ekf-failsafe-not-triggering-in-sitl-with-viso-only-no-gps-during-odometry-outage/143506
- [msf-paper]: Lynen, Achtelik, Weiss, Chli, Siegwart, "A Robust and Modular Multi-Sensor Fusion Approach Applied to MAV Navigation", IROS 2013, doi:10.1109/IROS.2013.6696917 — https://www.pure.ed.ac.uk/ws/files/17220481/Lynen_et_al_2013_A_Robust_and_Modular_Mutli_Sensor_Fusion.pdf
- [msf-wiki]: ethzasl_msf wiki — https://github.com/ethz-asl/ethzasl_msf/wiki
- [cf-est]: Bitcraze, state estimation — https://bitcraze.io/documentation/repository/crazyflie-firmware/2022.09/functional-areas/sensor-to-control/state_estimators
- [cf-blog]: Bitcraze blog, state estimation: to be or not to be — https://www.bitcraze.io/2020/01/state-estimation-to-be-or-not-to-be/

[px4-ekf]: https://docs.px4.io/v1.15/en/advanced_config/tuning_the_ecl_ekf
[px4-ev]: https://docs.px4.io/main/en/ros/external_position_estimation
[px4-evd]: https://discuss.px4.io/t/ekf2-ev-delay-configuration/9740
[px4-vlp]: https://docs.px4.io/main/en/msg_docs/VehicleLocalPosition
[ap-viso]: https://discuss.ardupilot.org/t/vision-position-estimate-not-appearing-in-qgroundcontrol/23978?page=4
[ap-delay]: https://discuss.ardupilot.org/t/calculate-the-delay-of-the-external-navigation-system-as-ekf3-inside-the-log/83095
[ap-fs]: https://discuss.ardupilot.org/t/bug-ekf-failsafe-not-triggering-in-sitl-with-viso-only-no-gps-during-odometry-outage/143506
[msf-paper]: https://www.pure.ed.ac.uk/ws/files/17220481/Lynen_et_al_2013_A_Robust_and_Modular_Mutli_Sensor_Fusion.pdf
[msf-wiki]: https://github.com/ethz-asl/ethzasl_msf/wiki
[cf-est]: https://bitcraze.io/documentation/repository/crazyflie-firmware/2022.09/functional-areas/sensor-to-control/state_estimators
[cf-blog]: https://www.bitcraze.io/2020/01/state-estimation-to-be-or-not-to-be/
