"""Workflow B capture plan: what a tuning campaign records, at which rate, and the session manifest.

- ``CAMPAIGN_SET`` lists the recorded variables. ``needed`` covers scoring (position, motors), the abort
  monitor and clock sync; ``optional`` holds the MRAC shadow signals in priority order. This module owns
  every symbol name: the scoring adapter (ground_station/analysis/workflow_b_adapter.py) imports
  ``POSITION_AXES`` and ``MOTORS`` from here, so it cannot disagree with what was recorded.
- ``slots_for(rate_hz)`` packs the set into stream slots under the slot limits and the planning budget.
  Needed variables must all fit; optional ones are dropped from the end of the list.
- ``probe_max_rate(stream)`` finds the highest rate the live link carries without loss.
- ``write_manifest`` / ``read_manifest`` store and check a session's ``manifest.json``.

Every CAMPAIGN_SET entry is one scalar, so it takes one stream range (manifests.yaml:135 lists array
elements one by one). The planner prices each at DEFAULT_VALUE_BYTES, an upper bound: motors are 2 B and
flight_phase is 1 B.
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

from ground_station.livewatch.manifest import DEFAULT_VALUE_BYTES, REQUIRED_SYNC_VARS
from ground_station.livewatch.stream import (
    FRAME_OVERHEAD,
    MAX_SLOTS,
    MAX_STREAM_RANGES,
    SEND_TASK_HZ_SUBSCRIBE_ONLY,
    STREAM_MAX_BYTES,
)

# The lab service runs the FC in SUBSCRIBE_ONLY (service/__main__.py), where the stream ticks at 200 Hz.
SEND_TASK_HZ = SEND_TASK_HZ_SUBSCRIBE_ONLY

# --- limits and budget -------------------------------------------------------------------------------

VARS_PER_SLOT = min(MAX_STREAM_RANGES, STREAM_MAX_BYTES // DEFAULT_VALUE_BYTES)  # 62 ranges bind first
# The firmware budget guard prices a slot at tick_hz / divider (API/subscribe.c Subscribe_StreamTickHz).
# In SUBSCRIBE_ONLY, the mode the lab service runs, the stream ticks every 5 ms cycle: 200 Hz (2026-10-24).
# A plan must pass the guard of the firmware it flies on.
GUARD_SEND_HZ = SEND_TASK_HZ
RADIO_BUDGET_BPS = 87552  # USART3 radio share: 95 % of 92160 B/s (livewatch/log_frames.md:73)
PLANNING_BUDGET_BPS = 0.8 * RADIO_BUDGET_BPS  # 20 % headroom for retries and the command channel
PROBE_RATES_HZ = (200, 100, 50, 25)

MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "wfb_session_v1"

# --- the campaign set ---------------------------------------------------------------------------------


class Axis(NamedTuple):
    """One tracked position axis: feedback and reference symbols, and the factor to metres."""

    feedback: str
    reference: str
    to_m: float


# The loc loops run in cm, Z_posPID in m (TASK/StabilizerTask.c:242-244).
POSITION_AXES: tuple[Axis, ...] = (
    Axis("Ctrler.locxPID.FB", "Ctrler.locxPID.Des", 0.01),
    Axis("Ctrler.locyPID.FB", "Ctrler.locyPID.Des", 0.01),
    Axis("Ctrler.Z_posPID.FB", "Ctrler.Z_posPID.Des", 1.0),
)
MOTORS: tuple[str, ...] = ("mymotor.motor1", "mymotor.motor2", "mymotor.motor3", "mymotor.motor4")
ATTITUDE: tuple[str, ...] = ("imu_data.rol", "imu_data.pit", "imu_data.yaw")
RATE_LOOPS: tuple[str, ...] = (
    "Ctrler.gyroxPID.FB", "Ctrler.gyroxPID.Des",
    "Ctrler.gyroyPID.FB", "Ctrler.gyroyPID.Des",
    "Ctrler.gyrozPID.FB", "Ctrler.gyrozPID.Des",
    "Ctrler.Z_ratePID.FB", "Ctrler.Z_ratePID.Des",
)
# Abort monitor and clock sync, plus the airborne flag (FLYING|LANDING, TASK/StabilizerTask.c:252).
STATUS: tuple[str, ...] = REQUIRED_SYNC_VARS + ("flight_phase",)
# Workflow B primitive and safety status (API/wfb_glue.h wfb_status_t, all float) and the OF EKF health
# flag (TASK/StabilizerTask.c, u8: 1 healthy, 0 diverged). The live runner and the auto-next check read them.
WFB_STATUS_FIELDS: tuple[str, ...] = (
    "prim_state", "traj_state", "traj_n", "traj_rx", "traj_crc_hi", "traj_crc_lo", "traj_t", "last_err",
    "safety_trip", "hb_age", "gs_flight_active", "hover_z", "airborne_t", "fence_push",
)
WFB_STATUS: tuple[str, ...] = tuple(f"g_wfb_status.{f}" for f in WFB_STATUS_FIELDS)
KF_HEALTH = "g_ekf_of_health"
# Keil preset status (TASK/AutoflyTask.c): the runner's preset step waits on traj_status; its exp check reads
# exp_active (TASK/StabilizerTask.c:163, campaign_runner.py:376) and refuses the flight when it is not streamed
PRESET_STATUS: tuple[str, ...] = ("traj_status", "exp_active")

MRAC_AXES = ("pitch", "roll", "yaw", "z_rate")
def _mrac_n_features() -> int:
    """Live feature count from the flashed ELF (`mrac_n_features`: 6 STRUCT6, 30 MULTI); 6 if unreadable."""
    try:
        from ground_station.livewatch.mrac_features import read_mrac_n_features

        return int(read_mrac_n_features())
    except Exception:  # no ELF on this machine: fall back to the STRUCT6 count
        return 6


MRAC_N_FEATURES = _mrac_n_features()
# Priority order (dropping takes from the end): the control split first, then the tracking error, then
# the weights Theta and the filtered features Whatf. Arrays go index-major, so every axis keeps its
# leading features when the tail is dropped.
MRAC_SHADOW: tuple[str, ...] = (
    tuple(f"mrac_state.{axis}.{m}" for m in ("u_ad", "u_nom", "u_def", "e", "e_dot") for axis in MRAC_AXES)
    + tuple(
        f"mrac_state.{axis}.{array}[{i}]"
        for array in ("Theta", "Whatf")
        for i in range(MRAC_N_FEATURES)
        for axis in MRAC_AXES
    )
)

CAMPAIGN_SET: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "needed": (
        STATUS
        + tuple(name for axis in POSITION_AXES for name in (axis.feedback, axis.reference))
        + ATTITUDE
        + RATE_LOOPS
        + MOTORS
        + WFB_STATUS
        + (KF_HEALTH,)
        + PRESET_STATUS
    ),
    "optional": MRAC_SHADOW,
})

# Agent-picked log groups (decision 8): an experiment's log_plan names groups recorded on top of the
# always-on needed set. Inside a group the order is priority: the link budget trims from the end.
VELOCITY_LOOPS: tuple[str, ...] = (
    "Ctrler.locxsPID.FB", "Ctrler.locxsPID.Des", "Ctrler.locysPID.FB", "Ctrler.locysPID.Des",
    "g_of_hold_active",   # RC ch6: 0 = angle mode, the loops above never reach tilt (10-06 f01)
    "Ctrler.locxPID.U", "Ctrler.locyPID.U", "Ctrler.pitchPID.Des", "Ctrler.rollPID.Des",
)
# Quality, the module's own height (it scales flow to velocity) and the frame counter (stale vs live): 10-06 f05
# drifted ~1 m +x while the logged flow summed to ~7 cm, and these were missing to tell why.
OPTICAL_FLOW: tuple[str, ...] = ("ano_of.of2_dx_fix", "ano_of.of2_dy_fix", "ano_of.of_quality", "ano_of.of_alt_cm",
                                 "ano_of.of_update_cnt")
# wfb_glue.c TAKEOFF gate inputs not already in core (10-06 f02: TAKEOFF refused STATE, gate not logged)
TAKEOFF_GATE: tuple[str, ...] = ("g_motor_idle_enabled", "sbus_lost")
# Estimator truth roam (research-state-estimation-2026-10-07.md step A): raw flow, the module gyro, RPM and the FC
# IMU for the offline fits (flow scale/lag, drag k_d, thrust k_T). Names as the 10-06 livewatch roam logged them.
ESTIMATOR_TRUTH: tuple[str, ...] = (
    "ano_of.of0_dx", "ano_of.of0_dy",
    "ano_of.gyr_data_x", "ano_of.gyr_data_y", "ano_of.gyr_data_z",
    "rpm_dbg_rpm[0]", "rpm_dbg_rpm[1]", "rpm_dbg_rpm[2]", "rpm_dbg_rpm[3]",
    "Gyro_X_Real", "Gyro_Y_Real", "Gyro_Z_Real",
    "Acc_X_Real", "Acc_Y_Real", "Acc_Z_Real",
    "ano_of.of1_dx", "ano_of.of1_dy", "ano_of.of2_h", "ano_of.of2_h_f2_v",
    "ano_of.acc_data_x", "ano_of.acc_data_y", "ano_of.acc_data_z",
)
# Thrust and residual-moment fits (10-07 investigation trail): the collective and torque commands and the hover
# base (pack voltage real_voltage is already in the core set). With RPM and Acc_Z_Real from estimator_truth
# they give k_T (m*(g+a_z)/cos tilt vs sum rpm^2) and the commanded-vs-achieved moment residual. g_thrust_est.*
# are the onboard per-motor thrust estimates thrust_replay scores against the weight.
THRUST_MODEL: tuple[str, ...] = (
    ("Throttle_out", "Throttle_th", "Ctrler.Z_posPID.U", "Ctrler.Z_ratePID.U", "u_gyrox", "u_gyroy", "u_gyroz")
    + tuple(f"g_thrust_est.empirical[{i}]" for i in range(4))
    + tuple(f"g_thrust_est.blade_element[{i}]" for i in range(4))
    + ("g_thrust_est.imu_total",)
)
# The two-channel OF EKF from the inside (build 0b0c2b0b): state [px vx bof_x py vy bof_y ba_x ba_y], innovations,
# rejected-sample counters and the P_vv / P_bof variances (block row*4+col: [5] = vv, [10] = bof), so a drift
# can be pinned on the state that carried it.
EKF_STATES: tuple[str, ...] = (
    tuple(f"s_ekf_of.x[{i}]" for i in range(8))
    + ("s_ekf_of.innov_x", "s_ekf_of.innov_y", "s_ekf_of.rej_x", "s_ekf_of.rej_y",
       "g_ekf_of_fallback", "g_of_bias_mode",
       "s_ekf_of.P[0][5]", "s_ekf_of.P[1][5]", "s_ekf_of.P[0][10]", "s_ekf_of.P[1][10]")
)
# 3L-v2 (f00412a, vp rows 16-18, pitch/roll only): the row that ran, the L2 prediction error pe, the leaky angle
# integral, the filtered regressor Phi_f and the D gain P. With Theta from mrac_shadow, adaptive_review rebuilds
# negd_f = pe + Theta'Phi_f (what u_ad has to cancel), so u_f and xd_f need not be logged. The injection flag splits
# a session into MRAC and PID segments for adaptive_review (the core set does not carry it).
MRAC_3L: tuple[str, ...] = (
    ("vp_active", "mrac_flags.output_injection_on", "mrac_var_id[0]", "mrac_var_id[1]")
    + tuple(f"mrac_state.{axis}.{m}" for m in ("pe", "e_int") for axis in ("pitch", "roll"))
    + tuple(f"mrac_state.{axis}.Phi_f[{i}]" for i in range(6) for axis in ("pitch", "roll"))
    + tuple(f"mrac_state.{axis}.p_gain" for axis in ("pitch", "roll"))
)
# Gate-memory flights (lab_m_memory_*): the weights pitch and roll run with rbf_on 0 (MRAC_N_STRUCT = 6,
# API/mrac_variant.h), so memory_premise sees the drift over the figure 8 and, with mrac_3l's Phi_f[0..5],
# adaptive_review rebuilds Theta'Phi_f. With rbf_on 1 the RBF weights 6..45 come from mrac_shadow.
MRAC_THETA: tuple[str, ...] = tuple(f"mrac_state.{axis}.Theta[{i}]" for i in range(6) for axis in ("pitch", "roll"))
# Camera fusion (g_ext_pos_on 2/3): the fix innovation e_x/e_y, the applied correction c_x/c_y, accepted and rejected
# fix counts, fix age and the stale flag, so a drift can be split into camera-not-arriving vs camera-not-trusted.
CAMERA_FUSION: tuple[str, ...] = (
    "g_ext_pos_on", "g_ext_pos.e_x", "g_ext_pos.e_y", "g_ext_pos.n_ok", "g_ext_pos.n_rej",
    "g_ext_pos.age_ms", "g_ext_pos.stale", "g_ext_pos.c_x", "g_ext_pos.c_y",
)
# ADRC / LQR / MPC xy outer loops (StabilizerTask.c s_xyc[2], x then y): the kind that ran, the PID fallback, the
# predictor delay and per axis the plant lag a_est, the error integral z, the ADRC ESO z1/z2/z3 (position,
# velocity, total disturbance) and the last applied command u_prev.
XY_CTRL: tuple[str, ...] = (
    ("s_xyc_kind", "g_xy_ctrl_kind", "g_ctrl_pid_fallback_active", "g_xyc_delay_ticks")
    + tuple(f"s_xyc[{k}].{m}" for m in ("a_est", "z", "z1", "z2", "z3", "u_prev") for k in (0, 1))
)
# Every input of the camera-off drift net (video_truth.NN4_CH, slot 1 then slot 2), so each flight is training
# data and a shadow test. It overlaps estimator_truth, optical_flow and ekf_states; plan_capture records a
# variable once.
NN4: tuple[str, ...] = (
    "Acc_X_Real", "Acc_Y_Real", "Acc_Z_Real", "Gyro_X_Real", "Gyro_Y_Real", "Gyro_Z_Real", "imu_data.rol",
    "imu_data.pit", "ano_of.of2_dx_fix", "ano_of.of2_dy_fix", "ano_of.of1_dx", "ano_of.of1_dy",
    "s_ekf_of.x[1]", "s_ekf_of.x[4]", "ano_of.of_alt_cm", "s_ekf_of.innov_x", "s_ekf_of.innov_y",
)
# The onboard drift net's output (NN/drift_nn_task.h): the live switch, whether the flight loop applied the residual
# this tick and why not (DRIFT_NN_WHY_*), the clamped residual it applied, the raw residual and the EKF velocity it
# was added to, then the task's health. Shadow or live, nn_io scores vx/vy against camera truth; live_on splits a
# flight into applied and not applied.
DRIFT_NN: tuple[str, ...] = (
    "g_drift_nn_live", "g_drift_nn.live_on", "g_drift_nn.live_why", "g_drift_nn.dvx", "g_drift_nn.dvy",
    "g_drift_nn.vx", "g_drift_nn.vy", "g_drift_nn.v_in[0]", "g_drift_nn.v_in[1]", "g_drift_nn.steps",
    "g_drift_nn.status", "g_drift_nn_clamp_mps", "g_drift_nn.alt_hold", "g_drift_nn.cyc_max",
)
LOG_GROUPS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "mrac_shadow": MRAC_SHADOW,
    "mrac_3l": MRAC_3L,
    "mrac_theta": MRAC_THETA,
    "velocity_loops": VELOCITY_LOOPS,
    "optical_flow": OPTICAL_FLOW,
    "takeoff_gate": TAKEOFF_GATE,
    "estimator_truth": ESTIMATOR_TRUTH,
    "thrust_model": THRUST_MODEL,
    "ekf_states": EKF_STATES,
    "camera_fusion": CAMERA_FUSION,
    "xy_ctrl": XY_CTRL,
    "nn4": NN4,
    "drift_nn": DRIFT_NN,
})
# Two speeds: `groups` ride with the core set at rate_hz (the control lane); `slow` groups get slots of their own
# at slow_hz (never above rate_hz). The firmware guard charges each slot at its own divider (API/subscribe.c:592),
# so a 25 Hz slot costs a quarter of a 100 Hz one.
LOG_PLAN_KEYS = ("rate_hz", "groups", "slow_hz", "slow")
# PROPOSED defaults: 50 Hz is the rate the 10 s stream_log check carried with 0 dropped after the last flash;
# 25 Hz (divider 4) for estimator, drift and fit variables that change slower than the control loop.
DEFAULT_LOG_PLAN: Mapping[str, Any] = MappingProxyType({"rate_hz": 50, "groups": (), "slow_hz": 25, "slow": ()})


class CaptureError(RuntimeError):
    """The campaign set cannot be captured: no loss-free rate, or the needed variables do not fit."""


class ManifestError(ValueError):
    """A session manifest is malformed; the message names the first problem found."""


# --- slot plan and rate probe -------------------------------------------------------------------------


def slots_for(
    rate_hz: float,
    *,
    budget_bps: float = PLANNING_BUDGET_BPS,
    optional: Sequence[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Pack CAMPAIGN_SET into stream slots for ``rate_hz``; returns ``(slots, dropped)``.

    ``optional`` replaces CAMPAIGN_SET["optional"] (plan_capture passes the log_plan groups).

    Each slot is ``{"slot": id, "hz": rate the drone emits, "vars": [...]}``. Variables fill slot 0 up
    to VARS_PER_SLOT, then slot 1, and so on: needed first, then optional in CAMPAIGN_SET order until
    the next one would exceed MAX_SLOTS or ``budget_bps``. That one and every later one are ``dropped``.
    Raises CaptureError if ``rate_hz`` is not positive or the needed variables alone do not fit.
    """
    if not (isinstance(rate_hz, (int, float)) and math.isfinite(rate_hz) and rate_hz > 0):
        raise CaptureError(f"rate must be a positive number of Hz, got {rate_hz!r}")
    divider = max(1, int(SEND_TASK_HZ / rate_hz))  # as the live host does (capture_preset.py:329)

    def fits(n_vars: int) -> bool:
        return math.ceil(n_vars / VARS_PER_SLOT) <= MAX_SLOTS and _guard_bps(n_vars, divider) <= budget_bps

    needed = CAMPAIGN_SET["needed"]
    optional = CAMPAIGN_SET["optional"] if optional is None else tuple(optional)
    if not fits(len(needed)):
        raise CaptureError(
            f"the {len(needed)} needed variables need {_guard_bps(len(needed), divider):.0f} B/s at "
            f"{rate_hz} Hz, over the {budget_bps:.0f} B/s budget"
        )
    kept = len(optional)
    while not fits(len(needed) + kept):
        kept -= 1
    recorded = needed + optional[:kept]
    slots = [
        {"slot": i, "hz": SEND_TASK_HZ / divider, "vars": list(recorded[start:start + VARS_PER_SLOT])}
        for i, start in enumerate(range(0, len(recorded), VARS_PER_SLOT))
    ]
    return slots, list(optional[kept:])


