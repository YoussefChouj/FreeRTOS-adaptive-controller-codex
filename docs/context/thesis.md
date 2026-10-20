# Context shard: Research Thesis

Use this shard when the task is about a thesis claim, a mathematical
derivation, a chapter section, a benchmark result, or a citation. Code edits
that produce thesis evidence belong here first; the operational firmware path
is in `flash.md`.

## Concept-to-Artifact Entry Points (verified by tools/doc_paths.py)

| Thesis concept | Where in the repo |
| --- | --- |
| Adaptive law analysis (Chapter 3) | `API/mrac.c`, `API/mrac_math.c`, `docs/context/flash.md` |
| State estimation / EKF bias (Chapter 4) | `API/ekf.c`, `API/ekf_of.c` |
| Controller studies (L1, MRAC variants, hybrid, ablation) | `sim/bench/ctrl_mrac.py`, `sim/bench/ctrl_l1.py`, `sim/bench/ctrl_hybrid.py`, `sim/bench/ablate.py` |
| Simulation ledger + run provenance | `sim/bench/ledger.py` |
| Controller coefficient table (one aligned row, with `_ROW`) | `API/pid.c` |
| Empirical flight logs (post-flight analysis) | `docs/lab/days/<YYYY-MM-DD>-report.md` |
| Subscribed telemetry that mirrors simulation state | `ground_station/comm/decode.py`, `ground_station/comm/subscribe_presets.py`, `API/subscribe.c` |
| Closest live binary for "what was running when we got this number" | `OBJ/JX_FLY.axf` (gitignored, built locally) |
| Funding / partner / hardware rules that bound what claims are valid | `AGENTS.md` (Authorizations + Safety) |

## Theoretical <-> Implementation Invariants

1. **A change in `flash.md` to a Lyapunov-relevant block must preserve the
   bound assumptions cited here.** If it cannot, the thesis claim is no
   longer supported by the firmware. File as `docs/context/inbox/<id>.md`
   and stop; do not "patch" the thesis silently.
2. **Reported RMSE / tracking-error numbers must be reproducible from a
   commit SHA + parameter set.** If you cannot cite both, do not put the
   number in the thesis. The run index exists for exactly this.
3. **A sim-only change is not a thesis claim** until at least one flash
   run exists in the run index that exercises the same condition.

## Tombstones (Do Not Re-Propose)

- **[2026-10] "Tracking error is dominated by v/Kp lag."** Refuted.
  Empirical figure-8 tracking error at the y-extreme is a growing overshoot
  on each x boundary; VFF+P model explains 12–15 cm of it. See
  `docs/lab/figure8-tracking-analysis-2026-10-19.md`. Do not re-derive the
  lag-only explanation.
- **[2026-10] "Landing offsets are an abort artifact."** Refuted. Eight
  landings ended 17–41 cm off pad at low altitude; the cause was the
  integrator-clear at 0.12 m in `API/land_profile.h`. See
  `docs/lab/landing-offset-analysis-2026-10-19.md`. Do not re-attribute it
  to abort logic.

## When this shard does not apply

If the task is hardware, MRAC, EKF tuning, or flight, switch to `flash.md`.
For meta questions about learning style or mental models, switch to
`learning.md`. Use the router in `README.md`.
