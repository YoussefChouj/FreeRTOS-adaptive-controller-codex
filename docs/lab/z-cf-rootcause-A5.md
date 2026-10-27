# A5 — offline vertical closed-loop study (simulation only)

The simulated CF variants do **not** reproduce the operator-reported +/-10–15 cm wobble under the primary PROPOSED plant. This is a failed reproduction, not a falsification of H1, H2 or H6: neither thrust response nor sensor latency/conversion timing has been measured. The exact existing code defect is covered by A1/A2 host tests, independently of this plant study. No hardware was contacted.

## Model fidelity and six gates

1. **Problem/falsifier:** reproduce the reported 0.8 m hover oscillation (brief: four `logs/campaigns/z-cf-hover_20261026-*` recordings). If corrected timing substantially changes margins/RMS in the same assumed plant, timing is a candidate mechanism. Failure to reproduce rejects this assumed model as a sufficient explanation; it does not identify a flown cause.
2. **Constraints/tradeoffs:** host-only; all physical parameters below are **PROPOSED**, not measured. No gain, firmware, flight or operating-limit recommendation follows automatically.
3. **Anti-patterns:** integration and D use firmware units per call, not continuous-time gains. Saturation, individual term limits and integral separation are retained in nonlinear runs. The margins locally disable gates/clamps/quantization; they do not justify operation under saturation.
4. **Coupling:** altitude PID -> climb-rate PID -> pure-PID collective -> motor lag -> rigid vertical motion -> ToF/acceleration estimator -> both feedback signals. Attitude/rate controllers, adaptive Z injection, tilt, battery sag, contact and aerodynamic coupling are absent. The altitude and climb loops are coupled; this study does not certify the 3–5x bandwidth separation or other axes.
5. **Fidelity:** vertical linear thrust slope, first-order motor, 20 Hz range sample-and-hold, 200 Hz acceleration, additive range/acceleration noise, 1 cm quantization, constant acceleration bias and thrust-correlated acceleration error. No mass or thrust curve can be identified from one hover CCR value. The coded free-flight offset 3050 CCR is used only as an offset. Assumed slope **0.020 m/s²/CCR**, motor 20/40/60 ms, range noise SD 5 mm, acceleration noise SD 0.15 m/s² per tick, constant bias +0.08 m/s², thrust-correlated error +0.08 times true acceleration. These errors are PROPOSED. The KF covariance is also PROPOSED (bias random walk 0.02 m/s²/sqrt(s)). No float32 or whole-firmware parity claim is made.
6. **V-model stage:** deterministic nonlinear nominal/stress simulation and isolated Python tests only. A1–A4 C host verification is separate; no bench/flight evidence or mass identification is supplied here.

## Controller fidelity and timing discrepancy

`API/pid.c` rows specify position `(Kp,Ki,Kd)=(0.7,0.005,0.1)` and climb `(400,0.435,0)`. The brief's position `Ki=0.001` is the documented ABBA runtime preset in `docs/lab/COMMANDS.md`; it is the primary scenario, and the committed `0.005` row is a sensitivity. The source is not edited by this study.

`ComputePID` uses `E=Des-FB`, adds `E` per invocation (no dt), and `Ud=Kd*(E-PreE)` (no division by dt). Thus this is D on **error**, including setpoint kick; the brief's “D on height” description is incomplete. Position limits `(U,P,I,D,SumE,EMin)=(1,.9,.3,.3,30,.3)` and rate limits `(300,300,100,60,250,10)` are retained; `|E|<EMin` is strict and the previous output participates in the legacy integration gate. Finite-state reset guards are included.

`Z_Compute` runs height every `POS_LOOP_DIV=2` ticks (**100 Hz**), climb every tick (200 Hz), and `Update_Des` precedes the new position calculation: the rate setpoint sees the prior position output. Both cadence and that one-tick scheduling delay are modeled. Running both integrators at 200 Hz would double effective outer integral gain. Pure-PID Z output is added to 3050 CCR, constrained to assumed full throttle window [2000,4000], with zero attitude torque. Runtime narrower windows, trajectory slew, MRAC, experiment headroom overrides and flight-phase logic are excluded. Rejected-sample integrator-freeze details and tick-based resynchronization are simplified; the simulation is an algorithm study rather than a complete flight-task execution.

