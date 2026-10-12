# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-13 night - mode 2/3 drift root cause found: 90 deg frame rotation; fix is tracker-side

Branch `overnight-2026-10-08`. Plan: `docs/flights/2026-10-13-overnight-and-test-plan.md`, section "Item 0 result".
The drone is on ELF 6da69207 (f6424fa1). No reflash is needed for this fix.
- **Cause (measured).** r6 F2/F3/F3b were flown with `--yaw-deg 0` (the script default). `live_tracker fit` measures
  the pad->EKF rotation at about 90 deg (F2 raw-path windows 90-98 deg). Rotated feedback has no restoring force,
  hence the drift and the RC fight.
- **Fix.** `live_tracker --align auto` (`AutoAlign`). The tracker stays in mode 1 until it has fitted the rotation in
  flight (span 80 cm, scale 0.6-1.5, rms 20 cm, 2 fits within 15 deg, all PROPOSED). It then sets the offset so the
  switch is bumpless and sends the mode. `camera_truth.sh fusion` adds it for modes 2/3.
- **Replay (`fit <stem> --auto`).** F4c locks at 85 deg (step 14.5 cm). F2 locks at 99 deg. The F4 hover and F4b do
  not lock (safe). Tests: `python -m pytest ground_station/analysis/tests/test_live_tracker.py -q` (25 pass).

## Next 3 actions
1. Overnight: measure the camera delay (F4 stick-pulse xcorr) -> `--delay-ms`. Mask RC-active segments for acceptance.
   Start the NN held-out training run (commands in `docs/agent/ledger/state-2026-10-12.md`).
2. Next lab: `camera_truth.sh fusion <name> 2`. Fly about 1 m out and back plus sideways. Wait for `align LOCK ~90 deg`,
   then 60 s hands-off. Repeat with mode 3.
3. Follow-up: online refit after lock (the EKF yaw wanders between flights).

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/ or logs/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
