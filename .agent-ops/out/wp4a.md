STATUS: done (branch: worker/wp4a, commit: 3ccf8c0)

files changed:
  ground_station/analysis/flightlab/rules/health.py — 10 health rules (dq_drop, dq_missing_loops, dq_missing_axes, dq_stuck, log_gains, mot_clamp, mot_yawpair, bat_low, bat_sag, alt_sag)
  ground_station/analysis/flightlab/rules/pid.py — 5 pid rules (pid_osc, pid_bias, pid_iwindup, pid_sat, pid_lag)
  ground_station/analysis/flightlab/rules/mrac.py — 5 mrac rules (mrac_ready, mrac_auth, mrac_drift, mrac_chatter, mrac_worse)
  ground_station/analysis/flightlab/tests/test_rules_health.py — unit tests for all health rules and skip logic
  ground_station/analysis/flightlab/tests/test_rules_pid.py — unit tests for all pid rules and skip logic
  ground_station/analysis/flightlab/tests/test_rules_mrac.py — unit tests for all mrac rules, boundary, and discovery

verification:
  python3 -m py_compile ground_station/analysis/flightlab/rules/{health,pid,mrac}.py → exit 0
  python3 -m pytest ground_station/analysis/flightlab/tests/test_rules_*.py → 25 passed, 0 failed (exit 0)
  python3 -m pytest ground_station/analysis/flightlab/tests -q → 109 passed, 0 failed (exit 0)

open questions / risks:
  - NOT RUN: flight16 integration (log not on this host)

SUBSTITUTIONS: none

---
SUPERVISOR REVIEW (session 2a667b8b, 2026-09-30, on main; pristine worker output stays on branch vps/wp4a)

fixes applied to the worker output:
  - rules/{health,pid,mrac}.py: every `cfg.get("thresholds", {}).get(RULE, {})` and `float(x.get(key, <number>))`
    replaced by direct indexing (41 hard-coded threshold fallbacks removed). A missing key in config/rules.yaml now
    fails that rule (run_rules reports it under rules_failed) instead of running on a number nobody configured.
  - rules/mrac.py: conv_window_s read from cfg["params"]["mrac"] only (fallback number removed).
  - rules/health.py: dq_missing_loops builds the core names from cfg["pid_fields"]; dq_missing_axes dead
    `num_fields == 0` branch removed; alt_sag no longer falls back to an invented cfg path for the alt_rate prefix.
  - rules/health.py rationales: DQ-STUCK gives a count (names stay in evidence), BAT-LOW names the threshold it
    crossed, DQ-MISSING / LOG-GAINS join names instead of printing a list repr.
  - rules/pid.py: targets are built only from a real prefix; a loop without a prefix is skipped in pid_osc,
    pid_bias, pid_iwindup, pid_lag (the worker emitted action=increase with target=None).
  - tests: test_rules_health.py DQ-STUCK rationale assertion adapted; test_rules_pid.py gains
    test_pid_rules_skip_loop_without_prefix (4 cases).

verification (laptop, Windows, python -m pytest):
  ground_station/analysis/flightlab/tests/test_rules_{health,pid,mrac}.py -> 29 passed
  ground_station/analysis/flightlab/tests -q -> 117 passed in 55.72s (84 WP0-3 + 29 rules + 4 from the untracked WP4b worker tests)

flight16 integration (logs/vofa/flight16.meta.json, firmware git 5f9baaf, mrac_mode shadow; manual pipeline
load -> segment -> run_plugins -> build_metrics -> validate -> run_rules with an empty ledger): 23 recommendations.

  id | severity | target | action | factor | confidence | key evidence
  BAT-SAG | warn | - | investigate | - | low | battery.sag_v_cell = 0.4086
  MOT-YAWPAIR | warn | - | investigate | - | low | motors.steady.yaw_pair_pct = -21.03
  MRAC-AUTH-pitch | warn | - | investigate | - | medium | mrac.pitch.steady.authority_ratio = 2.185
  MRAC-AUTH-roll | warn | - | investigate | - | medium | mrac.roll.steady.authority_ratio = 1.372
  MRAC-AUTH-yaw | warn | - | investigate | - | medium | mrac.yaw.steady.authority_ratio = 1.087
  MRAC-AUTH-z_rate | warn | - | investigate | - | medium | mrac.z_rate.steady.authority_ratio = 1.293
  MRAC-DRIFT-z_rate | warn | - | investigate | - | low | mrac.z_rate.weights.Theta[0].slope_last30 = -0.003516
  PID-BIAS-pos_x | warn | Ctrler.locxPID.Ki | increase | - | low | loops.pos_x.steady.e_mean = -10.66
  PID-BIAS-vel_x | warn | Ctrler.locxsPID.Ki | increase | - | low | loops.vel_x.steady.e_mean = -6.814
  PID-IWINDUP-att_roll | warn | Ctrler.rollPID.SumEMax | increase | - | low | loops.att_roll.airborne.sume_sat_frac = 0.1417
  PID-IWINDUP-att_yaw | warn | Ctrler.yawPID.SumEMax | increase | - | low | loops.att_yaw.airborne.sume_sat_frac = 0.5979
  PID-IWINDUP-pos_x | warn | Ctrler.locxPID.SumEMax | increase | - | low | loops.pos_x.airborne.sume_sat_frac = 0.984
  PID-IWINDUP-pos_y | warn | Ctrler.locyPID.SumEMax | increase | - | low | loops.pos_y.airborne.sume_sat_frac = 0.4891
  PID-LAG-att_yaw | warn | Ctrler.yawPID.Kp | increase | 1.15 | low | loops.att_yaw.airborne.des_std = 3.964
  PID-LAG-rate_yaw | warn | Ctrler.gyrozPID.Kp | increase | 1.15 | low | loops.rate_yaw.airborne.des_std = 9.596
  PID-LAG-vel_x | warn | Ctrler.locxsPID.Kp | increase | 1.15 | low | loops.vel_x.airborne.des_std = 3.677
  PID-LAG-vel_y | warn | Ctrler.locysPID.Kp | increase | 1.15 | low | loops.vel_y.airborne.des_std = 3.185
  DQ-MISSING-alt_pos | info | - | add_to_preset | - | low | loops.alt_pos.missing (1 name)
  DQ-MISSING-alt_rate | info | - | add_to_preset | - | low | loops.alt_rate.missing (1 name)
  DQ-MISSING-vel_x | info | - | add_to_preset | - | low | loops.vel_x.missing (1 name)
  DQ-MISSING-vel_y | info | - | add_to_preset | - | low | loops.vel_y.missing (1 name)
  DQ-STUCK | info | - | investigate | - | low | data_quality.stuck_vars (22 names)
  LOG-GAINS | info | - | add_to_preset | - | low | loops.<loop>.gains = None for all 12 loops

  rules_skipped: {}
  rules_failed: {}
  Not fired, as expected for this flight: MRAC-READY (authority above ready_hi on every axis), MRAC-WORSE (mode
  is shadow), BAT-LOW (3.680 V/cell > cell_warn 3.6), ALT-SAG (alt_pos steady e_mean -0.00711), DQ-DROP (0 drops).

SUBSTITUTIONS: none (real flight16 log, real config)
NOT RUN: nothing
