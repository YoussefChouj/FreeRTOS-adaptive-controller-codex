# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-12 - R4 overnight done; drift budget met in replay (20 Hz fixes), NOT flashed or flown

Branch `overnight-2026-10-08`. Report: `docs/flights/2026-10-12-r4-overnight-report.md` (+ `.html`, rebuild with
`video_truth report4 <md> --out <html>`). Phase 1/2 notes: ledger `state-2026-10-11.md` (end).
- Truth (R4, 13 flights): lens 0.287 px, coral rings 0 lost, pad jitter 0.6-1.1 cm. Flights b5/b6/b8/b9/b12 are
  landing-anchored (take-off detected 9-12 s late). b8/b10/b12 are truth suspect (coral jumps).
- EKF alone: 30-146 cm rms, hover b17 1 s-mean 60 s drift p90 19 cm. `nn4` camera-off NN: no fix (best 34 cm). Second pass `nn_io` (GRU/TCN, UTIL 78-flight Crazyflie pretrain,
  AirIO pretrained net): still no fix, best held-out b17 15.7 / b7 47.2 / b10 260 cm (head-only fine-tune, `--ft-only out`); AirIO adds nothing (report sec 3b).
  Installs: mss 10.2.0, markdown 3.11, torch 2.14.1+cpu; data D:/datasets/util_uwb, D:/datasets/airio_ckpt_*.
- Camera fusion, live tracker replay, retuned rule (gain 0.9, slew 10 cm, spread 0.1 s) + 20 Hz fixes (all
  PROPOSED), 1 s-d60 p90 at 0.15 s: b17 3.5, b13c 7.3, b5 7.4, b16 traj 7.4 cm = budget met on all 4 (10 Hz: b16
  12.1). 0.3 s: 5.6/11.3/11.8/12.3. Replay and circular. Gates: latency <=0.15 s and >=20 fixes/s received.
  `--beta` (db259a48) tried, not adopted. `replay --fw` runs the real `API/ekf_of.c` (gcc host driver
  `API/tests/ext_pos_replay_host.c`): 3.5/7.4/7.5/7.9 cm at 0.15 s, within 0.5 cm of the Python copy. FW takes 20-30 Hz (16-deep queue at 100 Hz, newest-wins mailbox).
- Firmware EXT_POS mode 2: e179e95f + 18c7d964 (spread 0.5 cm/5 ms tick so the locx D term (Kd 4.0) does not
  kick). Host test passes, Keil 0 errors. NOT flashed. All firmware host tests pass.
- b16 per-preset (`presets4`): estimate error 14-105 cm, controller lag 6-30 cm, fused 1.9-11.4. Yaw (`yaw4`):
  2-9 cm per 90 deg, not the main drift source.
- Ground: `live_tracker` (6e6e0759) + `stream_log --relay-port`; preset `drift_rc_extpos` priced, not applied.

## Next 3 actions
1. Lab, drone disarmed: follow `docs/lab/run-sheet-r5-fusion-bringup.md` from step 0 (flash 18c7d964+ with
   `rebuild_and_flash --yes`, re-price the preset, bench kick: c <=10 cm/fix, earth <=0.5 cm/tick, >=20 fixes/s).
2. Latency event, track check, shadow out-and-back (mode 1), then the closed-loop hover gate (mode 2, PROPOSED
   10 cm + a tape-measured landing). No sysid start or origin reset in mode 2.
3. Only after a pass: dense trajectory presets, then the load scenarios. Fail: mode 0, no dense/load flights.

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
