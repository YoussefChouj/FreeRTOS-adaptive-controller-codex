# R3: learned velocity, residual learning, yaw lever arm (2026-10-11)

All numbers are computed this session from R3 only (one flight, t = 70-236 s, ~166 s, 3300 samples at 20 Hz).
Truth = phone camera (ArUco floor pose), velocity = savgol derivative (15 frames, ~0.5 s) mapped into EKF body axes.
Scripts: `scripts/nn1.py`, `nn2.py`, `lag.py`, `lever.py`. Inputs (`tel_r3.npz`, `cam_r3.npz`, `rot.npy`, `o4.py`,
`o4k.py`) live in the 2026-10-10 scratch dir `9f213064-.../scratchpad/o1/`, not in the repo.

## Why from scratch
No pretrained model fits: TLIO / Cioffi / AI-IMU use other IMUs, 200-1000 Hz input, no optical flow
(see `research_drift_sota_2026-10-11.md`). torch is not installed, so models are pure numpy.

## 1. Absolute velocity from a 1 s IMU+OF window (blocked 5-fold CV, 2 s purge, out-of-fold)
Drift metric = rms of the 10 s moving mean of the velocity error (the part that integrates into position drift).

| model | 10 s-mean err x / y (cm/s) |
|---|---|
| firmware EKF velocity | **2.11 / 2.60** |
| ridge, 130 features | 1.71 / 4.38 |
| MLP 130-32-32-4, Gaussian NLL, 3-seed ensemble | 4.59 / 3.20 |
| rotor drag (0.5 s mean accel, linear) | 5.50 / 4.58 |
| always zero | 5.56 / 4.71 |

Accel drag signal is buried in vibration (corr 0.10). MLP sigma is roughly honest (z std 1.17 / 1.24).

## 2. Residual learning (target = camera vel - EKF vel, prediction = EKF + correction)

| model | 10 s-mean err x / y (cm/s) |
|---|---|
| firmware EKF | 2.11 / 2.60 |
| + gyro-only ridge (3 params/axis) | 1.76 / 1.97 |
| + ridge on IMU/OF/EKF-vel window | 1.24 / 2.70 |
| + MLP on same | 2.81 / 2.49 |

## 3. Yaw lever arm (omega_z x r)
Lag scan (`lag.py`): the gz coefficient is flat across -300..+300 ms (ex +6.3, ey -7.7 cm/s per rad/s): looks
like static geometry, |r| ~ 9.7 cm (fit on t >= 124 s). The gx/gy coefficients grow with gyro lead up to
+200..300 ms: EKF velocity lag during manoeuvres, not a lever arm. R2 is small (0.05-0.10).

Replay with flow += k * (omega_z x r) on of1 and of2 (fit t >= 124 s, hover slope scored on held-out t < 124 s):

| variant | rms | aff | rev med | rev p90 | hover (cm/s) |
|---|---|---|---|---|---|
| FW logged (gate 5) | 47.1 | 34.4 | 22.6 | 67.7 | 0.45 |
| replay gate 10, k = 0 | **32.4** | 20.8 | 17.8 | 38.5 | 0.44 |
| k = 0.5 | 33.2 | 20.3 | 18.0 | 39.0 | 0.44 |
| k = 1.0 | 35.0 | 20.5 | 18.1 | 43.0 | 0.44 |
| k = -1.0 (sign check) | 33.3 | 23.9 | 17.9 | 41.5 | 0.46 |

The lever correction does not reduce position error, and the hover slope is yaw-independent in this window.
The ~10 cm offset is a computed hint only: measure the OF sensor offset from the CG on the airframe.

## 4. R1 ranked items, replay sweep (`sweep.py`, in-sample, R3 only, gate 10 base)

| variant | rms | aff | rev med | rev p90 | hover (cm/s) |
|---|---|---|---|---|---|
| base | 32.4 | 20.8 | 17.8 | 38.5 | 0.44 |
| accel bias frozen (q_ba 0) | 32.6 | 20.8 | 18.2 | 38.4 | 0.46 |
| q_bof 1e-6 / 1e-4 | 32.5 / 31.5 | 21.8 / 20.8 | 17.3 / 17.7 | 39.2 / 39.8 | 0.44 / 0.44 |
| q_ba 0 + q_bof 1e-6 | 32.3 | 21.7 | 17.3 | 39.0 | 0.44 |
| flow_scale 0.90 / 0.95 / 1.05 | 30.2 / 30.8 / 33.6 | 18.6 / 19.6 / 22.6 | 15.9 / 16.3 / 20.0 | 34.5 / 36.5 / 40.1 | 0.41 / 0.42 / 0.50 |
| of1 off (R_of1 0) | 570.2 | 229.4 | 344.4 | 649.2 | 8.30 |
| ToF height x cos(pit) cos(rol) | 32.4 | 20.8 | 17.8 | 38.5 | 0.44 |

