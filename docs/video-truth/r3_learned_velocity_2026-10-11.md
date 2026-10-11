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
| replay gate 10, k = 0 | **32.4** | 20.8 | 12.0 | 30.4 | 0.44 |
| k = 0.5 | 33.2 | 20.3 | 12.1 | 34.8 | 0.44 |
| k = 1.0 | 35.0 | 20.5 | 12.2 | 42.3 | 0.44 |
| k = -1.0 (sign check) | 33.3 | 23.9 | 12.2 | 29.1 | 0.46 |

The lever correction does not reduce position error, and the hover slope is yaw-independent in this window.
The ~10 cm offset is a computed hint only: measure the OF sensor offset from the CG on the airframe.

## Verdict
With one flight nothing learned beats the EKF on position. The gyro-residual velocity gain (16-24%) does not carry
over to position. Camera position fusion (0.44 -> 0.06 cm/s, `r3_drift_truth_2026-10-11.md`) is still the only
thing that meets the 0.17 cm/s budget. To train a camera-free model, collect more camera-truth flights (R4+, top
marker id1, deliberate pure-yaw and hover segments) and refit on flights 1..n-1, test on flight n.
