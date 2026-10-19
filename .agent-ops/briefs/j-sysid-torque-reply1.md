Approach approved; proceed and open the PR without further questions unless blocked. Constraints on top:

1. Var ID: do not assume 54. Take the next ID that is unused in `s_var_write` (TASK/AutoflyTask.c) AND in the
   ground-station var catalog / firmware_contract (grep both); add it there too.
2. Target writes are refused unless sysid is idle, in the existing error-code style. Good.
3. Clamp: per-axis amplitude max in a `*_ROW` table per docs/firmware-table-pattern.md, values commented PROPOSED,
   in the same units as u_gyrox/u_gyroy/u_gyroz/Throttle_out. Add d to Throttle_out only if sysid already supports
   a Z / thrust axis; otherwise roll/pitch/yaw only, and say so in the PR body.
4. Target 0 must be bit-exact: branch on the target (no arithmetic added on the target-0 path).
   d = 0 unless running AND airborne; forced to 0 on any trip or land.
5. Telemetry: the new `sysid_cdu` group must go through the existing stream guard / budget logic; update
   ground_station/platform/telemetry_groups.py and any struct-size or group-list test; run those tests (scoped pytest).
6. Host tests only (`python tools/host_tests.py`, all pass); PR body lists every touched file:line.
