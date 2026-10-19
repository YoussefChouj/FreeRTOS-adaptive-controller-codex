Good catch on failure 1. About the torn-row test: on the untouched branch head it PASSES here
(`python -m pytest ground_station/service/tests/test_campaign_outputs.py -k merges_slots` = 3 passed). So the failure
most likely comes from your failure-1 change or from your environment. Please:
1. Check it on the untouched head in your workspace (a clean checkout of the branch, without your edits). If it passes
   there, your change broke it: fix your change so both tests pass, do not edit the test.
2. If it also fails on the untouched head, fix the root cause in the reader: the row-by-row fallback must skip a torn
   or non-numeric row (like `1400000000,0,sl`) exactly as the test name says, never turn it into a key with an empty
   value. Name the commit that introduced it in the PR body.
3. Keep to the do-not-edit list. If the root cause is in a listed file, do not patch it: put the minimal diff block
   and the cause in the PR body.
4. Then do failures 2 and 3 as in the brief and open ONE PR against `overnight-2026-10-08`, with each scoped test
   and its result, `ruff check` on changed files, and `python tools/host_tests.py` 26/26.
Do not ask again before the PR. If a test fails that you cannot fix in one try, list it in the PR body with the error
line and open the PR anyway.