def _slow_slots(
    candidates: Sequence[str], rate_hz: float, first_slot: int, used_bps: float, budget_bps: float,
) -> tuple[list[dict], list[str]]:
    """The slow lane: ``candidates`` packed into slots ``first_slot``, ``first_slot + 1``, ... at ``rate_hz``,
    trimmed from the end until they fit MAX_SLOTS and ``used_bps`` plus their cost fits ``budget_bps``."""
    divider = max(1, int(SEND_TASK_HZ / rate_hz))
    kept = len(candidates)
    while kept and (first_slot + math.ceil(kept / VARS_PER_SLOT) > MAX_SLOTS
                    or used_bps + _guard_bps(kept, divider) > budget_bps):
        kept -= 1
    slots = [
        {"slot": first_slot + i, "hz": SEND_TASK_HZ / divider, "vars": list(candidates[start:min(kept, start + VARS_PER_SLOT)])}
        for i, start in enumerate(range(0, kept, VARS_PER_SLOT))
    ]
    return slots, list(candidates[kept:])


def plan_bps(slots: Sequence[Mapping[str, Any]]) -> float:
    """What the firmware guard charges for these slots, each at its own divider (API/subscribe.c:592)."""
    return sum((FRAME_OVERHEAD + len(s["vars"]) * DEFAULT_VALUE_BYTES) * GUARD_SEND_HZ
               / max(1, round(SEND_TASK_HZ / s["hz"])) for s in slots)


