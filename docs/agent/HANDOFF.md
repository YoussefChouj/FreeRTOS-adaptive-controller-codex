# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-13 lab end - mode 3 quick fix in source, host-tested, NOT flashed

Branch `overnight-2026-10-08`. Plan: `docs/flights/2026-10-13-overnight-and-test-plan.md` (F3 drift, F2, item 0).
Drone runs ELF 91f9d404 (8b80693a). On it, **do not fly ext_pos mode 2 or 3** (mode 1 shadow is safe).
- Flown 10-12/13 (all logs in `logs/`, camera truth in `logs/video_truth/<name>/`): F4 x3 sysid (mode 1),
  `r6_f3_mode3` (re-anchor took a consistent ~960 cm wrong fix, earth_y -80 -> -937 cm in 20 ms, motors killed),
  `r6_f2_mode2` (132.6 s, no jumps, max FB step 2.7 cm, but drift-prone: operator fought it with RC).
- F2 measured: c_y = 1.10 * pos_y (r 0.93), c_x = 0.29 * pos_x (r 0.13). The camera loop carries nearly all y
  position, so the OF y path barely sees the motion. Cause UNVERIFIED (OF y scale, axis/sign, frame rotation, delay 0).
- Quick fix (this commit): `EXT_POS_ANCHOR_MAX_CM` 150 (PROPOSED) bounds re-anchor in `ext_pos_take` (modes 2/3);
  `Ext_Pos_Ekf` caps the mode 3 innovation at `EXT_POS_SLEW_CM` per fix. Twin `video_truth.EXT_POS["anchor_m"]`.
  C test: `gcc -std=c99 -Wall -Wextra -IAPI -IAPI/tests/stubs_wp14 API/tests/test_ext_pos.c API/ekf_of.c -lm`.

## Next 3 actions
1. Overnight item 0, extended: replay `r6_f3_mode3` through `replay --fw` and show the fix stops the jump; find the
   F2 y-axis mismatch on the F4 mode 1 logs (OF y vs camera y, scale and sign). Then plan items 1-12.
2. Next lab: `python -m ground_station.flashtool.rebuild_and_flash --yes` (drone powered, disarmed, probe on),
   then the fast mode 3 test `bash ground_station/lab/camera_truth.sh fusion r6_f3b_mode3 3` after the capture QA.
3. Measure the camera delay (F4 stick-pulse xcorr) before any further closed-loop flight; it is still `--delay-ms 0`.

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/ or logs/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
