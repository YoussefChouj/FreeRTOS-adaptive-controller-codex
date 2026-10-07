"""Software drone answering Workflow B typed commands identically to firmware.

Faithfully reproduces the state machines, error codes, limits, and safety
interlocks defined in API/wfb_prim.c, API/wfb_traj.c, and API/wfb_safety.c.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import IntEnum

from ground_station.autotune import excitation as ex
from ground_station.livewatch.transport import LiveTransportError
from ground_station.platform.transactions import Command, Outcome, parse_command
from ground_station.platform.wfb_commands import (
    CMD_ARM,
    CMD_KILL,
    CMD_PRIM,
    CMD_PROG,
    CMD_TRAJ,
    ArmIdx,
    PrimIdx,
    ProgIdx,
    TrajIdx,
)
from ground_station.platform import wfb_program
from ground_station.service.trajectory_pipeline import TrajLimits, TrajPoint, crc32, validate

PARAM_CMDS = (0x01, 0x0F, 0x14)  # PID_GAIN, MRAC/telemetry flags, SysID control: the livetune step's writes


class PrimState(IntEnum):
    IDLE = 0
    CLIMB = 1
    HOVER = 2
    TRAJ = 3
    RETURN = 4
    SETTLE = 5
    DESCEND = 6


class TrajState(IntEnum):
    EMPTY = 0
    LOADING = 1
    READY = 2
    EXECUTING = 3
    DONE = 4


class WfbErr(IntEnum):
    NONE = 0
    STATE = 1
    RANGE = 2
    COUNT = 3
    CRC = 4
    TIME = 5
    BOUNDS = 6
    ENDPOINT = 7
    SPEED = 8
    SAFETY = 9


class Trip(IntEnum):
    NONE = 0
    HEARTBEAT = 1
    LOW_V = 2
    AIRBORNE_CAP = 3
    FENCE = 4
    CEILING = 5
    TILT = 6


class Action(IntEnum):
    NONE = 0
    LAND_VIA_HOVER = 1
    LAND_IN_PLACE = 2
    KILL = 3


@dataclass(frozen=True)
class FakeParams:
    """Timing and rate parameters matching C tables and integration choices."""

    # Return XY rate, settle radius, settle time, return timeout (from API/wfb_prim.c table)
    xy_rate_mps: float = 0.3
    settle_radius_m: float = 0.15
    settle_time_s: float = 1.0
    return_timeout_s: float = 10.0
    # Rates left to integration (climb rate, descent rate, tracking speed).
    # PROPOSED: simulation-only values, not measured on the vehicle; F4 takes the
    # real climb and descent rates from the existing ch7 auto-climb / descent code.
    climb_rate_mps: float = 0.5
    descend_rate_mps: float = 0.3
    tracking_speed_mps: float = 1.5
    # Heartbeat timeout (from API/wfb_safety.c table)
    hb_timeout_s: float = 1.0
    # Airborne cap (from API/wfb_safety.c table)
    airborne_cap_s: float = 120.0
    # Low-voltage threshold and hold (from API/wfb_safety.c table)
    low_v: float = 14.0
    low_v_hold_s: float = 3.0
    # Tilt threshold and hold (from API/wfb_safety.c table)
    tilt_deg: float = 60.0
    tilt_hold_s: float = 0.2
    # Fence (from API/wfb_safety.c table)
    fence_x_m: float = 1.6
    fence_y_m: float = 2.0
    # Ceiling (from API/wfb_safety.c table)
    ceiling_m: float = 1.7
    # Fence push-back (from API/wfb_safety.c table): longest push outside, distance past the fence that
    # lands at once, and the push target inside the fence/ceiling
    fence_hold_s: float = 2.0
    fence_over_m: float = 0.3
    soft_margin_m: float = 0.3
    # Hover_z range (from docs/workflow-b/interfaces.md section 1)
    hover_z_min_m: float = 0.3
    hover_z_max_m: float = 1.4


# Fence push-back axis bits (WFB_PUSH_* in API/wfb_safety.h)
PUSH_X, PUSH_Y, PUSH_Z = 1, 2, 4


class FakeDrone:
    """Deterministic simulation of the drone flight controller."""

    def __init__(
        self,
        limits: TrajLimits = TrajLimits(),
        params: FakeParams = FakeParams(),
        hover_z: float = 0.5,
    ) -> None:
        self.limits: TrajLimits = limits
        self.params: FakeParams = params

        # Settable attributes
        self.sbus_live: bool = True
        self.vbat_v: float = 16.0
        self.roll_deg: float = 0.0
        self.pitch_deg: float = 0.0

        # State attributes
        self._armed: bool = False
        self._motors_idle: bool = False
        self._x: float = 0.0
        self._y: float = 0.0
        self._z: float = 0.0
        self._yaw_deg: float = 0.0
        self._hover_z: float = float(hover_z)

        # Firmware status fields
        self._prim_state: PrimState = PrimState.IDLE
        self._traj_state: TrajState = TrajState.EMPTY
        self._traj_n: int = 0
        self._traj_rx: int = 0
        self._traj_crc_hi: int = 0
        self._traj_crc_lo: int = 0
        self._traj_t: float = 0.0
        self._prog_mode: int = 0
        self._prog_err_seg: int = -1
        self._prog_stage: list[float] = [0.0] * wfb_program.F_COUNT
        self._prog_recs: list[tuple[float, ...]] = []
        self._last_err: WfbErr = WfbErr.NONE
        self._safety_trip: Trip = Trip.NONE
        self._safety_action: Action = Action.NONE
        self._hb_age: float = 0.0
        self._gs_flight_active: int = 0
        self._airborne_t: float = 0.0

        # Internal safety timers
        self._low_v_t: float = 0.0
        self._tilt_t: float = 0.0
        self._fence_t: float = 0.0
        self._push: int = 0

        # Internal primitive FSM variables
        self._prim_land_after_return: int = 0
        self._prim_x_sp: float = 0.0
        self._prim_y_sp: float = 0.0
        self._prim_settle_t: float = 0.0
        self._prim_return_t: float = 0.0
        self._prim_descend_frozen: int = 0

        # (cmd, idx, value) of every accepted PARAM_CMDS frame, in order
        self.param_writes: list[tuple[int, int, float]] = []

        # Trajectory storage
        self._traj_raw_floats: list[float] = []
        self._traj_points: list[TrajPoint] = []
        self._traj_crc_hi_set: bool = False
        self._traj_seg: int = 0

        # SysID (CMD 0x14) and MRAC flags (CMD 0x0F); not in status(), the firmware streams them elsewhere
        self.sysid_state: int = ex.STATE_IDLE
        self.sysid_params: dict[int, float] = {}
        self.sysid_starts: int = 0
        self._sysid_t: float = 0.0
        self.mrac_flags: dict[int, bool] = {}

    @property
    def armed(self) -> bool:
        """Whether the flight controller is currently armed."""
        return self._armed

    @property
    def motors_idle(self) -> bool:
        """Whether the motors are currently set to idle."""
        return self._motors_idle

    @property
    def position(self) -> tuple[float, float, float]:
        """Current world-frame coordinates (x, y, z) in metres."""
        return (self._x, self._y, self._z)

    @property
    def yaw_deg(self) -> float:
        """Current vehicle yaw angle in degrees."""
        return self._yaw_deg

    def send(self, frame: bytes) -> int:
        """Process an incoming command frame and return Outcome."""
        try:
            cmd = parse_command(frame)
        except (LiveTransportError, ValueError, struct.error):
            return int(Outcome.REJECTED)

        if cmd.command_id == CMD_ARM:
            return self._handle_arm(cmd)
        elif cmd.command_id == CMD_KILL:
            return self._handle_kill(cmd)
        elif cmd.command_id == CMD_PRIM:
            return self._handle_prim(cmd)
        elif cmd.command_id == CMD_TRAJ:
            return self._handle_traj(cmd)
        elif cmd.command_id == CMD_PROG:
            return self._handle_prog(cmd)
        if cmd.command_id in PARAM_CMDS:  # no dynamics behind them: recorded for the livetune tests
            self.param_writes.append((cmd.command_id, cmd.index, cmd.value))
        if cmd.command_id == ex.CMD_MRAC_FLAGS and cmd.index <= 12:
            self.mrac_flags[int(cmd.index)] = int(round(cmd.value)) != 0
            return int(Outcome.APPLIED)
        elif cmd.command_id == ex.CMD_SYSID:
            return self._handle_sysid(cmd)
        elif cmd.command_id in PARAM_CMDS:
            return int(Outcome.APPLIED)
        else:
            return int(Outcome.REJECTED)

    def _handle_arm(self, cmd: Command) -> int:
        # Accepted only on the ground
        is_on_ground = (self._prim_state == PrimState.IDLE and self._z <= 0.0)
        if not is_on_ground:
            return int(Outcome.REJECTED)

        if cmd.index == ArmIdx.ARM:
            if cmd.value >= 0.5:
                if not self.sbus_live:
                    return int(Outcome.REJECTED)
                self._armed = True
                return int(Outcome.APPLIED)
            else:
                self._armed = False
                self._motors_idle = False
                self._safety_trip = Trip.NONE
                self._safety_action = Action.NONE
                self._low_v_t = 0.0
                self._tilt_t = 0.0
                self._fence_t = 0.0
                self._push = 0
                self._airborne_t = 0.0
                return int(Outcome.APPLIED)

        elif cmd.index == ArmIdx.IDLE:
            if cmd.value >= 0.5:
                if not self._armed:
                    return int(Outcome.REJECTED)
                self._motors_idle = True
                return int(Outcome.APPLIED)
            else:
                self._motors_idle = False
                return int(Outcome.APPLIED)

        return int(Outcome.REJECTED)

    def _handle_sysid(self, cmd: Command) -> int:
        """CMD 0x14 as TASK/send_data.c:1838-1872: always applied; a start that fails SysID_Start's preconditions
        (armed, airborne, altitude band) silently stays IDLE. Start zeroes the position origin first."""
        if cmd.index <= 5:
            self.sysid_params[int(cmd.index)] = float(cmd.value)
        elif cmd.index == ex.IDX_START and cmd.value >= 0.5:
            self._x = self._y = self._prim_x_sp = self._prim_y_sp = 0.0
            z_ok = ex.ALT_BAND_M[0] <= self._z <= ex.ALT_BAND_M[1]
            if self.sysid_state == ex.STATE_IDLE and self._armed and self._prim_state != PrimState.IDLE and z_ok:
                self.sysid_state, self._sysid_t = ex.STATE_RAMP_IN, 0.0
                self.sysid_starts += 1
        elif cmd.index == ex.IDX_START:
            if self.sysid_state != ex.STATE_IDLE:
                self.sysid_state, self._sysid_t = ex.STATE_RECOVERY, 0.0
        elif cmd.index != ex.IDX_GEOFENCE:
            return int(Outcome.REJECTED)
        return int(Outcome.APPLIED)

    def _step_sysid(self, dt: float) -> None:
        if self.sysid_state == ex.STATE_IDLE:
            return
        self._sysid_t += dt
        if self.sysid_state == ex.STATE_RECOVERY:
            if self._sysid_t >= ex.RECOVERY_T_S:
                self.sysid_state = ex.STATE_IDLE
            return
        if self._prim_state == PrimState.IDLE or not self._armed:
            self.sysid_state, self._sysid_t = ex.STATE_RECOVERY, 0.0
            return
        dur = min(max(self.sysid_params.get(5, 20.0), 1.0), 60.0)
        t = self._sysid_t
        self.sysid_state = (ex.STATE_RAMP_IN if t < ex.RAMP_T_S else ex.STATE_RUNNING if t < ex.RAMP_T_S + dur
                            else ex.STATE_RAMP_OUT if t < ex.active_s(dur) else ex.STATE_IDLE)

    def _handle_kill(self, cmd: Command) -> int:
        if cmd.index != 0:
            return int(Outcome.REJECTED)
        self._motors_idle = False
        self._armed = False
        self._prim_state = PrimState.IDLE
        if self._traj_state == TrajState.EXECUTING:
            self._traj_state = TrajState.READY
        self._z = 0.0
        self._gs_flight_active = 0
        return int(Outcome.APPLIED)

    def _handle_prim(self, cmd: Command) -> int:
        if cmd.index == PrimIdx.TAKEOFF:
            if self._safety_trip != Trip.NONE or not self.sbus_live:
                self._last_err = WfbErr.SAFETY
                return int(Outcome.REJECTED)
            if not self._armed or not self._motors_idle or self._prim_state != PrimState.IDLE:
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)

            self._last_err = WfbErr.NONE
            self._prim_state = PrimState.CLIMB
            self._prim_settle_t = 0.0
            self._gs_flight_active = 1
            return int(Outcome.APPLIED)

        elif cmd.index == PrimIdx.LAND:
            if self._prim_state == PrimState.IDLE:
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)

            self._last_err = WfbErr.NONE
            if self._traj_state == TrajState.EXECUTING:
                self._traj_state = TrajState.READY

            if self._prim_state == PrimState.DESCEND:
                pass
            elif self._prim_state in (PrimState.RETURN, PrimState.SETTLE):
                if self._prim_land_after_return == 0:
                    self._prim_land_after_return = 1
                    self._prim_return_t = 0.0
            else:
                self._prim_state = PrimState.RETURN
                self._prim_land_after_return = 1
                self._prim_x_sp = self._x if math.isfinite(self._x) else 0.0
                self._prim_y_sp = self._y if math.isfinite(self._y) else 0.0
                self._prim_return_t = 0.0

            return int(Outcome.APPLIED)

        elif cmd.index == PrimIdx.HEARTBEAT:
            self._hb_age = 0.0
            self._last_err = WfbErr.NONE
            return int(Outcome.APPLIED)

        elif cmd.index == PrimIdx.SET_HOVER_Z:
            val = cmd.value
            if self._prim_state != PrimState.IDLE:
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)
            if not math.isfinite(val) or not (self.params.hover_z_min_m <= val <= self.params.hover_z_max_m):
                self._last_err = WfbErr.RANGE
                return int(Outcome.REJECTED)

            self._hover_z = float(val)
            self._last_err = WfbErr.NONE
            return int(Outcome.APPLIED)

        else:
            return int(Outcome.REJECTED)

    def _prog_reject(self, err: WfbErr, seg: int = -1) -> int:
        self._last_err, self._prog_err_seg = err, seg
        if self._traj_state == TrajState.LOADING:
            self._traj_state = TrajState.EMPTY
        return int(Outcome.REJECTED)

    def _handle_prog(self, cmd: Command) -> int:
        """CMD 0x1C program upload. COMMIT runs the firmware evaluator (wfb_program.preview, gcc) and loads its
        dense path as the trajectory, so START (0x1B), the executor and traj_state DONE are the trajectory ones."""
        idx, val = int(cmd.index), cmd.value
        if self._traj_state == TrajState.EXECUTING:
            return self._prog_reject(WfbErr.STATE)
        if idx == ProgIdx.BEGIN:
            if not math.isfinite(val) or math.floor(val) != val or not 1.0 <= val <= wfb_program.PROG_MAX_SEGS:
                return self._prog_reject(WfbErr.RANGE)
            self._traj_state, self._traj_n, self._prog_mode = TrajState.LOADING, int(val), 1
            self._prog_stage, self._prog_recs, self._traj_crc_hi_set = [0.0] * wfb_program.F_COUNT, [], False
            self._traj_points, self._prog_err_seg, self._last_err = [], -1, WfbErr.NONE
            return int(Outcome.APPLIED)
        if idx == ProgIdx.CLEAR:
            self._traj_state, self._traj_n, self._prog_recs, self._traj_points = TrajState.EMPTY, 0, [], []
            return int(Outcome.APPLIED)
        if self._traj_state != TrajState.LOADING:
            return self._prog_reject(WfbErr.STATE)
        if idx < wfb_program.F_COUNT:
            self._prog_stage[idx] = val
        elif idx == ProgIdx.PUSH:
            if int(val) != len(self._prog_recs) or len(self._prog_recs) >= self._traj_n:
                return self._prog_reject(WfbErr.COUNT)
            self._prog_recs.append(tuple(self._prog_stage))
        elif idx == ProgIdx.CRC_HI:
            self._traj_crc_hi, self._traj_crc_hi_set = int(val), True
        elif idx == ProgIdx.COMMIT:
            if not self._traj_crc_hi_set or len(self._prog_recs) != self._traj_n:
                return self._prog_reject(WfbErr.COUNT)
            segs = [wfb_program.Segment(wfb_program.Atom(int(r[0])), wfb_program.Profile(int(r[1])), *r[2:7],
                                        p=r[7:]) for r in self._prog_recs]
            if wfb_program.crc32(segs) != (self._traj_crc_hi << 16) | int(val):
                return self._prog_reject(WfbErr.CRC)
            try:
                pv = wfb_program.preview(segs, self._hover_z)
            except RuntimeError:
                return self._prog_reject(WfbErr.STATE)
            if not pv.ok:
                return self._prog_reject(WfbErr(pv.err), pv.err_seg)
            self._traj_points = [TrajPoint(x, y, z, yaw, t) for t, x, y, z, yaw in pv.points]
            self._traj_state, self._traj_seg, self._traj_t, self._last_err = TrajState.READY, 0, 0.0, WfbErr.NONE
        else:
            return self._prog_reject(WfbErr.RANGE)
        return int(Outcome.APPLIED)

    def _handle_traj(self, cmd: Command) -> int:
        val = cmd.value

        if cmd.index == TrajIdx.BEGIN:
            if self._traj_state == TrajState.EXECUTING:
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)
            self._prog_mode = 0
            if not math.isfinite(val) or math.floor(val) != val or not (2.0 <= val <= float(self.limits.max_points)):
                self._last_err = WfbErr.RANGE
                return int(Outcome.REJECTED)

            self._traj_n = int(val)
            self._traj_rx = 0
            self._traj_crc_hi = 0
            self._traj_crc_lo = 0
            self._traj_crc_hi_set = False
            self._traj_raw_floats = []
            self._traj_points = []
            self._traj_state = TrajState.LOADING
            self._last_err = WfbErr.NONE
            return int(Outcome.APPLIED)

        elif cmd.index == TrajIdx.APPEND:
            if self._traj_state != TrajState.LOADING:
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)
            if not math.isfinite(val):
                self._last_err = WfbErr.RANGE
                return int(Outcome.REJECTED)
            if self._traj_rx >= 5 * self._traj_n:
                self._last_err = WfbErr.COUNT
                return int(Outcome.REJECTED)

            self._traj_raw_floats.append(float(val))
            self._traj_rx += 1
            self._last_err = WfbErr.NONE
            return int(Outcome.APPLIED)

        elif cmd.index == TrajIdx.CRC_HI:
            if self._traj_state != TrajState.LOADING:
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)
            if not math.isfinite(val) or math.floor(val) != val or not (0.0 <= val <= 65535.0):
                self._last_err = WfbErr.RANGE
                return int(Outcome.REJECTED)

            self._traj_crc_hi = int(val)
            self._traj_crc_hi_set = True
            self._last_err = WfbErr.NONE
            return int(Outcome.APPLIED)

        elif cmd.index == TrajIdx.COMMIT:
            if self._traj_state != TrajState.LOADING:
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)

            if not self._traj_crc_hi_set:
                self._traj_state = TrajState.EMPTY
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)

            if self._traj_rx != 5 * self._traj_n:
                self._traj_state = TrajState.EMPTY
                self._last_err = WfbErr.COUNT
                return int(Outcome.REJECTED)

            if not math.isfinite(val) or math.floor(val) != val or not (0.0 <= val <= 65535.0):
                self._traj_state = TrajState.EMPTY
                self._last_err = WfbErr.RANGE
                return int(Outcome.REJECTED)

            points: list[TrajPoint] = []
            for i in range(self._traj_n):
                chunk = self._traj_raw_floats[i * 5 : (i + 1) * 5]
                points.append(TrajPoint(chunk[0], chunk[1], chunk[2], chunk[3], chunk[4]))

            expected_crc = (self._traj_crc_hi << 16) | int(val)
            calc_crc = crc32(points)
            self._traj_crc_hi = (calc_crc >> 16) & 0xFFFF
            self._traj_crc_lo = calc_crc & 0xFFFF

            if calc_crc != expected_crc:
                self._traj_state = TrajState.EMPTY
                self._last_err = WfbErr.CRC
                return int(Outcome.REJECTED)

            errs = validate(points, limits=self.limits, hover_z=self._hover_z)
            if errs:
                self._traj_state = TrajState.EMPTY
                first_tag = errs[0].split(":")[0].strip()
                tag_map = {
                    "COUNT": WfbErr.COUNT,
                    "RANGE": WfbErr.RANGE,
                    "TIME": WfbErr.TIME,
                    "BOUNDS": WfbErr.BOUNDS,
                    "ENDPOINT": WfbErr.ENDPOINT,
                    "SPEED": WfbErr.SPEED,
                }
                self._last_err = tag_map.get(first_tag, WfbErr.STATE)
                return int(Outcome.REJECTED)

            self._traj_state = TrajState.READY
            self._traj_points = list(points)
            self._traj_seg = 0
            self._traj_t = 0.0
            self._last_err = WfbErr.NONE
            return int(Outcome.APPLIED)

        elif cmd.index == TrajIdx.START:
            if self._traj_state != TrajState.READY or self._prim_state != PrimState.HOVER:
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)

            self._traj_state = TrajState.EXECUTING
            self._prim_state = PrimState.TRAJ
            self._traj_seg = 0
            self._traj_t = 0.0
            self._last_err = WfbErr.NONE
            return int(Outcome.APPLIED)

        elif cmd.index == TrajIdx.STOP:
            if self._traj_state != TrajState.EXECUTING:
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)

            self._traj_state = TrajState.READY
            self._prim_state = PrimState.RETURN
            self._prim_land_after_return = 0
            self._prim_x_sp = self._x if math.isfinite(self._x) else 0.0
            self._prim_y_sp = self._y if math.isfinite(self._y) else 0.0
            self._prim_return_t = 0.0
            self._last_err = WfbErr.NONE
            return int(Outcome.APPLIED)

        elif cmd.index == TrajIdx.CLEAR:
            if self._traj_state == TrajState.EXECUTING:
                self._last_err = WfbErr.STATE
                return int(Outcome.REJECTED)

            self._traj_state = TrajState.EMPTY
            self._traj_n = 0
            self._traj_rx = 0
            self._traj_crc_hi = 0
            self._traj_crc_lo = 0
            self._traj_crc_hi_set = False
            self._traj_raw_floats = []
            self._traj_points = []
            self._traj_seg = 0
            self._last_err = WfbErr.NONE
            return int(Outcome.APPLIED)

        else:
            return int(Outcome.REJECTED)

    def step(self, dt: float) -> None:
        """Advance simulation time by dt seconds."""
        if not math.isfinite(dt) or dt <= 0.0:
            raise ValueError(f"dt must be positive and finite, got {dt!r}")

        # 1. Timers
        self._hb_age += dt
        airborne = (self._prim_state != PrimState.IDLE)
        if airborne:
            self._airborne_t += dt
        if self._traj_state == TrajState.EXECUTING:
            self._traj_t += dt
        self._step_sysid(dt)

        # 2. Safety net
        out = 0
        if not airborne:
            self._tilt_t = 0.0
            self._low_v_t = 0.0
            self._fence_t = 0.0
        else:
            tilt_active = (not (abs(self.roll_deg) <= self.params.tilt_deg)
                           or not (abs(self.pitch_deg) <= self.params.tilt_deg))
            if tilt_active:
                self._tilt_t += dt
            else:
                self._tilt_t = 0.0

            if tilt_active and not (self._tilt_t < self.params.tilt_hold_s):
                if self._safety_action < Action.KILL:
                    self._safety_action = Action.KILL
                    self._safety_trip = Trip.TILT

            # Fence / ceiling: push back first (mirror of wfb_safety_step), land after fence_hold_s,
            # past fence_over_m, or with no GS flight to push with
            p = self.params
            far = 0
            if not (abs(self._x) <= p.fence_x_m):
                out |= PUSH_X
            if not (abs(self._y) <= p.fence_y_m):
                out |= PUSH_Y
            if not (self._z <= p.ceiling_m):
                out |= PUSH_Z
            if not (abs(self._x) <= p.fence_x_m + p.fence_over_m):
                far |= PUSH_X
            if not (abs(self._y) <= p.fence_y_m + p.fence_over_m):
                far |= PUSH_Y
            if not (self._z <= p.ceiling_m + p.fence_over_m):
                far |= PUSH_Z
            self._fence_t = self._fence_t + dt if out else 0.0
            land_now = out != 0 and (self._gs_flight_active == 0 or far != 0
                                     or not (self._fence_t < p.fence_hold_s))
            if land_now and self._safety_action < Action.LAND_IN_PLACE:
                self._safety_action = Action.LAND_IN_PLACE
                self._safety_trip = Trip.FENCE if (out & (PUSH_X | PUSH_Y)) else Trip.CEILING

            low_v_active = not (self.vbat_v >= self.params.low_v)
            if low_v_active:
                self._low_v_t += dt
            else:
                self._low_v_t = 0.0

            if low_v_active and not (self._low_v_t < self.params.low_v_hold_s):
                if self._safety_action < Action.LAND_VIA_HOVER:
                    self._safety_action = Action.LAND_VIA_HOVER
                    self._safety_trip = Trip.LOW_V

            if self._gs_flight_active != 0 and not (self._hb_age <= self.params.hb_timeout_s):
                if self._safety_action < Action.LAND_VIA_HOVER:
                    self._safety_action = Action.LAND_VIA_HOVER
                    self._safety_trip = Trip.HEARTBEAT

            if self._gs_flight_active != 0 and not (self._airborne_t <= self.params.airborne_cap_s):
                if self._safety_action < Action.LAND_VIA_HOVER:
                    self._safety_action = Action.LAND_VIA_HOVER
                    self._safety_trip = Trip.AIRBORNE_CAP

        self._push = out if self._safety_action == Action.NONE else 0
        if self._push and self._traj_state == TrajState.EXECUTING:
            self._traj_t -= dt   # the trajectory clock waits while pushing (wfb_glue)

        if self._safety_action == Action.KILL:
            self._motors_idle = False
            self._armed = False
            self._prim_state = PrimState.IDLE
            if self._traj_state == TrajState.EXECUTING:
                self._traj_state = TrajState.READY
            self._z = 0.0
            self._gs_flight_active = 0
            return

        elif self._safety_action == Action.LAND_IN_PLACE:
            if self._traj_state == TrajState.EXECUTING:
                self._traj_state = TrajState.READY
            if self._prim_state not in (PrimState.DESCEND, PrimState.IDLE):
                self._prim_state = PrimState.DESCEND
                self._prim_descend_frozen = 0

        elif self._safety_action == Action.LAND_VIA_HOVER:
            if self._traj_state == TrajState.EXECUTING:
                self._traj_state = TrajState.READY
            if self._prim_state not in (PrimState.RETURN, PrimState.SETTLE, PrimState.DESCEND, PrimState.IDLE):
                self._prim_state = PrimState.RETURN
                self._prim_land_after_return = 1
                self._prim_x_sp = self._x if math.isfinite(self._x) else 0.0
                self._prim_y_sp = self._y if math.isfinite(self._y) else 0.0
                self._prim_return_t = 0.0
            elif self._prim_state in (PrimState.RETURN, PrimState.SETTLE):
                if self._prim_land_after_return == 0:
                    self._prim_land_after_return = 1
                    self._prim_return_t = 0.0

        # 3. Primitive state machine
        out_x_sp = 0.0
        out_y_sp = 0.0
        out_z_sp = self._hover_z

        if self._prim_state == PrimState.IDLE:
            return

        elif self._prim_state == PrimState.CLIMB:
            out_x_sp = 0.0
            out_y_sp = 0.0
            dx = out_x_sp - self._x
            dy = out_y_sp - self._y
            dz = out_z_sp - self._z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if dist <= self.params.settle_radius_m:
                self._prim_settle_t += dt
                if self._prim_settle_t >= self.params.settle_time_s:
                    self._prim_state = PrimState.HOVER
                    self._prim_settle_t = 0.0
            else:
                self._prim_settle_t = 0.0

        elif self._prim_state == PrimState.HOVER:
            out_x_sp = 0.0
            out_y_sp = 0.0

        elif self._prim_state == PrimState.TRAJ:
            out_x_sp = 0.0
            out_y_sp = 0.0

        elif self._prim_state == PrimState.RETURN:
            self._prim_return_t += dt
            dx = 0.0 - self._prim_x_sp
            dy = 0.0 - self._prim_y_sp
            dist = math.hypot(dx, dy)
            if dist > 0.0:
                move = self.params.xy_rate_mps * dt
                if move >= dist:
                    self._prim_x_sp = 0.0
                    self._prim_y_sp = 0.0
                else:
                    rate = move / dist
                    self._prim_x_sp += dx * rate
                    self._prim_y_sp += dy * rate

            out_x_sp = self._prim_x_sp
            out_y_sp = self._prim_y_sp

            if self._prim_x_sp == 0.0 and self._prim_y_sp == 0.0:
                self._prim_state = PrimState.SETTLE
                self._prim_settle_t = 0.0

            if self._prim_land_after_return and self._prim_return_t > self.params.return_timeout_s:
                self._prim_state = PrimState.DESCEND
                self._prim_x_sp = self._x if math.isfinite(self._x) else self._prim_x_sp
                self._prim_y_sp = self._y if math.isfinite(self._y) else self._prim_y_sp
                self._prim_descend_frozen = 1
                out_x_sp = self._prim_x_sp
                out_y_sp = self._prim_y_sp

        elif self._prim_state == PrimState.SETTLE:
            self._prim_return_t += dt
            out_x_sp = 0.0
            out_y_sp = 0.0
            dx = out_x_sp - self._x
            dy = out_y_sp - self._y
            dz = out_z_sp - self._z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if dist <= self.params.settle_radius_m:
                self._prim_settle_t += dt
                if self._prim_settle_t >= self.params.settle_time_s:
                    if self._prim_land_after_return:
                        self._prim_state = PrimState.DESCEND
                        self._prim_x_sp = 0.0
                        self._prim_y_sp = 0.0
                        self._prim_descend_frozen = 1
                        out_x_sp = 0.0
                        out_y_sp = 0.0
                    else:
                        self._prim_state = PrimState.HOVER
                        self._prim_settle_t = 0.0
            else:
                self._prim_settle_t = 0.0

            if (self._prim_state == PrimState.SETTLE and self._prim_land_after_return
                    and self._prim_return_t > self.params.return_timeout_s):
                self._prim_state = PrimState.DESCEND
                self._prim_x_sp = self._x if math.isfinite(self._x) else self._prim_x_sp
                self._prim_y_sp = self._y if math.isfinite(self._y) else self._prim_y_sp
                self._prim_descend_frozen = 1
                out_x_sp = self._prim_x_sp
                out_y_sp = self._prim_y_sp

        if self._prim_state == PrimState.DESCEND:
            if not self._prim_descend_frozen:
                self._prim_x_sp = self._x if math.isfinite(self._x) else self._prim_x_sp
                self._prim_y_sp = self._y if math.isfinite(self._y) else self._prim_y_sp
                self._prim_descend_frozen = 1
            out_x_sp = self._prim_x_sp
            out_y_sp = self._prim_y_sp
            out_z_sp = 0.0

        # 4. Motion
        if self._prim_state == PrimState.TRAJ:
            if self._traj_state == TrajState.EXECUTING:
                running, pt = self._sample_traj(self._traj_t)
                if running:
                    sp = self._push_sp(pt.x, pt.y, pt.z)
                    self._yaw_deg = pt.yaw_deg
                    vec = (sp[0] - self._x, sp[1] - self._y, sp[2] - self._z)
                    dist = math.sqrt(vec[0]**2 + vec[1]**2 + vec[2]**2)
                    max_step = self.params.tracking_speed_mps * dt
                    if dist <= max_step:
                        self._x, self._y, self._z = sp
                    else:
                        self._x += (vec[0] / dist) * max_step
                        self._y += (vec[1] / dist) * max_step
                        self._z += (vec[2] / dist) * max_step
                else:
                    self._traj_state = TrajState.DONE
                    self._prim_state = PrimState.HOVER
                    sp = (0.0, 0.0, self._hover_z)
                    vec = (sp[0] - self._x, sp[1] - self._y, sp[2] - self._z)
                    dist = math.sqrt(vec[0]**2 + vec[1]**2 + vec[2]**2)
                    max_step = self.params.tracking_speed_mps * dt
                    if dist <= max_step:
                        self._x, self._y, self._z = sp
                    else:
                        self._x += (vec[0] / dist) * max_step
                        self._y += (vec[1] / dist) * max_step
                        self._z += (vec[2] / dist) * max_step

        elif self._prim_state == PrimState.CLIMB:
            dz = self._hover_z - self._z
            max_dz = self.params.climb_rate_mps * dt
            if abs(dz) <= max_dz:
                self._z = self._hover_z
            else:
                self._z += math.copysign(max_dz, dz)

            h_dist = math.hypot(-self._x, -self._y)
            max_h = self.params.tracking_speed_mps * dt
            if h_dist <= max_h:
                self._x = 0.0
                self._y = 0.0
            else:
                self._x += (-self._x / h_dist) * max_h
                self._y += (-self._y / h_dist) * max_h
            self._yaw_deg = 0.0

        elif self._prim_state == PrimState.DESCEND:
            dz = 0.0 - self._z
            max_dz = self.params.descend_rate_mps * dt
            if abs(dz) <= max_dz:
                self._z = 0.0
            else:
                self._z += math.copysign(max_dz, dz)

            dx = out_x_sp - self._x
            dy = out_y_sp - self._y
            h_dist = math.hypot(dx, dy)
            max_h = self.params.tracking_speed_mps * dt
            if h_dist <= max_h:
                self._x = out_x_sp
                self._y = out_y_sp
            else:
                self._x += (dx / h_dist) * max_h
                self._y += (dy / h_dist) * max_h

            if self._z <= 0.0:
                self._z = 0.0
                self._armed = False
                self._motors_idle = False
                self._prim_state = PrimState.IDLE
                self._gs_flight_active = 0
                self._low_v_t = 0.0
                self._tilt_t = 0.0
                self._fence_t = 0.0
                self._push = 0
                self._airborne_t = 0.0
                self._safety_trip = Trip.NONE
                self._safety_action = Action.NONE

        else:
            sp = self._push_sp(out_x_sp, out_y_sp, out_z_sp)
            vec = (sp[0] - self._x, sp[1] - self._y, sp[2] - self._z)
            dist = math.sqrt(vec[0]**2 + vec[1]**2 + vec[2]**2)
            max_step = self.params.tracking_speed_mps * dt
            if dist <= max_step:
                self._x, self._y, self._z = sp
            else:
                self._x += (vec[0] / dist) * max_step
                self._y += (vec[1] / dist) * max_step
                self._z += (vec[2] / dist) * max_step

    def _push_sp(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """Setpoint with each pushed axis moved to the soft boundary on the drone's side (wfb_safety_push_sp)."""
        p = self.params
        if self._push & PUSH_X:
            x = math.copysign(p.fence_x_m - p.soft_margin_m, -1.0 if self._x < 0.0 else 1.0)
        if self._push & PUSH_Y:
            y = math.copysign(p.fence_y_m - p.soft_margin_m, -1.0 if self._y < 0.0 else 1.0)
        if self._push & PUSH_Z:
            z = p.ceiling_m - p.soft_margin_m
        return x, y, z

    def _sample_traj(self, t_s: float) -> tuple[bool, TrajPoint]:
        n = len(self._traj_points)
        if n == 0:
            return False, TrajPoint(0.0, 0.0, 0.0, 0.0, 0.0)

        last_pt = self._traj_points[-1]
        if math.isnan(t_s) or t_s >= last_pt.t:
            return False, last_pt

        first_pt = self._traj_points[0]
        if t_s <= first_pt.t:
            self._traj_seg = 0
            return True, first_pt

        while self._traj_seg > 0 and t_s < self._traj_points[self._traj_seg].t:
            self._traj_seg -= 1
        while self._traj_seg + 1 < n - 1 and t_s >= self._traj_points[self._traj_seg + 1].t:
            self._traj_seg += 1

        k = self._traj_seg
        p0 = self._traj_points[k]
        p1 = self._traj_points[k + 1]
        dt = p1.t - p0.t
        frac = 0.0 if dt <= 0.0 else (t_s - p0.t) / dt

        x = p0.x + frac * (p1.x - p0.x)
        y = p0.y + frac * (p1.y - p0.y)
        z = p0.z + frac * (p1.z - p0.z)

        diff = self._wrap180(p1.yaw_deg - p0.yaw_deg)
        yaw = self._wrap180(p0.yaw_deg + frac * diff)
        return True, TrajPoint(x, y, z, yaw, t_s)

    @staticmethod
    def _wrap180(a: float) -> float:
        if a > 180.0:
            a -= 360.0
        elif a < -180.0:
            a += 360.0
        return a

    def status(self) -> dict[str, float]:
        """Telemetry status containing the 14 fields from interfaces.md section 2 plus prog_mode/prog_err_seg."""
        return {
            "prim_state": float(self._prim_state),
            "traj_state": float(self._traj_state),
            "traj_n": float(self._traj_n),
            "traj_rx": float(self._traj_rx),
            "traj_crc_hi": float(self._traj_crc_hi),
            "traj_crc_lo": float(self._traj_crc_lo),
            "traj_t": float(self._traj_t),
            "last_err": float(self._last_err),
            "safety_trip": float(self._safety_trip),
            "hb_age": float(self._hb_age),
            "gs_flight_active": float(self._gs_flight_active),
            "hover_z": float(self._hover_z),
            "airborne_t": float(self._airborne_t),
            "fence_push": float(self._push),
            "prog_mode": float(self._prog_mode),
            "prog_err_seg": float(self._prog_err_seg),
        }
