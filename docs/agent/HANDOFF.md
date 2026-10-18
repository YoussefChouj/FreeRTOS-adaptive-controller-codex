# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-18 lab prep (run sheet `docs/lab/run-sheet-2026-10-18.md`)

- Claude manager DISABLED: the CEO builds inline and merges; Argon High (`.agent-ops/argon.ps1`) researches and runs the Jules review loops (max 3 rounds), 160K cap.
- Learning session PAUSED at Q9 (Z forces -> block 1/2/3 -> isolating flight); Q1-Q8 scored in `docs/learning/gap-ledger.md`. Thesis: memory `thesis-core-claim`.
- Lab target (user): 20-30 flights in 3-4 h that also answer thesis questions: gate-memory premise (A->B->A, MRAC 100 vs 0, n=3) and Z force identification.
- Jules jobs launched 10-18 (briefs `.agent-ops/briefs/`): `j-lab-thesis` (lab_m_memory_u100/u0, lab_z_* .fly, memory_premise.py, run-sheet thesis section), `j-sysid-torque` (sysid u = c + d, log c/d/u), `j-z-feat` (Z block-1 features, `mrac_z_feat_mask` default 0).
- Unflashed firmware: 86fe9470, 37ecf87e, d29ada99, cb7ca992 (stream ladder A). The drone still runs 0e6e01b6.

## Next 3 actions
1. **Review + merge** the three Jules PRs (Argon review loop, then CEO QA: host_tests, scoped pytest, ruff, Keil build-only).
2. **Reflash** disarmed: `python ground_station/flashtool/rebuild_and_flash.py --yes`, restart 8081, check `arm 0`, `exp_active`, then bench the stream ladder (run sheet Step 0).
3. **Fly** in the run-sheet priority order: M1-M6 memory premise, Z flights, S1-S3, H0-H2, E1-E4, I1 at each pack end.

## Do not
- Do not treat a missing `logs/video_truth/<folder>/cam.mkv` as lost: it is on Drive (`docs/agent/video-archive.md`).
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
