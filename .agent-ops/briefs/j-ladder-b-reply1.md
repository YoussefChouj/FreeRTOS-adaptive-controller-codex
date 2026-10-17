Option 2, made precise. Not option 3 (mocking `_var_bytes` hides the real path), and do not rewrite the legacy assertions
(option 1): they guard the format the flashed firmware speaks today.

1. The bytes per value depend on the wire format the stream will actually use. Until the firmware is reflashed and the
   host has selected the int16 request (CMD 0x27, frame 0x0D), every value is 4 bytes. So: one explicit setting
   (for example `wire_format = "f32" | "i16"`, default `"f32"`), and the planner, the admission guard and the manifest
   price the bytes of the format that is requested. Only `"i16"` reads `var_scales.yaml`.
2. One source of truth: a single helper for bytes per value and frame size (next to the frame-size formula in
   `stream.py`), imported by `campaign_capture.py` and `manifest.py`. No second copy of `_var_bytes`.
3. The legacy tests stay as they are and must pass unchanged. Add new tests for the `"i16"` path whose expected counts
   are computed from the frame-size formula and the 80% wire cap (`HOST_SAFE_WIRE_PCT`), not hardcoded. Include a var with
   no yaml entry (falls back to 4 bytes or is refused, say which and why) and a mixed i16/f32 plan.
4. Never weaken the 80% cap or the guard. The guard rate stays `GUARD_SEND_HZ` (100).
5. Earlier review note: `API/subscribe.c` has whitespace-only changed lines around L505 and L523; revert those so the
   diff has only real changes.

Then: scoped pytest on the changed test files, `python tools/host_tests.py` 26/26 plus your new cases, `ruff check` on
changed .py files, CRLF. Do not ask again: open ONE PR against `overnight-2026-10-08`. If a test fails that you cannot
fix in one try, list it in the PR body with its error line and open the PR anyway.
