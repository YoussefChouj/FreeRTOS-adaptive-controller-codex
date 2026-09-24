"""System capability manifest generator.

Produces a single machine-readable manifest describing the whole system:
  - Firmware symbols (from DWARF in OBJ/JX_FLY.axf via SymbolResolver)
  - Commands (from COMMAND_TABLE in ground_station/platform/firmware_contract.py)
  - Telemetry keys published (from ground_station/comm/wifi_bridge.py)
  - Panels (from docs/dashboard-platform/shell/plugins/ and index.html)
  - API routes (from _ROUTE_MAP in ground_station/service/api.py)

Recorded with ELF identity and explicit staleness caveat.
Emits to docs/dashboard-platform/capability_manifest.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "dashboard-platform" / "capability_manifest.json"
ELF_PATH = ROOT / "OBJ" / "JX_FLY.axf"
PLUGINS_DIR = ROOT / "docs" / "dashboard-platform" / "shell" / "plugins"
INDEX_HTML = ROOT / "docs" / "dashboard-platform" / "shell" / "index.html"

STALENESS_CAVEAT = (
    "OBJ/ holds both current and stale build artifacts. A symbol in the ELF "
    "reflects the last build, which may not match what is currently flashed "
    "on the hardware. This manifest describes build artifacts and host decoder "
    "contracts; it does not assert that this build is currently running on the "
    "aircraft."
)


def get_elf_identity() -> dict[str, Any]:
    """Inspect OBJ/JX_FLY.axf to record its build identity."""
    if not ELF_PATH.exists():
        return {
            "elf_path": str(ELF_PATH.relative_to(ROOT)),
            "exists": False,
            "elf_sha256": None,
            "size_bytes": 0,
            "caveat": STALENESS_CAVEAT,
        }
    data = ELF_PATH.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    return {
        "elf_path": str(ELF_PATH.relative_to(ROOT)).replace("\\", "/"),
        "exists": True,
        "elf_sha256": sha256,
        "size_bytes": len(data),
        "caveat": STALENESS_CAVEAT,
    }


def get_firmware_symbols() -> dict[str, Any]:
    """Extract DWARF symbols using the existing SymbolResolver (same as /api/symbols)."""
    identity = get_elf_identity()
    if not identity["exists"]:
        return {
            "source": "unavailable",
            "elf_path": identity["elf_path"],
            "elf_sha256": None,
            "count": 0,
            "names": [],
            "error": "ELF file not found",
        }
    try:
        from ground_station.livewatch.symbols import SymbolResolver
        resolver = SymbolResolver(str(ELF_PATH))
        names = sorted(resolver.names())
        return {
            "source": "dwarf",
            "elf_path": identity["elf_path"],
            "elf_sha256": identity["elf_sha256"],
            "count": len(names),
            "names": names,
        }
    except Exception as exc:
        return {
            "source": "error",
            "elf_path": identity["elf_path"],
            "elf_sha256": identity["elf_sha256"],
            "count": 0,
            "names": [],
            "error": str(exc),
        }


def get_commands() -> dict[str, Any]:
    """Extract command specifications from firmware_contract.COMMAND_TABLE."""
    from ground_station.platform.firmware_contract import COMMAND_TABLE

    commands_dict: dict[str, Any] = {}
    for cmd_id in sorted(COMMAND_TABLE.keys()):
        cmd = COMMAND_TABLE[cmd_id]
        key = f"0x{cmd.id:02X}"
        params = [
            {
                "index": p.index,
                "name": p.name,
                "unit": p.unit,
                "min_val": p.min_val,
                "max_val": p.max_val,
                "range": [p.min_val, p.max_val] if (p.min_val is not None or p.max_val is not None) else None,
                "description": p.description,
                "symbol": p.symbol,
            }
            for p in cmd.params
        ]
        commands_dict[key] = {
            "id": cmd.id,
            "id_hex": key,
            "name": cmd.name,
            "description": cmd.description,
            "parameters": params,
            "safety_class": {
                "danger_level": cmd.safety.danger_level,
                "description": cmd.safety.description,
            },
            "preconditions": {
                "requires_disarmed": cmd.safety.requires_disarmed,
                "requires_armed": cmd.safety.requires_armed,
                "requires_sdk_mode": cmd.safety.requires_sdk_mode,
                "requires_ground_idle": cmd.safety.requires_ground_idle,
            },
            "returns_ack": cmd.returns_ack,
            "applied_async": cmd.applied_async,
            "notes": cmd.notes,
        }

    return {
        "source": "ground_station/platform/firmware_contract.py",
        "count": len(commands_dict),
        "commands": commands_dict,
    }


def get_telemetry_keys() -> dict[str, Any]:
    """Derive telemetry keys actually published by ground_station/comm/wifi_bridge.py.

    Honesty requirement:
      Distinguish between statically verified frame decoders, slot-0 mapped
      sidebar keys, and dynamically subscribed slot channels. Note unverified
      keys that panels attempt to read but are not published.
    """
    # 1. Frame A / slot-0 sidebar mapped keys (WifiBridge._slot0_to_sidebar)
    frame_a_sidebar = [
        "status.roll_deg",
        "status.pitch_deg",
        "status.yaw_deg",
        "status.arm",
        "status.flymode",
        "status.sbus_lost",
        "status.twc_execute",
        "status.twc_arrived",
        "status.rc_authority",
        "status.of_hold",
        "status.estimator_ready",
        "status.vbat",
        "mrac.pitch.e",
        "mrac.pitch.u_ad",
        "mrac.roll.e",
        "mrac.roll.u_ad",
        "mrac.yaw.e",
        "mrac.yaw.u_ad",
        "mrac.z.e",
        "mrac.z.u_ad",
        "ekf.vel_x",
        "ekf.vel_y",
        "ekf.vel_z",
        "ekf.bias_accel_x",
        "ekf.bias_accel_y",
        "ekf.bias_accel_z",
        "ekf.bias_gyro_x",
        "ekf.bias_gyro_y",
        "ekf.bias_gyro_z",
    ]

    # 2. Raw DWARF paths published directly on slot 0 (DASHBOARD_FRAME_A_VARS & BOOT_DEFAULT_VARS)
    from ground_station.comm.boot_default_layout import DASHBOARD_FRAME_A_VARS, BOOT_DEFAULT_VARS
    frame_a_raw_vars = sorted(set(DASHBOARD_FRAME_A_VARS) | set(BOOT_DEFAULT_VARS))

    # 3. Frame B keys (wifi_bridge.py::_decode_frame_b)
    # MRAC 4 axes x (theta_0..theta_N-1, u_nom, xm)
    frame_b_keys = []
    for axis in ["pitch", "roll", "yaw", "z"]:
        for i in range(6):  # MAX_NB default up to 6
            frame_b_keys.append(f"mrac.{axis}.theta_{i}")
        frame_b_keys.append(f"mrac.{axis}.u_nom")
        frame_b_keys.append(f"mrac.{axis}.xm")
    # PID 12 loops x (FB, Des, U)
    pid_loops = [
        "pitch", "roll", "yaw", "gyrox", "gyroy", "gyroz",
        "z_rate", "locx", "locy", "z_pos", "locxs", "locys"
    ]
    for loop in pid_loops:
        frame_b_keys.extend([f"pid.{loop}.FB", f"pid.{loop}.Des", f"pid.{loop}.U"])
    # Path tail & status
    frame_b_keys.extend([
        "status.vbat",
        "status.of_hold",
        "status.estimator_ready",
        "path.active_path_mode",
        "path.twc_target_x",
        "path.twc_target_y",
        "path.twc_target_z",
        "path.sinusoid_t_elapsed",
        "path.circle_theta",
        "path.twc_arrived",
    ])

    # 4. Frame C keys (wifi_bridge.py::_decode_frame_c)
    frame_c_keys = [
        "c.roll", "c.pitch", "c.yaw",
        "c.gyro_x", "c.gyro_y", "c.gyro_z",
        "c.earth_x", "c.earth_y", "c.altitude",
        "c.rpm", "c.seq",
        "motor.rpm_0", "motor.rpm_1", "motor.rpm_2", "motor.rpm_3",
    ]

    # 5. Frame ID keys (wifi_bridge.py::_decode_frame_id)
    frame_id_keys = [
        "id.counter",
        "status.arm",
        "status.mode",
        "status.flymode",
        "status.rc_authority",
        "status.of_hold",
        "status.estimator_ready",
        "status.sysid_state",
    ]

    # 6. MAVLink custom messages (wifi_bridge.py::_decode_mavlink_*)
    mavlink_keys = [
        # MSG 10001: MRAC_WEIGHTS
        "mav.time_usec",
        # MSG 10002: EKF_STATES
        "ekf.vel_body_x", "ekf.vel_body_y", "ekf.vel_body_z",
        "ekf.accel_bias_x", "ekf.accel_bias_y", "ekf.accel_bias_z",
        "ekf.gyro_bias_x", "ekf.gyro_bias_y", "ekf.gyro_bias_z",
        "ekf.roll_rad", "ekf.pitch_rad", "ekf.yaw_rad",
        # MSG 10003: CONTROL_DEBUG
        "ctrl.fsm_state", "ctrl.active_path",
        "ctrl.target_x", "ctrl.target_y", "ctrl.target_z",
        "ctrl.pitch.e", "ctrl.pitch.u_ad", "ctrl.pitch.r",
        "ctrl.roll.e", "ctrl.roll.u_ad", "ctrl.roll.r",
        "ctrl.yaw.e", "ctrl.yaw.u_ad", "ctrl.yaw.r",
        "ctrl.z.e", "ctrl.z.u_ad", "ctrl.z.r",
    ]

    # 7. JustFloat 16-byte attitude fallback
    justfloat_keys = [
        "status.roll_deg",
        "status.pitch_deg",
        "status.yaw_deg",
    ]

    # 8. Subscribe stream metadata (wifi_bridge.py::_decode_stream_frame)
    subscribe_stream_metadata = [
        "slot{slot}.t_ms",
        "slot{slot}.seq",
        "slot{slot}.received",
        "slot{slot}.dropped",
        "slot{slot}.loss_pct",
        "slot{slot}.crc_errors",
    ]

    # Combine all verified published keys (excluding parameterized templates)
    all_published = sorted(set(
        frame_a_sidebar + frame_a_raw_vars + frame_b_keys +
        frame_c_keys + frame_id_keys + mavlink_keys + justfloat_keys
    ))

    # Known phantom / unverified keys read by dashboard panels that are NOT published
    unverified_keys_in_panels = [
        {
            "key": "ano_of.earth_x",
            "panels": ["Time Series"],
            "status": "not_published",
            "reason": "Legacy ANO optical flow module protocol; no firmware publisher on USART3/UART4",
        },
        {
            "key": "ano_of.earth_y",
            "panels": ["Time Series"],
            "status": "not_published",
            "reason": "Legacy ANO optical flow module protocol; no firmware publisher on USART3/UART4",
        },
        {
            "key": "ano_of.of_alt_cm",
            "panels": ["Time Series"],
            "status": "not_published",
            "reason": "Legacy ANO optical flow module protocol; no firmware publisher on USART3/UART4",
        },
        {
            "key": "ano_of.of_alt_cm_m",
            "panels": ["Time Series"],
            "status": "not_published",
            "reason": "Legacy ANO optical flow module protocol; no firmware publisher on USART3/UART4",
        },
        {
            "key": "ahrs.rol",
            "panels": ["Time Series", "FFT Spectrum"],
            "status": "not_published",
            "reason": "Deprecated key; actual Euler attitude published under status.roll_deg or c.roll",
        },
        {
            "key": "ahrs.pit",
            "panels": ["Time Series", "FFT Spectrum"],
            "status": "not_published",
            "reason": "Deprecated key; actual Euler attitude published under status.pitch_deg or c.pitch",
        },
        {
            "key": "ahrs.yaw",
            "panels": ["Time Series", "FFT Spectrum"],
            "status": "not_published",
            "reason": "Deprecated key; actual Euler attitude published under status.yaw_deg or c.yaw",
        },
        {
            "key": "rate.roll",
            "panels": ["Flight Status"],
            "status": "not_published",
            "reason": "Deprecated key; actual body rates published under c.gyro_x",
        },
        {
            "key": "rate.pitch",
            "panels": ["Flight Status"],
            "status": "not_published",
            "reason": "Deprecated key; actual body rates published under c.gyro_y",
        },
        {
            "key": "rate.yaw",
            "panels": ["Flight Status"],
            "status": "not_published",
            "reason": "Deprecated key; actual body rates published under c.gyro_z",
        },
        {
            "key": "status.status_bits",
            "panels": ["Flight Status"],
            "status": "not_published",
            "reason": "Never packed in wifi_bridge decoders; individual flags published as status.arm, status.flymode, etc.",
        },
        {
            "key": "ekf.pos_x",
            "panels": ["EKF Estimator", "Session Replay"],
            "status": "not_published",
            "reason": "9-state EKF (Ekf9_t) has no position state; requires 12-state EKF firmware update",
        },
        {
            "key": "ekf.pos_y",
            "panels": ["EKF Estimator"],
            "status": "not_published",
            "reason": "9-state EKF (Ekf9_t) has no position state; requires 12-state EKF firmware update",
        },
        {
            "key": "ekf.pos_z",
            "panels": ["EKF Estimator"],
            "status": "not_published",
            "reason": "9-state EKF (Ekf9_t) has no position state; requires 12-state EKF firmware update",
        },
        {
            "key": "estimator.filter_status",
            "panels": ["EKF Estimator"],
            "status": "not_published",
            "reason": "Lives in Ekf9_t.active; not aliased on the subscribe path",
        },
        {
            "key": "estimator.cov_*",
            "panels": ["EKF Estimator"],
            "status": "not_published",
            "reason": "Lives in s_ekf.P[0..N]; covariance diagonals not aliased on subscribe path",
        },
    ]

    return {
        "source": "ground_station/comm/wifi_bridge.py",
        "derivation_method": "static_analysis_of_decoders_and_layouts",
        "derivation_note": (
            "Keys are derived from wifi_bridge.py frame decoders (_decode_frame_a, "
            "_decode_frame_b, _decode_frame_c, _decode_frame_id, _decode_mavlink_*, "
            "_slot0_to_sidebar) and boot_default_layout.py. Dynamic slot subscriptions "
            "(0x21 requests) publish variables prefixed with 'slot{N}.' using requested "
            "DWARF names or positional fallbacks 'ch{N}.{idx}'."
        ),
        "frame_decoders": {
            "frame_a_sidebar": {
                "tag": "a",
                "description": "Attitude, MRAC error/adaptive-effort, battery, and status flags mapped from slot-0",
                "count": len(frame_a_sidebar),
                "keys": frame_a_sidebar,
            },
            "frame_a_raw_vars": {
                "tag": "a",
                "description": "Raw DWARF symbol paths emitted directly on slot 0",
                "count": len(frame_a_raw_vars),
                "keys": frame_a_raw_vars,
            },
            "frame_b": {
                "tag": "b",
                "description": "MRAC weights vector, PID loop (FB/Des/U) values, and path telemetry",
                "count": len(frame_b_keys),
                "keys": frame_b_keys,
            },
            "frame_c": {
                "tag": "c",
                "description": "50 Hz body rates, attitude, earth position, altitude, and motor RPMs",
                "count": len(frame_c_keys),
                "keys": frame_c_keys,
            },
            "frame_id": {
                "tag": "id",
                "description": "100 Hz counter and flight status flags",
                "count": len(frame_id_keys),
                "keys": frame_id_keys,
            },
            "mavlink": {
                "tags": ["mav_w", "mav_e", "mav_c"],
                "description": "MAVLink v1.0 custom message decoders (MRAC, EKF, Control debug)",
                "count": len(mavlink_keys),
                "keys": mavlink_keys,
            },
            "justfloat_fallback": {
                "tag": "a",
                "description": "16-byte JustFloat attitude fallback when no stream is active",
                "count": len(justfloat_keys),
                "keys": justfloat_keys,
            },
            "subscribe_stream_metadata": {
                "tag": "s{slot}",
                "description": "Per-slot stream metadata embedded in JSON payload for each subscribe slot (0..3)",
                "count": len(subscribe_stream_metadata),
                "keys": subscribe_stream_metadata,
            },
        },
        "verified_published_keys_total": len(all_published),
        "verified_published_keys": all_published,
        "unverified_keys_in_panels": unverified_keys_in_panels,
    }


def get_panels() -> list[dict[str, Any]]:
    """Inventory all dashboard plugins, the keys they read, and the tabs they live on."""
    # Base metadata mapping from index.html PANEL_META
    panel_meta_defaults = {
        "Flight Status": {"workspace": "control", "gates": []},
        "PID Gains": {"workspace": "control", "gates": []},
        "What changed": {"workspace": "diagnostics", "gates": []},
        "Safety Limits": {"workspace": "control", "gates": []},
        "Command Panel": {"workspace": "control", "gates": ["connected", "disarmed"]},
        "Time Series": {"workspace": "telemetry", "gates": []},
        "MRAC Controller": {"workspace": "mrac", "gates": []},
        "EKF Estimator": {"workspace": "estimator", "gates": []},
        "Telemetry Explorer": {"workspace": "telemetry", "gates": []},
        "Slot Manager": {"workspace": "telemetry", "gates": []},
        "Bandwidth Manager": {"workspace": "telemetry", "gates": []},
        "FFT Spectrum": {"workspace": "telemetry", "gates": []},
        "Experiment Runtime": {"workspace": "experiments", "gates": []},
        "Path Planning": {"workspace": "paths", "gates": []},
        "Motor Bench": {"workspace": "bench", "gates": ["connected", "disarmed"]},
        "Session Replay": {"workspace": "replay", "gates": []},
        "RTOS Resources": {"workspace": "diagnostics", "gates": []},
        "Firmware Resource Map": {"workspace": "diagnostics", "gates": []},
        "Terminal":              {"workspace": "terminal", "gates": []},
    }

    plugin_files = [
        ("overview-panel.js", "System Overview", ["overview"], [], "PLC-style HMI overview: alarm banner + signal-chain mimic diagram"),
        ("status-panel.js", "Flight Status", ["control"], [], "Flight status, arming state, flight mode, battery, and safety flags"),
        ("mrac-panel.js", "MRAC Controller", ["mrac"], [], "MRAC tracking error, adaptive effort, and 6-basis weight vector"),
        ("estimator-panel.js", "EKF Estimator", ["estimator"], [], "EKF state estimation (velocity, sensor biases, covariance)"),
        ("resource-panel.js", "RTOS Resources", ["diagnostics"], [], "FreeRTOS queues, stack high-water marks, task execution counters"),
        ("safety-panel.js", "Safety Limits", ["control"], [], "Ground station safety limits and speed/angle parameter adjustments"),
        ("telemetry-explorer-panel.js", "Telemetry Explorer", ["telemetry"], [], "Live key-value telemetry explorer and schema monitor"),
        ("command-panel.js", "Command Panel", ["control"], ["connected", "disarmed"], "Command dispatch gateway and parameter configuration"),
        ("experiment-panel.js", "Experiment Runtime", ["experiments"], [], "Experiment runner, lifecycle management, and abort control"),
        ("motor-bench-panel.js", "Motor Bench", ["bench"], ["connected", "disarmed"], "Motor testing bench, RPM monitoring, and throttle safety interlock"),
        ("time-series-panel.js", "Time Series", ["telemetry"], [], "Real-time multi-variable time-series plot with bounded ring buffer"),
        ("fft-panel.js", "FFT Spectrum", ["telemetry"], [], "Fast Fourier Transform frequency spectrum visualization"),
        ("bandwidth-panel.js", "Bandwidth Manager", ["telemetry"], [], "Telemetry slot bandwidth, packet loss, and link budget monitor"),
        ("replay-panel.js", "Session Replay", ["replay"], [], "Historical flight session replay and CSV telemetry export"),
        ("path-panel.js", "Path Planning", ["paths"], [], "Waypoints, circle/sinusoid path tracking visualization"),
        ("slot-manager-panel.js", "Slot Manager", ["telemetry"], [], "Telemetry subscribe slot configuration and DWARF variable picker"),
        ("resource-map-panel.js", "Firmware Resource Map", ["diagnostics"], [], "RTOS task memory regions, UART ownership, and subscribe link budgets"),
        ("terminal-panel.js", "Terminal", ["terminal"], [], "Terminal PTY session via WebSocket (xterm.js)"),
    ]

    # Specific telemetry keys read per plugin
    keys_read_map = {
        "overview-panel.js": [
            "c.altitude", "c.earth_x", "c.earth_y",
            "c.gyro_x", "c.gyro_y", "c.gyro_z",
            "c.pitch", "c.roll", "c.yaw",
            "ekf.bias_gyro_x", "ekf.bias_gyro_y", "ekf.bias_gyro_z",
            "ekf.vel_x", "ekf.vel_y", "ekf.vel_z",
            "motor.rpm_0", "motor.rpm_1", "motor.rpm_2", "motor.rpm_3",
            "mrac.pitch.e", "mrac.pitch.u_ad", "mrac.roll.e", "mrac.roll.u_ad",
            "pid.gyrox.FB", "pid.gyrox.U", "pid.gyroy.FB", "pid.gyroy.U", "pid.gyroz.FB", "pid.gyroz.U",
            "status.arm", "status.estimator_ready", "status.flymode", "status.rc_authority", "status.sbus_lost", "status.vbat",
        ],
        "status-panel.js": [
            "c.gyro_x", "c.gyro_y", "c.gyro_z",
            "rate.pitch", "rate.roll", "rate.yaw",
            "slot0.ch0.11",
            "status.arm", "status.estimator_ready", "status.flymode", "status.mode",
            "status.of_hold", "status.rc_authority", "status.sbus_lost", "status.status_bits",
            "status.twc_arrived", "status.twc_execute", "status.vbat",
        ],
        "mrac-panel.js": [
            "ch0", "ch1", "ch10",
            "mrac.pitch.e", "mrac.pitch.theta_0", "mrac.pitch.theta_1", "mrac.pitch.theta_2", "mrac.pitch.theta_3", "mrac.pitch.theta_4", "mrac.pitch.theta_5", "mrac.pitch.u_ad",
            "mrac.roll.e", "mrac.roll.theta_0", "mrac.roll.theta_1", "mrac.roll.theta_2", "mrac.roll.theta_3", "mrac.roll.theta_4", "mrac.roll.theta_5", "mrac.roll.u_ad",
            "mrac.yaw.e", "mrac.yaw.theta_0", "mrac.yaw.theta_1", "mrac.yaw.theta_2", "mrac.yaw.theta_3", "mrac.yaw.theta_4", "mrac.yaw.theta_5", "mrac.yaw.u_ad",
            "mrac.z.e", "mrac.z.theta_0", "mrac.z.theta_1", "mrac.z.theta_2", "mrac.z.theta_3", "mrac.z.theta_4", "mrac.z.theta_5", "mrac.z.u_ad",
            "slot0.ch0.0", "slot0.ch0.1", "slot0.ch0.10",
        ],
        "estimator-panel.js": [
            "ekf.bias_accel_x", "ekf.bias_accel_y", "ekf.bias_accel_z",
            "ekf.bias_gyro_x", "ekf.bias_gyro_y", "ekf.bias_gyro_z",
            "ekf.pos_x", "ekf.pos_y", "ekf.pos_z",
            "ekf.vel_x", "ekf.vel_y", "ekf.vel_z",
            "estimator.cov_pxx", "estimator.cov_pyy", "estimator.cov_pzz",
            "estimator.cov_vxvx", "estimator.cov_vyvy", "estimator.cov_vzvz",
            "estimator.filter_status",
            "slot0.ch0.0", "slot0.ch0.1", "slot0.ch0.2", "slot0.ch0.10",
        ],
        "resource-panel.js": [
            "rtos.cmd_queue_depth", "rtos.cmd_queue_max", "rtos.dma_busy",
            "rtos.heap_free_bytes", "rtos.queue_depth", "rtos.scheduler_tick_count",
            "rtos.send_task_ticks", "rtos.usart3_tx_bytes", "rtos.usart3_tx_count", "rtos.usart3_tx_drops",
        ],
        "safety-panel.js": [
            "gs_max_horizontal_speed_mps", "gs_max_pitch_deg", "gs_max_roll_deg", "gs_max_vertical_speed_mps",
            "safety.gs_max_horizontal_speed_mps", "safety.gs_max_pitch_deg", "safety.gs_max_roll_deg", "safety.gs_max_vertical_speed_mps",
        ],
        "telemetry-explorer-panel.js": [
            "*"  # Auto-discovers all keys dynamically
        ],
        "command-panel.js": [
            "DroneStatus.ARM_Status", "slot1.g_of_bias_ema_freeze", "slot1.g_of_bias_mode",
            "status.arm", "status.rc_authority",
        ],
        "experiment-panel.js": [
            "safety.gs_max_horizontal_speed_mps",
        ],
        "motor-bench-panel.js": [
            "motor.motor_rpm_0", "motor.motor_rpm_1", "motor.motor_rpm_2", "motor.motor_rpm_3",
            "motor.rpm_0", "motor.rpm_1", "motor.rpm_2", "motor.rpm_3",
            "rpm.mot0", "rpm.mot1", "rpm.mot2", "rpm.mot3", "status.arm",
        ],
        "time-series-panel.js": [
            "ahrs.pit", "ahrs.rol", "ahrs.yaw",
            "ano_of.earth_x", "ano_of.earth_y", "ano_of.of_alt_cm", "ano_of.of_alt_cm_m",
            "c.altitude", "c.earth_x", "c.earth_y", "c.pitch", "c.roll", "c.yaw",
            "imu_data.pit", "imu_data.rol", "imu_data.yaw",
            "mrac.roll.e", "mrac.roll_gamma", "mrac_state.roll.e",
            "slot3.c.altitude", "slot3.c.earth_x", "slot3.c.earth_y",
            "status.pitch_deg", "status.roll_deg", "status.vbat", "status.yaw_deg",
        ],
        "fft-panel.js": [
            "ahrs.pit", "ahrs.rol", "ahrs.yaw",
            "imu_data.pit", "imu_data.rol", "imu_data.yaw",
            "mrac.roll.e", "mrac.roll_gamma", "mrac_state.roll.e",
            "status.pitch_deg", "status.roll_deg", "status.yaw_deg",
        ],
        "bandwidth-panel.js": [
            "slot{N}.dropped", "slot{N}.loss_pct", "slot{N}.received", "slot{N}.seq", "slot{N}.t_ms",
        ],
        "replay-panel.js": [
            "ekf.pos_x", "slot0.ch0.5", "slotN.chN.N",
        ],
        "path-panel.js": [
            "c.earth_x", "c.earth_y",
        ],
        "slot-manager-panel.js": [
            "(dynamic slot and DWARF symbol management via /slots and /api/symbols)"
        ],
        "resource-map-panel.js": [
            "(reads /api/view-model?stats=1 and shell state)"
        ],
    }

    panels = []
    for filename, name, workspaces, gates, desc in plugin_files:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
        keys_read = keys_read_map.get(filename, [])
        panels.append({
            "name": name,
            "slug": slug,
            "file": f"docs/dashboard-platform/shell/plugins/{filename}",
            "workspaces": workspaces,
            "gates": gates,
            "keys_read": keys_read,
            "description": desc,
        })
    return panels


def get_api_routes() -> dict[str, Any]:
    """Extract API routes from ground_station/service/api.py::_ROUTE_MAP."""
    from ground_station.service.api import _ROUTE_MAP
    return dict(_ROUTE_MAP)


def generate_manifest() -> dict[str, Any]:
    """Generate the complete system capability manifest."""
    elf_identity = get_elf_identity()
    firmware_symbols = get_firmware_symbols()
    commands = get_commands()
    telemetry = get_telemetry_keys()
    panels = get_panels()
    routes = get_api_routes()

    return {
        "manifest_version": "v1",
        "staleness_caveat": STALENESS_CAVEAT,
        "elf_identity": elf_identity,
        "firmware_symbols": firmware_symbols,
        "commands": commands,
        "telemetry": telemetry,
        "panels": panels,
        "routes": routes,
    }


def write_manifest(path: Path | None = None) -> Path:
    """Generate and write the capability manifest to JSON."""
    out_path = path or MANIFEST_PATH
    manifest = generate_manifest()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return out_path


def check_manifest_drift(path: Path | None = None) -> tuple[bool, str]:
    """Check if on-disk manifest has drifted from current codebase.

    Returns (is_in_sync, diff_description).
    """
    target = path or MANIFEST_PATH
    if not target.exists():
        return False, f"manifest file does not exist at {target}"
    current = generate_manifest()
    try:
        on_disk = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"cannot parse on-disk manifest JSON: {exc}"

    current_json = json.dumps(current, indent=2, sort_keys=True)
    on_disk_json = json.dumps(on_disk, indent=2, sort_keys=True)
    if current_json != on_disk_json:
        return False, "on-disk manifest differs from current generated capability manifest"
    return True, "in sync"


def main():
    parser = argparse.ArgumentParser(description="System Capability Manifest Generator")
    parser.add_argument("--check", action="store_true", help="Check for drift without writing")
    parser.add_argument("--out", type=Path, default=MANIFEST_PATH, help="Output JSON path")
    args = parser.parse_args()

    if args.check:
        synced, msg = check_manifest_drift(args.out)
        if not synced:
            print(f"DRIFT DETECTED: {msg}", file=sys.stderr)
            sys.exit(1)
        print("Capability manifest is in sync.")
        sys.exit(0)

    out = write_manifest(args.out)
    print(f"Wrote capability manifest to {out}")


if __name__ == "__main__":
    main()
