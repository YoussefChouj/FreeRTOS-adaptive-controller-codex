# j-cleanup: fix three pre-existing host failures (tests + lint), root cause first

Branch: `overnight-2026-10-08`. Open ONE PR against it. Host Python only. Three other Jules sessions are open on
this branch; do NOT edit these files (if the root cause is in one of them, do not patch it: write the cause and the
minimal patch as a diff block in the PR body instead):
`ground_station/livewatch/campaign_capture.py`, `ground_station/livewatch/stream.py`,
`ground_station/livewatch/var_scales.yaml`, `ground_station/autotune/frf.py`, `ground_station/autotune/excitation.py`,
`ground_station/analysis/att_ctrl.py`, `ground_station/analysis/hover_fit.py`, `ground_station/analysis/att_fit.py`,
anything under `API/`, `TASK/`, `USER/`, and `ground_station/service/campaigns/*.fly`.

## The three failures (measured tonight)

1. `ground_station/service/tests/test_campaign_outputs.py::test_live_capture_applies_plan_once_and_records_per_flight`
   fails at `svc.bridge.subscribe_slot.assert_not_called()`: "Called 1 times". Find who calls `subscribe_slot`
   on this path and why (git log -S / blame on the call site and on the test). Decide which is right: the test's
   claim (the plan is applied once, per-flight recording must not resubscribe) or the new call. Fix the wrong side.
2. `ground_station/livewatch/tests/test_mrac_presets.py::test_slot_frame_size_follows_the_feature_count` fails:
   `MultiSlotBudget(... wire_pct=81.597 ..., per_slot=[... SlotBudget(manifest_name='mrac_phi', num_ranges=184,
   hz=50.0, frame_size_bytes=748, bps=37400.0, wire_pct=40.58)], safe=False).safe` is False. The guard send rate
   moved from 200 to 100 Hz today (commit cb7ca992, `campaign_capture.GUARD_SEND_HZ`, `API/subscribe.h`
   SUBSCRIBE_SEND_TASK_HZ). Find whether the preset (its rate or divider), the budget limit, or the test is out of
   date relative to that change, and fix that one. Never hardcode 200 or 100: use `GUARD_SEND_HZ` and the planner.
3. `ruff check ground_station/analysis/live_tracker.py`: 5 x E702 (multiple statements on one line). Split them;
   no behavior change. Then run `ruff check ground_station/ tools/ research/` and fix every remaining finding in
   files NOT on the list above, behavior-neutral only (unused imports, E702, F541, etc.). Do not reformat whole
   files and do not change public names.

## Tests

Run scoped: `python -m pytest ground_station/service/tests/test_campaign_outputs.py
ground_station/livewatch/tests/test_mrac_presets.py ground_station/analysis/tests/test_live_tracker.py` plus the
test file of every module you touched, and `ruff check` on every changed file. `python tools/host_tests.py` must
stay 26/26. In the PR body: each test with its result, and for failures 1 and 2 the root cause in two sentences
with the commit that introduced it.

## Rules

CRLF line endings in .py like the existing files. No new dependencies. If a spec point conflicts with the code,
follow the code and say so in the PR.
