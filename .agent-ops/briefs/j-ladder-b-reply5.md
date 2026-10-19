Yes, the plan is good. Proceed with the host side now, then open ONE PR against `overnight-2026-10-08`.

Scope and rules for the host part:
1. `ground_station/livewatch/stream.py`: build the 0x27 narrow request when a plan asks for it (keep the old 0x26
   path as the default), decode int16 with `wire`/`scale`, and count saturated samples per variable (do not hide
   them: expose a counter the bandwidth panel and the manifest can read).
2. `ground_station/livewatch/var_scales.yaml`: one row per narrowed variable with scale, units and the source of
   the range (file:line of the firmware clamp, or PROPOSED). No range you did not derive from the code.
3. Budget checks: price frames with the existing planner and `campaign_capture.GUARD_SEND_HZ` (it is 100 now,
   step A). Never hardcode 200 or 100 in new code or tests.
4. `campaign_capture.py` is also edited by two other open sessions (stream groups). Keep your edits there small
   and local; prefer new code in stream.py or a new module.
5. Tests: pytest decode parity C vs Python (the same golden frames as the C harness), saturation counting, the
   0x26 path unchanged, the budget numbers. `python tools/host_tests.py` must stay 26/26 plus your new cases.
   Run scoped: the new and changed test files only, plus `ruff check` on the changed .py files.
6. CRLF line endings in .py/.c/.h like the existing files; YAML LF.
7. In the PR body: each test with its result, the B/s saved per group at 100 Hz (computed, mark it computed),
   and anything in the spec you changed because the code disagreed.

Do not ask again before the PR unless a test fails that you cannot fix.
