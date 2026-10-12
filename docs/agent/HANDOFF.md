# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-13 nightly run - tracker `docs/agent/nightly-2026-10-13.md` (one row per item, with commits)

Branch `overnight-2026-10-08`. The drone is on ELF 6da69207 (f6424fa1). Every change since then is tracker-side: no reflash.
- **Done:** items 1, 3, 4, 5, 9, 10, 11, 13. Mode 2/3 drift inputs fixed in the tracker, all measured on r6 replays:
  - `--align auto`: r6 flew `--yaw-deg 0` against a ~90 deg yaw.
  - Coral mask learned at start: stuck fixes; r6_f4 replay p50 272 -> 7.6 cm.
  - `--delay-ms 200`: camera lag 0.21 s.
- **Live video:** `camera_truth.sh fusion` (modes 1-3) or `video <name>` (window + recording only, for workflows A/B/C).
- **Background:** NN variant `tcn5h_s5_r6m1` (pool without mode 2/3 flights) and the VPS F3B worker (mode 3 loose
  tracking, brief `docs/agent/briefs/F3B-study.md`).
- **Open:** the per-flight constant offset between fixes and truth4 (40-95 cm, unexplained) and the r6_f2 replay p90 tail.

## Next 3 actions
1. When the background jobs finish: NN result into row 2; F3B fetch, verify (replay numbers, C tests), merge, row 7.
2. Next lab: `docs/lab/run-sheet-2026-10-13-drift-free.md`. Fusion mode 2, then mode 3: wait for `align LOCK`, then
   60 s hands-off. Pass mark PROPOSED: under 20 cm.
3. Item 6 (optional): online yaw refit after lock.

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/ or logs/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
