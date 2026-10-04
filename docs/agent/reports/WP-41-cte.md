# WP-41 report: API/ files to the pid.c standard (CEO inline, 2026-10-04/05)

Status: DONE for 13 files; 7 `API/` files remain (listed in `docs/firmware-structure.md`). No Keil build, no flash.
Proof per file: `python tools/fw_equiv.py <file>` against HEAD (host gcc -O2, per-function code + data);
`python tools/row_meta.py <file>` for every `*_ROW` table; `bash tools/check.sh` CHECK PASS per batch.

| file | change | proof |
|---|---|---|
| flight_fsm.c | header with transition list, s_enter/s_leave_flight steps | FW-EQUIV OK |
| rc_input.c | header, RC_TUNE_ROW, named raw centre/half-range, Update split | FW-EQUIV OK, row-meta OK |
| imu_update.c | header, IMU_TUNE_ROW, IMU_MG_PER_G, Mahony split | data same; code differs by load order only, bit-exact host differential test |
| gyro_filter.c | header, GYRO_TUNE_ROW, named fallback fs and 2Q | code 5 / data 4 same, row-meta OK |
| Filter.c | header for the empty placeholder unit | no code |
| Accel_Calibartion.c/.h | header; unused new_offset/new_scales/Acce_Unit removed | git grep: 0 readers |
| time_estimate.c/.h | header, parenthesised TIM5 macros, GBK comments to English | FW-EQUIV OK |
| send_prof.c | header, SEND_PROF_MIN_UNSET, s_last_start to file scope | FW-EQUIV OK |
| mrac_math.c | header, projection cases in one comment table | FW-EQUIV OK |
| SINS.c | header (no firmware caller), GBK comments to English, dead commented code removed | code 9 / data 13 same |
| calib.c | header, CAL_TRIM_ROW/CAL_HOT_ROW, s_bg_running to file scope, reject/commit helpers | code 4 / data 2 same, row-meta 8 cells |
| sysid.c | header, SYSID_SAFETY_ROW/SYSID_SHAPE_ROW, named scan constants, multisine design helper | code 9 / data 21 same, row-meta 10 cells |
| ekf.c | header, EKF_NOISE_ROW, section banners, 2x2 helper under Private helpers | code 9 / data 3 same, row-meta 6 cells |

Findings:
- sysid.c: moving the start preconditions into a `return 1` helper changed the -O2 branch layout, so they stay
  inline in SysID_Start; only the multisine design moved out.
- `OBJ/JX_FLY.map` (2026-10-04 14:40) predates ac782ba; it still shows pid.c reading SINS Cos_Yaw. Rebuild before
  using the map for dead-code calls.

PROPOSED (not done, needs a decision):
- Delete SINS.c: no function or global has a caller outside the file. Needs SINS.h split first (pid.h, BSP.c and
  StabilizerTask.h use its typedefs/macros) and a uvprojx edit plus Keil build.
- Remaining files: Ano_OF, GPS, bmi088_driver (fw_equiv fails at base on its inline-asm NOP; use git grep proofs),
  delay, fw_identity, sys, tf_mini_plus.