## Estimator timing

FD+LPF uses a held range and the 0.9/0.1 per-tick derivative LPF, including the old per-tick median's one-tick arrival delay. Current CF corrects using the previous frame value, reproducing the H1 timing mechanism for nominal held samples. A2 uses sample-frame median only as outlier reference and fuses the latest inlier raw sample, rather than introducing a median-sample delay. A3 compares with the delayed predicted history and propagates the correction to all newer buffered states. A4 queues finite correction packets at their delayed horizons, propagates their remaining corrections each tick, and distributes them over the previous observed frame interval. Each slice updates current and buffered states by backward propagation to that packet horizon; early frames leave old packets active. Packet bias corrections use the actual clamped increment, matching the bounded firmware scheduler. The KF uses the same delayed-state propagation with a steady-state 3-state Riccati gain. The model initializes at 0.8 m with zero velocity/bias and starts the step at 15 s; initial bias convergence therefore contributes to the early hover metric.

The primary sweep is **6 estimators x 5 latencies {0,25,50,75,100} ms x 3 motor constants x 2 Ki = 180 scenarios**. A separate 70 Hz frame sensitivity fuses repeated values from the same held 20 Hz conversion stream, with legacy/scaled CF gains. This is a PROPOSED H6 model, not a claim that the sensor converts at 20 Hz: measured frame counters cannot identify the conversion rate.

## Metrics and margins

Hover RMS is true-height error about 0.8 m over [5,15) s; half peak-to-peak uses the same window. Step overshoot is true height above 1.0 m after 15 s. Settle is the first time after which all remaining samples stay within **2% of the 0.2 m step (4 mm)** through 40 s. `>25` means it did not settle within that observation window. “Period” is the largest **detrended** hover spectral component in 0.1–3 Hz; a 10 s value is the finite-window lower edge, not proof of a sustained oscillator. RMS/period do not use estimator error as a substitute for true motion.

For margins the estimator is driven by complex height/consistent acceleration at 180 frequencies 0.025–12 Hz, retaining its 200/20 Hz predict/correct clock and history updates. After 60 s transient, the fundamental response is averaged for 20 s. The scalar return ratio is `L=C_rate * (C_pos*H_height + H_velocity) * P_height`, with discrete per-call PID transfer functions, exact ZOH motor/double-integrator plant, outer 100 Hz controller and its average two-tick hold plus scheduling delay. Noise, constant bias, thrust-error slope and nonlinear gates are omitted from this nominal linearization. This **fundamental-harmonic/averaged sample-hold approximation is not an exact lifted multirate or Floquet stability margin**; alias coupling is discarded. A blank upper gain margin means no downward -180° crossing within the scanned band, not infinite proven robustness. The low-frequency upward crossing supplies a separate *lower* gain boundary; treating that negative dB value as an upper gain margin would be wrong.

Primary example: **50 ms range latency, 40 ms motor, Ki=0.001**, all proposed errors enabled. Upper GM `—` has the limited meaning above.

| Estimator | PM deg | Upper GM dB | Lower gain boundary dB | Overshoot cm | Settle s | Hover RMS cm | Half pp cm | Dominant period s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FD+LPF | 12.95 | 2.08 | -27.59 | 3.63 | 24.95 | 0.50 | 1.27 | 0.83 |
| Current CF | 76.44 | — | -27.23 | 3.40 | >25 | 1.16 | 2.05 | 10.00 |
| CF+A2 | 71.32 | — | -27.68 | 3.43 | 24.09 | 1.14 | 2.08 | 10.00 |
| CF+A2+A3 | 66.36 | — | -28.10 | 3.68 | 24.09 | 1.26 | 2.28 | 10.00 |
| CF+A2+A3+A4 | 66.37 | — | -28.10 | 3.75 | 24.30 | 1.32 | 2.27 | 10.00 |
| Delayed 3-state KF | 66.53 | — | -28.10 | 2.78 | >25 | 0.42 | 0.53 | 2.00 |

