# Binding Table: Firmware Published Keys vs UI Expected Keys

This table documents the mismatches between what the ground station dashboard UI expects (legacy spec keys) and what the firmware actually publishes on the telemetry stream (DWARF symbols).

## 1. Mapped Keys (Dashboard aliases)

These are physical quantities that stream properly under their raw DWARF names but which the UI expects under legacy names.

| UI Expected Key | Firmware Published Key (DWARF) | Group |
|-----------------|---------------------------------|-------|
| `status.arm` | `DroneStatus.ARM_Status` | Status flags |
| `status.flymode` | `DroneStatus.FlyMode` | Status flags |
| `status.sbus_lost` | `sbus_lost` | Status flags |
| `status.twc_execute` | `TWC.execute` | Status flags |
| `status.twc_arrived` | `TWC_arrived` | Status flags |
| `status.rc_authority` | `s_authority` | Status flags |
| `status.of_hold` | `g_of_hold_active` | Status flags |
| `status.estimator_ready` | `g_estimator_ready` | Status flags |
| `status.vbat` | `real_voltage` | Battery |
| `status.roll_deg` | `imu_data.rol` | Attitude |
| `status.pitch_deg` | `imu_data.pit` | Attitude |
| `status.yaw_deg` | `imu_data.yaw` | Attitude |
| `mrac.pitch.e` | `mrac_state.pitch.e` | MRAC Error |
| `mrac.pitch.u_ad` | `mrac_state.pitch.u_ad` | MRAC Adaptive Output |
| `mrac.roll.e` | `mrac_state.roll.e` | MRAC Error |
| `mrac.roll.u_ad` | `mrac_state.roll.u_ad` | MRAC Adaptive Output |
| `mrac.yaw.e` | `mrac_state.yaw.e` | MRAC Error |
| `mrac.yaw.u_ad` | `mrac_state.yaw.u_ad` | MRAC Adaptive Output |
| `mrac.z.e` | `mrac_state.z_rate.e` | MRAC Error |
| `mrac.z.u_ad` | `mrac_state.z_rate.u_ad` | MRAC Adaptive Output |
| `mrac.pitch.theta_0` .. `5` | `mrac_state.pitch.Theta[0]` .. `[5]` | MRAC Weights |
| `mrac.roll.theta_0` .. `5` | `mrac_state.roll.Theta[0]` .. `[5]` | MRAC Weights |
| `mrac.yaw.theta_0` .. `5` | `mrac_state.yaw.Theta[0]` .. `[5]` | MRAC Weights |
| `mrac.z.theta_0` .. `5` | `mrac_state.z_rate.Theta[0]` .. `[5]` | MRAC Weights |

*Fix:* `telemetry_adapter.py` restricted aliasing these keys to `slot == 0` only. The fix is to remove `if slot != 0:` so the UI can find these mapped keys when subscribing to any slot (e.g. `slot 1` in presets).

## 2. Unverified / Missing Keys (Legacy / Deprecated)

These keys are expected by various panels but are completely absent from the firmware telemetry stream. They need to be updated in the panel code to use the modern equivalents.

| Expected Key | Found in Panel | Reason / Replacement |
|--------------|----------------|----------------------|
| `ano_of.earth_x` | Time Series | Legacy ANO protocol; replace with `c.earth_x` |
| `ano_of.earth_y` | Time Series | Legacy ANO protocol; replace with `c.earth_y` |
| `ano_of.of_alt_cm` | Time Series | Legacy ANO protocol; replace with `c.altitude` |
| `ahrs.rol` | Time Series, FFT | Deprecated; replace with `c.roll` or `status.roll_deg` |
| `ahrs.pit` | Time Series, FFT | Deprecated; replace with `c.pitch` or `status.pitch_deg` |
| `ahrs.yaw` | Time Series, FFT | Deprecated; replace with `c.yaw` or `status.yaw_deg` |
| `rate.roll` | Flight Status | Deprecated; replace with `c.gyro_x` |
| `rate.pitch` | Flight Status | Deprecated; replace with `c.gyro_y` |
| `rate.yaw` | Flight Status | Deprecated; replace with `c.gyro_z` |
| `status.status_bits` | Flight Status | Use `status.arm`, `status.flymode` directly |
| `ekf.pos_x`, `y`, `z` | EKF Estimator | EKF9 has no position state; requires EKF12 |
| `estimator.filter_status`| EKF Estimator | `Ekf9_t.active` not aliased |
| `estimator.cov_*` | EKF Estimator | Covariance diagonals not aliased |