def _guard_bps(n_vars: int, divider: int) -> float:
    """What the firmware guard charges for ``n_vars`` packed into full slots (API/subscribe.c:592)."""
    n_slots = math.ceil(n_vars / VARS_PER_SLOT)
    frame_bytes = n_slots * FRAME_OVERHEAD + n_vars * DEFAULT_VALUE_BYTES
    return frame_bytes * GUARD_SEND_HZ / divider


def probe_max_rate(
    stream: Callable[[float, float], float],
    rates: Sequence[float] = PROBE_RATES_HZ,
    secs: float = 3.0,
) -> float:
    """The highest rate in ``rates`` that the link carries without losing a frame.

    ``stream(rate_hz, secs)`` streams the campaign set at ``rate_hz`` for ``secs`` seconds and returns
    the fraction of frames lost. Rates are tried from high to low; the first with zero loss is returned.
    Raises CaptureError if every rate loses frames.
    """
    losses: dict[float, float] = {}
    for rate in sorted(rates, reverse=True):
        losses[rate] = stream(rate, secs)
        if losses[rate] == 0.0:
            return float(rate)
    raise CaptureError(f"every probed rate lost frames (rate Hz: loss fraction): {losses}")


# --- per-experiment log plan (decision 8) -------------------------------------------------------------


