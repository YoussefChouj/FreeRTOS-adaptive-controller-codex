# Your fusion notes, answered (2026-10-12)

This page answers the dictated notes on [2026-10-12-live-fusion-explained.md](2026-10-12-live-fusion-explained.md).
It goes from zero to the expert design, corrects the slips in the notes, and compares six ways to fuse the camera.

Number tags:
- **MEASURED** comes from flight r5_live_2.
- **SIMULATED** comes from `python -m ground_station.analysis.ekf_of_replay methods`. That simulator is synthetic: its truth paths are made up, not flown.
- **ILLUSTRATIVE** is round numbers picked to show the arithmetic.
- **PROPOSED** is not built or not flown yet.

## Bottom line

- **Yes, the camera can go inside the EKF on this drone.** It costs no more CPU than today: a 4×4 scalar
  update per fix, using the ring buffer we already have. In the simulator it cuts the position error by roughly 1.2× to 3×
  compared with today's "offset outside the EKF".

  SIMULATED rms error, in cm:

  | Flight | Today's offset (M1) | Camera inside the EKF (M3) |
  |---|---|---|
  | hover | 2.3 | 0.8 |
  | circle | 2.6 | 2.1 |
  | step | 2.7 | 1.5 |

  It also holds better through a 5 s camera dropout. Hover dropout p90 is 6.8 cm today and 2.8 cm inside the EKF (SIMULATED).
- **Your three modes are the right idea.** The firmware keeps the "shadow" mode it already has, so the numbers become:
  - 0: EKF only;
  - 1: shadow;
  - 2: offset outside (today);
  - 3: inside the EKF (new).
- **One thing the simulator found:** inside the EKF, the first fix after a dropout moves the position in one tick. The D term
  would see that as a fake jump in speed. The PreE shift (section 9) removes it. It ships in the same firmware.
- **What I am building next, in order:**
  1. PreE shift;
  2. safer re-anchor and a stale alarm;
  3. mode 3;
  4. RC and setpoint logging;
  5. reflash;
  6. a tool that measures the delay;
  7. a hands-off hover A/B flight.

## 0. What I am building (one picture)

Think of a ship's navigator doing dead reckoning:
- **The EKF is the navigator.** It plots the course from speed (optical flow) and engine thrust (accelerometer).
- **The camera is a lighthouse sighting.** It is exact, but it arrives 0.14 s late (MEASURED).
- **Today** a second person keeps a correction sticky note and pastes it onto the chart *after* the navigator has drawn it. The
  navigator never learns that his speed log reads 4 % high. He keeps making the same mistake, and the note keeps
  growing.
- **Mode 3** hands the sighting to the navigator. He corrects the position *and* his idea of the speed-log error, so he
  drifts less when the lighthouse is hidden.

```
                 today (mode 2)                              new (mode 3)
IMU + OF ──► EKF ──► earth path ──(+ offset a)──► PID     IMU + OF ──► EKF ──► earth path ──► PID
                          ▲                                             ▲
camera fix ──► ring: e = z − (past path + c) ──► c, a      camera fix ──► ring: innovation ──► EKF update (pos, vel, bias)
```

## 1. Corrections to the notes

| You wrote | What is true | Why it matters |
|---|---|---|
| "A larger gate excludes more" | **A larger gate accepts more.** The gate is a fence around the prediction: fixes inside it are used, fixes outside it are thrown away. A wider fence lets more through. | Set the gate too wide and outliers get in. Set it too narrow and real corrections get rejected. That causes reject bursts and then re-anchors (MEASURED: 106 anchors, 92 of them in bursts under 1 s). |
| "Learn the frame rate" | **Learn the frame *yaw***: the angle between the camera's x axis and the drone's earth x axis. | A yaw error turns every movement partly sideways. MEASURED residual yaw was −12 to −18° in the first 40 s. |
| "The 0.5 cm/tick spread limits tracking" | **It limits only how fast a *correction* reaches the controller** (0.5 cm per 5 ms = 1 m/s). The drone's own movement comes through the EKF path at any speed. | The real harm is the D term (section 4.3). |
| "The EKF output goes straight to the controller" | **It goes through four steps first.** The EKF increment is scaled to cm, rotated by yaw into earth, gets the offset `a` added (mode 2), and is then fed to the PID as FB. | See section 3. |
| Mode 0/1/2 numbering | The firmware already uses 1 for **shadow**: fixes are computed and logged but not applied. | Keeping 1 = shadow means old logs and tools keep their meaning. Your "in-EKF" mode becomes **3**. |

