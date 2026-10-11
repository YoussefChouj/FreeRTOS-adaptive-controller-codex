# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-12 - R4 overnight analysis done; camera fusion built, NOT flashed or flown

Branch `overnight-2026-10-08`. Report: `docs/flights/2026-10-12-r4-overnight-report.md` (+ `.html`, rebuild with
`video_truth report4 <md> --out <html>`). Phase 1/2 notes: ledger `state-2026-10-11.md` (end).
- Truth (R4, 13 flights): lens 0.287 px, coral rings 0 lost, pad jitter 0.6-1.1 cm. Flights b5/b6/b8/b9/b12 are
  landing-anchored (take-off detected 9-12 s late). b8/b10/b12 are truth suspect (coral jumps).
- EKF alone: 30-146 cm rms, hover b17 1 s-mean 60 s drift p90 19 cm. Camera replay (`fuse4`, offset c, gain 0.1,
  0.3 s): 5.1-13.6 cm rms on good tracks; 10 cm budget met on hover only (9.2 at 0.15 s), moves 16-27 (latency).
- `nn4` camera-off NN (LOFO): no fix, best 34 cm. The camera must stay in the loop.
- Firmware EXT_POS mode 2 (e179e95f): offset c on earth_x/y (replayed rule, not the design-doc EKF update), gain
  0.1, slew 3 cm, gate 40 cm, re-anchor after 3. Host test passes, Keil 0 errors. NOT flashed.
- Ground: `live_tracker` (6e6e0759) live coral tracker + UDP relay via `stream_log --relay-port`; preset
  `drift_rc_extpos` (d629deb1) priced, not applied.
- Not done: b16 per-preset tracking error, a separate pure-yaw drift ranking.

## Next 3 actions
1. Lab, drone disarmed: follow `docs/lab/run-sheet-r5-fusion-bringup.md` from step 0 (flash with
   `rebuild_and_flash --yes`, re-price the preset against the new ELF, bench 3 cm kick).
2. Latency event, track check, shadow out-and-back (mode 1), then the closed-loop hover gate (mode 2, PROPOSED
   10 cm + a tape-measured landing). No sysid start or origin reset in mode 2.
3. Only after a pass: dense trajectory presets, then the load scenarios. Fail: mode 0, no dense/load flights.

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