def check_log_plan(log_plan: Any) -> list[str]:
    """Problems with an experiment's ``log_plan`` (``{rate_hz, groups, slow_hz, slow}``, all optional); empty if
    valid."""
    if not isinstance(log_plan, Mapping):
        return ["must be a mapping"]
    problems = [f"unknown key {k!r} (allowed: {', '.join(LOG_PLAN_KEYS)})" for k in log_plan if k not in LOG_PLAN_KEYS]
    for key in ("rate_hz", "slow_hz"):
        rate = log_plan.get(key, DEFAULT_LOG_PLAN[key])
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= SEND_TASK_HZ:
            problems.append(f"{key}: must be a number in (0, {SEND_TASK_HZ}] (got {rate!r})")
    lanes = []
    for key in ("groups", "slow"):
        groups = log_plan.get(key, ())
        if not isinstance(groups, (list, tuple)) or not all(isinstance(g, str) for g in groups):
            problems.append(f"{key}: must be a list of group names")
            continue
        problems += [f"{key}: unknown group {g!r} (known: {', '.join(LOG_GROUPS)})"
                     for g in groups if g not in LOG_GROUPS]
        if len(set(groups)) != len(groups):
            problems.append(f"{key}: a group is listed twice")
        lanes.append(set(groups))
    if len(lanes) == 2 and lanes[0] & lanes[1]:
        problems.append(f"slow: {', '.join(sorted(lanes[0] & lanes[1]))} also in groups (a group rides one lane)")
    return problems


