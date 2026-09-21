"""Slot-0 subscribe layouts — the firmware's boot-default plus the dashboard frame.

This module lists the DWARF variable paths the host sends on slot 0. Two
layouts live here:

  * ``BOOT_DEFAULT_VARS`` mirrors the firmware's ``Subscribe_BootDefault()``
    in ``API/subscribe.c``. The firmware auto-subscribes these on slot 0 at
    boot; the host re-subscribes the same list to recover the 0x08 schema.
    Divider 20 → 200 / 20 = 10 Hz measured at the nominal 200 Hz Send_Task
    cadence.

  * ``DASHBOARD_FRAME_A_VARS`` is the host-side dashboard layout that
    supersedes the boot-default. It carries everything the dashboard's
    sidebar reads from the legacy Frame A payload — Euler attitude,
    status flags, battery, and the 8 MRAC bars (e/u_ad for the four
    adaptive axes). The mapping into Frame A sidebar keys lives in
    ``ground_station/comm/wifi_bridge.py::_slot0_to_sidebar``.

    Source of truth is ``ground_station/livewatch/manifests.yaml`` under
    ``manifests.dashboard_frame_a`` — keep the two in lockstep. Divider 4
    at the nominal 200 Hz Send_Task cadence gives 50 Hz requested, but the
    MIXED-mode measured cadence is ~80 Hz, so the wire emits 80 / 4 = 20 Hz
    (this is the "host multiplies by 2.5" knob — see
    ``.agent_contracts/2026-09-12-wire-budget/journal.md`` and
    ``.cursor/skills/capture-multislot/SKILL.md``). Once Send_Task runs at
    the nominal 200 Hz in MIXED mode (separate task), bump the divider
    to 10 to keep the wire at 20 Hz.

The host's auto-subscribe (``WifiBridge._request_boot_default_schema``)
defaults to the dashboard layout — that is the only layout the dashboard
cares about. The legacy ``BOOT_DEFAULT_VARS`` list stays here for firmware
mirror and for callers that explicitly want the firmware's 12-var boot
default (e.g. legacy telemetry-only sessions without the dashboard GUI).

Verified against live ELF (2026-08-21 / 2026-09-12):
  - imu_data.rol/pit/yaw: scalar float32 (4 B each)
  - DroneStatus.ARM_Status/FlyMode: scalar uint8/uint8 (read as float32 in wire format)
  - real_voltage: scalar float32
  - mrac_state.*.e/u_ad: scalar float32 (4 B each)
  - status flags (sbus_lost, TWC.*, s_authority, g_of_hold_active,
    g_estimator_ready): scalar u8/u32
  - system_monitor.* fields: all scalar float32
  - xTickCount: scalar uint32
  - UA3RxFrameCnt, UA3TxFrames: scalar uint32
"""
from __future__ import annotations

# Tuple of DWARF paths. All verified against OBJ/JX_FLY.axf with live reads.
# Size must be 1, 2, or 4 — subscribe_validate_range enforces this on firmware.

# ---------------------------------------------------------------------------
# BOOT_DEFAULT_VARS — firmware mirror. Do not extend without updating
# API/subscribe.c Subscribe_BootDefault() in lockstep.
# ---------------------------------------------------------------------------

BOOT_DEFAULT_VARS: tuple[str, ...] = (
    # Attitude: Euler angles from the IMU
    "imu_data.rol",
    "imu_data.pit",
    "imu_data.yaw",
    # Flight status: arm state and mode
    "DroneStatus.ARM_Status",
    "DroneStatus.FlyMode",
    # Battery voltage
    "real_voltage",
    # System health: task counters (increment at each task run)
    "system_monitor.IMUUpdateTask_cnt",
    "system_monitor.stabilizerTask_cnt",
    "system_monitor.USART4_task_cnt",
    # FreeRTOS tick counter (monotonic, ms resolution)
    "xTickCount",
    # USART3 link aliveness: TX and RX frame counters
    "UA3RxFrameCnt",
    "UA3TxFrames",
)

BOOT_DEFAULT_DIVIDER: int = 20   # 200 Hz / 20 = 10 Hz at nominal Send_Task cadence

# ---------------------------------------------------------------------------
# DASHBOARD_FRAME_A_VARS — host-side slot-0 layout for the dashboard sidebar.
# Mirror of ``ground_station/livewatch/manifests.yaml :: dashboard_frame_a``.
# Drives the ``a["status.*"]`` and ``a["mrac.*"]`` sidebar keys via
# ``WifiBridge._slot0_to_sidebar``.
# ---------------------------------------------------------------------------

