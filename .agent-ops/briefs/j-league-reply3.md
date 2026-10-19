Review of PR #12 (head 7a97dc51): request changes. Ruff is clean on the 4 files, but the feature is not wired in and
`flight_report.py` was damaged. Fix in the SAME PR branch (new commits, no second PR).

## Blocking

A. `flight_report.py` has no `--league` flag (`grep -n league` finds nothing), although the PR body says it was added.
   Add `python -m ground_station.analysis.flight_report --league DIR [DIR ...] [--out OUT]`, which calls
   `compare.league(dirs, out_dir)`. Default OUT the same way `--compare` picks its default.
B. Every other change in `flight_report.py` is an automatic `ruff --fix --unsafe-fixes` edit that breaks code meaning:
   bare statements whose result is thrown away (`len(mrac_active_indices) > 0`, `sum(dts)`, `any(...)`,
   `generate_report(...)`, `read_manifest(sd_path)`), `pass` put in place of code, and removed variables. Restore the
   file from the base (`git checkout overnight-2026-10-08 -- ground_station/analysis/flight_report.py`), then add ONLY
   the `--league` lines. Old lint warnings in that file are out of scope: it must not have MORE ruff errors than the
   base file has. Never run `ruff --fix --unsafe-fixes` on files you did not write.

## Code quality

1. `compare.league`: move `json`, `numpy`, `Path` and the matplotlib try/except to module level, the way the module
   already does its optional imports. No local imports.
2. `compare.league` defines its own `PROPOSED_GAP_THRES = 0.05`. Import it from `plugins/league.py` (one source).
3. `except Exception as e: print("Skipping ...")`: catch only `(OSError, json.JSONDecodeError, KeyError)`, and list each
   skipped flight with its reason in the league output file, not only on stdout.
4. `league.py:59` `rise_time = 0.0` when the threshold is never reached: no placeholder numbers. Return None, and print
   "n/a (reason)" in the table. Do the same for any other metric that cannot be computed.
5. `league.py:223` `> 0.1` step-detection threshold: a named module constant marked PROPOSED, with its unit.

## Tests

6. An end-to-end test that runs `flight_report.main(["--league", d1, d2, "--out", str(tmp_path)])` on 2 synthetic
   flight dirs and checks the league files exist and hold one row per flight per axis.
7. A test for item 4: a step that never reaches 90% gives "n/a" for rise time, not 0.

Then run `python -m pytest ground_station/analysis/flightlab/tests/test_league.py`, `ruff check` on league.py,
compare.py and test_league.py (clean), and `python tools/host_tests.py` (26/26). CRLF. Do not ask again: push the
commits to the PR branch. In the PR body list A, B and 1-7 as done or not done.
