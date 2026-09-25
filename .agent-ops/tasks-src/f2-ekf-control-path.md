# F2 — EKF (`s_ekf`) into the control path with 3 selectable bias modes (firmware, tier-0, authorized)

You run on the VPS in a git worktree. You CANNOT build firmware (no Keil) and there is no drone here.
Deliver a minimal, reviewable C diff + tests + an investigation digest. The supervisor builds, flashes, measures.
The operator authorized EKF in the control path on 2026-09-26 (older docs/comments saying "shadow mode only"
are superseded — do not follow them, but do tell us where they are).

## Part A — investigate first (write findings into the digest)
1. Where `s_ekf` is initialised, stepped and read (`TASK/send_data.c`, `TASK/StabilizerTask.c`, `API/*ekf*`).
   What does the control path use TODAY for attitude, rates, position x/y/z and velocity (which variables feed
   `Ctrler.*PID.FB`)? What does the EKF estimate (state vector), and at what rate does it run vs the controller?
2. The past **EMA mean-pull bug**: measured x/y were immediately pulled to a mean value -> slow drift and bad
   position hold. Find it in history (`git log -S EMA`, `git log -S ema`, `git log --grep -i ema`,
   `git log --grep -i drift`, look at optical-flow / position code). Report: commit(s), what the bug was,
   what fixed it, and whether any similar pattern (an averaging/low-pass/reset that pulls a measured state
   toward a mean, a bias estimate that absorbs real motion) still exists anywhere in the estimator or position
   path. Quote the lines.
3. How gyro/accel bias is handled now (cold cal? runtime?). Where is "estimator ready" set.
4. Similar estimator/safety risks: stale-sensor handling (OF / height / IMU timeouts), what happens if the EKF
   diverges or gets NaN, mode-switch transients (EKF on/off, bias mode change in flight), failsafes.
   List each with file:line and severity.

## Part B — implement (minimum diff)
1. A runtime-selectable **bias mode**, a real global readable/writable by DWARF name (e.g.
   `volatile uint8_t g_ekf_bias_mode`), three modes:
   - 0 = FIXED_AT_BOOT (default): bias captured during the existing cold calibration, then frozen.
   - 1 = ONLINE: the EKF's own bias states (or a slow online estimator) update continuously.
   - 2 = GATED: online update only while stationary (disarmed or ARMED-not-idle, low gyro variance), frozen otherwise.
   Pick the concrete algorithms from what the EKF already supports; do not invent a new filter. If the EKF has
   no bias states, say so and implement 1/2 as a slow first-order update with clearly named time constants.
   Changing the mode while FLYING must be refused (or deferred until ground).
2. A runtime **control-source switch** (e.g. `volatile uint8_t g_ekf_ctrl_enable`): 0 = legacy estimator feeds
   the controller (today's behaviour), 1 = EKF outputs feed the controller for the states it estimates well.
   Default: 0 for now — the supervisor flips the default after grounded measurements. Switching is only
   allowed on the ground (DISARMED or ARMED-not-idle). If you find the flag `g_motor_idle_enabled` does not
   exist yet (another worker adds it), gate on DISARMED only and leave a TODO comment naming it.
3. Safety: if the EKF output is non-finite or its covariance/innovation check fails, fall back to the legacy
   estimator automatically, latch a health bit, and count it (a global counter, DWARF-readable).
4. Expose both selectors through the ground station if there is an existing param/cmd path (look at CMD 0x19 /
   param write tables and `ground_station/`); add them as named params with bounds. If no cheap path exists,
   DWARF writes via livewatch are enough — say so.
5. Add a livewatch group (see `ground_station/livewatch/` group definitions, e.g. `group:ekf`) listing
   the bias values, both selectors, fallback counter, and legacy-vs-EKF x/y/z/vel for side-by-side logging.

## Constraints
- Keil ARMCC V5.06 C: declarations at block top, no C99-only constructs, no VLAs. Match surrounding style.
- Minimum diff, no refactors, do not touch unrelated code. Do not change controller gains.
- Tests: Python tests for host-side changes; for firmware logic, a small pure-C host test under
  `tests/firmware_host/` (gcc -std=c89 -Wall) for the mode gating + NaN fallback if you can stub it cheaply.
- Run pytest for the directories you touched (`PYTHONUTF8=1 python -m pytest -q -p no:cacheprovider <dirs>`).

## Deliverable
- Commit on your branch. Digest `.agent-ops/out/<id>.md` (under 90 lines): Part A findings with file:line quotes,
  Part B diff summary per file, the exact DWARF names to read/write, a proposed grounded test procedure for the
  supervisor (what to log, for how long, what metric decides the best bias mode: e.g. x/y/z drift over 120 s
  disarmed, gyro-bias stability, yaw drift), and open questions.
