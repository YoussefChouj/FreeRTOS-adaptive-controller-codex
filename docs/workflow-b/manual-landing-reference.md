# Manual landing reference (operator, 2026-10-06)

The operator lands better by hand than the autonomous LAND. Their key point: **near the ground, descend quickly so the
drone passes through ground effect fast.** This page records 8 manual landings (WiFi livewatch, 21 vars at 200 Hz,
`logs/livewatch/manual_landing_20261006.csv`, flymode 1 with the OF hold on). The operator rated the last one (M8) the best.

## M8: the reference landing (measured)

| segment | time | mean sink | commanded vz (stick) |
|---|---|---|---|
| hover 1.41 m, stick eased down | 267.6 s | 0 | -0.10 -> -0.22 |
| 1.41 -> 1.0 m | about 1.1 s | about 0.37 m/s | -0.22 -> -0.60 |
| 1.0 -> 0.5 m | 0.69 s | 0.72 m/s | -0.60 -> -0.75 |
| 0.5 -> 0.3 m | 0.23 s | 0.87 m/s | about -0.70 |
| 0.3 -> 0.13 m (ground effect) | 0.28 s | 0.61 m/s | -0.69 -> -0.87 (full stick) |
| touchdown (z <= 0.13 m) to disarm | 0.14 s | | |

During the descent, attitude stayed within ±1.7 deg and OF dx/dy within ±4. The operator **speeds up** as the
ground gets near, then disarms right after contact.

The other landings that reached the ground show the same pattern. Through 0.3 -> 0.13 m, M3, M4, M7 and M8 took
0.28-0.31 s, and disarm came 0.02-0.18 s after.

## Autonomous LAND for comparison (F6, 2026-10-06, hover 1.0 m)

| segment | F6 auto | M8 manual |
|---|---|---|
| 1.0 -> 0.5 m | 2.12 s (0.24 m/s) | 0.69 s |
| 0.5 -> 0.3 m | 0.58 s (0.34 m/s) | 0.23 s |
| 0.3 -> 0.13 m | 0.86 s (0.20 m/s) | 0.28 s |

F6 drifted about 30 cm in -x, "mostly at landing" (operator). The OF reading and the position estimate never saw
that drift.

## Why the auto LAND is slow at the bottom

`Des_Height` (TASK/StabilizerTask.c) ramps the **position** setpoint down:
- 0.30 m/s above 0.40 m;
- 0.15 m/s below 0.40 m (the 2026-10-02 slow stage).

Z_posPID (Kp 0.7) turns height error into a sink rate. So once the ramp reaches the floor, the commanded sink rate
is about 0.7 × height, and it shrinks as the drone gets lower. The sink bias then grows by only 0.2 m/s per second.
The result is that the drone slows down in the ground-effect band. The operator does the opposite.

## PROPOSED (not flown): rate-mode two-stage landing from M8

In LANDING, command the climb-rate loop directly instead of ramping the position setpoint. PX4's LAND works the
same way (MPC_LAND_SPEED is a velocity setpoint).

- Stage 1: -0.4 m/s from hover to 0.5 m. M8 measured 0.37 m/s from 1.41 m to 1.0 m.
- Stage 2: -0.7 m/s below 0.5 m to contact. M8 measured 0.61-0.87 m/s.
- Keep the existing touchdown cuts unchanged (rest cut 0.2 s, sink bias, 15 s net).

Estimated LAND time from 1.0 m is about 1.8 s, against about 4.5 s now. This is arithmetic, not measured.

This reverses the 2026-10-02 slow final stage. It is a firmware change, so it needs the operator's flash and an A/B
flight that measures landing drift with a tape.
