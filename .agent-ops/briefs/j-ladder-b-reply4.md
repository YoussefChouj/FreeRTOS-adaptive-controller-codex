No, do not replace `API/subscribe.c` wholesale. It is flight-critical and a whole-file rewrite cannot be reviewed.
Keep the diff against `overnight-2026-10-08` minimal and local, like this:

1. Find the real cause first. Run `git diff origin/overnight-2026-10-08 -- API/subscribe.c` and read
   `HandleStreamRequest` in the patched file: check that `is_narrow` is computed from the same buffer and offset that
   `Uart5_Subscribe_HandleRequest` tested, before anything clears or shifts that buffer, and that the narrow branch
   calls `Subscribe_ParseStreamNarrowRequest`. Also check the harness: it must call the same entry point the firmware
   uses and must not leave a stale `UA5RxSubscribeBuf`/`UA5RxSubscribeLen` from the previous test.
2. Fix only the faulty lines. In the PR description, quote the bug in one sentence and the lines you changed.
3. Then `python tools/host_tests.py` fully green (all old tests too), with the reply-3 items: a clean reset per test,
   an assert that the narrow parse succeeds (print the reject reason on failure), the rounding edges, int16
   saturation and NaN/Inf.
4. Then the host side (`stream.py`, `var_scales.yaml`, the planners, decode-parity pytest) and open the PR without
   waiting for another confirmation.