## 2. The basics, from zero

### 2.1 State, predict, update

- **State** is what the filter believes. For each axis it holds four numbers:
  - position `p`;
  - velocity `v`;
  - OF bias `bof`, a constant error in the optical-flow reading;
  - accelerometer bias `ba`.

  There are two axes, so 8 numbers in all (`API/ekf_of.c`).
- **Predict** runs every 5 ms, using the accelerometer. **Yes, predict uses acceleration.**
  ```
  u = a_meas − ba                 (remove the accelerometer's own error)
  p = p + v·dt + ½·u·dt²          (move by the current speed, plus the speed-up)
  v = v + u·dt
  ```
  ILLUSTRATIVE example, with dt = 0.005 s:
  - start from p = 1.000 m, v = 0.200 m/s, a_meas = 0.5 m/s², ba = 0.1 m/s²;
  - u = 0.4;
  - p = 1.000 + 0.001 + 0.000005 = 1.001005 m;
  - v = 0.202 m/s.
- **Update** runs when a measurement arrives:
  - optical flow at 50 Hz;
  - the camera at about 26 fixes/s (MEASURED).

  The filter compares the measurement with what it expected, then moves part of the way toward it.

### 2.2 The weight K: how far to move

Every estimate carries a **variance P**: how unsure it is, in units of position². A sensor has its own noise variance **R**.
For a single number, the weight is:

```
K = P / (P + R)          y = z − estimate (the "innovation": the surprise)
new estimate = estimate + K·y
new P        = (1 − K)·P
```

ILLUSTRATIVE example:
- The estimate is 10 cm with P = 4 cm² (sd 2 cm).
- The camera says 15 cm, with R = 1 cm² (sd 1 cm).
- K = 4/5 = 0.8, so the new estimate is 10 + 0.8·5 = **14 cm**.
- New P = 0.2·4 = 0.8 cm². That is sd 0.89 cm, better than either source alone.

**K is not a constant you pick.** It comes out of P and R.
- When the EKF has drifted for a while, P is big, K is near 1, and the filter trusts the camera.
- Right after a fix, P is small and K is smaller.
- R stays fixed: it is the camera noise. MEASURED sd is 1.7 cm (x) and 0.8 cm (y).

### 2.3 The gate

The gate rejects fixes whose surprise is too big to be believable.
- **In the EKF (mode 3):** reject if `y² > gate²·S`, where `S = P + R` is the expected size of the surprise.
  - With gate = 5 and the numbers above, S = 5 cm², so √S = 2.24 cm.
  - The filter accepts |y| ≤ 11.2 cm.
  - With gate = 10 it would accept |y| ≤ 22.4 cm: **bigger gate, more accepted**.
  - Because S grows with P, the fence widens by itself when the EKF has drifted. That is the right behaviour.
- **Today (mode 2):** a fixed 40 cm radius on `e` (`EXT_POS_GATE_CM`, `API/ekf_of.c:372`). It does not widen when the EKF is
  unsure, so a long drift turns into a reject burst and then a re-anchor.

### 2.4 Earth frame and heading frame

- **Earth frame:** axes fixed to the room. The camera measures here.
- **Heading frame:** axes turn with the drone's nose, flat on the ground. The EKF works here, because the flow sensor and
  accelerometer are bolted to the drone.
- To go from heading to earth, rotate by yaw:
  - `earth_x = cos(yaw)·h_x − sin(yaw)·h_y`;
  - `earth_y = sin(yaw)·h_x + cos(yaw)·h_y`.

  ILLUSTRATIVE: at yaw 30°, a 1 m step along the heading x axis is (0.87, 0.50) m in earth.
- The firmware rotates each 5 ms *increment* of the EKF position and adds it up into `earth_x/earth_y`. Mode 3 must
  do the reverse with the camera's correction: rotate earth to heading, then convert cm to EKF metres (section 8).

## 3. What reaches the controller

