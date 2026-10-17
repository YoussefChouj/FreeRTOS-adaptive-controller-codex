Yes, proceed now. No more questions before the PR.
1. Fix the reader's row-by-row fallback so a torn or non-numeric row (like `1400000000,0,sl`) is skipped and never
   becomes a key. Keep the test as it is. Both campaign_outputs tests must pass.
2. Then do failure 2 (mrac_phi safe=False after GUARD_SEND_HZ 100; read the rate from the constant, never hardcode
   200 or 100) and failure 3 (5x E702 in live_tracker, then a behavior-neutral repo-wide ruff pass).
3. Keep to the do-not-edit list from the brief. If a root cause is in a listed file, put a diff block in the PR body.
4. Open ONE PR against `overnight-2026-10-08` with each scoped test and its result, `ruff check` on the changed
   files, and `python tools/host_tests.py` 26/26. If a test fails that you cannot fix in one try, list it with its
   error line and open the PR anyway.