DASHBOARD_FRAME_A_VARS: tuple[str, ...] = (
    # Attitude (3 vars) -- dashboard attitude display + Euler sidebar
    "imu_data.rol",
    "imu_data.pit",
    "imu_data.yaw",
    # MRAC e + u_ad for the 4 axes the dashboard bar monitor reads.
    # 8 vars * 4 B = 32 B -- the exact size of the legacy Frame A's MRAC block.
    "mrac_state.pitch.e",
    "mrac_state.pitch.u_ad",
    "mrac_state.roll.e",
    "mrac_state.roll.u_ad",
    "mrac_state.yaw.e",
    "mrac_state.yaw.u_ad",
    "mrac_state.z_rate.e",
    "mrac_state.z_rate.u_ad",
    # Status flags (8 vars). Mirrors _decode_frame_a 9-byte status block.
    # TWC is a 36-byte struct on the wire; the subscribe protocol only accepts
    # size 1/2/4, so we read TWC.execute (u32) as a 0/1 flag here. TWC_arrived
    # is a separate scalar u32.
    "DroneStatus.ARM_Status",
    "DroneStatus.FlyMode",
    "sbus_lost",
    "TWC.execute",
    "TWC_arrived",
    "s_authority",
    "g_of_hold_active",
    "g_estimator_ready",
    # Battery voltage
    "real_voltage",
    # Monotonic ms clock -- added so this layout alone satisfies
    # REQUIRED_SYNC_VARS (a multi-slot preset can drive slot 0 with this
    # layout in place of the lean `sync` manifest). Mirrors
    # manifests.yaml :: dashboard_frame_a.
    "xTickCount",
    # ---------------------------------------------------------------------
    # MRAC full theta vector (added S15) — TELEMETRY_SPEC.md and
    # S15-audit.md §2.4 require the full 6-element adaptive-weight vector
    # for each of the 4 axes. Verified DWARF paths against OBJ/JX_FLY.axf
    # (live reads 2026-09-17): each `mrac_state.<axis>.Theta[N]` resolves
    # to a 4-byte float, MAX_NUM_BASIS = NUM_BASIS(4) + INCLUDE_CONTROL(2) = 6.
    # 4 axes × 6 weights = 24 vars * 4 B = 96 B on the wire.
    # ---------------------------------------------------------------------
    "mrac_state.pitch.Theta[0]",
    "mrac_state.pitch.Theta[1]",
    "mrac_state.pitch.Theta[2]",
    "mrac_state.pitch.Theta[3]",
    "mrac_state.pitch.Theta[4]",
    "mrac_state.pitch.Theta[5]",
    "mrac_state.roll.Theta[0]",
    "mrac_state.roll.Theta[1]",
    "mrac_state.roll.Theta[2]",
    "mrac_state.roll.Theta[3]",
    "mrac_state.roll.Theta[4]",
    "mrac_state.roll.Theta[5]",
    "mrac_state.yaw.Theta[0]",
    "mrac_state.yaw.Theta[1]",
    "mrac_state.yaw.Theta[2]",
    "mrac_state.yaw.Theta[3]",
    "mrac_state.yaw.Theta[4]",
    "mrac_state.yaw.Theta[5]",
    "mrac_state.z_rate.Theta[0]",
    "mrac_state.z_rate.Theta[1]",
    "mrac_state.z_rate.Theta[2]",
    "mrac_state.z_rate.Theta[3]",
    "mrac_state.z_rate.Theta[4]",
    "mrac_state.z_rate.Theta[5]",
    # ---------------------------------------------------------------------
    # EKF shadow-mode state (added S15) — TELEMETRY_SPEC.md slot 3 and
    # S15-audit.md §2.4 require per-axis velocity, accel bias and gyro
    # bias. Source is `s_ekf` (Ekf9_t in TASK/send_data.c, static +
    # DWARF-visible) — 9-state body-frame vector:
    #     x[0..2] = v_body  (vel_x, vel_y, vel_z)         m/s
    #     x[3..5] = b_a_body (accel bias x, y, z)         m/s²
    #     x[6..8] = b_g_body (gyro  bias x, y, z)         rad/s
    # The 9-state model has NO POSITION STATE — for ekf.pos_x/y/z the
    # firmware must add either (a) a 12-state position-aware variant, or
    # (b) explicit `ekf.pos_x/y/z` scalar aliases. Until then the
    # position keys remain absent (the sidebar / panels fall back to
    # `s_ekf.x[0..2]` for velocity and a TODO note for position).
    # Verified DWARF size: each element is float32 (4 B).
    # ---------------------------------------------------------------------
    "s_ekf.x[0]",  # ekf.vel_x          v_body[0]  (m/s)
    "s_ekf.x[1]",  # ekf.vel_y          v_body[1]  (m/s)
    "s_ekf.x[2]",  # ekf.vel_z          v_body[2]  (m/s)
    "s_ekf.x[3]",  # ekf.bias_accel_x   b_a_body[0] (m/s²)
    "s_ekf.x[4]",  # ekf.bias_accel_y   b_a_body[1] (m/s²)
    "s_ekf.x[5]",  # ekf.bias_accel_z   b_a_body[2] (m/s²)
    "s_ekf.x[6]",  # ekf.bias_gyro_x    b_g_body[0] (rad/s)
    "s_ekf.x[7]",  # ekf.bias_gyro_y    b_g_body[1] (rad/s)
    "s_ekf.x[8]",  # ekf.bias_gyro_z    b_g_body[2] (rad/s)
    # TODO(firmware): expose ekf.pos_x/y/z — the 9-state EKF has no
    #                 position state. Add either a 12-state variant in
    #                 API/ekf.c or three scalar aliases in the dashboard
    #                 layout. Until then the estimator panel's Position
    #                 section renders "—".
    # TODO(firmware): expose estimator.filter_status — currently in the
    #                 Ekf9_t.active field; needs an aliased scalar for
    #                 the subscribe path.
    # TODO(firmware): expose estimator.cov_* — currently in s_ekf.P[0..N];
    #                 the dashboard wants the 6 diagonal scalars as
    #                 estimator.cov_pxx, _pyy, _pzz, _vxvx, _vyvy, _vzvz.
)

