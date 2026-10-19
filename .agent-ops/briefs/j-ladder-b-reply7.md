Yes, your plan is right. Re-apply the C changes in `API/subscribe.c`, `API/subscribe.h` and
`API/tests/test_subscribe_harness.c`, and revert the whitespace-only edits at subscribe.c ~L505/L523.

One correction: the legacy f32 tests do NOT fail on the clean base. On `overnight-2026-10-08`,
`python tools/host_tests.py` is 26/26 and the scoped pytest below is green. If any f32 test fails on
your branch, your branch caused it: find the cause and fix it, do not skip or xfail it.

Before you push, all of these must pass, with the output pasted in the PR body:
- `python -m pytest -q ground_station/tests/test_stream.py ground_station/tests/test_campaign_capture.py ground_station/tests/test_manifest.py`
- `ruff check --output-format concise` on every changed .py file
- `python tools/host_tests.py` = 26/26

Then push and open the PR against `overnight-2026-10-08`. No more questions needed; finish it.
