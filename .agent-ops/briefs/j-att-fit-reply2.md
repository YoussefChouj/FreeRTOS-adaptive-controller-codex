Thanks for the update (head 88fb30a6). Review result: request changes. Points 1 (lc.spans seconds), 3 (one-step and
0.5 s-ahead now computed) and 8 (Student-t, weighted residuals) are done. Please fix the rest in the SAME PR branch
(new commits, no second PR). Line numbers are from 88fb30a6.

## Lint (blocking)

A. `ruff check` fails with 8 x E402: `att_fit.py:43,45,46` and `hover_fit.py:10,11,12,14,16`. Move every import to
   the top of the module. If a `sys.path` insert is truly needed, do it the way the other `ground_station/analysis`
   modules do (run as `python -m ground_station.analysis.att_fit`, no path hack).

## Still open from reply1 (please do each one, the reply1 text is the spec)

2. `att_fit.py:138-180` `prepare_segments`: still splits on the GRIDDED seq (interpolation hides gaps) and drops short
   segments silently. Split on the raw logged seq (wrap-aware uint8 diff) and raw dt dropouts BEFORE `lc.grid`; return
   a `skipped` list with start time, length and reason; record "seq absent, no SEQ split" in the result, not a print.
   Do not add `u_ad` in place on the gridded array (`u += ...`); build a new array.
4. Confirm the delay grid is 0-12 ticks at 200 Hz for 100 Hz logs too, and that the JSON exports `D_ticks_200hz` and
   `D_s` only.
5. Per-segment input bias: make it a fitted parameter in the output-error problem (not the linear per-segment bias in
   `calc_val_metrics`). Remove the unused `_A_d`, `_B_d`.
6. The model state must include `y' = w` (attitude/height); the JSON carries the full model.
7. True leave-one-flight-out in both modules. `main` (L390-395) trains on `sessions[:-1]` and validates on the last,
   so with one session train == val. Report per held-out flight plus the summary; with one flight say LOFO is not
   possible, never report train-as-val numbers. `hover_fit.loocv` (L172) must be LOFO too.
9. Hover-to-empty: group the chained `hold 85` runs of one pack as one sag record (by pack/session, ordered by time).
   Name every detection threshold as a PROPOSED constant.
10. `hover_fit.py:26`: cite `TASK/StabilizerTask.c:73` exactly; take V0 from `battery_model.py` or say why not.
11. `att_fit.py:233`: seed from `research/sim/cascade.py` `fit_rate_plant` (L432) or the autotune defaults; JSON bounds
    come from the constants.
12. Column aliases (`status.vbat`, `status.roll_deg`, `status.arm`, `thrust_replay.py:72-97`): only vbat is handled in
    `extract_hover_points` (L45). Missing optional columns degrade. List in the PR body which `campaigns/*.fly` log
    groups carry every needed column.
13. Separate fit/validation for SysID vs trajectory flights (`id.sysid_state`).
14. `direct_iv` (L431-437) is still only imported and printed: call it on the SysID segments and print both parameter
    sets side by side.
15. JSON: `hover` block (c0, c1, bounds, V0, LOFO, linear and quadratic), the skipped list, sessions, git sha.
16. CLI: `--segments` and `--out` in the style of `autotune/cli.py` (`main` L381 has only `sessions`/`--axes`);
    `hover_fit.main` (L257) needs `--out` too.
17. Remove `sim_zoh` (L56) and `fit_segment` (L129) if nothing calls them.
18. `test_hover_fit.py`: `extract_hover_points` from a synthetic LOG (aliased columns, holds, ground effect, tilt);
    c0/c1 within 2%.
19. `test_att_fit.py`: closed-loop synthetic log at 200 Hz sampled at 100 Hz with SEQ gaps incl. a 255 -> 0 wrap;
    D exact incl. an odd 200 Hz tick; the 200 Hz log case too.
20. Test that the fit never integrates across a SEQ gap and that a short segment is skipped with its reason.
21. `test_json_schema` (`test_att_fit.py:72-88`) still asserts on a local `mock_out` dict. Run the real module on a
    synthetic log and check its JSON.

## New small points

B. `hover_fit.py:77-81`: the `vxy` line is duplicated and carries "Wait, let's check units" guesses. The units are
   known: `locxsPID.FB`/`locysPID.FB` are cm/s (`TASK/StabilizerTask.c:1110-1122`, `fb_dx/fb_dy` cm/s);
   `Z_posPID.FB` is height in m and `Z_ratePID.FB` climb rate in m/s (`TASK/StabilizerTask.c:1138`). Keep one line,
   cite these, remove the guess comments.
C. `att_fit.py:362` `val_all_metrics.get("rms_05s_ahead", 0.0)` and the `else 0.0` at L336-337: no placeholder
   numbers. When there is nothing to score, write null and a reason.
D. `att_fit.py:255-257`: remove the "Actually one-step is tricky..." musing comments.

Run scoped: `python -m pytest ground_station/analysis/tests/test_hover_fit.py ground_station/analysis/tests/test_att_fit.py`
and `ruff check` on the changed files (must be clean). CRLF line endings. Same do-not-edit list as reply1. Do not ask
again before pushing; if a test fails that you cannot fix in one try, list it with its error line in your reply.
