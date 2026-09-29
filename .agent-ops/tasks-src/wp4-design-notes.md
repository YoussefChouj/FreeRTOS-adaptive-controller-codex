# flightlab WP3/WP4 supervisor notes (checkpoint 2026-09-30)

Supervisor working notes, not a worker brief. Resume from here after compaction.
"measured" = measured on flight16 on 2026-09-29. Rule conditions and threshold values: spec section 7 and
config/rules.yaml are authoritative; this file only records decisions that go beyond them.

## 1. Before spawning wp3 (one commit together with the brief)
Brief: .agent-ops/tasks-src/wp3-spectrum-mrac.md (assembled, uncommitted). Its contract B4 uses `conv_window_s`,
which contradicts the unpatched spec wording, so patch the spec first.
Grep the DQ-DROP / DQ-MISSING rows first to copy the format; keep each file's EOL; print match counts.
- docs/analysis/flightlab-spec.md
  - regex `over (the )?last 30 ?% of airborne` -> "per s, least-squares over the last `conv_window_s` s of airborne"
  - `remaining_airborne_s` -> `conv_window_s`
  - insert after the `| DQ-MISSING |` row:
    | DQ-STUCK | a stuck var matches no `expected_const` pattern | info: variable not updating; check logging/sensor |
- ground_station/analysis/flightlab/config/rules.yaml: under `thresholds:`, after the DQ-DROP line (check its indent):
  `DQ-STUCK: {expected_const: [...]}` with the list in 2.4.
Commit with an explicit pathspec (the other session keeps files staged):
  git add B S R; git commit -m "agent-ops: WP3 brief (spectrum+mrac), spec slope/DQ-STUCK" \
    -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- B S R; git push
Spawn: bash .agent-ops/vps-worker.sh spawn wp3 agy:gemini-3.8-flash-high,agy:gemini-3.1-pro-high .agent-ops/tasks-src/wp3-spectrum-mrac.md
Then ONE background command: bash .agent-ops/vps-worker.sh wait wp3

## 2. WP4a rules brief (not written yet)
### 2.1 Shape
.agent-ops/tasks-src/wp4a-rules.md, same layout as the wp3 brief: `# Task wp4a: flightlab rules`, guardrails
(`sed -n '3,26p' .agent-ops/tasks-src/wp2-metrics-plugins.md`), <context>, then <task> LAST.
ALLOW-LIST: ground_station/analysis/flightlab/rules/{health,pid,mrac}.py, tests/test_rules_{health,pid,mrac}.py,
.agent-ops/out/wp4a.md. rules/__init__.py exists. Supervisor owns tests/conftest.py, test_pipeline.py,
test_contract_edges.py. Before writing: read plugins/pid_loops.py ~L85-100 (core_reqs) for DQ-MISSING "partial".
Chain: agy:gemini-3.8-flash-medium,agy:gemini-3.1-pro-high (pair a medium with the running wp3 high).

### 2.2 API facts (read 2026-09-29 from registry.py, pipeline.py)
- register_rule(requires) stores RULES[fn.__name__]: function names must be unique across all rule modules.
- get_path(metrics, "a.b.c"): None when a step is missing or not a dict; no list indices.
- fn(metrics, cfg, ctx) -> list[Recommendation]; ctx = {"ledger_rows": list[dict], "log": FlightLog}.
- pipeline.run_rules (pipeline.py:140): entries sorted by name; any None requires path -> skipped; per-rule
  try/except -> failed traceback; a non-Recommendation return -> TypeError, recorded as failed;
  returns (sort_recommendations(recs), skipped, failed); sort key (SEVERITIES.index, id).
- Recommendation (registry.py:53): id; severity info|warn|critical; category data|pid|mrac|hardware|battery|logging;
  target str|None; action increase|decrease|investigate|add_to_preset|enable|disable; factor float|None;
  evidence {dotted path: value}; rationale fixed template; confidence low|medium|high; __post_init__ validates.
- mode = metrics["controller"]["mrac_mode"] (null|off|shadow|active), from pipeline.controller_info(log, segs).

