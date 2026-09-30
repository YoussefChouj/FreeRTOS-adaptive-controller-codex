# G7 inline rewrite plan (supervisor, 2026-10-01)

Worker vps/wfb-g7 (14c2cbc) REJECTED; write fresh in `.worktrees/wfb` (branch workflow-b). Brief = allow-list,
names and tests in `$S/g7-body.md` (6 files: analysis/controller_descriptor.py, analysis/tuner.py,
analysis/controllers/{pid,mrac}.yaml, analysis/tests/test_{controller_descriptor,tuner}.py). Style reference:
`ground_station/analysis/battery_model.py` (module docstring, `from __future__ import annotations`, ValueError
subclass, collect-all-problems loader). Acceptance:
`python -m pytest ground_station/analysis/tests/test_controller_descriptor.py ground_station/analysis/tests/test_tuner.py -q -p no:cacheprovider`

## Facts gathered (all verified in the wfb tree)
- Wire decode, TASK/send_data.c:1474-1519.
  - 0x01 PID_GAIN: axis, gain = divmod(idx, 3); axes 0..6 = pitchPID, rollPID, yawPID, gyroxPID, gyroyPID,
    gyrozPID, Z_ratePID (Ctrler.*); gain 0/1/2 = Kp/Ki/Kd; accepted only when 0 <= val <= 200.
  - 0x02 gamma, 0x05 What_limit, 0x08 What_tol: axis = idx >> 4, elem = idx & 0x0F; axes 0..3 =
    mrac_config_pitch/roll/yaw/z.
    - 0x02 accepted only when val > 0.
    - 0x05 accepted only when val >= What_tol[elem] (elem 0 also sets What_lower_limit[0] = -val).
    - 0x08 accepted only when 0 <= val <= What_limit[elem].
- firmware_contract.py lists the FIELDS, not the packed idx. params = (axis, gain_type|elem, value):
  - 0x01 at :175-184: axis 0..6, gain_type 0..2, value 0..200 (:181).
  - 0x02 at :185-194: axis 0..3, elem 0..5, gamma 0..None (:191).
  - 0x05 at :218-227: What_limit 0..None (:224).
  - 0x08 at :252-261: What_tol 0..None (:258).
  - CommandParam has index/name/unit/min_val/max_val (None = unbounded).
- Contract fields vs. firmware names: the contract's axis/elem ranges stay the authority. The name tuples must have
  length (axis max + 1); a test asserts this.
- `Z_ratePID.Kp` default 400 > the 0x01 cap of 200, so it is unreachable and left out. `locx/locy/locxs/locys` have
  no 0x01 axis, so they are left out. Record both in the digest/plan.
- PID defaults, API/pid.c rows (Kp, Ki, Kd):
  - :15 pitchPID 3.0 / 0.1 / 8
  - :16 rollPID 3.0 / 0.1 / 8
  - :17 yawPID 6.0 / 0.04 / 0
  - :19 gyroxPID 5 / 0.01 / 10
  - :20 gyroyPID 5 / 0.01 / 10
  - :21 gyrozPID 8.0 / 0.001 / 0.02
  - :24 Z_ratePID 400 / 0.435 / 0
- MRAC defaults, API/mrac.c MRAC_BASIS rows (gamma, limit, tol):
  - :571 pitch0 1.50 / 0.15 / 0.03
  - :572 pitch1 0.20 / 0.05 / 0.01
  - :577 roll0 1.50 / 0.15 / 0.03
  - :578 roll1 0.20 / 0.05 / 0.01
  - :583 yaw0 1.00 / 0.09 / 0.018
  - :589 z0 2.00 / 1.00 / 0.20
  - elems: 0 bias, 1 rate, 2 rate_tanh, 3 cross, 4 u_nom, 5 xm.
  - For What_limit, lo = 0.5 x default >= tol = 0.2 x default, so the firmware always accepts the value.
- Shadow symbols: `mrac_state.{pitch,roll,yaw,z_rate}.u_ad` (API/mrac.h:260-264; facts-gs.md:42-45). PID has no
  shadow output (`shadow_outputs: []`).
- bench: `to_x` :82, `from_x` :90, SIGMA0 = 0.2 :20; <= 14 knobs (facts-gs.md:111).

## Design
- controller_descriptor.py:
  - Knob, Descriptor (frozen); DescriptorError(ValueError) with .problems; load(path).
  - `wire_target(cmd_id, idx) -> str | None` returns the firmware variable written, e.g. "gyroxPID.Kd" or
    "mrac_config_roll.gamma[0]", or None.
  - Check: symbol == wire_target(cmd_id, idx). This catches a wrong idx, the worker's bug class.
  - Every field is checked independently: a missing or wrong-typed key never skips the other checks. Types:
    `type(v) is int` for ints (bool rejected); numbers are int/float, not bool, finite.
  - Value range comes from the contract value param (max None = unbounded).
  - Only 0x01/0x02/0x05/0x08 are tuning commands; any other cmd_id is a problem.
