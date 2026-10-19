Do 1 first, then 2. A failing test is a blocker: the C logic is not done until the harness proves it.

1. Fix `API/tests/test_subscribe_harness.c`. Each test must start from a clean subscription state: stop every slot
   with the existing stop/reset API (or a harness `reset_streams()` helper that calls it) at the top of each test.
   Never rely on what a previous test left. Then assert that `Subscribe_ParseStreamNarrowRequest` returns success,
   and print the reject reason if it fails, so a rejected setup can never pass silently as "streams g_theta".
   Cover the rounding edges: +0.5 -> 1, -0.5 -> -1 (round half away from zero, or whatever the spec says; match
   the host decoder exactly), saturation at the int16 limits, and NaN/Inf -> a defined sentinel.
   `python tools/host_tests.py` must be fully green, with all old tests still passing.
2. Then do the host side as planned: `ground_station/livewatch/stream.py`, `var_scales.yaml` and the planners,
   with pytest covering decode-parity against the C rounding.

Before you open the PR, list each test you added and its result.