Committed-default Ki=0.005 sensitivity at the same latency/motor/errors:

| Estimator | PM deg | Hover RMS cm | Overshoot cm |
|---|---:|---:|---:|
| FD+LPF | 13.12 | 0.48 | 7.38 |
| Current CF | 76.55 | 0.86 | 7.40 |
| CF+A2 | 71.41 | 0.85 | 7.49 |
| CF+A2+A3 | 66.40 | 0.91 | 7.42 |
| CF+A2+A3+A4 | 66.40 | 0.95 | 7.56 |
| Delayed 3-state KF | 66.57 | 0.37 | 7.86 |

Across all latency/motor/Ki values, every CF/KF case remains within the model domain; the largest CF half peak-to-peak is 2.59 cm, far below 10–15 cm. Corrected timing alone is therefore insufficient to reproduce the reported CF wobble in this plant. FD at 100 ms latency with 40/60 ms motor lag has negative approximate PM (down to -12.57 deg) and diverges outside the 5 cm–5 m range model. Those rows have `model_domain_exceeded=True`; their enormous unclipped trajectory metrics are numerical instability evidence, not predicted physical flight heights. Sensor/plant delay can destabilize this assumed FD loop, but this is not a reproduction of a CF wobble.

Factor isolation at 50/40 ms and Ki=0.001 (true-height hover RMS cm):

| Factor | Current CF | CF+A2+A3 | Delayed KF |
|---|---:|---:|---:|
| no_errors | 0.00 | 0.00 | 0.00 |
| noise_quantization | 0.38 | 0.38 | 0.27 |
| constant_bias | 0.80 | 0.93 | 0.27 |
| thrust_bias | 0.00 | 0.00 | 0.00 |
| all_errors | 1.16 | 1.26 | 0.42 |

The constant-bias startup transient is the largest isolated CF contribution in this chosen case. The thrust-correlated term is a PROPOSED relative-thrust/acceleration error proxy, not a measured vibration model: by itself it does not excite an initially stationary hover; its effect appears during motion/step. Flat hover signals have no defined period and are stored blank, rather than assigning the first spectral bin. None of these factors produces the reported CF amplitude here.

70 Hz frame / held-20 Hz sensor sensitivity (same 50/40 ms, Ki=0.001), hover RMS cm:

| Estimator | Legacy gains | dt-scaled gains |
|---|---:|---:|
| Current CF | 1.11 | 1.12 |
| CF+A2 | 1.07 | 1.15 |
| CF+A2+A3 | 1.22 | 1.27 |
| CF+A2+A3+A4 | 1.22 | 1.27 |

This cadence sensitivity also fails to reproduce the reported amplitude. It establishes sensitivity for the assumed timing only; it cannot resolve H6 without sensor conversion timing and aligned acceleration/range data.


## Reproduce and evidence

Run `python -m ground_station.lab.z_cf_sim --output docs/lab/data/z-cf-rootcause-A5`. It has no network or vehicle client. NumPy 2.4.4 / SciPy 1.17.1; seed 71027. Raw deterministic outputs: [sweep.csv](data/z-cf-rootcause-A5/sweep.csv), [factors.csv](data/z-cf-rootcause-A5/factors.csv), [cadence.csv](data/z-cf-rootcause-A5/cadence.csv), [assumptions.json](data/z-cf-rootcause-A5/assumptions.json). Raw scoped test output: [pytest.txt](data/z-cf-rootcause-A5/pytest.txt), from `python -m pytest ground_station/lab/tests/test_z_cf_sim.py -q`.

## Estimator x delay table (A5 primary PID, PROPOSED 40 ms motor)

