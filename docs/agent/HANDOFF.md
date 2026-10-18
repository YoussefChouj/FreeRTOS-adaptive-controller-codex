# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-19 lab = redo of all 10-18 flights (`.\labday 2026-10-19`, day file `docs/lab/days/2026-10-19.yaml`)

- 10-18 post-lab fixes, all committed: relay bind retry 5e0e545c; labday starts 8081 before the camera + endlive 7ffb9ff0; stale-telemetry trip only after 0.3 s f51d3de3 (operator OK); bridge re-pushes slots after 3 s silence a0d15173; dry run once per unchanged flight + width-safe cooldown line 6cfd8a85; one camera loop + run-HHMMSS dirs + CR in tracker PIDs 056eb0c3; landing zoom off d2879e1d; 8081 log to `logs/service/<port>-<ts>.log` + QuickEdit off 9b7046d3; abort link snapshot a2efc3ad; 1 Hz `link` events d4c653c6.
- Tracker bias (~30 cm fix offset on 10-18): the fire-station print on the mat sat in the keep disc and pulled the dot off the drone. Fixed 52784922 (floor-scan scenery mask, cached PNG in the floor dir). Floor = floor-1018; if the camera moved, re-scan (`camera_truth.sh floor floor-1019`, `--floor floor-1019`).
- Takeoff link losses 220517/220919 (after P15): cause still UNKNOWN (WiFi vs host rx). The new service log + abort/link events will tell on the next one.
- No firmware source change since the 10-18 flash: no reflash.
- Claude manager DISABLED; Argon High researches + runs Jules loops, 160K cap. Thesis: memory `thesis-core-claim`.

## Next 3 actions
1. Lab: `.\labday 2026-10-19` (it starts 8081, the camera and the cached dry run). Check the dot sits on the parked drone before mem1.
2. After mem1 stop: check fixes vs optical flow (no steady offset) and the `link` events.
3. Open: service bind takes ~41 s after start (look later); Jules sessions j-sysid-torque / j-z-feat state UNVERIFIED.

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
