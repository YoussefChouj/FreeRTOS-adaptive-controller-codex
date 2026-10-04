# WP-37 report: firmware refactor to the pid.c standard, telemetry groups, subscribe slots

Status: DONE. The CTE (acct B) wrote parts A, B and C (9 `cte(wp37)` commits) and then hit the account-B session
limit (try 6 of 10). On the operator's request the CEO stopped the lane and finished part D and this report.
NOT flashed; the CEO Keil build-check runs at merge.

## A. Refactor (behaviour-preserving, one commit per module)
| Module | Change | Proof |
|---|---|---|
| `TASK/StabilizerTask.c` | header, named constants, 4 long functions split, `ComputePID_Hold` -> `pid.c` `ComputePID_GatedHold`, dead code out (fast_atan2 + table, cnt_locs/cnt_yaw, stale externs) | fw_trace stab vs wp/36 identical, 4 seeds x 200000 ticks, 99.45% line coverage; pid_guards bit-exact |
| `API/controller.c` | header, quad-X mixer as one `MIX_ROW` table used by `Mix_Compute` and the MRAC V2 deficit | test_mixer 4.8M checks vs the wp/36 expressions; fw_trace identical |
| `TASK/send_data.c` | 630-line if/else command chain -> `k_gs_cmds` table of 27 handlers, named reject reasons, dead `ANO_Report_UserData1` out | fw_trace cmd identical (99.8% of the command path); GS test ids == `COMMAND_TABLE` |
| `API/mrac.c` | `MRAC_UpdateAxis` (386 lines) split into 6 named steps, simplex legend, named limits | run_mrac_equiv EQUIV OK; fw_trace mrac identical (97.3% of lines) |
| `API/ekf_of.c`, `API/thrust_estimators.c` | headers, named state indices, one PWM knot row + aligned thrust-curve table (was 2 copies) | fw_equiv ekf_of code+data same; ThrustEst bit-identical on 2M steps |
New proof tools: `tools/fw_equiv.py` (normalized -O2 code and data vs a git ref), `tools/fw_trace.py` (differential trace).
Not yet refactored (25 files): listed in `docs/firmware-structure.md`.

## B. Telemetry groups
`g_tlm` (`API/flight_telemetry.h`): 7 float32 groups, 60 floats, 240 B in CCM, written once per tick by `Tlm_Snapshot()`
at the end of the Stabilizer tick, so one subscribe range streams all of it from one tick. Nothing renamed.
GS mirror `ground_station/platform/telemetry_groups.py` + manifest section + tests. fw_trace checks g_tlm == sources.

## C. Subscribe slots
Measured (host build): `sizeof(Subscribe_Stream_t)` = 508 B. Slot table + staging moved to CCM: 4 slots = 2540 B
moved out of SRAM; 8 slots = 4572 B of CCM, 0 B of SRAM. The link-budget guard sums all slots per transport, so N
does not change the wire load. One `SUBSCRIBE_MAX_SLOTS` switch; harness `subscribe_8_slots` passes (35 checks).
Default stays 4: `ground_station/livewatch/stream.py` `MAX_SLOTS` is outside this package. Recommendation: 8
(PROPOSED; the per-tick CPU of 8 busy slots and the stream on the drone are unmeasured).

## D. `docs/firmware-structure.md` (60 lines): module map, how to add a tunable / a telemetry group, the slot budget.

## Acceptance (CEO re-run 2026-10-04, wt-wp37)
- `bash tools/check.sh`: CHECK PASS (host tests 16/16, EQUIV OK, c-pytest 26 passed, SIL smoke 10 passed,
  clang-tidy 12/12, row-meta 7 tables 170 cells 0 errors, arm-syntax SKIP: no arm-none-eabi-gcc).
- `python API/tests/run_mrac_equiv.py`: EQUIV OK: 3728208 lines identical (plain) + 4120208 (sigma-prior).
- `pytest ground_station/platform ground_station/comm`: 4 failed, 377 passed, 1 error. Main has 1 failed + 1 error
  (manifest drift, harness `test_frame_type`). The 3 extra fail on the worktree's stale `OBJ/JX_FLY.axf`
  (unknown symbols `g_of_full_tilt`, `g_ekf_of_vel_fb`, `mrac_inj`, all older than WP-37); re-run after the Keil build.

## PROPOSED / open
- Flip `SUBSCRIBE_MAX_SLOTS` to 8 (3 one-line edits) after a CPU-per-tick measurement.
- Pre-existing drift pinned in a test: 0x19 SIMPLEX has no `COMMAND_TABLE` entry; 0x1F CTRL_SELECT has no handler.
- Regenerate the capability manifest after the Keil build.