### 2.3 Conventions
T = cfg["thresholds"][RULE]; id `<RULE>-<loop|axis|slotN>`, bare `<RULE>` when single-instance; evidence keys are
dotted paths with list indices as segments (`data_quality.slots.0.drop_pct`); rationale is a fixed f-string template;
a None input skips that item, never raise; every module docstring says HEURISTIC.
Tests: positive + negative per rule (hand-built metrics dicts + cfg fixture), one run_rules skip test, one test that
constant-by-design vars are not flagged.

### 2.4 health.py
- DQ-DROP ["data_quality.slots"]: per slot drop_pct > drop_crit critical, > drop_warn warn; data, investigate.
- DQ-MISSING loops ["loops"]: partial = missing non-empty and >= 1 of Des/FB/U present; info, logging, add_to_preset.
  DQ-MISSING axes, separate function, ["mrac"]: partial = 0 < len(missing) < len(M["fields"]).
- DQ-STUCK ["data_quality.stuck_vars"]: unexpected = vars matching no fnmatchcase pattern in T["expected_const"];
  one aggregated rec, id "DQ-STUCK": info, data, investigate, confidence low, evidence = the unexpected list.
  expected_const: "*.Kp", "*.Ki", "*.Kd", "Ctrler.locxPID.Des", "Ctrler.locyPID.Des", "DroneStatus.ARM_Status",
  "DroneStatus.FlyMode", "Gyro_Z_Offset", "flight_phase", "mrac_flags.*", "g_ctrl_select", "g_of_bias_mode",
  "g_of_handheld_test", "g_yaw_mix_dir", "dbg_motor_manual", "TWC.execute", "g_ekf_gate.ctrl_enable",
  "g_estimator_ready", "g_of_bias_ema_freeze", "g_gyro_z_bias_*", "s_of_bias_*"
  (bias vars: fixed-at-boot bias is the default mode). On the flight16 list in 5 this leaves flagged:
  ano_of.of_quality, g_thrust_est.*, mrac_state.*.Whatf*, mrac_state.*.u_def (22 matched / 22 flagged,
  counted from the list, not run).
- LOG-GAINS ["loops"]: one aggregated rec when any loop has gains None or Kp None; info, logging, add_to_preset.
- MOT-CLAMP motors.airborne.clamp_hi_frac: > clamp_crit critical, > clamp_warn warn.
- MOT-YAWPAIR motors.steady.yaw_pair_pct: abs > yawpair_warn -> warn.
- BAT-LOW battery.v_min_airborne_cell: < cell_crit critical, < cell_warn warn.
- BAT-SAG battery.sag_v_cell: > sag_warn -> warn.
- ALT-SAG loops.alt_pos.steady.e_mean > alt_sag_m -> warn. Loops e = Des - FB (pid_loops.py:68), so FB below Des
  gives e > 0. position.alt_e is FB - Des (opposite sign, measured): do not use it. Target: the Z_rate integrator
  fields; choose the exact target/action when writing the brief.

### 2.5 pid.py (loops in yaml order; conditions per spec section 7)
- PID-OSC steady, rate/attitude levels: osc_peak_hz > d_band_hz -> decrease `<prefix>.Kd` x factor,
  else decrease `<prefix>.Kp` x factor.
- PID-BIAS steady (bias_abs indexed by loop level): increase `<prefix>.Ki`.
- PID-IWINDUP airborne sume_sat_frac > sat_warn: increase `<prefix>.SumEMax`.
- PID-SAT airborne u_sat_frac > sat_warn: investigate.
- PID-LAG airborne: increase `<prefix>.Kp` x factor.

### 2.6 mrac.py (requires ["mrac"])
- MRAC-READY: mode shadow, steady authority_ratio in [ready_lo, ready_hi], weights non-empty and all converged True,
  u_ad_hf_frac < hf_max -> info, enable, target mrac_flags.output_injection_on.
- MRAC-DRIFT: any weight converged False -> warn (action: proposal investigate; decide when writing).
- MRAC-CHATTER: u_ad_hf_frac > hf_max -> warn.
- MRAC-WORSE: mode active only. Baseline = last ctx["ledger_rows"] row with the same flight.preset and
  mrac_mode != "active"; compare float(row["e_rms_steady_<loop>"]) (CSV strings; skip unparseable) with
  loops[<axis loop>].steady.e_rms; fires when current > base * (1 + worse_pct / 100) -> warn, disable,
  target mrac_flags.output_injection_on.

