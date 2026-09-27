# Night run STATE (rewrite at every milestone; <= 80 lines)
Updated: 2026-09-27 23:25 CST | Phase: 0 (plant + simulator) | Bench: none yet (bench_v1 not frozen)

## Done
- d25d9e0 brief committed unchanged. Keep-awake on (session_idle).
- Workers spawned on VPS (laptop has ~1.7 GB RAM free; local power guard refused a local worker):
  - night-fwinv (agy gemini-3.1-pro-high): firmware inventory -> .agent-ops/out/night/fw_inventory.md
    brief .agent-ops/tasks/night-fwinv.md (gitignored dir)
  - night-prev (agy gemini-3.1-pro-high): PREV priors + 3-layer planning digest -> prev_digest.md
    PREV copied to VPS ~/prev (127 files) incl. transcript excerpt of 2026-09-23 planning
    (source: this project's transcript 4523aeda...jsonl, 32 msgs). brief .agent-ops/tasks/night-prev-vps.md
  - Retrieve: `bash .agent-ops/vps-worker.sh wait <id>` (background) then `fetch <id>`; verify, cherry-pick file.
- Found: PREV has no phase0-spec.md; 3-layer refs: PREV docs/adr/0007-frequency-dependent-basis-activation.md,
  .agent_contracts/feature-gating-phase1/spec.md, this repo docs/research-platform/SPEC.md.

## Top 5 on tune split
(none yet)

## Open hypotheses (ranked)
(none registered yet)

## Running jobs
- VPS: night-fwinv, night-prev

## Next action
1. Read sim/adaptive_compare/sim_core.py + sim_coupled.py (what exists) while workers run.
2. Write sim/bench/plant.py (6-DOF, motors, battery, sensors, EKF-approx, firmware cascade).
3. Flight-log calibration from logs/vofa/flight1..8 (untracked; local only).
4. Freeze bench_v1 (scenarios, splits, seeds, metrics) and commit before any tuning.