These steps happen every 5 ms (`TASK/StabilizerTask.c`, mode-2 path):

1. `ekf_dx = Δp_x · 100 · g_of_scale`. This is the EKF position change this tick, converted to cm.
2. Rotate `(ekf_dx, ekf_dy)` by yaw and add it to `earth_x, earth_y`.
3. Mode 2 only: add the spread step of the camera offset `a`.
4. `locxPID.FB = earth_y` and `locyPID.FB = −earth_x`. The PID axes are swapped against earth.

So in mode 0 the controller sees the EKF path in earth cm. In mode 2 it sees that path plus `a`. In mode 3 it sees the
EKF path again, but the EKF itself has been corrected by the camera.

## 4. Today's offset method, in plain words

### 4.1 `a` and `c`

- **raw** is the EKF path (earth cm) without any camera correction. The ring stores `raw = earth − a` every 50 ms.
- **c** is the *target* offset: where the camera says the raw path should be shifted to. Each accepted fix
  moves it by `gain·e`, clamped to 10 cm.
- **a** is the *applied* offset: the part already added into `earth`. It walks toward `c` by at most 0.5 cm per tick.
- **e** is the camera's surprise: `e = z − (raw at capture time + c)` (`API/ekf_of.c:370`).

Analogy: `c` is where you want the picture frame on the wall. `a` is where your hands have slid it so far.

### 4.2 Linear interpolation (reading the past)

The ring keeps one sample every 50 ms. A fix taken between two samples reads a blend of the two. ILLUSTRATIVE:
- the capture is at 1.23 s;
- the ring holds 10.0 cm at 1.20 s and 12.0 cm at 1.25 s;
- the weight is (1.23 − 1.20)/0.05 = 0.6;
- so raw = 10 + 0.6·2 = **11.2 cm**.

### 4.3 Spread and the D kick

`Ud = Kd·(E − PreE)` reacts to how much the error changed since the last tick. Real motion at 0.2 m/s changes E by
0.1 cm per tick. A spreading correction changes it by up to 0.5 cm per tick. To the D term that looks like the drone
moving at 1 m/s. With Kd = 4.0, the kick is 2.0 instead of 0.4 (ILLUSTRATIVE arithmetic on the real Kd). This is
a *fake* speed. The fix is the PreE shift (section 9): it tells the D term "this change was a correction, not motion".

### 4.4 Gain 0.9: is it reactive?

Yes. Each accepted fix closes 90 % of the gap in `c`, and after two fixes 99 % is closed. The best constant gain on the
real data was **K = 0.84 (x) and 0.81 (y)** (MEASURED). So 0.9 is close to the best, and dropping to 0.3 would be worse.
Here is the trade-off for a constant gain K:

```
lag   = v·Δt·(1 − K)/K        (v = how fast the error grows, Δt = time between fixes)
noise = σ·sqrt(K/(2 − K))     (σ = camera noise)
```

ILLUSTRATIVE, with v = 0.5 m/s, Δt = 0.04 s and σ = 1.7 cm:

| K | lag | noise |
|---|---|---|
| 0.9 | 0.22 cm | 1.54 cm |
| 0.3 | 4.7 cm | 0.71 cm |

A small K smooths the noise but falls behind. The camera is accurate and the EKF error grows fast, so the best K is
high. In mode 3, K comes out of P and R by itself (section 2.2): there is no gain to hand-tune.

### 4.5 Offset and random walk

- **Offset** is the gap between the EKF path and the truth. It is the thing `c` tries to cancel.
- **Random walk** describes how that gap moves. Each step adds a small random kick, and nothing pulls it back. That is how the
  OF bias behaves. With the camera off, the position error therefore keeps growing (MEASURED hover drift 0.44 cm/s).
- Today the EKF never learns the bias, because the camera never reaches it. So `c` has to chase a target that never stops moving.

## 5. Section 3.1 of the explainer, simplified

Take one axis and four numbers `[p, v, bof, ba]`.

- **Predict.** As in section 2.1. The uncertainty grows as `P = F·P·Fᵀ + Q`. In words:
  - position doubt grows from velocity doubt;
  - velocity doubt grows from accelerometer-bias doubt;
  - every state gets a little extra doubt, Q, each tick.

  The non-zero off-diagonal entries of F say who feeds whom:
  - `F[p,v] = dt`: velocity moves position;
  - `F[v,ba] = −dt`: an accelerometer bias corrupts velocity;
  - `F[p,ba] = −½dt²`: it also corrupts position.