All values are simulation outputs with PROPOSED plant/noise assumptions, not flight measurements. Upper GM `--` means no downward phase crossing in the scanned band; it is not an infinite-margin claim. Period 10.00 s is the lower frequency boundary of a 10 s record, not an identified sustained oscillator. OOB means trajectory left the model domain; nonlinear metrics are omitted. Settle `>25` means not reached within 25 s after the step.

| Estimator | Delay ms | PM deg | Upper GM dB | Overshoot cm | Settle s | Hover RMS cm | Spectral period s |
|---|---:|---:|---:|---:|---:|---:|---:|
| fd_lpf | 0 | 34.22 | 7.40 | 2.96 | >25 | 0.27 | 0.71 |
| fd_lpf | 25 | 23.59 | 4.29 | 3.06 | 24.18 | 0.37 | 1.00 |
| fd_lpf | 50 | 12.95 | 2.08 | 3.63 | 24.95 | 0.50 | 0.83 |
| fd_lpf | 75 | 2.31 | 0.34 | 4.69 | 24.92 | 0.98 | 0.83 |
| fd_lpf | 100 | -8.33 | -1.11 | OOB | OOB | OOB | OOB |
| current_cf | 0 | 71.32 | -- | 3.49 | 24.19 | 1.12 | 10.00 |
| current_cf | 25 | 73.99 | -- | 3.44 | 24.09 | 1.12 | 10.00 |
| current_cf | 50 | 76.44 | -- | 3.40 | >25 | 1.16 | 10.00 |
| current_cf | 75 | 78.38 | -- | 3.60 | 24.04 | 1.14 | 10.00 |
| current_cf | 100 | 79.52 | -- | 3.38 | 24.05 | 1.16 | 10.00 |
| cf_a2 | 0 | 66.33 | -- | 3.46 | 24.09 | 1.14 | 10.00 |
| cf_a2 | 25 | 68.71 | -- | 3.51 | 24.05 | 1.15 | 10.00 |
| cf_a2 | 50 | 71.32 | -- | 3.43 | 24.09 | 1.14 | 10.00 |
| cf_a2 | 75 | 73.99 | -- | 3.30 | 24.09 | 1.14 | 10.00 |
| cf_a2 | 100 | 76.44 | -- | 3.34 | 24.05 | 1.15 | 10.00 |
| cf_a2_a3 | 0 | 66.33 | -- | 3.46 | 24.09 | 1.14 | 10.00 |
| cf_a2_a3 | 25 | 66.35 | -- | 3.55 | 24.06 | 1.19 | 10.00 |
| cf_a2_a3 | 50 | 66.36 | -- | 3.68 | 24.09 | 1.26 | 10.00 |
| cf_a2_a3 | 75 | 66.37 | -- | 3.73 | 24.13 | 1.31 | 10.00 |
| cf_a2_a3 | 100 | 66.36 | -- | 3.76 | 24.30 | 1.41 | 10.00 |
| cf_a2_a3_a4 | 0 | 66.35 | -- | 3.65 | 24.12 | 1.19 | 10.00 |
| cf_a2_a3_a4 | 25 | 66.36 | -- | 3.67 | 24.23 | 1.24 | 10.00 |
| cf_a2_a3_a4 | 50 | 66.37 | -- | 3.75 | 24.30 | 1.32 | 10.00 |
| cf_a2_a3_a4 | 75 | 66.36 | -- | 3.90 | 24.36 | 1.40 | 10.00 |
| cf_a2_a3_a4 | 100 | 66.35 | -- | 3.88 | 24.20 | 1.49 | 10.00 |
| delayed_kf | 0 | 66.45 | -- | 2.78 | >25 | 0.37 | 3.33 |
| delayed_kf | 25 | 66.50 | -- | 2.80 | >25 | 0.40 | 3.33 |
| delayed_kf | 50 | 66.53 | -- | 2.78 | >25 | 0.42 | 2.00 |
| delayed_kf | 75 | 66.56 | -- | 2.73 | >25 | 0.43 | 2.50 |
| delayed_kf | 100 | 66.56 | -- | 2.90 | >25 | 0.48 | 2.50 |
