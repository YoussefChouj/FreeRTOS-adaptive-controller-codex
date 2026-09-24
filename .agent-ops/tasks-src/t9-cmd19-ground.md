# Task T9: ground-side mapping for firmware CMD 0x19 (Simplex / variant switch)

The firmware (merged in main, commit 4458435, `TASK/send_data.c`, doc `docs/research-platform/SIMPLEX.md`)
accepts CMD 0x19 with a param index and a value:
idx 0 mode, 1 variant, 2 roll_max, 3 pitch_max, 4 w_norm_max, 5 sat_ticks_max, 6 hold_ticks,
7 reset counters. Read send_data.c for the exact frame layout (value type, scaling, checksum) and
mirror how an existing param command (for example the one that DashboardBackend or the executor
already uses for MRAC gains) is encoded on the ground.

Do:
1. Add a ground encoder for CMD 0x19 next to the existing command encoders in `ground_station/`
   (find them with rg for the other CMD ids used in send_data.c).
2. Expose it as named params (`simplex.mode`, `simplex.variant`, `simplex.roll_max`, ...) wherever the
   executor or DashboardBackend maps param names to commands, so a plan can write them. Keep
   existing safety tiers: mode and variant are tier-0 and need the same gating as other tier-0 writes.
3. Add unit tests: byte-exact encoding for each idx, plus name mapping. Run
   `.agent-ops/win.sh "python -m pytest ground_station -q -p no:cacheprovider -x --deselect ground_station/flashtool"`
   in your worktree and paste the summary line.
4. Add one short section to `docs/research-platform/SIMPLEX.md` on the ground names.

Rules: no firmware edits, no build, no flashing, no probe, no contact with 127.0.0.1:8081. Keep LF
line endings. Commit on your branch (message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`).
Write the result file with the diff stat and test summary.

## Workspace rule (hard)
Work ONLY inside your worktree. `git rev-parse --show-toplevel` must end in .worktrees/<id>.
