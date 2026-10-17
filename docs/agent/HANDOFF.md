# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-17 lab day (`88e71602` + run sheet)

- Claude manager DISABLED (user, 10-17): the CEO builds inline; Gemini 4 Argon High (`.agent-ops/argon.ps1`) does research and second opinions only, 160K cap.
- WP-46 merged (`d29ada99`): experiment rows `exp_id`/`exp_active` (8 rows, `--list-exp`), xy no-fight reference model, `lab_e_exp_*` 3-layer matrix (4 flights).
- `lab_h_speed_ladder_l0/l1/l2`: Gerono a60, 67/100/134 cm/s under trajv 80/110/150, heading held (`yaw_follow=0`).
- Unflashed firmware: 86fe9470 (PID fallback), 37ecf87e (yaw dE wrap + Mahony decouple), d29ada99 (exp rows). The drone still runs 0e6e01b6.

- **LAB DAY ON HOLD (user, 10-17 08:55):** no flights until the stream ladder is bench-measured: A guard prices the real rate, D 2 Mbaud, B int16/uint8 types, C delta codec after Argon research, E parity frame + link-settled check. Grilling still open (tracker zoom, NN): `.claude_state.md` GRILL line. No code until shared understanding.

## Next 3 actions after the hold (`docs/lab/run-sheet-2026-10-17.md`)
1. **Reflash** disarmed: `python ground_station/flashtool/rebuild_and_flash.py --yes`, restart 8081, check `arm 0` and `exp_active` streamed.
2. **F1** `.\fly lab_c_robust --pack <1|2> --once`: first flight of the new yaw code, land if the heading swings.
3. **E1-E4, H0-H2, then 10-16 F2-F6** in the run-sheet order.

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/ or logs/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
- Run `lab end` on test or rehearsal state against the real catalog (it rewrites `docs/lab/catalog.yaml`).

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-16 02:01 (mechanical, no LLM)
- Tree: .; branch / HEAD: overnight-2026-10-08 @ 944aca05; unpushed commits: 5
- Dirty paths outside OBJ/: 108
  -  M .agent-ops/.ark-last-spawn
  -  M .agent-ops/served/estimator-panel.js
  -  M .agents/agents/codebase-scout/agent.md
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  -  M USER/build_log.txt
  -  M USER/main.c
  -  M ground_station/analysis/live_tracker.py
  - ... +100 more
- Dashboard 8081: UP (HTTP 200)
- Last commits:
  - 944aca05 docs(handoff): complete 2026-10-15 autonomous lab overhaul (fixes 1-5, frictions A1-A7/B1-B16/C/D2, 207 pytest + 24 host C pass)
  - 86fe9470 feat(fw,lab): RC/command PID fallback latch (Section C), measured B5 slot/ACK rates (99.92% ACK), ./fly --list-gains, --dry-run, and --post-takeoff-gains; 24/24 host C + 75 pytest pass
  - 58c76c23 feat(lab): .fly # gains directives & livetune_best persistence (A1-A6), sim session filter (B15), AXF flash/symbol guard (B16), /api/streams lab layout (B6), cam touchdown offset (D2/B2), stream_log.out guard (B9); 69 tests pass
  - 0fba18f6 feat(lab): native ./fly stop-and-wait gain writer (A1-A7), 4-pt goto & preset start_z_m (B1/B3/fix5), ground preload (fix2), preflight race (fix4), heartbeat backoff (B8/B13), events ms/--gaps (B7/B11/B14); 257 tests pass
  - bed695d1 docs(lab): measured F1 takeoff drift replay (C3 Kp 0.3214 = 40.2% restoring vel vs 9 normal takeoffs 6.3-16.6 cm) and B3 flight-5 preset start_z_m 0.85m root cause
<!-- AUTO:END -->
