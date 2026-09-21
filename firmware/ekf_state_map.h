/*
 * @file     ekf_state_map.h
 * @brief    EKF state-vector contract — the single source of truth for
 *           naming and layout. Future agents should read this before
 *           adding new telemetry, dashboards, or aliases.
 *
 * @module   firmware/ekf_state_map.h
 * @subsystem comm
 * @depends  API/ekf.h
 * @owns     the documented contract between Ekf9_t fields, the host
 *           manifest, and the dashboard spec keys.
 *
 * @caution  DO NOT edit the layout table below without updating:
 *              - API/ekf.c (the struct itself)
 *              - TASK/send_data.c (frame 0x05 telemetry emission)
 *              - ground_station/livewatch/manifests.yaml (host alias)
 *              - any dashboard plugin reading these keys
 *
 *           The 9 floats are NOT interchangeable. x[0..2] are body-frame
 *           VELOCITIES (m/s), not positions. The state vector is:
 *
 *               x[0] = v_body.x    (body-frame velocity, m/s, +X forward)
 *               x[1] = v_body.y    (body-frame velocity, m/s, +Y left)
 *               x[2] = v_body.z    (body-frame velocity, m/s, +Z down)
 *               x[3] = b_a.x       (accel bias, m/s^2)
 *               x[4] = b_a.y       (accel bias, m/s^2)
 *               x[5] = b_a.z       (accel bias, m/s^2)
 *               x[6] = b_g.x       (gyro bias, rad/s)
 *               x[7] = b_g.y       (gyro bias, rad/s)
 *               x[8] = b_g.z       (gyro bias, rad/s)
 *
 *           The filter is a constant-velocity random-bias model. There
 *           are NO position states. `ekf.pos_x/y/z` does not exist and
 *           will not exist — the position estimate lives in `ano_of`
 *           (optical-flow integrated), which is a separate estimator
 *           and the active control path. The EKF is shadow-mode.
 *
 * @see      API/ekf.h for the Ekf9_t struct definition.
 * @see      TASK/send_data.c:Send_Groundstation_Telemetry_UART4 for
 *           how the state is packed into frame 0x05 (s16 at 1 mm/s for
 *           v_body, 1e-3 for P/NIS/K, 1 mg / 1e-4 rad/s for biases).
 * @see      ground_station/livewatch/manifests.yaml:dashboard_frame_a
 *           for the host-side alias to dashboard spec keys.
 *
 * ====================================================================
 * Why this header exists (2026-09-18)
 * ====================================================================
 *
 * The PLANNING_PROMPT listed "Add ekf.pos_x/y/z scalar aliases in
 * TASK/send_data.c" as a firmware gap. The audit on 2026-09-18 found
 * the gap was a misreading of the EKF struct: there is no position
 * state to alias, and the names that already exist on the wire
 * (`s_ekf.x[0..2]`, dashboard-mapped to `ekf.vel_x/y/z`) are the
 * correct names. Adding `pos_*` aliases would have created a phantom
 * telemetry surface that contradicts the model.
 *
 * This header is the forward-looking fix: it documents the contract so
 * the next agent who reads PLANNING_PROMPT sees the misreading already
 * noted, and has a single place to verify what `s_ekf.x[N]` actually
 * represents before adding telemetry or writing a plugin that reads
 * these fields.
 *
 * No extern declarations or pointer aliases are provided — the host
 * already resolves `s_ekf.x[N]` via DWARF, and the manifest maps those
 * paths to the dashboard spec keys. Adding another indirection would
 * duplicate the contract and create a second source of truth.
 */

#ifndef PLATFORM_EKF_STATE_MAP_H
#define PLATFORM_EKF_STATE_MAP_H

/* Intentionally empty. The contract lives in the file-level comment
 * above. See @caution and @see for the cross-reference list. */

#endif /* PLATFORM_EKF_STATE_MAP_H */
