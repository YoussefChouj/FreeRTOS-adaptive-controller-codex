# Task sim-credible: fix modelling defects so multi-axis + coupled results are physically credible

## Goal
Every per-axis and coupled scenario in `sim/adaptive_compare/` produces finite, physically
plausible results with no modelling artefacts, the figures and JSON are regenerated, and a
digest explains each defect found and how it was fixed. Nominal PID must reproduce itself (0).

This is for an interview presentation. Fix MODEL BUGS; never tune scenarios or gains so that
the adaptive layers "win". If after the fixes an adaptive layer is worse, report it honestly.

## Known defects (verify each root cause before fixing)
1. **z axis** (`sim_axes.py`, `run_axes.py`): all S1–S5 scenario runs are flagged diverged (inf)
   for every controller, because the thrust-loss factor 0.85 is applied from t=0 and the
   altitude sinks past the 10 m divergence check. S1 must be nominal (no fault). Faults should
   start at a fault time (as roll does with `t_f`), and the hover loop must have trim/headroom
   so a 15% thrust loss is recoverable by the firmware PID. Check the z plant units and signs.
2. **yaw axis**: the Flight 8 yaw bias (`bias0 = yaw_bias`) is injected into EVERY yaw scenario
   including S1 and the MC, but the reference run has no bias, so nominal PID shows rms_ref 37 deg.
   S1 must be bias-free (rms_ref ~0 for PID); put the Flight 8 bias only in a clearly named
   scenario. Also S2/S3/S5 give ~104/85/115 deg for ALL controllers alike -> find out why
   (wrap artefact? authority clip? divergence?) and fix the cause.
3. **coupled** (`sim_coupled.py`, `run_coupled.py`): C2 (Flight 8 yaw imbalance) gives the same
   ~24 deg yaw drift for every controller although each adaptive layer has a constant feature that
   should cancel a constant bias. Find why the yaw adaptive term does not act (clip? U_tot_y ±650
   budget? mixer clamp? features/normalisation?). C5 saturates motors ~55% of the time and C4
   raises saturation for adaptive layers: check the scenario magnitudes against realistic values
   (gust torque, CG offset, motor loss) and the mixer headroom (motors 2000–4000, hover ~2950).
4. Any other artefact you find in the new code (compare against `sim_core.py`, the validated
   roll model, which is the reference implementation style).

## Context pointers
- `sim/adaptive_compare/sim_core.py` (roll, validated), `sim_axes.py`, `run_axes.py`,
  `sim_coupled.py`, `run_coupled.py`, `figures_axes/results_axes.json`, `results_coupled.json`
- Previous digest: `.agent-ops/out/sim-fix.md`

## Constraints
- Do not touch firmware (`API/ TASK/ BSP/ USER/ Global_file/`), `OBJ/`, or `sim_core.py` roll results.
- No hardware, no port 8081. Foreground commands only; do not commit.

## Deliverables
1. Code fixes in the sim files above; regenerated `figures_axes/*` and coupled figures + JSONs.
2. `.agent-ops/out/sim-credible.md` (<= 40 lines): per defect root cause + fix; then final tables:
   per axis rms_ref S1–S5 + MC median for all 6 controllers; coupled rms roll/pitch/yaw/z + sat%
   for C1–C5 and all 6 controllers; state any remaining weakness honestly.

## Verification (paste verbatim output)
- `python run_axes.py` and `python run_coupled.py` complete with no inf/diverged entries in any
  scenario that is not intentionally extreme; PID S1/C1 rms_ref == 0 on every axis.
- `python -c "import json;d=json.load(open('figures_axes/results_axes.json'));print({a:d[a]['scenarios']['PID']['rms_ref'] for a in d})"`
