# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-15 evening post-lab autonomous overhaul (`bed695d1`, `0fba18f6`, `58c76c23`, `86fe9470`)

- **Terminal-native `./fly` workflow**: `# gains:` directives (`c3`, `defaults`, `livetune`), stop-and-wait parameter writer with `[y/N]` prompt, `| Axis | idx | Old | New | ACK | ms | Retries |` table, 8081 journal read-back, `--restore-defaults`, `--list-gains`, `--dry-run`, `--post-takeoff-gains`, and F2/F3 -> F4 `livetune_best.json` persistence.
- **Trajectory & preflight fixes**: 4-point `goto` (`20` floats, `~1.6 s` upload on flashed `0e6e01b6` firmware), pre-arm ground preload (`upload_preload`), `start_z_m` preset altitude sync (fixes B3), CRC upload cache (B1a), 1.5 s preflight position stream wait (Fix 4), heartbeat backoff (B8/B13).
- **Analysis & safety guards**: F1 takeoff drift analysis (`docs/lab/f1-takeoff-drift-analysis-2026-10-15.md`), B5 measured slot/ACK rates (`99.92%` ACK), B15 sim session filter, B16 AXF flash/symbol mismatch guard, B6 `/api/streams` lab layout, D2/B2 camera touchdown offset, Section C RC/command PID fallback (`API/pid_fallback.h`, un-flashed so `OBJ/JX_FLY.axf` stays matched to `0e6e01b6`).

## Next 3 actions (turnkey lab execution, `docs/lab/run-sheet-2026-10-16.md`)
1. **Bring-up**: Start 8081, verify `arm 0` & motors ~3050, re-scan floor (`bash ground_station/lab/camera_truth.sh floor <name>`), start fusion 3 (`CT_FLOOR=... bash ground_station/lab/camera_truth.sh fusion f1-robust 3 --yaw-deg 94`).
2. **Run F1**: `.\fly lab_c_robust --pack 1 --once` (auto-prompts `[y/N]` to write C3 gains stop-and-wait, preloads 4-pt `goto` on ground before RC arm; fallback `--gains c2` or `--post-takeoff-gains c3` if pad ground-effect drift recurs).
3. **Run F2–F6** per `docs/lab/run-sheet-2026-10-16.md`: `lab_d_livetune_rate` & `lab_d_livetune_pos` (fusion 2, auto-restores defaults & saves `livetune_best.json`), then `lab_c_autotune` (auto-loads `livetune_best.json`), `lab_c_lqr`, `lab_c_mpc` (fusion 3).

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/ or logs/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
- Run `lab end` on test or rehearsal state against the real catalog (it rewrites `docs/lab/catalog.yaml`).

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-15 22:08 (mechanical, no LLM)
- Tree: .; branch / HEAD: overnight-2026-10-08 @ 86fe9470; unpushed commits: 4
- Dirty paths outside OBJ/: 109
  -  M .agent-ops/.ark-last-spawn
  -  M .agent-ops/served/estimator-panel.js
  -  M .agents/agents/codebase-scout/agent.md
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  -  M USER/build_log.txt
  -  M USER/main.c
  -  M docs/agent/HANDOFF.md
  - ... +101 more
- Dashboard 8081: UP (HTTP 200)
- Last commits:
  - 86fe9470 feat(fw,lab): RC/command PID fallback latch (Section C), measured B5 slot/ACK rates (99.92% ACK), ./fly --list-gains, --dry-run, and --post-takeoff-gains; 24/24 host C + 75 pytest pass
  - 58c76c23 feat(lab): .fly # gains directives & livetune_best persistence (A1-A6), sim session filter (B15), AXF flash/symbol guard (B16), /api/streams lab layout (B6), cam touchdown offset (D2/B2), stream_log.out guard (B9); 69 tests pass
  - 0fba18f6 feat(lab): native ./fly stop-and-wait gain writer (A1-A7), 4-pt goto & preset start_z_m (B1/B3/fix5), ground preload (fix2), preflight race (fix4), heartbeat backoff (B8/B13), events ms/--gaps (B7/B11/B14); 257 tests pass
  - bed695d1 docs(lab): measured F1 takeoff drift replay (C3 Kp 0.3214 = 40.2% restoring vel vs 9 normal takeoffs 6.3-16.6 cm) and B3 flight-5 preset start_z_m 0.85m root cause
  - 874f6307 docs(handoff): record 2026-10-15 full project sync to origin/main and overnight-2026-10-08
<!-- AUTO:END -->