def plan_capture(
    log_plan: Mapping[str, Any] | None = None,
    *,
    max_rate_hz: float | None = None,
    budget_bps: float = PLANNING_BUDGET_BPS,
) -> dict[str, Any]:
    """One experiment's capture: the always-on needed set, then the log_plan groups in their order, then the
    slow groups in slots of their own.

    The rate is the log_plan ``rate_hz`` capped at ``max_rate_hz`` (probe_max_rate on the live link); the slow
    rate is ``slow_hz`` capped at that rate. A variable in several groups is recorded once, in the first lane
    that has it (fast before slow). The fast lane takes the budget first, the slow lane the slots and bytes left.
    Returns ``{"rate_hz", "requested_hz", "groups", "slow_hz", "slow", "slots", "dropped"}``; the rates are what
    the drone emits after the Send_Task divider (``slow_hz`` is None with no slow slot). Raises CaptureError on
    an invalid log_plan.
    """
    plan = {**DEFAULT_LOG_PLAN, **(log_plan or {})}
    problems = check_log_plan(plan)
    if problems:
        raise CaptureError("log_plan: " + "; ".join(problems))
    requested = float(plan["rate_hz"])
    rate = min(requested, float(max_rate_hz)) if max_rate_hz else requested
    groups, slow = list(plan["groups"]), list(plan["slow"])
    needed = set(CAMPAIGN_SET["needed"])
    fast_vars = [v for v in dict.fromkeys(v for g in groups for v in LOG_GROUPS[g]) if v not in needed]
    slots, dropped = slots_for(rate, budget_bps=budget_bps, optional=fast_vars)
    fast_hz = slots[0]["hz"]
    recorded = {v for s in slots for v in s["vars"]}
    slow_vars = [v for v in dict.fromkeys(v for g in slow for v in LOG_GROUPS[g]) if v not in recorded]
    slow_slots: list[dict] = []
    if slow_vars:
        slow_slots, slow_dropped = _slow_slots(slow_vars, min(float(plan["slow_hz"]), fast_hz), len(slots),
                                               plan_bps(slots), budget_bps)
        dropped = [v for v in dropped if v not in slow_vars] + slow_dropped
    return {"rate_hz": fast_hz, "requested_hz": requested, "groups": groups,
            "slow_hz": slow_slots[0]["hz"] if slow_slots else None, "slow": slow, "slots": slots + slow_slots,
            "dropped": dropped}


