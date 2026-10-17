Yes, finalize and open the PR now against `overnight-2026-10-08`.

Before you open it:
1. Run `python -m pytest ground_station/analysis/tests/test_hover_fit.py ground_station/analysis/tests/test_att_fit.py`
   and `ruff check` on the new files; both must be green.
2. Confirm the new .py files use CRLF line endings like the existing ones.
3. No scratch files, no notebooks, no edits to `att_ctrl.py` or `frf.py`.

In the PR description: list each test and its result, every named threshold constant with its PROPOSED value,
and any point where you followed the code instead of the brief (and why).