- **OF update.** The sensor reads `v + bof`, so `h = [0, 1, 1, 0]`: it sees velocity plus bias, and cannot tell them apart.
  The filter splits the surprise between them by their doubt. ILLUSTRATIVE:
  - P_vv = 0.01, P_bb = 0.0001, R = 0.0001;
  - S = 0.0102;
  - so velocity takes 98 % of the surprise and the bias takes 1 %.
- **ZUPT** (on the ground, velocity known to be 0, `h = [0, 1, 0, 0]`). Now velocity is pinned. Any OF reading left over must be bias,
  and that is how the bias is learned at rest.
- **Why the bias gets updated at all:** it appears in the OF measurement. If the filter never estimated it, a constant OF error
  would be read as a constant velocity, and the position would drift without end.
- **Camera update (mode 3):** `h = [1, 0, 0, 0]`, which measures position directly. Through P's links between states, a position
  surprise also corrects velocity and bias. That is the whole gain of mode 3: the camera teaches the EKF its own errors.

## 6. The latency chain and the screen grab

The capture travels through these stages:

1. phone camera exposure;
2. encode;
3. scrcpy over USB;
4. PC decode;
5. window draw;
6. **mss screen grab**;
7. detection;
8. UDP fix to the `stream_log` relay (port 47800);
9. WiFi to the MCU;
10. `Cmd_ExtPos`.

The total is about **0.14 s (MEASURED by the fit)**. The single stages are not measured yet.

**mss copies the screen pixels inside the window rectangle.** It does not read the video stream. So:
- If another window covers it, the tracker reads that window.
- If it is minimized, the frame is black or stale.
- If it moves, the grab reads the wrong place until the size is read again.

That is why scrcpy runs with `--always-on-top`, and why the tracker waits for the window and re-reads its size (commit 7ce3bffa).

## 7. Six ways to fuse a late fix, with numbers

The six methods, all SIMULATED in the same simulator:

| ID | Method | What it does | Who uses it |
|---|---|---|---|
| M0 | EKF only | No camera. | our mode 0 |
| M1 | Offset outside | Today's firmware logic (`ext_pos_replay`, the host twin of the firmware). | our mode 2 |
| M2 | In-EKF, delay ignored | The fix is fused as if it were fresh. | the naive way |
| M3 | In-EKF, compare in the past, apply now | Innovation is taken against the ring's past estimate, and the update is applied to the current state. | Larsen-style; **proposed mode 3** |
| M4 | In-EKF, re-propagate | Roll back to the stored past state, update, then re-run every prediction since. | MSF |
| M5 | Delayed horizon + output predictor | The EKF lives 0.14 s in the past. A light predictor brings it to "now" with buffered IMU data. | PX4 EKF2 |

**Simulator setup (all SIMULATED; config is `SIM` in `ekf_of_replay.py`):**

| Item | Setting |
|---|---|
| Runs | 16 seeds × 60 s |
| Camera | 25 Hz, sd 1.7 cm, delay 0.14 s ± 10 ms |
| Outliers | 1 %, 0.3 to 1 m |
| Dropout | 5 s, from 30 to 35 s |
| Optical flow | scale 1.04, random-walk bias |
| Accelerometer | noise 0.3 m/s², bias 5 cm/s² |

**Columns in the table:**
- "fake vel" is how fast the estimate's error jumps per tick. It is what the D term would wrongly see as speed.
- "predicts/s" is the CPU cost, counted as EKF predictions per second.

