# Fix 10-19 labday P10 bad_end on aborted flights and scorecard carried-flight IDs / preset TRAJ

Read AGENTS.md first. Touch ONLY `ground_station/lab/labday.py`, `ground_station/analysis/scorecard.py`, `ground_station/lab/tests/test_labday.py`, and `ground_station/analysis/tests/test_scorecard.py`. Do not touch `USER/`, `OBJ/`, or `logs/`.

## Context & Root Causes (2026-10-19 Lab)
1. **`labday.py` (`bad_end`, P10) marked aborted and 0-airtime flights as `OK` (`"ok": true`):**
   - On 2026-10-19, `mem1..mem6`, `z1`, `z2` (all `ABORTED L1`) and `1017-E1..E4` (`air 0.0 s`, `ABORTED L1 ... takeoff refused at set exp_id`) all returned `CampaignReport.status == "complete"`.
   - Because `bad_end(rc, ev)` (`ground_station/lab/labday.py:L230-L238`) only checked `ev.get("status") != NORMAL_END`, `labday` printed `OK`, saved `"ok": true` in `labday_state.json`, skipped the `AUTO STOP` prompt, and treated `1017-E1..E4` as completed so they would not carry over!
2. **`scorecard.py` (`day_cards`) missed carried-over flights (`1017-E1..E4`) and preset figure-8 `traj` samples:**
   - `day_cards(day)` (`ground_station/analysis/scorecard.py:L543-L562`) read `docs/lab/days/<day>.yaml` with raw `yaml.safe_load` instead of `labday.load_day(plan_path)`, so carried-over flights (`1017-E1..E4`) were missing from `plan_flights` and appeared as `09_lab-e-exp-matrix` with `fly: -` and no pass criteria.
   - In `flight_card` (`ground_station/analysis/scorecard.py:L357`), `traj = [s for s in air if s["prim"] == 3 or s["traj"] == 3]` only matches uploaded polynomial trajectories (`prim == 3`), NOT Keil trajectory presets (`preset figure8`, which run in `TASK/AutoflyTask.c` while `prim == 2` and move `x_des` / `y_des`). Thus `mem2..mem6` reported `traj_n = 0` even though ~10 s of figure-8 ran before the abort.

## Required Changes
1. **`ground_station/lab/labday.py` (`bad_end` & `debrief_event`):**
   - In `bad_end(rc, ev)` (`L230-L238`):
     - If `ev.get("aborted")` is truthy or `ev.get("abort_reason")`: return `f"aborted: {ev.get('abort_reason') or 'safety monitor'}"`.
     - If `ev.get("folder")`: check `<ROOT>/<folder>/debrief.txt` (when that file exists):
       - If any line starts with `"ABORTED L"`: return that line (or its short prefix up to `" (the safety"`).
       - If any line starts with `"landing offset: no landing (never airborne)"`: return `"never airborne (0 s air)"`.
     - If `ev.get("air_s") is not None and float(ev.get("air_s")) <= 0`: return `"never airborne (0 s air)"`.
2. **`ground_station/analysis/scorecard.py` (`day_cards` & `flight_card`):**
   - In `day_cards(day)` (`L543-L562`): when `plan_path.is_file()`, load the plan via `from ground_station.lab.labday import load_day; plan = load_day(plan_path)` (falling back to `yaml.safe_load` only if `load_day` raises `Exception`), so carried-over flights (`1017-E1..E4`, etc.) are included in `plan_flights` in exact plan order with their `id`, `fly`, and `pass` fields.
   - In `flight_card` (`L356-L365`): when `traj` (`s["prim"] == 3 or s["traj"] == 3`) is empty, detect Keil preset trajectory samples inside `air` where `s["prim"] == 2` and the xy reference speed `hypot(dx_des, dy_des) / dt > 0.02` m/s (`2.0` cm/s), and separate those samples into `traj` (removing them from `hold`) so `traj_n`, `traj_ekf_rms_cm`, and `traj_cam_rms_cm` are computed for `preset figure8` flights.
3. **Tests (`ground_station/lab/tests/test_labday.py` & `ground_station/analysis/tests/test_scorecard.py`):**
   - In `test_labday.py`: test `bad_end` when `ev` has `status="complete"` but `debrief.txt` has `ABORTED L1 ...` or `landing offset: no landing (never airborne)`, and when `ev` has `aborted=True` / `abort_reason="position_error"`.
   - In `test_scorecard.py`: test that `day_cards` resolves carried-over flights via `load_day` and that `flight_card` populates `traj_n > 0` when `prim == 2` has a moving xy setpoint (`> 0.02 m/s`).

## Verification
Run and report in the PR:
- `python -m pytest ground_station/lab/tests/test_labday.py ground_station/analysis/tests/test_scorecard.py -q`
- `bash tools/check.sh doc-paths`
