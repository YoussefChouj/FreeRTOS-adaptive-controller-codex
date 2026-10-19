# j-sysid-multi: 4-axis orthogonal multisine SysID + direct IV fit

Branch: `overnight-2026-10-08`. Open ONE PR against it. Quality bar: flight-critical firmware. A parallel Jules
session builds `API/att_ctrl.*` and edits `TASK/StabilizerTask.c` Mix_Compute, `TASK/AutoflyTask.c` VAR rows
54-127 and `campaign_capture.py`. Keep your edits to those files small and local; use VAR ids from 128 up.
Read first: `API/sysid.h/.c` (FSM, geofence, dead-man, Schroeder tables ~182, axis check ~219, Z reject ~224),
`TASK/StabilizerTask.c` (Att_Compute ~2205: `SysID_Update`, the `*PID.Des += SysID_GetRateSetpoint(...)` sites and
the Z_ratePID.Des site), `TASK/send_data.c` 728-790 (0x03 ID frame), `ground_station/autotune/excitation.py`
(~80-101), `ground_station/autotune/frf.py` (plant_frf ~165, indirect division ~187, fit_plant ~195),
`ground_station/livewatch/campaign_capture.py` (stream groups ~191-212), `docs/firmware-table-pattern.md`.

## Goal

One 20 s hover flight identifies roll, pitch, yaw and Z at once. Each axis gets its own disjoint set of exact
harmonics of one period T, so the four inputs are orthogonal over every whole period and one flight gives four
SISO frequency responses (and the 4x4 cross-coupling as a bonus). The existing one-axis mode stays as it is: it
is the run-sheet fallback.

## Signal

- Period T = 10 s = 2000 ticks at 200 Hz. Tone k of axis a: f = m/T, integer m, with m mod 4 = a
  (ROLL 0, PITCH 1, YAW 2, Z 3). Bands: roll/pitch 0.4-25 Hz, yaw and Z 0.3-10 Hz. At most 24 tones per axis,
  roughly log-spaced inside the allowed set (generator option). Flat amplitude per tone (option: 1/f shaping).
- Peak setpoint amplitude per axis (PROPOSED, VAR rows): roll/pitch 20 deg/s, yaw 20 deg/s, Z 0.15 m/s.
- Phases: minimize the peak factor per axis (L-infinity: Schroeder start, then iterative clipping or the
  Guillaume method). Target relative peak factor <= 1.45 (UNVERIFIED target): report what you achieve per axis.
- Sequence: 0.5 s cosine fade-in, 2 whole periods (20 s), 0.5 s fade-out. Abort = the existing FSM abort path.
- Evaluation must be exact and drift-free: phase index n = (m*k + p) mod 2000 with an integer phase offset p
  (phase quantized to 1/2000 cycle), sine from a quarter-wave lookup table. No sinf in the tick, no recursive
  oscillators. Python evaluates the same integers, so parity is exact to float rounding.
- The table (m, amplitude, p per tone, per axis) is generated into `API/sysid_ms_table.h` (`*_ROW` macro tables)
  by `python -m ground_station.autotune.excitation`, with a header comment giving the achieved peak factors.

## Firmware

- A new sysid mode MULTI with an axis mask (default all four; 2-axis roll+pitch must work). Same FSM, geofence
  and RC dead-man as today. MULTI starts only from a stable hover in position hold.
- Z in MULTI is allowed with extra bounds (PROPOSED, VAR rows): start height >= 0.40 m (ground effect), abort if
  height leaves z0 +- 0.25 m or |vz| > 0.5 m/s. The one-axis mode keeps rejecting Z as today.
- Injection uses the existing sites: rate setpoints for roll/pitch/yaw, the Z_ratePID.Des site for Z.
- Expose for logging: `sysid_tick` (uint32, the k used in the table), the per-axis dither actually added
  (4 floats), the FSM state. Read them at the same tick as the rest of the group.

## Logging

Add a `sysid4` stream group in campaign_capture.py: per axis the rate setpoint r, the measured rate (Z: climb
rate), the plant input u = u_nom + u_ad (the `mrac_state` members; check what the 0x03 frame sends), the dither;
plus `sysid_tick`, the FSM state and the battery voltage. Price it with the existing planner at 100 Hz and state
its B/s. All members of one sample must come from one stabilizer tick (say how you guarantee it). Do not change
the 0x03 frame.

## Host fit (`frf.py`)

- Add `direct_iv` (keep the old indirect path as the default for one-axis runs): for each axis a, at its own
  harmonic bins only, P_a = X_a / U_a where X and U are the exact DFTs over whole periods (no window, no leakage),
  instrumented by the known dither. Use the logged `sysid_tick` to align, drop the fade-in and fade-out.
- Noise per bin from the period-to-period difference (2 periods): report SNR per bin instead of coherence;
  weight the fit by it.
- Off-diagonal: response of axis i at the bins of axis j gives the 4x4 coupling FRF; output it, do not fit it.
- `fit_plant` option with a damping pole: b / ((s + a)(tau*s + 1)) * exp(-s*D). Output b, a, tau, D per axis
  (the j-att-ctrl model, same names), with confidence bounds.

## Tests (must pass; list each with its result in the PR)

C harness in `API/tests/` (run by `python tools/host_tests.py`): table evaluation parity vs Python golden values;
exact periodicity (sample k equals sample k + 2000); per-axis orthogonality (the DFT of axis a's dither is zero at
every other axis's bins); fade-in and abort ramps; Z guard rejections; one-axis mode unchanged (golden output).
pytest: the generator is deterministic and the header is up to date; the peak factor meets what the header claims;
`direct_iv` recovers b, a, tau, D within 5% from a simulated closed loop (PID feedback, process noise, 4 axes
excited at once, 100 Hz sampling of a 200 Hz sim); the old indirect path's results are unchanged.
gcc flags for the harness: `-std=c99 -Wall -Wextra -Werror -Wdouble-promotion -Wvla -Wdeclaration-after-statement`.

## Rules

ARMCC 5.06 C99 subset: locals at the top of a block, float literals with `f`, no double, malloc, VLA or recursion.
CRLF line endings in .c/.h/.py, like the existing files. Flash for the tables <= 4 KB (state it). Do not touch
MRAC, subscribe, the frames or any USER/ file except adding a new .c file to `USER/JX_FLY.uvprojx` if you make
one. If a spec point conflicts with the code, follow the code, say so in the PR.