def log_plan_table(plan: Mapping[str, Any]) -> str:
    """Markdown table of a plan_capture() plan for the operator's launch approval (launch Q3)."""
    hz_of = {v: s["hz"] for s in plan["slots"] for v in s["vars"]}
    hz, slow_hz = plan["rate_hz"], plan.get("slow_hz")
    rows = ([("core (always on)", CAMPAIGN_SET["needed"], hz)] + [(g, LOG_GROUPS[g], hz) for g in plan["groups"]]
            + [(g, LOG_GROUPS[g], slow_hz) for g in plan.get("slow", ())])
    lines = ["| group | vars recorded | rate Hz | note |", "|---|---|---|---|"]
    for name, group_vars, lane_hz in rows:
        kept = sum(v in hz_of for v in group_vars)
        notes = [f"{len(group_vars) - kept} dropped (link budget)"] if kept < len(group_vars) else []
        faster = sum(lane_hz is not None and hz_of.get(v, 0) > lane_hz for v in group_vars)
        if faster:
            notes.append(f"{faster} at {hz:g} Hz (in a fast group)")
        rate = "-" if lane_hz is None else f"{lane_hz:g}"
        lines.append(f"| {name} | {kept}/{len(group_vars)} | {rate} | {'; '.join(notes)} |")
    capped = f", requested {plan['requested_hz']:g} Hz" if plan["requested_hz"] != hz else ""
    lines.append(f"{len(plan['slots'])} slot(s){capped}, {plan_bps(plan['slots']):.0f} of "
                 f"{PLANNING_BUDGET_BPS:.0f} B/s planning budget")
    return "\n".join(lines)


