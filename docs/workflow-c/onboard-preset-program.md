# Onboard preset program (design, PROPOSED, operator 2026-10-07)

Operator: "use presets for all, be strategic; F and G can use a preset line; same or different presets can be
combined in the firmware. I don't mind flashing." Then: "presets should also have the functionality to choose
different trajectory profiles in the firmware (S profile, and others); highly modular and easily combined; build
all the basic presets that more complex presets can be composed from."

## Problem (measured 2026-10-07)

- `path` steps upload one float per CMD 0x1B, serial, ack each: median round trip 0.353 s (D circle), 0.267 s
  (step B). Circle 322 commands = ~114 s, so D hovered until the 120 s airborne cap (run
  wfc-circle-d_20261007-183606, abort L1 "append rejected at float 102").
- The legacy presets (TASK/AutoflyTask.c circle/sinusoid/figure-8, CMD 0x0C/0x0B/0x11) are not usable as-is:
  they need FlyMode_SDK, run outside the WFB TRAJ state (no RETURN/fence/airborne-cap integration), start at the
  current position, use cm for x/y, step the velocity at start, and run one at a time.
- The firmware has no velocity profiles (grep: no s-curve/trapezoid/min-jerk/quintic). The ground
  `trajectory_pipeline.time_profile` is trapezoid-only.

## Design: path x velocity decomposition

A **program** is a list of segments. Each segment = one geometry **atom** (where to go, parametrised by a path
variable s) + one velocity **profile** (how fast s moves). Any atom takes any profile. Evaluated analytically at
200 Hz inside the WFB TRAJ state, so it keeps today's safety path: TRAJ -> RETURN -> SETTLE -> land, abort, RC
takeover, fence push (pauses the clock), 120 s airborne cap.

### Atoms (the basic presets)

| atom | parameters | s runs over | notes |
|---|---|---|---|
| LINE | x, y, z, yaw | metres, 0..L | straight line to an absolute point; yaw interpolated along s; L = 0 allowed (pure yaw/hold) |
| TURN | yaw | degrees, 0..abs(dyaw) | yaw in place, shortest arc |
| ARC | cx, cy, sweep_deg, dz, yaw_mode | metres, 0..abs(sweep)*r | radius and start angle from the start point; + sweep = CCW; dz makes a helix; yaw_mode 0 hold, 1 follow tangent |
| LISSA | a_i, n_i, p_i (x, y, z), cycles | cycles, 0..cycles | start-relative: p_i(phi) = p0_i + a_i (sin(2 pi n_i phi + p_i) - sin p_i); fig-8, sine on one axis, z bob are special cases; peak speed bound sqrt(sum (2 pi a_i n_i f)^2) |
| HOLD | dwell_s | time | still hold at the current point |

Every atom starts where the previous one ended (no jumps by construction).

### Profiles (velocity shapes, selectable per segment)

One planner for all shapes: ramp (v_in -> v_cruise), cruise, ramp (v_cruise -> v_out), limits a_max (and j_max).
Each shape is a symmetric unit ramp f(tau), f(tau) + f(1 - tau) = 1, so the ramp distance is
(v0 + vc)/2 * Ta for every shape and the planner does not care which shape it runs.

| profile | f(tau) | integral F(tau) | ramp time Ta | use |
|---|---|---|---|---|
| TRAP | tau | tau^2/2 | dv/a | today's ground profile; sharpest |
| SCURVE | 7-segment jerk-limited | piecewise | dv/a + a/j (or 2 sqrt(dv/j) if a is not reached) | smooth default |
| SINE | (1 - cos pi tau)/2 | tau/2 - sin(pi tau)/(2 pi) | pi dv/(2a) | smooth, no j parameter |
| QUINTIC | 10tau^3 - 15tau^4 + 6tau^5 | 2.5tau^4 - 3tau^5 + tau^6 | 1.875 dv/a | min-jerk, zero accel at both ends |

- s(t) = v0 t + (vc - v0) Ta0 F(t/Ta0) in ramp 1; d0 + vc (t - Ta0) in cruise; mirrored in ramp 2.
- If the segment is too short for the cruise speed, the planner lowers vc (bisection) until it fits; if even
  v_cruise = max(v_in, v_out) does not fit, the segment is rejected (RANGE).
- v_in / v_out let segments blend without stopping (circle laps at different speeds); 0 = stop-and-go.

### Composition (complex presets = macros on the ground, expanded to atoms)

| flight | program |
|---|---|
| step C | LINE to (0,-0.5) + HOLD, then LINE/HOLD per cardinal step, LINE home |
| D circle | LINE to start + ARC 360 at 0.2, ARC 360 at 0.3 (v_in 0.2), ARC 360 at 0.4, LINE home |
| E figure-8 | LINE to start + LISSA (x n=1, y n=2) per speed, LINE home |
| F speed ladder | LINE/HOLD sequence at 0.4 then 0.6 m/s |
| G height ladder | LINE z 0.5 + HOLD, LINE z 1.1 + HOLD, LINE +x 0.5 + HOLD, LINE home |

### Upload (CMD 0x1C PROG, on the ground before arm)

- Same 9-byte frame (one float). idx = field slot of a **sticky staging record** (fields keep their last value,
  so the GS sends only the fields that changed), plus control slots BEGIN, PUSH_SEG, CRC_HI, COMMIT, CLEAR.
  Program START reuses TRAJ START (needs prim HOVER).
- CRC32 (same polynomial as wfb_crc32) over the canonical segment array, mirrored in Python.
- Upload in IDLE on the ground (TRAJ BEGIN/COMMIT are not gated on prim state): zero airborne cost.

### Validation at COMMIT (analytic, runs in the command critical section)

- Bounding box per segment against the soft limits (wfb_traj_default_limits): LINE endpoints, ARC centre +- r
  (and z range with dz), LISSA start +- 2 abs(a_i).
- Peak speed <= v_max (1.0 m/s) and a_max/j_max within table caps; planner feasibility (above).
- Program starts and ends at the hover point (0, 0, hover_z) within endpoint_tol (0.10 m), like the point buffer.
- abs(yaw) <= 180 at every LINE/TURN end.

### Storage and hook

- Segment array in `s_wfb` (MRAC_CCM) next to the 600-point buffer; a mode flag picks points vs program and
  `wfb_traj_sample` dispatches to the program evaluator. Everything downstream is unchanged.
- Tunables (caps, defaults) as *_ROW tables (docs/firmware-table-pattern.md).
- Host unit tests for the planner and every atom; the Python mirror is checked against the same vectors.

## Ground

- `scenario_schema` gets a `program` step (list of atoms + macros circle/fig8/square/ladder that expand to atoms).
- `campaign_fly` uploads the program after preflight, before "arm by RC"; D, E, F, G and step C become programs.
