Good, proceed. Guidance:

1. `test_read_telemetry_merges_slots_in_file_order_and_skips_rows_without_a_number` PASSES on our host at 452de6fe
   (Windows, Python 3.11: `test_campaign_outputs.py` = 15 passed, 1 failed, the failing one is only
   `test_live_capture_applies_plan_once_and_records_per_flight`). So either your fix for failure 1 broke it, or your
   environment differs (pandas/numpy version). Check that first: run it on a clean checkout of 452de6fe. If it fails
   there too, it is an environment-dependent parse: fix `ground_station/service/campaign_outputs.py` so a torn row
   (a row whose value field is missing or not a number) is skipped on every pandas/numpy version, and say in the PR
   which versions you ran. If your change caused it, fix your change instead. Do not weaken the test.
2. Failure 1: say in the PR which file your deep-copy fix edits. If it is `ground_station/livewatch/campaign_capture.py`
   (on the do-not-edit list), revert it there and put the minimal patch as a diff block in the PR body, unless the
   fix is test-side only.
3. Then failures 2 and 3 as in the brief (never hardcode 200 or 100: use `GUARD_SEND_HZ` and the planner).
4. Then open ONE PR against `overnight-2026-10-08`. `python tools/host_tests.py` stays 26/26. CRLF in .py. The PR
   body lists each test with its result, and for failures 1 and 2 the root cause in two sentences with the commit
   that introduced it.

Do not ask again before the PR unless a test fails that you cannot fix.
