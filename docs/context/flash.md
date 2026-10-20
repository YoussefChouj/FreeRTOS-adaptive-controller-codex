# Context shard: Flash & Firmware

Use this shard when the task touches firmware, hardware, MRAC, EKF, PID,
motor mixing, the inner control loops, or anything that needs Keil + pyOCD.

## Concept-to-Artifact Entry Points (verified by tools/doc_paths.py)

| Concept | Where in the repo |
| --- | --- |
| PID loop structure (gyrox = roll, gyroy = pitch) | `API/pid.c` |
| MRAC adaptive law per axis (history of the controllers) | `API/mrac.c`, `API/mrac_math.c` |
| EKF estimator (`s_ekf`, 3 bias modes, fixed-by-default) | `API/ekf.c` |
| Optical-flow fusion (`s_ekf_of`, NN drift block, EKF velocity FB) | `API/ekf_of.c`, `API/Ano_OF.c` |
| Inner control loop glue (gyro -> PID -> MRAC -> mixer) | `TASK/StabilizerTask.c`, `API/wfb_glue.c` |
| Trajectory glue (figure-8, landings) | `TASK/AutoflyTask.c` |
| Vertical takeoff bias landing state machine + gates | `API/land_profile.h`, `API/land_gate.c`, `API/land_xy_gate.c` |
| RC input, preflight arm gate | `API/rc_input.c`, `API/prearm.c` |
| Hardware safety / tier-0 paths | `AGENTS.md` (Authorizations table); zones and `tools/tier0.txt` in `docs/agent/governance-zones.md` |
| Flash + verify pipeline | `ground_station/flashtool/rebuild_and_flash.py` |
| Live memory/register read for any DWARF symbol | `ground_station/livewatch/` |
| Telemetry subscribe protocol (frame types 0x08..0x0C) | `docs/telemetry-protocol.md` |
| Bench scripts for controller studies (MRAC, L1, hybrid) | `sim/bench/bench.py`, `sim/bench/ctrl_mrac.py` |
| Bench ledger for sim + flash proof rows | `sim/bench/ledger.py`, `docs/lab/days/` |

## Cross-cutting invariant

A change here to the adaptive law, EKF bias mode, or PID architecture
**must preserve the Lyapunov / tracking bound assumptions in
`docs/context/thesis.md` § Theoretical <-> Implementation.** If a change
appears to violate that invariant, append it to `docs/context/inbox/<id>.md`
and stop. Do not silently expand scope to "fix" the thesis.

## Tombstones (Do Not Re-Propose)

- **[2026-09] Unbounded integral action on the inner roll loop during dense waypoint transitions.**
  Effect: actuator saturation at high `pos_err` slew rates. Cause: missing
  projection in the integrator. Fix: projection operator on the integrator.
  Use it; do not "simplify it away".
- **[2026-09] Switching EKF bias to dynamic online estimation during aggressive trajectory steps.**
  Effect: 100–500 ms phase-lag divergence in the inner attitude loop; the
  EKF output *leads* truth by one sample window during high-rate turns.
  Fix: keep the bias mode **fixed-at-boot** by default (operator,
  2026-09-26). Other modes require fresh empirical evidence AND operator
  approval.
- **[2026-10] Cold-boot == flash.** A successful firmware flash is a cold
  boot. Code that assumes warm state after `rebuild_and_flash --yes` is wrong;
  always verify with `python -m ground_station.livewatch verify` before
  any post-flash test.
- **[2026-10] UV4 silent link noop.** UV4 sometimes exits 0 without
  programming flash. Reflashing does NOT mean the new firmware ran.
  See `AGENTS.md` > Hardware notes; verify before flying.

## When this shard does not apply

If the task is about a research claim, math, or bench suite, switch to
`thesis.md`. If the task is about your own learning / mental models, switch to
`learning.md`. The router (`README.md`) decides.
