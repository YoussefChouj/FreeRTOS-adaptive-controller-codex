# wfb G5+G6 inline plan (supervisor rewrite; worker output rejected 2026-10-01)

Brief: `.agent-ops/tasks-src/wfb-g56.md` (allow-list, tests, acceptance command). Worker branch `vps/wfb-g56`
@8498856 kept for reference only; its files were removed from the wfb tree. Clean it after the rewrite lands.

## Why the worker output was rejected
- `slots_for`: hard-coded `send_hz = 200`, treated 62 vars as the 62-range limit, ignored the 508-float frame
  limit, and silently returned an over-budget plan when the needed vars alone did not fit (must raise).
- `write_manifest` mutated the caller's slot dicts; `read_manifest` checked only the schema string.
- G6: fuzzy substring column matching (`'motor' in col.lower()`), pandas beyond loading, `ano_of`/`of_alt_cm`
  fallbacks for symbols it never streams. 9 tests vs ~20 cases in the brief. Stray `check_syms*.py`.

## Facts (read this session; cite these, do not re-derive)
- `symbol_catalog.json` = 59 entries, TOP-LEVEL only (`Ctrler.locxPID`, `mymotor`, `DroneStatus`,
  `mrac_state.roll`, `xTickCount`, ...), each `{name, hash, address, elem_type, count, size_bytes}`. It is the
  firmware's on-board symbol table (gen_symbol_table.py:1-16), NOT the full streamable set: `real_voltage` and
  `ano_of.*` are absent yet streamed by manifests.yaml (:63, :217) through host DWARF resolution
  (manifest.py:1-5, `Registry.expand`). So "symbol exists" test = catalog root prefix match, and list
  `real_voltage` as a needed var that is DWARF-only (manifest.py:40 REQUIRED_SYNC_VARS, "verified against
  OBJ/JX_FLY.axf 2026-09-11"). Decide: include it (needed by abort monitor) and test it against
  REQUIRED_SYNC_VARS instead of the catalog. No safety-trip symbol exists (grep of catalog + manifests).
- Symbols: position `Ctrler.{locxPID,locyPID,Z_posPID}.{FB,Des}` (flightlab loops.yaml:21,24-25);
  motors `mymotor.motor1..4` (loops.yaml:33); battery `real_voltage` (battery.yaml:6); status
  `DroneStatus.ARM_Status/.FlyMode`, clock `xTickCount` (manifest.py:40-45); rate loops
  `Ctrler.gyro{x,y,z}PID` + `Z_ratePID`; attitude `imu_data.rol/pit/yaw`? -> verify member names in
  manifests.yaml before use. MRAC optional: `mrac_state.<axis>.{u_ad,u_nom,u_def,e,e_dot,Theta[i],Whatf[i]}`
  (facts-gs sec 1; mrac.h:223-260).
- Units: `Z_posPID.FB` is metres (TASK/AutoflyTask.c:373 `FB <= 0.2f` landing check). `locxPID.FB` unit
  UNVERIFIED (loops.yaml unit: null; des_hold_tol 5.0 hints cm). MUST verify from firmware (who writes
  locxPID.FB: search pid.c / AutoflyTask.c for `&Ctrler.locxPID` PID call args, or send_data.c:117 scaling)
  before rmse_xy is in metres. If unverifiable: per-axis scale constant with an OPEN note, never a guess.
- Budget: reuse manifest.py constants `DATA_FRAME_OVERHEAD_B=12` :226, `DEFAULT_VALUE_BYTES=4` :237,
  `USART3_WIRE_BPS=92160` :219, `HOST_SAFE_WIRE_PCT=80.0` :231, and the divider rule of
  `compute_multi_slot_budget` :266-363: `divider = max(1, int(send_task_hz/hz))`, bps =
  frame * send_task_hz / divider, send_task_hz 200 (API/subscribe.h:271). Brief says budget 87552*0.8
  (log_frames.md:72-73); manifest.py uses 92160*80 %. Pick one named constant and cite; prefer brief's.
  Limits: 4 slots, 62 ranges/slot (1 scalar var = 1 range, as manifest.py:326), 508 floats/frame.
- bench (sim/bench/bench.py): `SAT_HI, SAT_LO, SAT_BUDGET = 3995, 2005, 0.05` :19; `metrics` :35 computes
  rmse=inf when diverged but still rmse_xy/rmse_z/sat; `objective(rows)` :76 uses only rmse, sat. bench
  imports siblings `plant, scen` (:13) at import time. Worker test run took 35 s: check whether bench import
  or pandas is the cost; keep G6 on csv+numpy.
- stream_log CSV: header `t_src_ms,t_host_s,seq` + symbol columns, arrays `name[i]` (stream_log.py:422,
  :119-132), one file per slot.

## Design (to implement)
- campaign_capture.py: frozen `SlotPlan` not needed; `slots_for(rate_hz) -> tuple[list[dict], list[str]]`
  (brief allows returning dropped; document it). Greedy: needed first, fill slots up to 62 ranges / 508
  floats, then add optional vars in order while total bps <= budget; `CaptureError` if needed alone does not
  fit. `probe_max_rate` as brief. `write_manifest` builds new dicts (no mutation), validates slots, writes
  UTF-8 JSON with sorted segments; `read_manifest` validates keys/types/segment t0 < t1.
- workflow_b_adapter.py: module constants for the position/motor columns (symbols are firmware loop names,
  not controller names: flightlab calls them loops). Load each slot CSV with csv+numpy; align slots by
  nearest `t_host_s` within 0.1 s else NaN; segment [t0, t1); rows keys as bench `metrics`.
- Tests per brief list, plus mutants: drop-needed-before-optional, divider round vs int, alignment gap,
  half-open segment end.

## Facts verified 2026-10-01 (session 2) -- supersede the UNVERIFIED notes above
- Units: `locxPID/locyPID .FB/.Des` are cm, `Z_posPID` is m: TASK/StabilizerTask.c:242-244 (workflow-b)
  `in.x_m = Ctrler.locxPID.FB * 0.01f; /* TWC / loc loops are cm, the glue is m */`, `in.z_m = Z_posPID.FB; /* already m */`.
- Attitude: `imu_data.rol/pit/yaw` (manifests.yaml:66-68). Airborne: `flight_phase` (1 B; airborne =
  FLYING|LANDING, StabilizerTask.c:252). Motors `mymotor.motor1..4` are `short` (2 B), CCR 2000..4000 (bench SAT 2005/3995).
- ELF check works and is fast (1.3 s): `SymbolResolver("OBJ/JX_FLY.axf").resolve(sym).size` (livewatch/symbols.py;
  test_manifest.py:20 uses the same ELF path `parents[3]/"OBJ"/"JX_FLY.axf"`). All resolve: real_voltage 4,
  flight_phase 1, DroneStatus.ARM_Status 1, .FlyMode 1, imu_data.rol 4, mymotor.motor1 2, Ctrler.locxPID.FB 4,
  mrac_state.roll.Theta[5] 4, .u_def 4, mrac_state.z_rate.e_dot 4, xTickCount 4.
  Tests: (a) every symbol resolves in the ELF with size <= 4 (strong); (b) brief's catalog test = catalog root
  prefix match, with a named `DWARF_ONLY = {"real_voltage", "flight_phase"}` exception (not in the on-board table).
- MRAC: `MRAC_N_FEATURES 6` (API/mrac_variant.h:12) = MAX_NUM_BASIS (mrac.h:47); members e, Theta[6], Whatf[6],
  u_nom, u_ad, u_def, e_dot (mrac.h:233-255). Axes pitch, roll, yaw, z_rate. Manifests list elements one by one
  (`mrac_state.roll.Theta[0]`, manifests.yaml:135), so every CAMPAIGN_SET entry is ONE scalar = one range.
- Divider (two different 200/100 figures, both real): the live host sends `divider = int(100 / hz)`
  (capture_preset.py:329; stream.py:97 `SEND_TASK_HZ = 100`, the real Send_Task cadence), but the firmware
  budget guard prices a slot at `frame * 200 / divider` (API/subscribe.c:592, subscribe.h:271
  `SUBSCRIBE_SEND_TASK_HZ 200U`). Plan with both: `divider = max(1, int(SEND_TASK_HZ / rate_hz))`,
  `guard_bps = (FRAME_OVERHEAD + 4*n) * 200 / divider`, budget `87552 * 0.8 = 70041.6` (brief; stricter than
  manifest.py's 92160*80 %). Slot `hz` = `SEND_TASK_HZ / divider` (what the drone emits); 200 and 100 both give divider 1.
- Limits: import `MAX_SLOTS, MAX_STREAM_RANGES, STREAM_MAX_BYTES (2032 = payload, subscribe.h:213), FRAME_OVERHEAD,
  SEND_TASK_HZ` from livewatch/stream.py and `DEFAULT_VALUE_BYTES, REQUIRED_SYNC_VARS` from manifest.py; no copies.
  `VARS_PER_SLOT = min(MAX_STREAM_RANGES, STREAM_MAX_BYTES // DEFAULT_VALUE_BYTES)` (= 62; both limits in one line).
- Expected plan (arithmetic, assert in tests): needed 26 = REQUIRED_SYNC_VARS 4 + flight_phase + position 6 +
  attitude 3 + rate FB/Des 8 + motors 4; optional 68 = 4 axes x 17. At 50 Hz (div 2): 62+32 vars,
  (260+140)*100 = 40000 B/s, nothing dropped. At 100 Hz (div 1): keep 55 optional (62+19 vars, 52000+17600 =
  69600 B/s), drop the last 13; 56 would be 70400 > 70041.6.
- CSV naming: stream_log `_slot_path(out, slot)` = `<stem>.slot<N>.csv` (stream_log.py:595); header row
  `["t_src_ms","t_host_s","seq"] + columns_for(schema)` (:422). Manifest `csv` = that file name, relative.
- bench import is 6.2 s cold (plant+scen). Adapter imports bench lazily through one cached `_bench()` helper so
  importing the adapter stays cheap; the other-cwd test runs a subprocess with PYTHONPATH=repo root.
- No module in ground_station/analysis imports livewatch yet; analysis -> livewatch (reader of what capture
  writes) is the right direction.

## Final design decisions (session 2)
- campaign_capture.py owns every symbol name: `Axis(NamedTuple): feedback, reference, to_m`;
  `POSITION_AXES` (x, y cm -> 0.01; z m -> 1.0, citing StabilizerTask.c:242-244), `MOTORS`, `ATTITUDE`,
  `RATE_LOOPS`, `STATUS = REQUIRED_SYNC_VARS + ("flight_phase",)`, `MRAC_SHADOW` (priority order: u_ad, u_nom,
  u_def per axis, then e, e_dot, then Theta, then Whatf; dropping takes from the end). The adapter imports
  POSITION_AXES + MOTORS, so it contains no symbol or controller names and cannot disagree with the capture.
- `slots_for(rate_hz, *, budget_bps=PLANNING_BUDGET_BPS) -> tuple[list[dict], list[str]]`: slots
  `{"slot", "hz", "vars"}`; CaptureError if rate <= 0 or needed alone does not fit (<= 4 slots and budget).
- `CaptureError(RuntimeError)` for probe/budget; `ManifestError(ValueError)` for bad manifests. Validation shared
  by write and read: schema, pack_id, rate_hz > 0 finite, 1..4 slots with unique slot ids 0..3, hz > 0, csv a plain
  file name, vars non-empty + unique across slots, dropped list[str], segments unique names, finite t0 < t1,
  non-overlapping, written sorted by t0.
- Adapter: base timeline = slot holding `POSITION_AXES[0].feedback`; other columns by nearest `t_host_s` within
  0.1 s else NaN (searchsorted); segment [t0, t1). diverged = n < 10 or any non-finite tracking error (a telemetry
  gap counts: never score a flight the tuner could not see). rmse_xy/rmse_z computed as bench even when diverged
  (NaN if no samples). sat over finite motor samples, NaN if none (then J is NaN -> tuner's Result.usable False).
  `score([])` -> ValueError.

## NEXT
1. Write the 4 files from "Final design decisions" (no more reading needed), fixture built inside the tests
   via write_manifest + csv (no committed fixture dir).
2. Acceptance cmd from the brief with `-p no:cacheprovider`; mutants: optional-before-needed, divider round vs
   int, alignment gap tolerance, half-open segment end, guard 200 vs 100.
3. Commit on workflow-b by pathspec, push, ledger line, DONE record here, state, `vps-worker.sh clean wfb-g56`.

## DONE dc928e6 (pushed origin/workflow-b, 2026-10-01, supervisor inline)
Files: ground_station/livewatch/campaign_capture.py (330), ground_station/analysis/workflow_b_adapter.py (148),
tests test_campaign_capture.py (213) + analysis/tests/test_workflow_b_adapter.py (155).
Acceptance: 46 passed (`-p no:cacheprovider`). Mutants killed (7): optional-before-needed, divider round vs int,
guard 100 vs 200 Hz, alignment gap check off, closed segment end, MIN_SAMPLES `<=`, non-finite error not diverged.
Plan numbers measured: 26 needed / 68 optional; 100 Hz -> slots 62+19 = 69600 B/s, last 13 optional dropped;
50 Hz -> 62+32 = 40000 B/s, nothing dropped; 60 Hz -> divider 1 -> 100 Hz.

Deviations from the brief (all deliberate):
- CAMPAIGN_SET is a read-only MappingProxyType of tuples, not a dict of lists.
- Extra public names: Axis, POSITION_AXES, MOTORS/ATTITUDE/RATE_LOOPS/STATUS/MRAC_SHADOW groups, PLANNING_BUDGET_BPS,
  GUARD_SEND_HZ, CaptureError, ManifestError (the adapter imports its columns from here; holds no symbol names).
- Manifest is stricter than the brief: exact top-level and slot keys, unique slot ids and csv names, csv is a bare
  file name, no var recorded twice or both recorded and dropped, segments sorted + non-overlapping; atomic write.
- Rows carry an extra `n` key; `sat` pools the finite samples of all four motors and is NaN when there are none.
- The adapter raises ManifestError for an unrecorded scoring var, a missing CSV column, an empty CSV or a ragged row.