# divider=4 at the MIXED-mode measured 80 Hz Send_Task cadence gives 20 Hz on
# the wire. At the nominal 200 Hz Send_Task cadence this would be 50 Hz -- if
# the firmware-side Send_Task rate fix lands, bump this to 10 to keep the wire
# at 20 Hz. See module docstring for the full rationale.
DASHBOARD_FRAME_A_DIVIDER: int = 4

# ---------------------------------------------------------------------------
# Slot-1 panel extras (2026-09-22 binding-table task, audit "global pattern").
# Every panel that read a Frame B/C key the WiFi link never delivers gets the
# same firmware variable streamed here. They CANNOT join DASHBOARD_FRAME_A_VARS
# on slot 0: the firmware 0x21 stream request caps a slot at
# SUBSCRIBE_MAX_STREAM_RANGES = 62 ranges (API/subscribe.h:189, rejected with
# "E:too many ranges"), and 54 + 25 = 79 > 62 would kill the whole sidebar
# stream. So the service auto-subscribes these 25 on slot 1 instead.
# All DWARF paths verified against the live ELF via /api/symbols
# (2026-09-21); each is the exact symbol the legacy frame builder packs
# (TASK/send_data.c:1090-1130 gyro/earth/alt, :1196-1198 PID,
# flight_fsm.c:7-8 FSM, StabilizerTask.c estimator-mode flags).
# ---------------------------------------------------------------------------
DASHBOARD_PANEL_EXTRA_VARS: tuple[str, ...] = (
    # Flight FSM state + phase — Overview flight-state-machine widget.
    "s_state",
    "flight_phase",
    # Raw body gyro rates (rad/s, BMI088 pre-filter) — gyro stage,
    # pre-flight "IMU publishing" item, estimator raw-IMU group.
    "Gyro_X_Real",
    "Gyro_Y_Real",
    "Gyro_Z_Real",
    # Raw body accel (m/s^2, BMI088) — estimator raw-IMU accel group.
    "Acc_X_Real",
    "Acc_Y_Real",
    "Acc_Z_Real",
    # Optical-flow world position (m) + ANO module altitude (cm) —
    # position-estimate stage, Path panel, estimator altitude.
    "ano_of.earth_x",
    "ano_of.earth_y",
    "ano_of.of_alt_cm",
    # Rate-loop PID feedback and output — block-diagram "Rate filter
    # (flown)" and "Rate controllers" stages (Frame B equivalent).
    "Ctrler.gyroxPID.FB",
    "Ctrler.gyroxPID.U",
    "Ctrler.gyroyPID.FB",
    "Ctrler.gyroyPID.U",
    "Ctrler.gyrozPID.FB",
    "Ctrler.gyrozPID.U",
    # Estimator-mode readback flags — command-panel OF-bias section.
    "g_of_bias_mode",
    "g_of_bias_ema_freeze",
    # GS safety parameter readback — safety-limits panel. Slow-changing,
    # but 6 * 4 B at 16 Hz is negligible on a 91 kB/s wire.
    "gs_max_horizontal_speed_mps",
    "gs_max_vertical_speed_mps",
    "gs_max_pitch_deg",
    "gs_max_roll_deg",
    "gs_throttle_max_pct",
    "gs_throttle_min_pct",
)

# divider=5 at the MIXED-mode measured 80 Hz Send_Task cadence gives 16 Hz on
# the wire (20 Hz at the nominal 100 Hz cadence). Slot-1 frame is
# 25 vars * 4 B + 12 B overhead = 112 B, so ~1.8 kB/s -- about 2% of the
# 91.3 kB/s USART3 wire, on top of slot 0's ~4.6 kB/s.
DASHBOARD_PANEL_EXTRA_DIVIDER: int = 5
