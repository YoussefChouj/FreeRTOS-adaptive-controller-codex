"""
Firmware Contract Manifest — versioned source of truth for the ground station.

This module encodes the complete firmware interface as a structured Python
data object. It replaces hand-maintained documentation and enables:
  * Host-side validation of command parameters before transmit.
  * Per-command safety gates and precondition checks.
  * Schema ID / build ID validation against received frames.
  * Host decoder generation from the same source as the firmware.
  * Contract tests against ``send_data.c`` dispatch table.

The manifest is deliberately versioned so a host built against v1 can detect
and reject a firmware that has been flashed to v2 without the host's
knowledge.

Manifest versioning
~~~~~~~~~~~~~~~~~~~
``contract_version`` is the schema version for this Python data structure.
``firmware_build`` / ``firmware_git`` / ``elf_sha256`` carry the firmware
identity. The host rejects a response whose build ID does not match the
manifest's ``firmware_build`` when the manifest is pinned (i.e. when
``strict_build_check`` is True).

Version history
~~~~~~~~~~~~~~
  v1 (2026-09-18): initial manifest from ``send_data.c`` dispatch table,
                    ``subscribe.h``, ``command_protocol.h``.
                    Captures all command IDs 0x01..0x1E, subscribe limits,
                    telemetry frame types, and safety interlocks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The current contract schema version. Bump this whenever the manifest
# structure changes in a way that affects host-side validation.
CONTRACT_VERSION = "v1"

# Firmware protocol version embedded in Frame A (send_data.c).
# Must match ``GS_PROTO_VERSION`` in the firmware source.
GS_PROTO_VERSION = 14

# Platform command protocol version (command_protocol.h).
PLATFORM_COMMAND_VERSION = 1


# ---------------------------------------------------------------------------
# Reject reason names matching the firmware's RejectReason enum.
# These are the strings the firmware returns in the 0x31 result frame
# detail field and the ones the host surfaces in /api/faults.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SafetyClass:
    """Safety classification for one command ID.

    Commands are classified to drive the dashboard's enable/disable logic
    and to make the agent observability endpoints truthful about what the
    firmware will accept.
    """

    requires_disarmed: bool = False
    requires_armed: bool = False
    requires_sdk_mode: bool = False
    requires_ground_idle: bool = False
    description: str = ""
    danger_level: str = "safe"  # "safe" | "caution" | "dangerous"


@dataclass(frozen=True)
class CommandParam:
    """One parameter field of a command."""
    index: int          # idx byte value
    name: str           # human-readable name
    unit: str = ""     # e.g. "deg/s", "Hz", "m/s"
    min_val: float | None = None
    max_val: float | None = None
    description: str = ""
    symbol: str | None = None


@dataclass(frozen=True)
class CommandSpec:
    """Complete specification for one command ID."""
    id: int             # hex command ID, e.g. 0x01
    name: str           # short name, e.g. "PID_GAIN"
    description: str
    params: tuple[CommandParam, ...] = field(default_factory=tuple)
    safety: SafetyClass = field(default_factory=SafetyClass)
    returns_ack: bool = True     # firmware sends 0x30 ACK before applying
    applied_async: bool = True  # applied asynchronously; readback requires telemetry
    notes: str = ""


# ---------------------------------------------------------------------------
# Subscribe protocol limits (from subscribe.h)
# ---------------------------------------------------------------------------

SUBSCRIBE_MAX_SLOTS = 4          # slots 0..3
SUBSCRIBE_MAX_RANGES = 62        # per-slot range tuple limit
SUBSCRIBE_STREAM_MAX_BYTES = 1024  # max data-frame payload
SUBSCRIBE_STREAM_FRAME_OVERHEAD = 12  # non-value bytes in a data frame
SUBSCRIBE_SEND_TASK_HZ = 100     # nominal Send_Task rate (Hz) (100 Hz by design)
SUBSCRIBE_BUDGET_PCT_USART3 = 95  # fraction of USART3 wire usable by subscribe
SUBSCRIBE_BUDGET_PCT_UART5 = 20   # fraction of UART5 wire usable by subscribe


def frame_size_subscribe_request(n_ranges: int) -> int:
    """Wire bytes for a 0xCC 0xDE 0x21 subscribe request with n ranges.

    Formula: 9 header/footer + n*8 range payload.
    Source: subscribe.c build_stream_request / Subscribe_ParseStreamRequest.
    """
    return 9 + n_ranges * 8


def frame_size_schema_reply(n_ranges: int) -> int:
    """Wire bytes for a 0xAA 0xBB 0x08 schema reply with n ranges.

    Formula: 11 + n*8 (header + config + n*range + CRC).
    Source: subscribe.c Subscribe_BuildSchema.
    """
    return 11 + n_ranges * 8


def frame_size_data_frame(n_ranges: int, total_bytes: int) -> int:
    """Wire bytes for a 0xAA 0xBB 0x09+slot data frame.

    Formula: 10 header/timestamp + total_bytes + 2 CRC16.
    Source: subscribe.c Subscribe_BuildStreamFrame.
    """
    return 10 + total_bytes + 2


def effective_rate_hz(divider: int) -> float:
    """Effective emission rate given the Send_Task divider."""
    return SUBSCRIBE_SEND_TASK_HZ / divider


def link_budget_usart3(frame_bytes: int, divider: int) -> float:
    """Bytes per second this subscription consumes on USART3.

    At 921600 baud, 8N1 = 92160 bytes/s wire capacity.
    """
    baud = 921600
    wire_bps = baud / 10
    bps = (frame_bytes * SUBSCRIBE_SEND_TASK_HZ) / divider
    pct = bps / wire_bps * 100
    return pct


def link_budget_uart5(frame_bytes: int, divider: int) -> float:
    """Bytes per second this subscription consumes on UART5.

    At 115200 baud, 8N1 = 11520 bytes/s wire capacity.
    """
    baud = 115200
    wire_bps = baud / 10
    bps = (frame_bytes * SUBSCRIBE_SEND_TASK_HZ) / divider
    pct = bps / wire_bps * 100
    return pct


# ---------------------------------------------------------------------------
# Complete command table (from TASK/send_data.c dispatch table)
# ---------------------------------------------------------------------------

# Safety interlock reasons returned by CommandSafetyReject (send_data.c:1324):
#   0  = pass
#   4  = unknown command
#   6  = safety interlock

COMMAND_TABLE: dict[int, CommandSpec] = {
    0x01: CommandSpec(
        id=0x01, name="PID_GAIN",
        description="Update PID gain (Kp/Ki/Kd) for one of 7 control axes.",
        params=(
            CommandParam(0, "axis", "axis", 0, 6),
            CommandParam(1, "gain_type", "enum", 0, 2),
            CommandParam(2, "Kp_or_Ki_or_Kd", "gain", 0.0, 200.0),
        ),
        safety=SafetyClass(description="Safe at runtime; changes control response immediately."),
    ),
    0x02: CommandSpec(
        id=0x02, name="MRAC_GAMMA",
        description="Update MRAC adaptation gain gamma[axis][elem]. Positive values only.",
        params=(
            CommandParam(0, "axis", "axis", 0, 3),
            CommandParam(1, "elem", "index", 0, 5),
            CommandParam(2, "gamma", "1/s", 0.0, None),
        ),
        safety=SafetyClass(description="Safe at runtime; changes adaptation rate."),
    ),
    0x03: CommandSpec(
        id=0x03, name="MIXER_SATURATION",
        description="Update mixer gain and saturation limits.",
        params=(
            CommandParam(0, "param", "enum", 0, 9),
            CommandParam(1, "value", "float", 0.0, 1.0),
        ),
        safety=SafetyClass(description="Safe at runtime."),
    ),
    0x04: CommandSpec(
        id=0x04, name="FLIGHT_MODE_ABORT",
        description="Abort all paths and recover SDK authority. DANGEROUS: idx=0 is an immediate stop.",
        params=(
            CommandParam(0, "abort", "enum", 0, 0),
            CommandParam(1, "recover_sdk", "enum", 1, 1),
        ),
        safety=SafetyClass(
            requires_sdk_mode=False,
            description="idx=0: abort all paths, zero sticks, dangerous stop. "
                        "idx=1: recover SDK authority.",
            danger_level="dangerous",
        ),
    ),
    0x05: CommandSpec(
        id=0x05, name="MRAC_WEIGHT_LIMIT",
        description="Update MRAC weight limit What_limit[axis][elem]. Non-negative.",
        params=(
            CommandParam(0, "axis", "axis", 0, 3),
            CommandParam(1, "elem", "index", 0, 5),
            CommandParam(2, "What_limit", "float", 0.0, None),
        ),
        safety=SafetyClass(description="Safe at runtime."),
    ),
    0x06: CommandSpec(
        id=0x06, name="VIRTUAL_STICK",
        description="Inject virtual RC stick value. SDK mode only.",
        params=(
            CommandParam(0, "axis", "axis", 0, 3),
            CommandParam(1, "value", "norm", -1.0, 1.0),
        ),
        safety=SafetyClass(
            requires_sdk_mode=True,
            description="SDK mode required. sbus_lost is not checked; RC is emergency fallback.",
            danger_level="caution",
        ),
    ),
    0x07: CommandSpec(
        id=0x07, name="BENCH_MODE",
        description="Enable/disable bench test mode (prop-wash safety gate).",
        params=(
            CommandParam(0, "enable", "bool", 0, 1),
        ),
        safety=SafetyClass(
            description="Disables automatic FLYING transition. Throttle NOT capped.",
            danger_level="caution",
        ),
    ),
    0x08: CommandSpec(
        id=0x08, name="MRAC_TOLERANCE",
        description="Update MRAC weight tolerance What_tol[axis][elem]. Non-negative.",
        params=(
            CommandParam(0, "axis", "axis", 0, 3),
            CommandParam(1, "elem", "index", 0, 5),
            CommandParam(2, "What_tol", "float", 0.0, None),
        ),
        safety=SafetyClass(description="Safe at runtime."),
    ),
    0x09: CommandSpec(
        id=0x09, name="SAFETY_LIMITS",
        description="Set velocity and attitude safety limits from ground station.",
        params=(
            CommandParam(0, "param", "enum", 0, 3),
            CommandParam(1, "value", "float", None, None),
        ),
        safety=SafetyClass(
            description="idx 0: horizontal speed m/s (0.05..20). "
                        "idx 1: vertical speed m/s (0.05..10). "
                        "idx 2: max pitch deg (3..60). idx 3: max roll deg (3..60).",
            danger_level="caution",
        ),
    ),
    0x0A: CommandSpec(
        id=0x0A, name="TWC_TARGET",
        description="Set three-waypoint (TWC) target position. SDK mode only.",
        params=(
            CommandParam(0, "target_x", "m", None, None),
            CommandParam(1, "target_y", "m", None, None),
            CommandParam(2, "target_z", "m", None, None),
            CommandParam(3, "set_yaw", "deg", None, None),
            CommandParam(4, "execute", "bool", 0, 1),
        ),
        safety=SafetyClass(
            requires_sdk_mode=True,
            description="Path following in SDK mode.",
            danger_level="dangerous",
        ),
    ),
    0x0B: CommandSpec(
        id=0x0B, name="SINUSOID_PATH",
        description="Configure and activate sinusoidal reference path. SDK mode only.",
        params=(
            CommandParam(0, "center_x", "m", None, None),
            CommandParam(1, "center_y", "m", None, None),
            CommandParam(2, "center_z", "m", None, None),
            CommandParam(3, "amplitude", "m", None, None),
            CommandParam(4, "frequency", "Hz", None, None),
            CommandParam(5, "duration", "s", None, None),
            CommandParam(6, "axis", "enum", 0, 2),
            CommandParam(7, "active", "bool", 0, 1),
        ),
        safety=SafetyClass(
            requires_sdk_mode=True,
            description="Sinusoidal path in SDK mode.",
            danger_level="dangerous",
        ),
    ),
    0x0C: CommandSpec(
        id=0x0C, name="CIRCLE_PATH",
        description="Configure and activate circular reference path. SDK mode only.",
        params=(
            CommandParam(0, "center_x", "m", None, None),
            CommandParam(1, "center_y", "m", None, None),
            CommandParam(2, "center_z", "m", None, None),
            CommandParam(3, "radius", "m", None, None),
            CommandParam(4, "angular_speed", "rad/s", None, None),
            CommandParam(5, "duration", "s", None, None),
            CommandParam(6, "active", "bool", 0, 1),
        ),
        safety=SafetyClass(
            requires_sdk_mode=True,
            description="Circular path in SDK mode.",
            danger_level="dangerous",
        ),
    ),
    0x0D: CommandSpec(
        id=0x0D, name="ABORT_ALL_PATHS",
        description="Abort all active paths (TWC, sinusoid, circle, figure-8).",
        params=(
            CommandParam(0, "abort", "enum", 0, 0),
        ),
        safety=SafetyClass(
            description="Stops all path following. Neutral sticks. Dangerous stop.",
            danger_level="dangerous",
        ),
    ),
    0x0E: CommandSpec(
        id=0x0E, name="SDK_ARM_AUTHORITY",
        description="Ground-station arm switch. idx 0: arm authority (val>=0.5 arm, "
                    "<0.5 release). idx 1: motor idle enable (val>=0.5 enable idle PWM, "
                    "<0.5 disable; requires ARMED + GROUND_IDLE).",
        params=(
            CommandParam(0, "arm_auth", "bool", 0, 1),
            CommandParam(1, "motor_idle_enable", "bool", 0, 1),
        ),
        safety=SafetyClass(
            description="Arms the drone or enables motor idle. Critical.",
            danger_level="dangerous",
        ),
    ),
    0x0F: CommandSpec(
        id=0x0F, name="MULTIPLEX_FLAGS",
        description="Multiplexed command: MRAC flags (idx 0..12) or telemetry mode (idx 100..102).",
        params=(
            # MRAC flags (idx 0..12)
            CommandParam(0,  "adaptation_on",       "bool", 0, 1),
            CommandParam(1,  "projection_on",        "bool", 0, 1),
            CommandParam(2,  "deadzone_on",          "bool", 0, 1),
            CommandParam(3,  "hard_freeze_on",       "bool", 0, 1),
            CommandParam(4,  "tanh_saturation_on",   "bool", 0, 1),
            CommandParam(5,  "e_modification_on",     "bool", 0, 1),
            CommandParam(6,  "l1_filtering_on",      "bool", 0, 1),
            CommandParam(7,  "axis_enable_pitch",    "bool", 0, 1),
            CommandParam(8,  "axis_enable_roll",     "bool", 0, 1),
            CommandParam(9,  "axis_enable_yaw",       "bool", 0, 1),
            CommandParam(10, "output_injection_on",   "bool", 0, 1),
            CommandParam(11, "id_frame_on",           "bool", 0, 1),
            CommandParam(12, "of_frame_on",           "bool", 0, 1),
            # Telemetry mode switch
            CommandParam(100, "telemetry_legacy",       "enum", 0, 0),
            CommandParam(101, "telemetry_mixed",         "enum", 0, 0),
            CommandParam(102, "telemetry_subscribe_only", "enum", 0, 0),
        ),
        safety=SafetyClass(
            description="MRAC runtime flags: adaptation_on, output_injection_on, "
                        "id_frame_on, of_frame_on. "
                        "Telemetry mode switch: idx 100=LEGACY, 101=MIXED, 102=SUBSCRIBE_ONLY.",
            danger_level="caution",
        ),
    ),
    0x10: CommandSpec(
        id=0x10, name="RESET_ORIGIN",
        description="Reset world-frame optical flow origin to current location.",
        params=(
            CommandParam(0, "reset", "enum", 0, 0),
        ),
        safety=SafetyClass(description="Safe at runtime."),
    ),
    0x11: CommandSpec(
        id=0x11, name="FIGURE8_PATH",
        description="Configure and activate figure-8 (lemniscate) path. SDK mode only.",
        params=(
            CommandParam(0, "center_x", "m", None, None),
            CommandParam(1, "center_y", "m", None, None),
            CommandParam(2, "center_z", "m", None, None),
            CommandParam(3, "amplitude", "m", None, None),
            CommandParam(4, "angular_speed", "rad/s", None, None),
            CommandParam(5, "duration", "s", None, None),
            CommandParam(6, "type", "enum", 0, 1),
            CommandParam(7, "active", "bool", 0, 1),
        ),
        safety=SafetyClass(
            requires_sdk_mode=True,
            description="Figure-8 path in SDK mode.",
            danger_level="dangerous",
        ),
    ),
    0x12: CommandSpec(
        id=0x12, name="WAYPOINT_SPACING",
        description="Set waypoint density spacing (cm units from GUI).",
        params=(
            CommandParam(0, "spacing_cm", "cm", 0.0, None),
        ),
        safety=SafetyClass(description="Safe at runtime."),
    ),
    0x13: CommandSpec(
        id=0x13, name="REF_MODEL_TYPE",
        description="Select reference model type: 0=passthrough, 1=1st-order, 2=2nd-order.",
        params=(
            CommandParam(0, "type", "enum", 0, 2),
        ),
        safety=SafetyClass(
            description="Bumpless switch: reference states snap to plant on change.",
            danger_level="caution",
        ),
    ),
    0x14: CommandSpec(
        id=0x14, name="SYSID_CONTROL",
        description="Configure and start/stop system identification excitation.",
        params=(
            CommandParam(0, "axis", "enum", 0, 3),
            CommandParam(1, "signal", "enum", 0, 1),
            CommandParam(2, "f0_Hz", "Hz", None, None),
            CommandParam(3, "f1_Hz", "Hz", None, None),
            CommandParam(4, "amplitude", "deg/s", None, None),
            CommandParam(5, "duration_s", "s", None, None),
            CommandParam(6, "start", "bool", 0, 1),
            CommandParam(7, "geofence_enable", "bool", 0, 1),
        ),
        safety=SafetyClass(
            description="System identification: chirp or multisine excitation.",
            danger_level="caution",
        ),
    ),
    0x15: CommandSpec(
        id=0x15, name="GYRO_LPF",
        description="Enable/disable gyro low-pass filter and set cutoff frequency.",
        params=(
            CommandParam(0, "enable", "bool", 0, 1),
            CommandParam(1, "cutoff_Hz", "Hz", None, None),
        ),
        safety=SafetyClass(description="Safe at runtime."),
    ),
    0x16: CommandSpec(
        id=0x16, name="MOTOR_BENCH",
        description="Motor bench test mode. Disarmed-only.",
        params=(
            CommandParam(0, "motor_id", "enum", 0, 3),
            CommandParam(1, "ccr", "counts", 0.0, 2000.0),
        ),
        safety=SafetyClass(
            requires_disarmed=True,
            description="Motor bench: disarmed only. Streams frame 0x04.",
            danger_level="caution",
        ),
    ),
    0x17: CommandSpec(
        id=0x17, name="OF_BIAS_CAPTURE",
        description="One-shot optical-flow velocity-bias capture. Pilot holds drone still ~2 s.",
        params=(
            CommandParam(0, "capture", "trigger", 0, 0),
        ),
        safety=SafetyClass(description="Safe at runtime. Averaged over ~2 s."),
    ),
    0x18: CommandSpec(
        id=0x18, name="FORCE_RECALIBRATE",
        description="Force full recalibration (accel + gyro + EKF). Ground-idle + disarmed only.",
        params=(
            CommandParam(0, "recalibrate", "trigger", 0, 0),
        ),
        safety=SafetyClass(
            requires_disarmed=True,
            requires_ground_idle=True,
            description="Re-enters cold-cal. Clears all bias and EKF state.",
            danger_level="dangerous",
        ),
    ),
    0x1E: CommandSpec(
        id=0x1E, name="OF_BIAS_MODE",
        description="OF bias estimation mode: FIXED=0, EMA=1, EKF=2. EMA freeze flag idx=1.",
        params=(
            CommandParam(0, "bias_mode", "enum", 0, 2, symbol="g_of_bias_mode"),
            CommandParam(1, "ema_freeze", "bool", 0, 1, symbol="g_of_bias_ema_freeze"),
        ),
        safety=SafetyClass(description="Safe at runtime."),
    ),
}


# ---------------------------------------------------------------------------
# Telemetry frame definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetryFrameSpec:
    type_byte: int
    name: str
    rate_hz: float
    payload_bytes: int
    description: str
    key_signals: tuple[str, ...] = field(default_factory=tuple)


TELEMETRY_FRAMES: tuple[TelemetryFrameSpec, ...] = (
    TelemetryFrameSpec(0x01, "Frame_A", 100.0, 41,
        "MRAC errors + MRAC adaptive + status flags + GS_PROTO_VERSION",
        ("mrac.pitch.e", "mrac.pitch.u_ad", "status.arm", "status.flymode")),
    TelemetryFrameSpec(0x02, "Frame_B", 20.0, None,  # varies with MAX_NUM_BASIS
        "Full theta weights + PID FB/Des/U + path state",
        ("mrac.pitch.theta_0", "pid.pitch.FB")),
    TelemetryFrameSpec(0x03, "Frame_SysID", 100.0, 36,
        "High-rate system-ID stream (replaces A/B when id_frame_on)",
        ("r", "x", "u_nom", "u_ad", "xm")),
    TelemetryFrameSpec(0x04, "Frame_Bench", 100.0, 20,
        "Motor bench stream (replaces A/B when motor_test_active)",
        ("rpm0", "rpm1", "rpm2", "rpm3")),
    TelemetryFrameSpec(0x05, "Frame_OF", 200.0, 55,
        "OF calibration + fusion + ADR-0011 always-on bias/cal fields",
        ("of2_dx_fix", "of2_dy_fix", "bias_mode", "bias_ema_freeze")),
    TelemetryFrameSpec(0x06, "Frame_C", 50.0, 46,
        "Attitude + body-rate + earth position + RPM (sent back-to-back with Frame A)",
        ("imu_data.rol", "imu_data.pit", "earth_x", "earth_y")),
)


# ---------------------------------------------------------------------------
# Supported experiment types (G1 registry)
# ---------------------------------------------------------------------------

EXPERIMENT_TYPES: tuple[str, ...] = (
    "step_response",
    "controls",
    "chirp",
    "frequency_sweep",
    "multisine",
)


# ---------------------------------------------------------------------------
# Firmware contract manifest
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FirmwareContract:
    """Complete versioned firmware interface contract."""
    contract_version: str
    firmware_build: str          # e.g. "JX_FLY-20260918"
    firmware_git: str | None     # git rev-parse HEAD at build time
    gs_proto_version: int       # must match GS_PROTO_VERSION in firmware
    platform_command_version: int
    commands: dict[int, CommandSpec]
    telemetry_frames: tuple[TelemetryFrameSpec, ...]
    subscribe_max_slots: int
    subscribe_max_ranges: int
    subscribe_stream_max_bytes: int
    experiment_types: tuple[str, ...] = EXPERIMENT_TYPES
    notes: str = ""

    def validate_command(self, command_id: int, index: int, value: float,
                         sdk_mode: bool = False, disarmed: bool = True,
                         ground_idle: bool = True) -> tuple[bool, str]:
        """Validate a command against the contract.

        Returns (True, "") if the command passes all checks.
        Returns (False, "reason") with a human-readable rejection reason.

        This is a host-side pre-transmit gate. It does NOT replace the
        firmware's own safety checks; it is an early-warning layer that
        surfaces the expected rejection reason before the round-trip.
        """
        spec = self.commands.get(command_id)
        if spec is None:
            return False, f"unknown command 0x{command_id:02X}"

        safety = spec.safety
        if safety.requires_sdk_mode and not sdk_mode:
            return False, f"command 0x{command_id:02X} requires SDK mode"
        if safety.requires_disarmed and disarmed:
            return False, f"command 0x{command_id:02X} requires disarmed state"
        if safety.requires_ground_idle and not ground_idle:
            return False, f"command 0x{command_id:02X} requires ground-idle state"

        # Validate parameter ranges
        for param in spec.params:
            if param.index == index:
                if param.min_val is not None and value < param.min_val:
                    return False, f"{param.name} {value} below min {param.min_val}"
                if param.max_val is not None and value > param.max_val:
                    return False, f"{param.name} {value} above max {param.max_val}"
                break

        return True, ""

    def command_spec(self, command_id: int) -> CommandSpec | None:
        """Return the spec for one command ID, or None if unknown."""
        return self.commands.get(command_id)

    def subscribe_validate_plan(self, n_ranges: int, total_bytes: int,
                                divider: int, transport: int) -> tuple[bool, str]:
        """Validate a subscribe plan against the firmware's limits."""
        if n_ranges == 0:
            return False, "n_ranges must be > 0 (use divider=0 to stop)"
        if n_ranges > SUBSCRIBE_MAX_RANGES:
            return False, f"n_ranges {n_ranges} exceeds max {SUBSCRIBE_MAX_RANGES}"
        if total_bytes > SUBSCRIBE_STREAM_MAX_BYTES:
            return False, f"total_bytes {total_bytes} exceeds max {SUBSCRIBE_STREAM_MAX_BYTES}"
        if divider < 1:
            return False, "divider must be >= 1"
        if transport not in (0, 1):
            return False, f"transport must be 0 (UART5) or 1 (USART3), got {transport}"
        return True, ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the contract for JSON export."""
        return {
            "contract_version": self.contract_version,
            "firmware_build": self.firmware_build,
            "firmware_git": self.firmware_git,
            "gs_proto_version": self.gs_proto_version,
            "platform_command_version": self.platform_command_version,
            "subscribe": {
                "max_slots": self.subscribe_max_slots,
                "max_ranges": self.subscribe_max_ranges,
                "stream_max_bytes": self.subscribe_stream_max_bytes,
                "send_task_hz": SUBSCRIBE_SEND_TASK_HZ,
                "budget_pct_usart3": SUBSCRIBE_BUDGET_PCT_USART3,
                "budget_pct_uart5": SUBSCRIBE_BUDGET_PCT_UART5,
            },
            "commands": {
                f"0x{k:02X}": {
                    "name": v.name,
                    "description": v.description,
                    "safety": {
                        "requires_sdk_mode": v.safety.requires_sdk_mode,
                        "requires_disarmed": v.safety.requires_disarmed,
                        "requires_ground_idle": v.safety.requires_ground_idle,
                        "danger_level": v.safety.danger_level,
                        "description": v.safety.description,
                    },
                    "returns_ack": v.returns_ack,
                    "applied_async": v.applied_async,
                    "params": [
                        {
                            "index": p.index,
                            "name": p.name,
                            "unit": p.unit,
                            "min_val": p.min_val,
                            "max_val": p.max_val,
                            "description": p.description,
                            "symbol": p.symbol,
                        }
                        for p in v.params
                    ]
                }
                for k, v in self.commands.items()
            },
            "experiment_types": list(self.experiment_types),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Singleton contract instance
# ---------------------------------------------------------------------------

# The canonical contract for the current firmware build.
# ``firmware_build`` is updated each time the firmware is rebuilt;
# agents compare this against the ELF hash or DWARF build-info string.
current = FirmwareContract(
    contract_version=CONTRACT_VERSION,
    firmware_build="JX_FLY-unknown",  # set by flash tool on rebuild
    firmware_git=None,               # set by CI / flash tool
    gs_proto_version=GS_PROTO_VERSION,
    platform_command_version=PLATFORM_COMMAND_VERSION,
    commands=COMMAND_TABLE,
    telemetry_frames=TELEMETRY_FRAMES,
    subscribe_max_slots=SUBSCRIBE_MAX_SLOTS,
    subscribe_max_ranges=SUBSCRIBE_MAX_RANGES,
    subscribe_stream_max_bytes=SUBSCRIBE_STREAM_MAX_BYTES,
    experiment_types=EXPERIMENT_TYPES,
    notes="Generated from TASK/send_data.c dispatch table, "
          "API/subscribe.h, firmware/command_protocol.h as of 2026-09-18.",
)
