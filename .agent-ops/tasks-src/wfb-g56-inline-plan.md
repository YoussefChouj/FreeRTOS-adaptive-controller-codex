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

## NEXT
1. Verify locxPID.FB unit and imu attitude member names (2 greps).
2. Write the 4 files + fixture script inside the test (no committed fixture dir unless needed).
3. Acceptance, mutants, commit on workflow-b by pathspec, push, ledger, DONE record here, state, clean vps.
