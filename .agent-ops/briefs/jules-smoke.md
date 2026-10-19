# Smoke test of the Jules lane (read-only, no PR)

Do not change, create or delete any file. Do not open a PR.

1. Print `git log -1 --oneline` and the current branch name.
2. Count the files matching `ground_station/**/tests/test_*.py` and list the top-level directories of the repo.
3. Run `python -m pytest ground_station/analysis/tests -q -x --co` (collect only) and report how many tests were collected, or the first error.

Reply in at most 10 lines.
