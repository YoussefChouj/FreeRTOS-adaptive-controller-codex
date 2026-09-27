# Cost table: adaptive augmentation candidates vs FwPID baseline

Assumptions: Cortex-M4F @ 168 MHz, FPU. VADD/VMUL/VMLA 1 cyc, VDIV/VSQRT 14 cyc,
expf/tanhf ~100 cyc (newlib-nano). Budget for 2 axes (roll+pitch), 200 Hz.

## Per-axis resource table

| Controller | State floats (B) | Param floats | add/sub | mul | div | sqrt | transcend. |
|---|---|---|---|---|---|---|---|
| FwPID (baseline) | 0 (0) | 0 | 0 | 0 | 0 | 0 | 0 |
| L1 | 3 (12) | 5 (20) | 8 | 5 | 2 | 0 | 0 |
| MRAC_S6 | 9 (36) | 1 (4) | 24 | 25 | 2 | 1 | 1 tanh |

## Cycle/time budget (2 axes, 5 ms period)

| Controller | cyc/axis | cyc 2ax | us @ 168 MHz | % of 5 ms |
|---|---|---|---|---|
| FwPID | 0 | 0 | 0.0 | 0.0% |
| L1 | 41 | 82 | 0.49 | 0.010% |
| MRAC_S6 | 191 | 382 | 2.27 | 0.045% |

L1: 8(1)+5(1)+2(14)=41. MRAC_S6: 24+25+28+14+100=191. No FMA; actual ~30% less.

## Stability / saturation / PID fallback

**L1**: L1 adaptive control (Hovakimyan & Cao 2010). Low-pass filter `l1_f_hz`
separates adaptation from plant bandwidth; `l1_clip` hard-limits u_ad. Mixer
saturation: u_ad clipped independently, no wind-up. PID fallback: `l1_clip=0`.
Freeze on ground: reset u_ad=0, w_hat=gyro on arm (cf. gyrozPID.SumE=0 fix,
StabilizerTask.c:1025).

**MRAC_S6**: Lyapunov via normalised gradient + sigma-mod (Ioannou & Sun ch.8).
`SIGMA=0.01` leak bounds drift; `TH_MAX=3` norm clamp is hard safety limit;
`OMEGA_U=25` LPF prevents chatter. Mixer saturation: Th bounded by TH_MAX +
sigma-mod. PID fallback: `gamma=0` (Th stays zero, u_ad decays). Freeze on
ground: hold gamma=0 until armed+airborne; pre-arm reset zeros Th and u_ad,
preventing FLIGHT8 pattern (U=650 wound up before takeoff).

## Fit to Controller_Update(axis, u_nom)

**L1** needs only `gyro_axis` beyond u_nom:
- `Ctrler.gyroxPID.FB` (StabilizerTask.c:621), `.gyroyPID.FB` (:620)
- All params are precomputed constants. Integration: trivial.

**MRAC_S6** extra inputs (all already acquired in `MRAC_Control`, mrac.c:637-664):
- `pm_meas`: gyroPID.FB * DEG2RAD (mrac.c:637-639)
- `phm_meas`: attitude angle from Mahony (existing mrac_state.*.x_angle)
- `pmk, phmk`: reference model — existing firmware ref model (mrac.c:612-615)
- `q_cross * r_cross`: already computed (mrac.c:648-649)
- `gamma`: stored in mrac_config_*.gamma (flash or GS-writable)
Integration: replace MRAC_UpdateAxis body or add new ctrl_ops_t slot; reads only.
