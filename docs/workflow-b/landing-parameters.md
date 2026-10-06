# Landing protocol parameters

All landing parameters are `#define`s in `TASK/StabilizerTask.c` (not protected). They are compile-time values:
none of them can be written at runtime with CMD 0x01. Line numbers are as of f81da19; values as of F8 (2026-10-06).

## How the landing runs

```
WFB: HOVER -> RETURN -> SETTLE 1 s -> DESCEND          (API/wfb_prim.c, protected: propose-only)
Stabilizer: LANDING
  stage 1  sink LAND_VZ_MPS        down to LAND_FAST_ALT
  stage 2  sink LAND_VZ_FAST_MPS   down to contact
  contact  LAND_CONTACT_* or spool  zero xy lean, xy integrators cleared (PX4 style, F8)
  gate     rest | stable-rate | sink-bias saturation | timeout   -> touchdown
  spool    LAND_SPOOL_TICKS        collective held (capped at hover), motors fade linearly to ZERO (F9)
  LANDED   disarm, motors zero
```

## Parameters

| parameter | line | now | what it does | to land faster | less bounce | less drift |
|---|---|---|---|---|---|---|
| `LAND_VZ_MPS` | 75 | 0.60 m/s (F7 0.40) | stage-1 sink speed, from hover height down to `LAND_FAST_ALT` | raise (0.6) | - | - |
| `LAND_FAST_ALT` | 76 | 0.50 m | height where stage 2 starts | lower | raise (longer stage 2 if stage 2 is the slower one) | - |
| `LAND_VZ_FAST_MPS` | 77 | 0.50 m/s (F7 0.70) | stage-2 sink speed: this is the speed at which the gear hits the floor | raise | **lower** (impact energy goes with speed squared) | lower |
| `LAND_MAX_TICKS` | 81 | 3000 (15 s) | safety net: forced disarm if no gate fires | - | - | - |
| `LAND_REST_MARGIN` | 84 | 0.03 m | "on the ground" = height within this of the pre-takeoff rest height | raise (fires earlier) | - | raise (less time sliding) |
| `LAND_SINK_BIAS_MAX` | 85 | 0.40 m/s | ground-effect sink bias at saturation also counts as ground | - | - | - |
| `LAND_REST_CUT_TICKS` | 92 | 10 (50 ms) | how long the rest condition must hold before the spool starts | lower | - | lower |
| `LAND_SPOOL_TICKS` | 99 | 100 (0.5 s; F7 60) | motor fade time after touchdown, then disarm | lower | **raise** (0.5 s = 100) | lower |
| `LAND_REST_MIN` | 105 | 0.05 m | floor for the rest height (the first flight per battery reads 0.00) | - | - | - |
| `LAND_CONTACT_ALT` / `_VZ` / `_TICKS` | 114-116 | 0.15 m / 0.10 m/s / 60 | ground-contact stage: freezes the xy integrators | - | - | see "drift" below |
| `LAND_RATE_SLOW_ALT_M` | 1058 | 0.20 m | below this height the stable-rate threshold widens (LOW applies) | - | - | - |
| `LAND_RATE_THR_LOW` / `_HIGH` | 1059-1060 | 0.08 / 0.02 m/s | \|vz\| that counts as "still": 0.08 below 0.20 m, 0.02 above | raise | - | - |
| `LAND_STABLE_TICKS` | 1061 | 10 (50 ms) | how long the vertical speed must stay still | lower | - | - |
| `LAND_CUT_ALT_M` | 1062 | 0.15 m | the stable-rate gate only works below this height | raise | - | - |
| `LAND_SINK_BIAS_STEP` | 1681 | 0.001 m/s per tick | ground effect: how fast the extra sink grows while stuck | raise | - | - |
| `LAND_SINK_SLOW_VZ` | 1682 | 0.10 m/s | below this vertical speed the drone counts as stuck in ground effect | - | - | - |
| SETTLE time | `API/wfb_prim.c:167-180` | 1 s | pause at the origin before DESCEND (protected: propose-only) | lower | - | - |

