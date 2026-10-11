# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-12 - R4 overnight done; camera fusion retuned, NOT flashed or flown

Branch `overnight-2026-10-08`. Report: `docs/flights/2026-10-12-r4-overnight-report.md` (+ `.html`, rebuild with
`video_truth report4 <md> --out <html>`). Phase 1/2 notes: ledger `state-2026-10-11.md` (end).
- Truth (R4, 13 flights): lens 0.287 px, coral rings 0 lost, pad jitter 0.6-1.1 cm. Flights b5/b6/b8/b9/b12 are
  landing-anchored (take-off detected 9-12 s late). b8/b10/b12 are truth suspect (coral jumps).
- EKF alone: 30-146 cm rms, hover b17 1 s-mean 60 s drift p90 19 cm. `nn4` camera-off NN: no fix (best 34 cm).
- Camera fusion, live tracker replay, retuned rule (gain 0.9, slew 10 cm, spread 0.1 s, PROPOSED), 1 s-d60 p90:
  0.15 s latency b17 4.2, b13c 9.7, b5 9.6, b16 traj 12.1 cm; 0.3 s 6.3/13.4/13.6/16.5. First pass (gain 0.1,
  3 cm slew) was slew-limited. Replay and circular; latency 0.15 s or less REQUIRED.
  Offset-rate term (`--beta`, db259a48) tried, not adopted: b16 9.0 but b13c/b5 10.8-13.3. b16 gap is tracker
  fix error at speed (synthetic fixes 7.8).
- Firmware EXT_POS mode 2: e179e95f + 18c7d964 (spread 0.5 cm/5 ms tick so the locx D term (Kd 4.0) does not
  kick). Host test passes, Keil 0 errors. NOT flashed. `test_ekf_of_shadow` `test_reset_bias` fails at HEAD too.
- b16 per-preset (`presets4`): estimate error 14-105 cm, controller lag 6-30 cm, fused 1.9-11.4. Yaw (`yaw4`):
  2-9 cm per 90 deg, not the main drift source.
- Ground: `live_tracker` (6e6e0759) + `stream_log --relay-port`; preset `drift_rc_extpos` priced, not applied.

## Next 3 actions
1. Lab, drone disarmed: follow `docs/lab/run-sheet-r5-fusion-bringup.md` from step 0 (flash 18c7d964+ with
   `rebuild_and_flash --yes`, re-price the preset against the new ELF, bench kick: c <=10 cm/fix, earth <=0.5 cm/tick).
2. Latency event, track check, shadow out-and-back (mode 1), then the closed-loop hover gate (mode 2, PROPOSED
   10 cm + a tape-measured landing). No sysid start or origin reset in mode 2.
3. Only after a pass: dense trajectory presets, then the load scenarios. Fail: mode 0, no dense/load flights.

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