## 3. WP4b (after WP4a)
report/figures.py, render_md.py, render_html.py, ledger.py, compare.py.
- render_md.render(metrics, recs, figs, out_dir) -> Path; render_html.render(...) -> Path
- ledger.read_rows(path) -> list[dict]; ledger.upsert(metrics, recs, path) -> None; ledger.rebuild() takes no args
  (__main__ calls it; re-analyses logs/vofa/*.meta.json, spec CLI `ledger --rebuild`) and returns a printable
- compare.compare(a, b) returns a printable path
- REPORTS_DIR = logs/vofa/reports; LEDGER_PATH = docs/flights/ledger.csv
- ledger (spec lines 205-208): upsert keyed on (flight, started_at); columns flight, started_at, analyzed_at, git,
  notes, preset, mrac_mode, duration_s, airborne_s, worst_drop_pct, v_rest_start, v_min_airborne,
  e_rms_steady_<loop> per loop, clamp_hi_frac, yaw_pair_pct, n_warn, n_critical.

## 4. Verify each worker branch vps/<id>
1 diffstat vs base: allow-list files only. 2 grep: "# Wait", dead code, 0.0 used as unknown, broad except.
3 HEURISTIC docstrings. 4 `git show vps/<id>:<f> > <f>`. 5 `python -m pytest ground_station/analysis/flightlab/tests -q`.
6 flight16 manual pipeline (below). 7 fix inline. 8 `git add $F; git commit -m .. -m "Co-Authored-By: ..." -- $F;
git push`, then `bash .agent-ops/vps-worker.sh clean <id>`. rc=0 / DONE is a claim: read the digest and log tail.

Manual pipeline until WP4b lands (analyze() raises ImportError):
    from pathlib import Path
    from ground_station.analysis.flightlab import pipeline as pl, segments as sm
    log = pl.load("logs/vofa/flight16.meta.json")
    cfg = dict(pl.load_config()); cfg["runtime"] = {"pdf": False}
    segs = dict(sm.segment(log, cfg)); sw = [f"segments: {w}" for w in segs.pop("warnings", None) or []]
    pl.discover("plugins")
    m = pl.build_metrics(log, segs, pl.run_plugins(log, segs, cfg), sw)
    pl.validate(m)
    figs, fw = pl.run_figures(log, segs, cfg, m["plugins_run"], OUT / "figs")  # OUT = any scratch Path

## 5. flight16 facts (measured 2026-09-29)
- clock drift -39.9 ppm, 0 drops; armed [6.7, 123.17], airborne [29.17, 116.41], landing [116.41, 123.17];
  5 steady intervals.
- controller mrac_mode shadow, ctrl_select 1;
  preset flight_default_pid_flight_test_motors_thrust_position_hold_of_adaptive_flight_test.
- steady: loops.alt_pos e_mean -0.00711 (position.alt_e_mean +0.00711), loops.alt_rate e_mean 0.01270,
  position of_alt_cm_mean 103.00, of_alt_cm_std 57.45, alt_e_rms 0.1273.
- 44 stuck_vars: Ctrler.locxPID.Des, Ctrler.locyPID.Des, DroneStatus.ARM_Status, DroneStatus.FlyMode,
  Gyro_Z_Offset, TWC.execute, ano_of.of_quality, dbg_motor_manual, flight_phase, g_ctrl_select,
  g_ekf_gate.ctrl_enable, g_estimator_ready, g_gyro_z_bias_blocks, g_gyro_z_bias_track, g_of_bias_ema_freeze,
  g_of_bias_mode, g_of_handheld_test, g_thrust_est.empirical[0..3], g_thrust_est.imu_total, g_yaw_mix_dir,
  mrac_flags.adaptation_on, mrac_flags.output_injection_on, mrac_state.{pitch,roll,yaw,z_rate}.Whatf[0..2],
  mrac_state.{pitch,roll,yaw,z_rate}.u_def, s_of_bias_seeded, s_of_bias_x, s_of_bias_y.
  flight_phase is presumably "stuck" because stuck_vars is computed over airborne (definition not re-read).
- Unverified, do not quote as fact: 25 Hz roll limit cycle; locx saturation; why Whatf stays 0
  (mrac.c:390 makes Whatf a low-pass of Theta; mrac.c:626-629 reset it to 0).