def subscribe_steps(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Agent plan steps (action ``subscribe``, service/agent.py) that put ``plan`` on the stream slots."""
    return [
        {"action": "subscribe",
         "args": {"slot": s["slot"], "divider": max(1, round(SEND_TASK_HZ / s["hz"])), "ranges": list(s["vars"])}}
        for s in plan["slots"]
    ]


# --- session manifest ---------------------------------------------------------------------------------

_MANIFEST_KEYS = ("schema", "pack_id", "rate_hz", "slots", "dropped", "segments")
_SLOT_KEYS = ("slot", "hz", "csv", "vars")
_SEGMENT_KEYS = ("name", "t0_host_s", "t1_host_s")


def write_manifest(
    session_dir: str | os.PathLike[str],
    *,
    pack_id: str,
    rate_hz: float,
    slots: Sequence[Mapping[str, Any]],
    dropped: Sequence[str],
    segments: Sequence[Mapping[str, Any]] = (),
) -> Path:
    """Validate and write ``<session_dir>/manifest.json``; returns its path.

    ``slots`` are slots_for() slots, each with its ``csv`` file name added. ``segments`` are
    ``{"name", "t0_host_s", "t1_host_s"}`` (host clock, s) and are written sorted by start time.
    The inputs are not modified. Raises ManifestError (and writes nothing) if the manifest is invalid.
    """
    manifest = _checked({
        "schema": MANIFEST_SCHEMA,
        "pack_id": pack_id,
        "rate_hz": rate_hz,
        "slots": slots,
        "dropped": dropped,
        "segments": segments,
    })
    path = Path(session_dir) / MANIFEST_NAME
    tmp = path.with_name(MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)  # a crash mid-write leaves the old manifest or none, never half a file
    return path


def read_manifest(session_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Read and validate ``<session_dir>/manifest.json`` (segments come back sorted by start time).

    Raises ManifestError if it is not JSON or not a valid wfb_session_v1 manifest.
    """
    path = Path(session_dir) / MANIFEST_NAME
    try:
        return _checked(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path}: not valid JSON ({exc})") from exc
    except ManifestError as exc:
        raise ManifestError(f"{path}: {exc}") from None


def _checked(manifest: Any) -> dict[str, Any]:
    """A validated copy of ``manifest`` with plain lists and floats; raises ManifestError."""
    _require_keys(manifest, _MANIFEST_KEYS, "manifest")
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise ManifestError(f"schema is {manifest['schema']!r}, expected {MANIFEST_SCHEMA!r}")
    pack_id = manifest["pack_id"]
    if not isinstance(pack_id, str) or not pack_id:
        raise ManifestError(f"pack_id must be a non-empty string, got {pack_id!r}")
    slots = _checked_slots(manifest["slots"])
    dropped = _names(manifest["dropped"], "dropped")
    both = set(dropped).intersection(v for s in slots for v in s["vars"])
    if both:
        raise ManifestError(f"{sorted(both)} are both recorded and dropped")
    return {
        "schema": MANIFEST_SCHEMA,
        "pack_id": pack_id,
        "rate_hz": _positive(manifest["rate_hz"], "rate_hz"),
        "slots": slots,
        "dropped": dropped,
        "segments": _checked_segments(manifest["segments"]),
    }


def _checked_slots(raw: Any) -> list[dict[str, Any]]:
    slots = [_checked_slot(item) for item in _sequence(raw, "slots")]
    if not 1 <= len(slots) <= MAX_SLOTS:
        raise ManifestError(f"{len(slots)} slots; a session has 1 to {MAX_SLOTS}")
    for key in ("slot", "csv"):
        _no_repeats([s[key] for s in slots], f"slot {key}")
    _no_repeats([v for s in slots for v in s["vars"]], "recorded variable")
    return slots


def _checked_slot(item: Any) -> dict[str, Any]:
    _require_keys(item, _SLOT_KEYS, "slot")
    slot = item["slot"]
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < MAX_SLOTS:
        raise ManifestError(f"slot id must be an int in 0..{MAX_SLOTS - 1}, got {slot!r}")
    csv_name = item["csv"]
    if not isinstance(csv_name, str) or csv_name in ("", ".", "..") or "/" in csv_name or "\\" in csv_name:
        raise ManifestError(f"slot {slot} csv must be a file name in the session directory, got {csv_name!r}")
    names = _names(item["vars"], f"slot {slot} vars")
    if not names:
        raise ManifestError(f"slot {slot} records no variables")
    return {"slot": slot, "hz": _positive(item["hz"], f"slot {slot} hz"), "csv": csv_name, "vars": names}


def _checked_segments(raw: Any) -> list[dict[str, Any]]:
    segments = sorted((_checked_segment(item) for item in _sequence(raw, "segments")),
                      key=lambda s: s["t0_host_s"])
    _no_repeats([s["name"] for s in segments], "segment name")
    for prev, cur in zip(segments, segments[1:]):
        if cur["t0_host_s"] < prev["t1_host_s"]:
            raise ManifestError(f"segments {prev['name']!r} and {cur['name']!r} overlap")
    return segments


def _checked_segment(item: Any) -> dict[str, Any]:
    _require_keys(item, _SEGMENT_KEYS, "segment")
    name = item["name"]
    if not isinstance(name, str) or not name:
        raise ManifestError(f"segment name must be a non-empty string, got {name!r}")
    t0, t1 = (_finite(item[key], f"segment {name!r} {key}") for key in ("t0_host_s", "t1_host_s"))
    if not t0 < t1:
        raise ManifestError(f"segment {name!r} must start before it ends (t0 {t0}, t1 {t1})")
    return {"name": name, "t0_host_s": t0, "t1_host_s": t1}


def _require_keys(obj: Any, keys: Sequence[str], what: str) -> None:
    if not isinstance(obj, Mapping):
        raise ManifestError(f"{what} must be a JSON object, got {type(obj).__name__}")
    missing = [k for k in keys if k not in obj]
    extra = sorted(set(obj) - set(keys))
    if missing or extra:
        raise ManifestError(f"{what} keys: missing {missing}, unexpected {extra}")


def _sequence(value: Any, what: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise ManifestError(f"{what} must be a list, got {type(value).__name__}")
    return value


def _names(value: Any, what: str) -> list[str]:
    names = list(_sequence(value, what))
    if not all(isinstance(n, str) and n for n in names):
        raise ManifestError(f"{what} must hold non-empty strings")
    return names


def _no_repeats(values: Sequence[Any], what: str) -> None:
    repeated = sorted(str(v) for v, count in Counter(values).items() if count > 1)
    if repeated:
        raise ManifestError(f"repeated {what}: {repeated}")


def _finite(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ManifestError(f"{what} must be a finite number, got {value!r}")
    return float(value)


def _positive(value: Any, what: str) -> float:
    number = _finite(value, what)
    if number <= 0:
        raise ManifestError(f"{what} must be positive, got {value!r}")
    return number
