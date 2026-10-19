Review of PR #11 (head a83b9a35): request changes. The 37 tests pass. Please fix the points below in the SAME PR branch
(new commits, no second PR).

## Blocking

A. `ruff check ground_station/analysis/live_tracker.py` fails with 5 errors: E731 at L1360, L1362, L1364 (lambdas
   assigned to `height_fn`) and E701 at L1391 and L1439 (`if not ok: break`, `if len(vu) < 3: return 0.0`). Use a
   small named `def` for the height function and one statement per line. `ruff check` on every changed .py must be clean.
B. Delete the three helper scripts at the repo root: `fix_all.py`, `fix_e702.py`, `fix_pip_label.py`. They are not part
   of the feature.

## Code quality

1. `import time` inside `CoralTracker.step()`: the module already imports `time` at the top (L27); remove the local
   import. Same for the local `csv`, `Path` and `cv2` imports in `ab_replay`: module level only.
2. `CoralTracker.last_ms` stores seconds. Store ms, or rename it so the name matches the unit.
3. `Preview.active_zoom` is set in `__call__` but never in `__init__`. Initialise it to 1.0 in `__init__` and drop the
   `getattr(self, "active_zoom", 1.0)` in `_draw_landing_pip`. Do the same for `ring.active_zoom` in `run_source`
   (give the tracker the attribute in its `__init__`; no `getattr` fallback).
4. Two blank lines before and after every top-level `def`/`class` (`pip_label` has 3 before, `Preview` has 1 before).
5. `ab_replay` magic numbers: the `seed=(320, 240)` fallback, the 0.15 m default height and the `0.30` in
   `z_eff_b = 2.0 if (0.0 < z_m <= 0.30) else 1.0`. Make them named module constants marked PROPOSED, and derive
   `z_eff_b` from the same `land_zoom` / `land_z_m` values that run B used, not a second hardcoded copy.
6. No placeholder numbers: `jitter()` returns 0.0 when there are fewer than 3 points, and `disag` falls back to 0.0
   with no overlap. Return None and print "n/a (reason)" in the summary. Guard `na/dur_a` and `nb/dur_b` against a zero
   duration the same way.
7. `replay_main` picks the A/B mode from the file extension (`.mkv/.avi/.mp4`). Make it explicit: a subcommand or an
   `--ab` flag, so a video name never changes the meaning of the command.
8. Validate the new CLI values the way `--zoom` is checked: `--land-zoom >= 1.0` and `--land-z-m > 0`.
9. Write `ab_summary.md` and `ab_frames.csv` with `encoding="utf-8"`.

## Tests

10. Add a test for the A/B summary on a tiny synthetic video (a few frames with a drawn marker): the summary file
    exists, the CSV row count equals the frame count, and the fewer-than-3-points case prints "n/a" instead of a number.
11. A test that `z_eff_b` in `ab_frames.csv` follows `--land-zoom` / `--land-z-m` (non-default values).

Then run `python -m pytest ground_station/analysis/tests/test_live_tracker.py`, `ruff check` on the changed files
(clean) and `python tools/host_tests.py` (26/26). CRLF line endings. Do not ask again: push the commits to the PR
branch. If a test fails that you cannot fix in one try, list it with its error line in your reply.