| scenario | method | rms cm | p90 cm | max cm | dropout p90 cm | fake vel p99 m/s | fake vel max m/s | predicts/s |
|---|---|---|---|---|---|---|---|---|
| hover | M0 EKF only | 49.3 | 76.4 | 192.1 | 73.9 | 0.46 | 0.96 | 200 |
| hover | M1 offset outside (fw now) | 2.3 | 3.2 | 18.5 | 6.8 | 1.24 | 1.78 | 200 |
| hover | M2 in-EKF, delay ignored | 1.0 | 1.3 | 6.6 | 3.6 | 0.45 | 11.22 | 200 |
| hover | **M3 in-EKF, ring** | **0.8** | **1.0** | 5.8 | **2.8** | 0.45 | 10.04 | 200 |
| hover | M4 in-EKF, re-propagate | 0.7 | 1.0 | 4.6 | 2.3 | 0.49 | 10.00 | 834 |
| hover | M5 delayed horizon + output pred | 0.9 | 1.3 | 5.1 | 2.5 | 0.05 | 0.20 | 400 |
| circle | M0 EKF only | 52.8 | 87.6 | 195.2 | 93.8 | 0.47 | 1.05 | 200 |
| circle | M1 offset outside (fw now) | 2.6 | 3.4 | 21.0 | 8.9 | 1.23 | 1.88 | 200 |
| circle | M2 in-EKF, delay ignored | 6.0 | 5.8 | 21.7 | 17.8 | 0.56 | 47.68 | 200 |
| circle | **M3 in-EKF, ring** | **2.1** | **1.8** | 14.0 | 9.2 | 0.47 | 20.18 | 200 |
| circle | M4 in-EKF, re-propagate | 2.1 | 1.7 | 13.2 | 9.1 | 0.51 | 20.56 | 834 |
| circle | M5 delayed horizon + output pred | 2.3 | 2.3 | 13.5 | 9.3 | 0.07 | 0.38 | 400 |
| step | M0 EKF only | 52.7 | 86.5 | 200.1 | 92.3 | 0.47 | 1.04 | 200 |
| step | M1 offset outside (fw now) | 2.7 | 3.5 | 20.7 | 9.5 | 1.23 | 1.91 | 200 |
| step | M2 in-EKF, delay ignored | 4.4 | 8.1 | 56.8 | 5.4 | 0.87 | 105.75 | 200 |
| step | **M3 in-EKF, ring** | **1.5** | **2.0** | 7.5 | 5.1 | 0.47 | 11.64 | 200 |
| step | M4 in-EKF, re-propagate | 1.5 | 2.0 | 6.9 | 5.1 | 0.51 | 11.75 | 834 |
| step | M5 delayed horizon + output pred | 1.8 | 2.6 | 7.1 | 5.3 | 0.06 | 0.23 | 400 |

**What happens when the assumed delay is wrong** (circle rms in cm, true delay 0.14 s, SIMULATED):

| assumed delay | M1 | M3 | M4 | M5 |
|---|---|---|---|---|
| 0.10 s | 2.7 | 2.4 | 2.3 | 2.5 |
| 0.20 s | 3.5 | 3.8 | 4.0 | 4.4 |

**How to read it:**

1. **The camera is the big win** (M0 → anything). The simulated drift is larger than the real one: the measured hover drift is 0.44 cm/s.
   So read M0 only as "no camera = unbounded drift".
2. **Ignoring the delay costs a lot when moving.** M2 circle rms is 6.0 cm against 2.1 for M3, and its step max is 56.8 cm.
   At 0.5 m/s, 0.14 s of delay is 7 cm of position, which is more than your 5 cm waypoint spacing.
3. **M3 ≈ M4 at a quarter of the CPU.** At 25 Hz fixes with 0.14 s delay, re-running the predictions buys about 0.1 cm.
4. **M3 beats today's M1 everywhere, and most in hover and in dropouts.** The reason is that the EKF learns its bias (section 5).
   M1's p99 fake velocity is always about 1.2 m/s: that is the spread, running all the time.
5. **The fake-velocity max of M2/M3/M4 (10 to 20 m/s) is a one-tick jump.** I traced it: the top spikes sit at t = 35.2 s, the first
   fix after the dropout (and in the first second, which the score skips). A 5 cm jump in one 5 ms tick reads as 10 m/s.
   The PreE shift hides exactly this from the D term. M5 is smooth by construction, because its predictor spreads every
   correction over tau = 0.25 s.
6. **Measure the delay; do not guess it.** Assuming 0.20 s instead of 0.14 s makes every in-EKF method worse than today's.
   That is why the delay tool is on the list.

