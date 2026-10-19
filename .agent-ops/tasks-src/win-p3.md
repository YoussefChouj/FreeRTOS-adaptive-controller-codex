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
Do not flash. Supervisor flashes and verifies. Digest: .agent-ops/out/win-p3.md