- pid.yaml, 8 knobs (x0.5..x2, log):
  - pitchPID.Kp idx0, rollPID.Kp idx3, pitchPID.Kd idx2, rollPID.Kd idx5
  - gyroxPID.Kp idx9, gyroyPID.Kp idx12, gyroxPID.Kd idx11, gyroyPID.Kd idx14
  - gyroxPID.Kd hi = 20 <= 200 OK.
- mrac.yaml, 8 knobs:
  - gamma: pitch0 idx0, roll0 idx16, pitch1 idx1, roll1 idx17, yaw0 idx32, z0 idx48
  - What_limit (0x05): pitch0 idx0, roll0 idx16
  - shadow_outputs: 4 x u_ad.
- tuner.py, (1+1)-ES:
  - `_to_unit`/`_from_unit` cite bench.py:82/:90. Clamp the final value to [lo, hi] (a log from_x(1) can land
    1 ulp over hi).
  - No results: exact defaults.
  - Parent = the best valid, finite-J result over history + own records.
  - No valid result: centre on the defaults with sigma = min(sigma, SIGMA0 / 2).
  - 1/5 rule in record(): success (valid, finite, J < best before) -> sigma *= exp(1/3); else *= exp(-1/12).
    Clamp sigma to [0.01, 0.5].
  - History params missing a knob take its default; extra keys are ignored.
  - rng = default_rng(seed), drawn only in propose.

## DONE 0f2732d (pushed origin/workflow-b, 2026-10-01, supervisor inline)
Acceptance: 61 passed (`-p no:cacheprovider`). Mutants killed: symbol==wire_target check removed; MRAC decode
swapped for divmod(idx,16); finite-J guard dropped from Result.usable; restart sigma cap removed.

| symbol | cmd | idx | default | lo | hi | default cite |
|---|---|---|---|---|---|---|
| pitchPID.Kp | 0x01 | 0 | 3.0 | 1.5 | 6.0 | API/pid.c:15 |
| rollPID.Kp | 0x01 | 3 | 3.0 | 1.5 | 6.0 | API/pid.c:16 |
| pitchPID.Kd | 0x01 | 2 | 8 | 4 | 16 | API/pid.c:15 |
| rollPID.Kd | 0x01 | 5 | 8 | 4 | 16 | API/pid.c:16 |
| gyroxPID.Kp | 0x01 | 9 | 5 | 2.5 | 10 | API/pid.c:19 |
| gyroyPID.Kp | 0x01 | 12 | 5 | 2.5 | 10 | API/pid.c:20 |
| gyroxPID.Kd | 0x01 | 11 | 10 | 5 | 20 | API/pid.c:19 |
| gyroyPID.Kd | 0x01 | 14 | 10 | 5 | 20 | API/pid.c:20 |
| mrac_config_pitch.gamma[0] | 0x02 | 0 | 1.5 | 0.75 | 3.0 | API/mrac.c:571 |
| mrac_config_roll.gamma[0] | 0x02 | 16 | 1.5 | 0.75 | 3.0 | API/mrac.c:577 |
| mrac_config_pitch.gamma[1] | 0x02 | 1 | 0.2 | 0.1 | 0.4 | API/mrac.c:572 |
| mrac_config_roll.gamma[1] | 0x02 | 17 | 0.2 | 0.1 | 0.4 | API/mrac.c:578 |
| mrac_config_yaw.gamma[0] | 0x02 | 32 | 1.0 | 0.5 | 2.0 | API/mrac.c:583 |
| mrac_config_z.gamma[0] | 0x02 | 48 | 2.0 | 1.0 | 4.0 | API/mrac.c:589 |
| mrac_config_pitch.What_limit[0] | 0x05 | 0 | 0.15 | 0.075 | 0.3 | API/mrac.c:571 |
| mrac_config_roll.What_limit[0] | 0x05 | 16 | 0.15 | 0.075 | 0.3 | API/mrac.c:577 |

Contract value params: 0x01 firmware_contract.py:181 (0..200), 0x02 :191, 0x05 :224 (0..None). Cited once per
command in each YAML header rather than on every knob line (same citation for every knob of a command).

Deviations from the brief (all deliberate):
- Left out locx/locy/locxs/locys (no 0x01 axis) and Z_ratePID.Kp (default 400 > the 0x01 cap of 200).
- Duplicate check: a checked knob's symbol is fixed by its (cmd_id, idx), so "unique symbols" and "unique
  (cmd_id, idx)" are one check, reported once ("knobs #i and #j both write X").
- `shadow_outputs` is a required key ([] for PID) so every descriptor says so explicitly.
- Added public helpers: `wire_target`, `value_param`, `CONTROLLERS_DIR`; tuner exposes read-only `best`
  (Result | None) and `sigma` for the campaign runner and the tests; `Result` dataclass is public.
- 1/5 rule: success = usable and J < incumbent J (the incumbent covers the last propose() history + own records);
  the first usable result counts as a success.
- History values outside [lo, hi] (e.g. an older campaign's range) clamp to the nearer edge in `_to_unit`.