Freezing accel bias does nothing; height tilt correction has no effect (height enters only the gyro term).
Flow scale 0.90 is a small in-sample gain (needs R4 to confirm). of1 is essential. No onboard variant gets near 0.17 cm/s.

## 5. Pure-yaw drift (operator observation), camera truth (`yawseg.py`, `levseg.py`, `levseg2.py`, `levcv.py`)
Yaw segments = 1 s mean |gz| > 0.3 rad/s. Displacements in cm, camera frame. All computed, R3 only.

| segment (s) | yaw turned | camera: drone really moved | FW EKF error | replay EKF error | replay + lever (fit t>=124, in-sample) |
|---|---|---|---|---|---|
| 160.6-167.6 | 185 deg | 63.4 | 45.8 | 33.8 | 12.9 |
| 183.4-190.8 | 374 deg | 6.6 | 10.4 | 19.8 | 18.9 |
| 197.3-204.7 | 540 deg | 35.0 | 41.2 | 15.2 | 9.1 |

Calm 7 s windows (n 16): FW EKF error median 10.5 cm. So pure yaw really moves the drone, and the EKF misses most of it.
The lever arm (sign fixed: lever.py fits truth - fw = cf*gz) explains about half the FW error vector in segments 1 and 3.

Checks:
- gyro z bias in calm hover -0.0004 rad/s: not the cause. Lever applied only inside yaw segments: same segment gain, rms 34.5.
- Whole-flight rms still rises (32.4 -> 35.0), hover slope unchanged 0.44 cm/s.
- Cross-fit (honest): fit on segment 3 only -> cf (+3.4, -2.7) cm/s per rad/s, |r| 4.3 cm; held-out segment 1 error 33.8 -> 24.8 cm,
  whole-flight rms 32.9, p90 39.1 (neutral). Fit on segment 1 or on t<124 is ill-conditioned (gz nearly constant or ~0): |r| 38-41 cm, nonsense.
- So the direction is consistent, the size is not pinned by one flight (4.3-9.7 cm).
- Gyro z reads 6% below the attitude yaw rate (slope 0.936; turn integrals 175/185, 356/374, 513/540 deg). Which one is right needs the top marker (R4).

Next: tape-measure the OF lens offset from the motor centroid (fixes r without data), then R4 with in-place yaw spins
both ways at 2 rates to validate a firmware `of_lever_xy` term (PROPOSED, not written).

## 6. OF-IMU delay (R1 item 4; `delay.py`, `xlag.py`, computed, R3 only)
Direct: raw flow of1 vs gyro (0.5 s high-pass, 100 Hz grid), main correlation lobe: of1_dx~Gyro_Y r -0.53 at -31 ms,
of1_dy~Gyro_X r +0.50 at -34 ms. So flow(t) matches gyro(t+~32 ms): the logged gyro trails the flow (inference: gyro LPF group delay).
Side lobes of opposite sign at about +-140 ms show a ~3.5 Hz attitude oscillation.

Replay, the four flow channels shifted against gyro/tilt (replay dt 9.5 ms):

| shift (ms) | -47 | -28 | -19 | -9 | 0 | +9 | +19 | +28 |
|---|---|---|---|---|---|---|---|---|
| rms (cm) | 33.8 | 33.8 | 38.2 | 36.0 | **32.4** | 32.7 | 34.6 | 34.2 |
| rev p90 (cm) | 40.7 | 36.7 | 38.1 | 39.0 | 38.5 | 39.2 | 40.9 | 43.0 |
| hover (cm/s) | 0.43 | 0.51 | 0.60 | 0.52 | **0.44** | 0.46 | 0.55 | 0.46 |

No shift beats zero on rms or hover. The scores move non-monotonically between neighbours, so gate flips dominate.
Delay compensation is not a lever for the hover-drift budget.

## Verdict
With one flight nothing learned beats the EKF on position. The gyro-residual velocity gain (16-24%) does not carry
over to position. Camera position fusion (0.44 -> 0.06 cm/s, `r3_drift_truth_2026-10-11.md`) is still the only
thing that meets the 0.17 cm/s budget. To train a camera-free model, collect more camera-truth flights (R4+, top
marker id1, deliberate pure-yaw and hover segments) and refit on flights 1..n-1, test on flight n.