**Recommendation for our drone: M3 + PreE shift as firmware mode 3.**
- It costs the same CPU as today and uses the ring we already have.
- It is about as accurate as M4.
- M5 is the gold standard for smoothness. But it needs 2× the predictions, an IMU buffer, and a second filter in the
  firmware. Keep it as a later step if a flight shows D kicks even with the PreE shift.

**Caveats of this simulator:**
- The estimators run open-loop: the truth path is given, and there is no controller in the loop.
- Yaw misalignment is not simulated.
- Outliers and delay jitter are simple.

Treat it as "which method is better and by how much, roughly". It is not a flight prediction. The hover A/B flight is the real test.

## 8. Mode 3, step by step (PROPOSED firmware)

When a fix `z` (earth cm) arrives, taken `d` ms ago:

1. **Read the past.** Interpolate the earth path at capture time from the ring. In mode 3 `a = c = 0`, so the ring stores
   the plain earth path.
2. **Innovation in earth.** `y_e = z − earth_past`.
3. **Rotate to heading and convert to EKF metres:** `y_h = R(−yaw)·y_e / (100·g_of_scale)`.
4. **Gate per axis.** Reject if `y_h² > gate²·(P_pp + R_cam)`, with R_cam = (0.017/1.04 m)² from the measured camera sd.
5. **Update now.** Run the scalar EKF update with `h = [1, 0, 0, 0]` and measurement `p_now + y_h`. This is the M3 trick: the
   surprise is measured in the past, and the correction is applied to the present state. Position, velocity and both biases
   all move, according to P.
6. **Release.** After 5 rejections in a row (REJ_RELEASE), re-open P_pp and take the fix. This is the re-anchor, done inside the EKF.
   Section 10 makes it consistency-checked.
7. **PreE shift.** The position change from steps 5 and 6 reaches `earth` as one increment on this tick. On the same tick,
   shift `locxPID.PreE` and `locyPID.PreE` by the matching change in E, so the D term does not see it.

The host twin is already written and tested: `EkfOfModel.update_pos` in `ground_station/analysis/ekf_of_model.py`
(test `test_update_pos_gain_gate_release`). Mode switching stays on `Cmd_ExtPos` idx 0xFF. The parse gets a value-3
case, and `live_tracker --mode` gets the choice 3.

## 9. The PreE shift (hiding jumps from the D term)

- E = Des − FB. When a correction moves FB by δ in one tick, E moves by −δ, and the D term reads `Kd·(E − PreE)` as a kick.
- The fix: on that tick, also do `PreE −= δ`. Then `E − PreE` holds only the real motion.
- The P term still sees the new position, which is right: the drone really is somewhere else.
- PX4 does the same through reset bookkeeping. Its position message carries a reset counter and the jump `delta_xy`, so
  the *consumers* of the estimate (the controllers) can shift their own stored values
  ([VehicleLocalPosition][px4-vlp]). "Consumer" simply means any code that reads the estimate.
- The shift works in mode 2 (on spread ticks) and in mode 3 (on update ticks).

## 10. Re-anchor and stale (PROPOSED)

- **Today:** 3 gated fixes in a row make the next one move `c` by up to 10 cm, as a re-anchor. On r5_live_2 most anchors came in
  bursts (92 of 106 under 1 s, MEASURED). The gate was rejecting real fixes, then the re-anchor jumped repeatedly.
- **New rule:** re-anchor only when N rejected fixes agree with *each other*. For example, all within 5 cm of their own mean after
  moving each one forward by the EKF motion. A real offset gives agreeing fixes; outliers scatter. N and 5 cm are PROPOSED.
- **Stale alarm:** if no accepted fix arrives for `EXT_POS_STALE_MS`, raise a visible flag. The camera then stops counting, and
  after recovery N good fixes are needed before trust is back. ArduPilot users report the same weak spot: a vision outage
  that does not raise a failsafe ([forum][ap-fs]).

## 11. The rest of the notes

- **PX4: delayed horizon, buffers, output predictor.** This is M5. Every sensor waits in a FIFO buffer until the filter's
  "now" (which is 0.14 s behind) reaches its time stamp. A small complementary filter then pushes the result forward with
  the newest IMU data ([PX4 ECL EKF][px4-ekf]).
