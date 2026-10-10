# Logging option f: estimator error signals (APPROVED, presets edited)

Status: APPROVED (operator "go ahead", 2026-10-10). The list and rates below were added to presets vp15 to vp24
(drift slot 50 Hz, status slot 25 Hz). Re-checked with `plan_budget` after the edit: totals match the table below,
all OK. Nothing is recorded yet; recording starts only on an operator request at the lab.

Estimator in the loop: the 8-state optical-flow EKF `s_ekf_of` (`TASK/StabilizerTask.c`, `API/ekf_of.h`),
velocity feedback on at boot (`g_ekf_of_vel_fb = 1`). State `x = [pos_x, vel_x, bof_x, pos_y, vel_y, bof_y, ba_x, ba_y]`.
Velocities are already logged as `Ctrler.locx/ysPID.FB`; this adds what the filter itself sees.

## Proposed additions (same for vp15 to vp24)

| slot (rate) | variable | why |
|---|---|---|
| drift (50 Hz) | `s_ekf_of.innov_x`, `s_ekf_of.innov_y` | OF innovation: the estimator's own error signal; OF frames are 50 Hz (ekf_of.h) |
| drift (50 Hz) | `s_ekf_of.x[2]`, `s_ekf_of.x[5]` | flow bias states bof_x, bof_y: slow drift the filter is absorbing |
| drift (50 Hz) | `s_ekf_of.x[6]`, `s_ekf_of.x[7]` | accel bias states ba_x, ba_y |
| status (25 Hz) | `s_ekf_of.rej_x`, `rej_y`, `rej_run_x`, `rej_run_y` | gated-out OF samples (total and current run): when the filter stops trusting flow |
| status (25 Hz) | `s_ekf_of.P[0][5]`, `s_ekf_of.P[1][5]` | velocity variance: filter confidence, jumps on gate-lockout recovery |
| status (25 Hz) | `g_ekf_of_vel_fb`, `g_ekf_of_shadow` | which estimator feeds the loop |

Not proposed for today: x/y MRAC signals (`mrac_state.x_vel/y_vel.e`, `u_ad`, `xm`). The x/y layers are off at
boot and `MRAC_Control` does not update them while off (mrac.c, axis_enable_x/y), so they would log zeros. Add them
to the preset of the first x/y flight. Their g_tlm copies already exist (dd7b22f).

## Budget (computed by `vofa_studio.core.plan_budget` against the ELF on disk = board image of 11:04)

| family | now B/s | with f B/s | limit B/s | ranges per slot with f (max 62) |
|---|---|---|---|---|
| vp15 | 60450 | 63850 | 87552 | 22 / 3 / 19 / 22 |
| vp16 to vp19 | 61250 | 64650 | 87552 | 22 / 5 / 23 / 22 |
| vp20 to vp24 | 69650 | 73050 | 87552 | 22 / 3 / 19 / 23 |

All variables resolve. `g_land_gate_sat_n` and `g_land_gate_sat_max_s` (status slot, 06f85f4) do not resolve
before the lab flash, because build-only runs restore the board-matching ELF (`flashtool/artifact_custody.py`);
they were left out of this check and resolve after the flash.
