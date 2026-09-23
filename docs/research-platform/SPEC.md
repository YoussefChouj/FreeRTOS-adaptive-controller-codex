# Research platform spec (grilling session 2026-09-23/24)

Goal: every armed flight, debug session and feature check adds to one searchable research dataset.
A dashboard-native agent runs experiments semi-autonomously inside guardrails built from
deterministic workflows. Thesis context: frequency-routed MRAC for dense-waypoint tracking.
The physical constants live in the original project (`sim/plant.py`, `docs/sysid_results.md`);
reference them there and never copy numbers from memory.

## Decisions

| # | Decision |
|---|---|
| Safety | No always-on firmware trips. Each experiment declares its own abort envelope, and the operator holds the RC kill switch. |
| Simplex (run-time assurance) | Lives in the firmware and is inert unless the executor is running an experiment that declares an envelope. Fallback: freeze Ŵ and fade u_ad to 0 over about 100 ms. PID keeps flying and nothing disarms. Triggers come from the spec: the state envelope, plus adaptive-layer health (‖Ŵ‖ bound, u_ad saturated for more than N ms, gate sum ≠ 1). Every trip is a labelled Run event. |
| Simplex concern (operator) | Health triggers may kill adaptation too early. Mitigations: trigger thresholds are per-spec with wide defaults; Ŵ is frozen, not reset; adaptation resumes after the state is back inside the envelope for a hold time (hysteresis); an `observe_only` mode logs would-be trips without acting, so thresholds are tuned from data first. |
| Run record | One schema for `kind ∈ {experiment, debug, validation, flight}`. Fields: intent, hypothesis, phase, firmware hash, git commit, full parameter snapshot, controller variant, trajectory, captures, Simplex events, notes (operator and agent), outcome, metrics. |
| Storage | Data lives outside git in `D:/uav-runs/` (override: `UAV_RUNS_DIR`): Parquet captures plus one SQLite index. Git keeps specs, workflows, findings and reports. |
| Autonomy | L0 observe, L1 bench (disarmed) and L2 an approved single spec are available now. L3 campaigns (the operator approves a parameter envelope and the agent picks points inside it) are the target. |
| Workflows | Typed building blocks (`set_params`, `fly_trajectory`, `capture`, `wait_until`, `analyze`, `revert`) composed into version-controlled YAML workflows. An agent-written workflow must pass a schema check and a sim/replay dry run, and then the operator approves it into the library. |
| Analysis | The same core metrics every time a Run ends (per-axis RMSE, overshoot, settling, saturation time, spectrum, Simplex events), plus metric plugins. The first plugins are thesis metrics: RMSE vs xm_physics, u_ad vs e_aug spectrum, ‖Ŵ‖ convergence, gate saturation, σ_eff, u_ad spike > 5× median. |
| Variants | Compile both variants in and select at runtime with a parameter, so A/B runs happen in one session. Check the flash and CPU budget first. |
| Sim harness | One harness for Phase 0 validation and for workflow dry runs. A campaign config must pass in sim before it runs on hardware. |
| Thesis link | Runs carry `phase` and `hypothesis`. Results tables are queries over the index. |
| Trajectories | Presets, parametric families and excitation overlays (multisine and chirp, for persistent excitation), with a feasibility check against the rig limits. The fixture frees roll and pitch (±40°), yaw (unbounded) and z (0.42–0.65 m), with no x or y translation. Hand-drawn canvas trajectories are for free flight only. |
| Position truth | On board: optical flow and height only; there is no motion capture. Decide before thesis Phase 3 (risk: cross-track RMSE judged against the drone's own estimate). |
| Agent runtime | A terminal panel (xterm.js + WebSocket + PTY) running opencode by default, switchable to Claude Code. Broad repo and shell access. Hooks gate only the physical tier (arm, motors, tier-0 writes while armed, flashing) behind the operator. |
| Agent knowledge | A guide generated from code (workflow catalog, step signatures, parameter meanings, safety tiers) plus a short hand-written research guide. |
| Findings | Durable findings go in `research/findings/*.md` (a header that links Run ids); urgent items go as live notes; an index sits behind MCP. |

## Build order
1. Dashboard fixes: no subscribed data after a service restart; co-pilot silent.
2. Run record, index and deterministic analysis (works on recorded CSVs, which serves thesis Phase 0).
3. Sim/replay harness.
4. Executor and workflow library (ground side). The firmware Simplex and runtime variants need the operator's review, because they are tier 0.
5. Terminal agent and findings channel.
