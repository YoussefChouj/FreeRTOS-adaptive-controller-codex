# P3 — pluggable controller interface (firmware + host), research in .agent-ops/out/r1b.md
Goal: controllers PID, MRAC, structured MRAC, RBF-NN MRAC and a 3-layer stack selectable at runtime, the active
controller logged in session metadata, and comparable metrics for all of them.
First inventory what exists (TASK/StabilizerTask.c, API/*mrac*, API/*pid*, grep "controller", "mrac_state"): which
controllers are already implemented, how the current one is selected. Then:
1. Firmware (tier-0; authorized): a small C89 vtable-style interface, e.g. `ctrl_ops_t {init, reset, update}` per
   axis, a `volatile uint8_t g_ctrl_select` + `g_ctrl_select_req` switched ONLY when disarmed or via bumpless
   `reset()` (r1b item 1), fallback to PID on invalid selection/NaN output (r1b item 2), `axis_mask` (item 3).
   Wrap existing controllers; do NOT change their math. Unimplemented controllers (e.g. RBF-NN if absent) are
   stubs that return PID output and report `available=0`. Build must pass (0 Error(s)).
2. Host test in tests/firmware_host for selection/fallback/bumpless logic (gcc -std=c89 -pedantic -Wall -Werror).
3. Service: record the active controller id/name (read from telemetry or livewatch registry symbol) in session
   metadata; add metrics per r1b item 6 (tracking RMSE per axis, control-effort total variation) to the report
   pipeline so sessions with different controllers are comparable. Tests for both.
Do not flash. Supervisor flashes and verifies. Digest: .agent-ops/out/vp3.md

## Rules (VPS Linux workers)
- Work ONLY inside your git worktree (cwd). Commit on your branch when done; do not merge or touch other worktrees.
- There is no drone, no probe, no Keil and no dashboard here. Never flash, never run livewatch/pyOCD, never POST anywhere.
- Firmware: Keil ARMCC V5.06 C89 (declarations at block top, no VLAs, no C99). You cannot build with Keil; the
  supervisor builds. Check C with host tests: tests/firmware_host using `gcc -std=c89 -pedantic -Wall -Werror`.
- Many files are CRLF: preserve line endings.
- Tests: `PYTHONUTF8=1 python3 -m pytest -q -p no:cacheprovider <dirs you touched>`; paste the exact summary lines.
- Minimum code that solves the problem. Match surrounding style. No patch_*.py, no binaries in commits.
- Only report numbers you measured. Digest at the path named in the task, under 80 lines: what changed (file:line),
  how verified (exact test summary lines), open issues, anything the supervisor must verify live.
