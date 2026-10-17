Yes, go ahead, with one change of shape:

1. Keep the logic in `ground_station/analysis/flightlab/`: the aggregation over flights, the bootstrap CIs, the ranking
   with ties, the gap-fraction gate and the league.md / league.json / plot writers live in `plugins/league.py` (or a
   sibling module there), with a `compare.py` entry. `flight_report.py` gets only a thin `--league dirA dirB ... --out D`
   flag that calls it. No metrics, CI or table code in `flight_report.py` (it is already very large).
2. `pyelftools` in your environment is fine, but do not add it (or anything else) as a dependency and do not import it in
   the new code.
3. Everything else as in the brief: the 6 tests, scoped pytest, `ruff check` on changed files, `python tools/host_tests.py`
   26/26, CRLF, the do-not-edit list, and the PR body items (each test with its result, the per-axis kind source, every
   PROPOSED constant).

Do not ask again: open ONE PR against `overnight-2026-10-08` when done. If a test fails that you cannot fix in one try,
list it in the PR body with its error line and open the PR anyway.
