Review of PR #10 (head bb14376b): request changes. Better: ruff is clean, `sim_zoh`/`fit_segment` and the musing
comments are gone, and `direct_iv` is now called. Still open, checked at bb14376b. Fix in the SAME PR branch.

## Blocking

1. Tests (reply2 items 18-21): there are still only 5 tests, and neither test file changed in this round. Add each one:
   a. `extract_hover_points` on a synthetic log with aliased column names, hold segments, a ground-effect stretch
      (below `PROPOSED_GE_HEIGHT_M`) and a tilted stretch: only the valid hover points come back.
   b. A 200 Hz closed-loop log sampled at 100 Hz with SEQ gaps, including a 255 -> 0 wrap that is NOT a gap.
   c. No integration across a SEQ gap (a fitted segment never spans one).
   d. A segment shorter than `PROPOSED_MIN_SEGMENT_S` is skipped and its reason appears in the output.
   e. `test_json_schema` runs the real `main([...])` on a `tmp_path` log and reads the JSON file it writes.
2. PR body: it still says `PROPOSED_HOVER_THR_FREE = 2253.0` and "4 passed". Rewrite it: 3050 is the value
   (`TASK/StabilizerTask.c:73`), give the real test count, and list each reply2 item number as done or not done.

## Code quality

3. `direct_iv`: `ground_station/autotune/frf.py` on this base has no `direct_iv` (it comes from another open PR). Import
   it once at module level, `try: from ground_station.autotune.frf import direct_iv` / `except ImportError: direct_iv =
   None`. When it is None, write `"direct_iv": null` and `"direct_iv_skip": "<reason>"` in the JSON. Add a test that
   monkeypatches a fake `direct_iv` and checks that both parameter sets are in the JSON.
4. No `except Exception` with `# noqa: BLE001` (att_fit.py:225, 337, 429). Catch the errors those calls really raise
   (e.g. `ValueError`, `np.linalg.LinAlgError`, `RuntimeError` from `optimize`), and keep the reason text.
5. `hover_fit.py:88` says `# assume m, convert to cm`. Look up the unit of `Ctrler.Z_posPID.FB` in the firmware and write
   it in the comment with the file:line you read it from, the same way as L86.

Then run `python -m pytest ground_station/analysis/tests/test_att_fit.py ground_station/analysis/tests/test_hover_fit.py`,
`ruff check` on the 4 files (clean) and `python tools/host_tests.py` (26/26). CRLF. Do not ask again: push the commits.
Do not reformat or `--fix` files you did not write.
