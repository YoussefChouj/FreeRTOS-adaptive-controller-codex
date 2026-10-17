1. **Explore & Analyze Module 1**: `ground_station/analysis/hover_fit.py`
   - Needs to extract hover segments across flights.
   - Detect steady holds: `|vz|` and `|vxy|` small, height near target, no SysID active (these will be thresholds named with `PROPOSED`).
   - Ground effect check for height < `0.40` m (`PROPOSED`).
   - Find hover throttle input: `Z_ratePID.U + HOVER_THR_FREE`. `HOVER_THR_FREE` is typically ~3050 based on code (or `bench_mode_active` check).
   - Needs tilt correction: `cosR*cosP` (roll/pitch).
   - Fit `u_hov*cosR*cosP = c0 + c1*(V0 - V)`. Note V0 is full charge voltage (can be gotten from battery model? the prompt says "e.g. the pack's full-charge voltage from battery_model", maybe a stated constant is enough).
   - Report statistics (RMS, leave-one-out cross-validation).
   - Add quadratic term if leave-one-out error drops by >20%.

2. **Explore & Analyze Module 2**: `ground_station/analysis/att_fit.py`
   - Estimate design plant models: `y' = w, w' = -a*w + b*m, m' = (u_d - m)/tau, u_d(t) = u(t - D)` per axis.
   - Map from `u` (PID output `+ mrac_state.axis.u_ad` when MRAC active).
   - To `w` (gyro rate, or climb rate for `z`).
   - Handle gaps in segments properly, don't integrate across them (check for `SEQ` dropping? The prompt says "stream drops are counted by SEQ: split there".)
   - Run simulation of the model with ZOH at log rate from logged `u`.
   - Grid search over `D` (0-12 ticks at 200Hz) and optimize `b, a, tau`.
   - Save to `att_model_<date>.json`.

3. **Tests**: Create tests in `ground_station/analysis/tests/test_hover_fit.py` and `test_att_fit.py`.

4. **Verify & Pre-Commit**: Verify everything runs well and tests pass. Call `pre_commit_instructions` before submitting.

5. **Submit**: Create PR.
