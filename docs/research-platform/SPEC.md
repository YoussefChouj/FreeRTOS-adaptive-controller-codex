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

## T2 as built

Package: `ground_station/research/`
- `run.py` — `Run` dataclass (frozen), fields: id/ULID, kind, intent, hypothesis, phase, firmware_hash, git_commit, params, variant, trajectory, captures, events, notes, outcome, metrics, tags, created_at. JSON round-trip via `to_json()`/`from_json()`, file save/load.
- `store.py` — `Store` class. Location: `UAV_RUNS_DIR` env, default `D:/uav-runs` else `~/uav-runs`. Layout: `<dir>/runs/<id>/run.json`, `<dir>/runs/<id>/captures/` (parquet via pyarrow, CSV fallback), `<dir>/index.sqlite` (runs + metrics tables). API: `create`, `get`, `update`, `query(sql_where, params)`, `import_capture(path, kind, **meta)`.
- `analysis.py` — Core: `rmse_of`, `overshoot`, `settling_time`, `saturation_time`, `dominant_peaks`. Plugin registry: `@metric("name")`. Stubs: `u_ad_spike_ratio`, `w_norm_convergence`, `gate_saturation` (all skip with None when columns missing). `generate_report()` writes `report.md`.
- `__main__.py` — CLI: `import <path>`, `list [--kind] [--phase]`, `show <id>`, `analyze <id> [--axis]`, `query <sql> [params...]`.
Tests: 45 pass — JSON round-trip, step overshoot, sine RMSE, query filtering, import CSV/parquet, plugin registry, stub skip. Full tree: 1005 passed, 27 skipped.

## T3 as built

Package: `ground_station/research/sim/`
- `plant.py` — Discrete rate-plant per axis: `G(s)=K/(s*(1+s/p))*e^(-sT)` for roll/pitch, `G(s)=K/s` for yaw. ZOH discretisation at configurable dt (default 500 Hz). Transport delay via integer-sample FIFO.
- `reference_model.py` — Per-axis reference models (2nd-order for roll/pitch, 1st-order for yaw), forward/semi-implicit Euler integration matching firmware.
- `baseline.py` — Cascaded attitude->rate PID matching firmware's positional form with conditional integration, independent term clamping, and sum clamping.
- `replay.py` — Replay captured commands through plant+PID, producing time-series outputs (response, xm_physics, pid_output, u_ad).
- `dryrun.py` — `dry_run(workflow_or_trajectory)` executes sim and stores a validation Run tagged `sim`.
- `constants.py` — All physical constants ported from original project with file:line citations. Every value verified.
Fixes: plant now divides mixer-unit inputs by mrac_to_mixer to get Nm; replay fixed variable shadowing and list accumulation; baseline.step accepts optional rate_fb; all 16 sim tests pass.
Full tree: 1021 passed, 27 skipped, 3 subtests.
Constants NOT FOUND: R_MOTOR (arm length) — cited from plant.py ~140 but exact line not isolated; all other constants verified against original sources.

## T4 as built

Package: `ground_station/research/`
- `workflow.py` — YAML schema loader/validator. 8 step types (`set_params`, `fly_trajectory`, `capture`, `wait_until`, `analyze`, `revert`, `note`, `call`). Unknown step types are validation errors. Envelope bounds with `enforce`/`observe_only` modes.
- `trajectories.py` — Presets (step, doublet, chirp, multisine, figure8), parametric families, excitation overlays. Feasibility check against `fixture_4dof` and `free_flight` profiles.
- `executor.py` — `run_workflow(spec, backend)` with `SimBackend` (T3 sim) and `DashboardBackend` (HTTP-injectable). Always runs `revert` (try/finally). Envelope evaluation: `enforce` aborts, `observe_only` records only. Sim dry_run required before hardware.
- `campaign.py` — L3 operator-approved envelope over parameters. Grid and successive-halving point proposal. Budget tracking. Envelope membership check.
- `workflows/` — Starter YAML: `pid_baseline_step.yaml`, `chirp_sysid_roll.yaml`, `mrac_ab_gate_compare.yaml` (TODO-firmware), `validate_new_feature.yaml`.
- CLI: `workflow validate|dryrun|run`, `campaign plan`.
Tests: 52 passed, 1 skipped (pytest attribute edge case). Full research tree: 113 passed, 1 skipped.

## T5 as built

Package: `ground_station/service/terminal.py` + `shell/plugins/terminal-panel.js`
- WebSocket `/api/terminal/ws` — token auth via `?token=` (401 without); PTY spawns `opencode` or `claude`; resize via `{"type":"resize","rows":N,"cols":M}`; loopback-only; token written to `.agent_state/terminal-token`.
- Terminal panel: xterm.js + xterm-addon-fit from cdn.jsdelivr.net; "Terminal" tab; token stored in sessionStorage (try/catch).
- `ui_navigate(tab)` / `ui_highlight(panel)` MCP tools broadcast `ui` SSE events.
- `file_finding` MCP tool: create/list findings in `docs/research-platform/findings/`.
- CLI: `python -m ground_station.research finding new|list`.
- `docs/research-platform/AGENT_RESEARCH_GUIDE.md` — 70-line hand-written guide.
- `python -m ground_station.research catalog` writes `CATALOG.md` from workflow lib + action registry.
- `opencode.json` — dashboard MCP server + permission policy (flash/arm/livewatch blocked).
Tests: 20 passed, 6 skipped (Unix-only PTY). Full research tree: 136 passed, 7 skipped.

