Thanks. Review result: request changes. Please fix all of the points below in the SAME PR branch (push new commits,
do not open a second PR), then reply with each test and its result. Line numbers are from head ece6d06e.

## Crashes and wrong results on real logs (fix first)

1. `hover_fit.py:82-84`: `lc.spans` returns (t_start, t_end) in SECONDS (`log_corpus.py:202-208`), not indices.
   `t[b]` / `t[a]` with float indices crashes. Use the times directly.
2. `att_fit.py:126-149`: SEQ is interpolated by `lc.grid` before `np.diff(seq) > 1`, which hides every gap, and the
   uint8 wrap 255 -> 0 is not handled. Split on the RAW logged seq (and on raw dt dropouts) BEFORE gridding, with
   wrap-aware differences. Every skipped segment (too short, gap) is logged with its reason and returned in the
   result, never dropped silently (`att_fit.py:145`).
3. `att_fit.py:315-316`: the one-step and the 0.5 s-ahead errors are hardcoded 0.0. Compute them: one-step = the
   model propagated one tick from the measured state; 0.5 s-ahead = simulate 0.5 s from the measured state at each
   start point (stride is fine) and report the RMS. No placeholder numbers anywhere in the output.
4. Delay grid at 100 Hz logs (`att_fit.py:366`, `range(7)`) cannot represent odd 200 Hz ticks. Keep the spec grid
   0-12 ticks at 200 Hz for both log rates (for 100 Hz logs, simulate at 200 Hz with the input held, then sample at
   100 Hz; or an equivalent exact method). Export `D_ticks_200hz` and `D_s`, never log-rate ticks (`att_fit.py:322`).
5. Validation bias `u0 = w_bar/(b/a) - u_bar` (`att_fit.py:288-294`) blows up as a -> 0. Estimate the per-segment
   input bias as a fitted parameter (linear in the output-error problem), per segment as the spec says.
6. The model must include `y' = w` (the attitude/height state) as in the spec; the JSON carries the full model.

## Spec items missing or partial

7. LOFO: true leave-one-flight-out in both modules (`att_fit.py:339-344` is only last-session hold-out; hover_fit
   uses aggregate LOOCV). Report per held-out flight and the summary.
8. hover_fit bounds (`hover_fit.py:130-131`): weighted residuals in the covariance and Student-t (n-p) instead of
   1.96. Always report the linear AND the quadratic fit with both LOFO errors, and which one passes the 20% rule
   (`hover_fit.py:213` reports only one).
9. Hover-to-empty flight (`campaigns/lab_i_hover_empty.fly`): it is flown as 5-8 chained `hold 85` sub-cap runs on
   one pack. Treat the runs of one pack as one sag record (group by pack/session, ordered by time), and the hold
   segments of every other flight as extra points. State every detection threshold as a named PROPOSED constant
   (`hover_fit.py:36`, L65/67, L99, L194, L213 are unnamed now).
10. `PROPOSED_HOVER_THR_FREE = 3050.0` (`hover_fit.py:17`) is the right value: cite its source in a comment,
    `TASK/StabilizerTask.c:73` (`#define HOVER_THR_FREE 3050`). Do NOT change it to 2253 (that is a line number).
    V0: take it from `battery_model.py` instead of the hardcoded 16.7 (`hover_fit.py:16`), or say in the PR why not.
11. Initial guess (`att_fit.py:221`, `[10, 5, 0.03]`) and bounds (`att_fit.py:25`, L324 static strings): seed from
    `research/sim/cascade.py` `fit_rate_plant` (L432) or the autotune defaults; reuse its delay/tau logic instead of
    duplicating it where that fits. Bounds in the JSON come from the constants, not from strings.
12. Real-log column reading (`hover_fit.py:38-51`): accept the telemetry aliases the repo already uses
    (`status.vbat`, `status.roll_deg`, `status.arm`; see `thrust_replay.py:72-97`) and the case where `seq` is
    absent (then no SEQ split, say so in the result). Missing optional columns degrade, they do not empty the fit.
    In the PR body list which `campaigns/*.fly` log groups carry every column the fit needs (read them, do not edit).
13. SysID vs trajectory: report the fit and validation separately on SysID flights and on trajectory flights
    (detect SysID by `id.sysid_state`).
14. `direct_iv` (`att_fit.py:384-391` is a print stub): if `ground_station.autotune.frf.direct_iv` imports, call it
    on the SysID segments and print both parameter sets side by side; if not, a one-line note. Soft import only.
15. JSON: add the `hover` block (c0, c1, bounds, V0, LOFO, linear and quadratic) (`att_fit.py:352` is an empty
    comment), the skipped-segment list, sessions, git sha.
16. CLI: `--segments` filter (time ranges or segment kinds) and `--out` path, same style as `autotune/cli.py`.
17. Dead code: remove the discarded arrays in `sim_zoh` (`att_fit.py:47-76`) and the no-op `fit_segment` (L101-109).

## Tests

18. `test_hover_fit.py`: test `extract_hover_points` from a synthetic LOG (columns as in item 12, holds, ground
    effect, tilt), not only from pre-built HoverPoints. c0/c1 within 2%.
19. `test_att_fit.py`: closed-loop synthetic log (PID feedback, process + sensor noise), simulated at 200 Hz and
    sampled at 100 Hz, with SEQ gaps including a 255 -> 0 wrap; D exact (including an odd 200 Hz tick), the rest
    within 5%. Also the 200 Hz log case.
20. A test that the fit never integrates across a SEQ gap and that a short segment is skipped with its reason.
21. The golden JSON key test must run the real module output (not a local `mock_out` dict, `test_att_fit.py:72-88`).

Run scoped: `python -m pytest ground_station/analysis/tests/test_hover_fit.py ground_station/analysis/tests/test_att_fit.py`
and `ruff check` on the changed files. CRLF line endings. Do not edit `att_ctrl.py`, `frf.py`, `excitation.py`,
`campaign_capture.py`, `stream.py`, or anything under `API/`, `TASK/`, `USER/`, `campaigns/`. If a point conflicts
with the code, follow the code and say so. Do not ask again before pushing unless a test fails that you cannot fix.