## How to change one

1. Edit the `#define` in `TASK/StabilizerTask.c`.
2. Compile check, run from `USER/`, must return rc 0:
   ```
   "/c/Keil_v5/ARM/ARMCC/Bin/armcc.exe" --c99 -c --cpu Cortex-M4.fp -O0 -DSTM32F40_41xxx -DUSE_STDPERIPH_DRIVER -I../USER -I../stm32_lib -I../TASK -I../Global_file -I../BSP -I../API -I../firmware -I../FreeRTOS/include -I../FreeRTOS/portable/RVDS/ARM_CM4F -I/c/Keil_v5/ARM/ARMCC/include -o "$TEMP/st_chk.o" ../TASK/StabilizerTask.c
   ```
3. `bash tools/check.sh` shows `CHECK PASS`.
4. Commit (one change per commit, with the reason).
5. Flash, disarmed: `python -m ground_station.flashtool.rebuild_and_flash --yes`, or Keil. Flashing resets
   every runtime gain to `API/pid.c`.
6. Fly, then `python -m ground_station.analysis.landing_report <session dir>` for the numbers.

## What PX4 and ArduPilot do (read from source / docs on 2026-10-06)

| | PX4 (main) | ArduPilot Copter | ours |
|---|---|---|---|
| descent speed | `MPC_LAND_SPEED` 0.7 m/s below `MPC_LAND_ALT2` 5 m | `LAND_SPD_MS` 0.5 m/s below `LAND_ALT_LOW_M` 10 m | 0.40 to 0.5 m, then 0.70 |
| near-ground slow-down | `MPC_LAND_CRWL` 0.3 m/s below `MPC_LAND_ALT3` 1 m, only with a range sensor | none | none |
| contact test | low thrust (30% of the way from min to hover thrust) and no vertical motion (`LNDMC_Z_VEL_MAX` 0.25 m/s) | motors at lower limit, throttle at min, accel <= 1 m/s^2, \|vz\| < 1 m/s, small attitude error | height at rest height, or \|vz\| still, or sink-bias saturation |
| contact debounce | 1 s split in three stages of 1/3 s (ground contact, maybe landed, landed) | about 1 s | 50 ms |
| on ground contact | position setpoint cleared (no xy correction), thrust commanded to zero, **all integrators reset** | motors to ground idle | xy integrators frozen, xy P and D still active |
| motors after landed | idle, auto-disarm after `COM_DISARM_LAND` 2 s | shut down and disarm | 0.3 s linear fade, then disarm |

Sources: PX4 `src/modules/mc_pos_control/multicopter_takeoff_land_params.yaml`,
`src/modules/land_detector/land_detector_params_mc.yaml`, `MulticopterLandDetector.cpp`,
`MulticopterPositionControl.cpp`, `src/modules/commander/commander_params.yaml`; ArduPilot
`ardupilot.org/copter/docs/land-mode.html`.

The difference that matters for drift: once PX4 sees ground contact it stops correcting position. It clears the
xy setpoint and resets the integrators, so the attitude loop does not tilt the drone to chase an estimate that drifts
while the gear touches. Our contact stage only freezes the integrators, and the xy P and D terms keep tilting the
drone while it is on the ground. That is a code change, not a `#define`.

F8 (2026-10-06) adds that: in contact, or once the spool-down starts, the xy lean command is zero and every xy
integrator is cleared (end of `Pos_Compute`). Before F8 the contact stage never latched in practice: the rest gate
started the spool 0.05 s after contact, before the 60-tick contact debounce.

F9 (2026-10-06) holds the collective during the spool. Before F9 the climb loop kept running with vzDes 0 while
the height estimate sat frozen on the ground at vz -0.33 m/s, so it added thrust just as the skids touched (F7:
motor avg 3085 at contact, 3139 at spool start). Now the spool latches that tick's collective, capped at
`HOVER_THR_FREE`, and only the fade lowers it (PX4 also commands zero thrust on ground contact). Check with
`landing_report`: motor avg at spool start should be no higher than at contact.