- **ArduPilot's accelerometer-peak trick.** It measures the delay by lining up IMU acceleration peaks with peaks in the vision
  velocity ([forum][ap-delay]). We will do the same: short stick pulses in flight, then cross-correlate the camera velocity
  with the EKF velocity. The lag at the peak is the delay. It is sent with `--delay-ms` (Cmd_ExtPos idx 0xFE), with no reflash.
- **MSF: multi-rate fusion and calibration states** ([paper][msf-paper]). Sensors at different rates and delays share one filter.
  Unknown constants, such as the camera-to-world yaw and offset and the OF scale, become **extra states**, so the filter learns them.
  - You linked this to a future NN that self-calibrates the OF. The principled first step is the MSF way: add `g_of_scale`
    and the frame yaw as EKF states that the camera can observe.
  - An NN is worth it only for errors that do not fit a simple model, for example surface-texture effects on the flow.
  - Keep the NN goal (camera-off, 1 s-mean d60s p90 ≤ 10 cm) for after mode 3 flies.
- **Crazyflie: fixed std.** Each mocap position update uses one fixed measurement sd ([Bitcraze][cf-est]). That is exactly our
  R_cam. It works because the mocap noise is steady, as is our camera's.
- **Predictive estimation for 5 cm waypoints.** Two separate jobs:
  - The *estimate* must be current, not 0.14 s old. That is what M3, M4 and M5 do.
  - The *controller* should know where the path goes next. It should use the trajectory's velocity and acceleration as
    feedforward, so it does not wait for an error to build. Dense 5 cm waypoints at speed need both.

## 12. Order of work (status)

| # | Step | Status |
|---|---|---|
| 1 | Methods simulator + `update_pos` host twin + test | done (this commit) |
| 2 | Firmware: PreE shift, consistency re-anchor, stale alarm, mode 3, Des/RC logging | next |
| 3 | Host tests (`API/tests/test_ext_pos.c`, `ext_pos_replay_host.c`), reflash with `rebuild_and_flash --yes` | after 2 |
| 4 | Delay tool (stick pulses + cross-correlation) → `--delay-ms`; windowed yaw in the tracker | after 3 |
| 5 | Hands-off hover A/B flight: modes 0 / 2 / 3, same battery | needs you in the lab |

## Sources

- [px4-ekf]: PX4 docs, Using the ECL EKF — https://docs.px4.io/v1.15/en/advanced_config/tuning_the_ecl_ekf
- [px4-vlp]: PX4 uORB VehicleLocalPosition — https://docs.px4.io/main/en/msg_docs/VehicleLocalPosition
- [ap-delay]: ArduPilot forum, delay of the External Navigation System — https://discuss.ardupilot.org/t/calculate-the-delay-of-the-external-navigation-system-as-ekf3-inside-the-log/83095
- [ap-fs]: ArduPilot forum, EKF failsafe with VISO only — https://discuss.ardupilot.org/t/bug-ekf-failsafe-not-triggering-in-sitl-with-viso-only-no-gps-during-odometry-outage/143506
- [msf-paper]: Lynen et al., IROS 2013 — https://www.pure.ed.ac.uk/ws/files/17220481/Lynen_et_al_2013_A_Robust_and_Modular_Mutli_Sensor_Fusion.pdf
- [cf-est]: Bitcraze state estimation — https://bitcraze.io/documentation/repository/crazyflie-firmware/2022.09/functional-areas/sensor-to-control/state_estimators

[px4-ekf]: https://docs.px4.io/v1.15/en/advanced_config/tuning_the_ecl_ekf
[px4-vlp]: https://docs.px4.io/main/en/msg_docs/VehicleLocalPosition
[ap-delay]: https://discuss.ardupilot.org/t/calculate-the-delay-of-the-external-navigation-system-as-ekf3-inside-the-log/83095
[ap-fs]: https://discuss.ardupilot.org/t/bug-ekf-failsafe-not-triggering-in-sitl-with-viso-only-no-gps-during-odometry-outage/143506
[msf-paper]: https://www.pure.ed.ac.uk/ws/files/17220481/Lynen_et_al_2013_A_Robust_and_Modular_Mutli_Sensor_Fusion.pdf
[cf-est]: https://bitcraze.io/documentation/repository/crazyflie-firmware/2022.09/functional-areas/sensor-to-control/state_estimators
