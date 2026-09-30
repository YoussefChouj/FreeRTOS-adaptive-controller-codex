# Task wp4a: flightlab rules (rules/health.py, rules/pid.py, rules/mrac.py + tests)

You are a worker in a git worktree of this repo. Quality bar: production code a senior reviewer merges unchanged.

## Guardrails (hard)
- EDIT ONLY: `ground_station/analysis/flightlab/rules/health.py`, `rules/pid.py`, `rules/mrac.py`,
  `ground_station/analysis/flightlab/tests/test_rules_health.py`, `tests/test_rules_pid.py`, `tests/test_rules_mrac.py`,
  `.agent-ops/out/wp4a.md`. `rules/__init__.py` already exists (empty): leave it. Touch nothing else (not pipeline.py,
  registry.py, config/*.yaml, conftest.py, test_pipeline.py, test_contract_edges.py, plugins/, docs/).
- Never invent a metric path, config key or firmware variable name. Every path you read must exist in the plugin
  code (`plugins/*.py`) or `schema/metrics.schema.json`; every threshold comes from `config/rules.yaml`.
- A None / missing input skips that item (or the rule via `requires`). Never raise from a rule, never use 0.0 or
  a default number to mean "unknown", no broad `except`, no dead code, no placeholder comments, no TODOs.
- Python 3, stdlib + what the package already imports. Match the style of `plugins/pid_loops.py` and `registry.py`.
- Do not start or POST to anything on port 8081. No hardware commands. Do not read `logs/` except the one flight16
  path below.
- Commit ONLY the allow-listed files on your worktree branch (`git add <files> && git commit -m ... -- <files>`).
  Never commit `logs/` or `docs/flights/`.

## Read first
1. `.agent-ops/tasks-src/wp4-design-notes.md` sections 2.2-2.6 (API facts, conventions, per-rule design).
   THIS BRIEF OVERRIDES the notes wherever they differ.
2. `docs/analysis/flightlab-spec.md` section 7 (Recommendation + v1 rule table), `config/rules.yaml`,
   `registry.py` (register_rule, get_path, Recommendation, SEVERITIES), `pipeline.py` run_rules (~L140).
3. `plugins/pid_loops.py` (loop metrics incl. osc_*, track_*, sume_sat_frac, u_sat_frac; ~L85-100 core_reqs),
   `plugins/mrac.py` (weights: final, max_abs, slope_last30, converged, t90_s; airborne/steady stats),
   `plugins/motors.py`, `plugins/battery.py`, `plugins/data_quality.py`.

## Facts (measured on flight16 metrics, 2026-09-30)
- `metrics["loops"]` is keyed by loop name in loops.yaml order; each loop: prefix (e.g. "Ctrler.gyroxPID"), level
  (rate|attitude|velocity|position|altitude_pos|altitude_rate), gains (None when not streamed), limits, missing,
  airborne{...}, steady{...}.
- `metrics["mrac"][axis]`: prefix, missing, mode_frac{off,shadow,active}, airborne{e_rms,u_nom_rms,u_ad_rms,
  authority_ratio,u_ad_max_abs,u_ad_hf_frac,corr_*}, steady{same}, weights{"Theta[0]": {final, max_abs,
  slope_last30, converged, t90_s}, ..., "Whatf[0]": ...}.
- `cfg["mrac"]["axes"][axis] = {"prefix": ..., "loop": ...}` (roll->rate_roll, pitch->rate_pitch, yaw->rate_yaw,
  z_rate->alt_rate); `cfg["mrac"]["fields"]` is the dict of per-axis fields (DQ-MISSING axes uses its length).
- `cfg["params"]["mrac"]["conv_window_s"]` (30.0) is the window the plugin's slope_last30 / converged use.
- `metrics["flight"]`: name, started_at ("2026-09-29 08:51:13", sorts chronologically as a string), preset, ...
- `metrics["controller"]["mrac_mode"]`: None|"off"|"shadow"|"active".

## Rules (ids, conditions, outputs). T = cfg["thresholds"][<RULE>]. Every module docstring says HEURISTIC.
Function names must be unique across the three modules (RULES is keyed by `fn.__name__`); use snake_case of the id
(`dq_drop`, `dq_missing_loops`, `dq_missing_axes`, `dq_stuck`, `log_gains`, `mot_clamp`, `mot_yawpair`, `bat_low`,
`bat_sag`, `alt_sag`, `pid_osc`, `pid_bias`, `pid_iwindup`, `pid_sat`, `pid_lag`, `mrac_ready`, `mrac_auth`,
`mrac_drift`, `mrac_chatter`, `mrac_worse`). Ids: `<RULE>-<loop|axis|slotN>`; bare `<RULE>` when single-instance.
Evidence: {dotted metrics path (list indices as segments, e.g. `data_quality.slots.0.drop_pct`): value}.
Rationale: one fixed f-string template per rule (no free text). Confidence: low unless stated.

health.py: exactly as notes 2.4, plus:
- ALT-SAG: requires ["loops.alt_pos.steady.e_mean"]; fires when e_mean > T["alt_sag_m"] (loops e = Des - FB, so
  e > 0 means altitude below setpoint; do NOT use position.alt_e, it is FB - Des). warn, category pid, action
  increase, target = `<loops.alt_rate.prefix>.Ki` (action investigate, target None when that prefix is None),
  factor None, confidence low. Rationale must name hover-thrust feedforward as the alternative cause.

pid.py: exactly as notes 2.5 and spec 7 (PID-OSC only for levels rate/attitude; PID-BIAS uses T["bias_abs"][level],
skip a loop whose level is not a key; PID-OSC/PID-BIAS read `steady`, PID-IWINDUP/PID-SAT/PID-LAG read `airborne`).
Targets `<prefix>.Kd|Kp|Ki|SumEMax`; factor from T where the spec gives one (PID-OSC T["factor"], PID-LAG
T["factor"]), else None. PID-SAT action investigate, target None.

mrac.py (all read `mrac.<axis>.steady.*` except weights; `mode` = controller.mrac_mode; W = T of MRAC-DRIFT;
cw = cfg["params"]["mrac"]["conv_window_s"]):
- Weight drift test (one private helper, used by DRIFT and READY): a weight DRIFTS iff converged is False and
  slope_last30 is not None and abs(slope_last30) * cw >= W["min_change"]. A weight is EFFECTIVELY CONVERGED iff
  converged is True, or (converged is False and slope_last30 is not None and abs(slope_last30) * cw <
  W["min_change"]). converged None -> neither (unknown).
- MRAC-DRIFT-<axis>: >= 1 drifting weight. warn, mrac, investigate, target None; evidence = for each drifting weight
  `mrac.<axis>.weights.<w>.slope_last30` and `.final`. Rationale: reduce gamma or enable projection before active.
- MRAC-READY-<axis>: mode == "shadow", steady authority_ratio within [ready_lo, ready_hi], weights non-empty and ALL
  effectively converged (any unknown -> not asserted), steady u_ad_hf_frac < T["hf_max"]. info, mrac, enable,
  target "mrac_flags.output_injection_on".
- MRAC-AUTH-<axis>: mode in ("shadow", "active") and steady authority_ratio > T["auth_max"]. warn, mrac,
  investigate, target None, confidence medium. Rationale: adaptive term would dominate the nominal PID; check
  gamma / regressor scaling before enabling injection.
- MRAC-CHATTER-<axis>: steady u_ad_hf_frac > T["hf_max"]. warn, mrac, investigate, target None. Rationale: low-pass
  u_ad or reduce gamma.
- MRAC-WORSE-<axis>: mode == "active". loop = cfg["mrac"]["axes"][axis]["loop"]; current =
  loops.<loop>.steady.e_rms. Baseline row = among ctx["ledger_rows"] with row["preset"] == flight.preset,
  row["mrac_mode"] != "active", row["flight"] != flight.name, and row["started_at"] < flight.started_at (both
  non-empty strings), the one with the greatest started_at. base = float(row["e_rms_steady_<loop>"]); skip the axis
  when missing, unparseable, non-finite or <= 0. Fires when current > base * (1 + T["worse_pct"] / 100): warn, mrac,
  disable, target "mrac_flags.output_injection_on" (global flag; say so in the rationale). Evidence:
  `loops.<loop>.steady.e_rms`, `ledger.<baseline flight>.e_rms_steady_<loop>`.

## Tests (pytest, no files under logs/, no network)
Per rule: one positive and one negative case with hand-built minimal metrics dicts + cfg from
`pipeline.load_config()` (or conftest fixtures if they fit). Also: a run_rules test where a required path is None
-> rule listed in skipped; DQ-STUCK does not flag vars matching expected_const (e.g. "Ctrler.gyroxPID.Kp",
"mrac_flags.adaptation_on") but flags "ano_of.of_quality"; DRIFT boundary (change just below / above min_change);
READY not asserted when any weight converged is None; WORSE picks the latest earlier non-active same-preset row and
ignores later rows, other presets, active rows and unparseable values; every returned object passes
Recommendation validation (it is a Recommendation); after `pipeline.discover("rules")` all 20 names above are keys
of RULES (subset check: other tests may register extra rules) and no name is defined in two modules. Run the full tree: `python -m pytest ground_station/analysis/flightlab/tests -q` must pass.

## Integration check (report it, do not commit outputs)
render_md does not exist in your worktree, so `analyze()` cannot run. Use the manual pipeline in notes section 4
with `pl.load(r"C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\logs\vofa\flight16.meta.json")`,
`OUT` = a temp dir, then `pl.discover("rules")` and
`recs, skipped, failed = pl.run_rules(m, cfg, {"ledger_rows": [], "log": log})`. failed must be empty.

## Report `.agent-ops/out/wp4a.md` (commit it)
Branch, commit hash, changed files; verbatim tail of the pytest run; the flight16 integration output as a table
(id, severity, target, action, key evidence) plus skipped/failed lists; any spec ambiguity you resolved and how.
SUBSTITUTIONS: none (or name them). NOT RUN: <steps> if any.
