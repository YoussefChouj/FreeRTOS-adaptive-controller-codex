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
)

# divider=4 at the MIXED-mode measured 80 Hz Send_Task cadence gives 20 Hz on
# the wire. At the nominal 200 Hz Send_Task cadence this would be 50 Hz -- if
# the firmware-side Send_Task rate fix lands, bump this to 10 to keep the wire
# at 20 Hz. See module docstring for the full rationale.
DASHBOARD_FRAME_A_DIVIDER: int = 4
