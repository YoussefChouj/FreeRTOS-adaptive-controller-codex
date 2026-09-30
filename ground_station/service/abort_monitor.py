"""Ground-station abort monitor for autonomous tuning flights (Workflow B).

Evaluates telemetry samples against safety limits and latches an abort decision:
  0 = NO_ABORT (keep flying)
  1 = Stop the trajectory and land
  3 = Campaign stop
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class AbortLimits:                      # PROPOSED = not yet validated on the bench or in flight
    pos_err_m: float = 0.25             # PROPOSED: 3-D distance between position and reference
    pos_err_hold_s: float = 0.3         # PROPOSED
    tilt_deg: float = 35.0              # PROPOSED: |roll| or |pitch|
    tilt_hold_s: float = 0.2            # PROPOSED
    osc_rms_dps: float = 60.0           # PROPOSED: rate-error RMS, worst axis
    osc_window_s: float = 1.0           # PROPOSED
    sat_frac: float = 0.5               # PROPOSED: mean motor-saturation fraction
    sat_window_s: float = 1.0           # PROPOSED
    stale_s: float = 0.5                # PROPOSED: telemetry age
    soc_min_pct: float = 30.0           # spec Q5, not PROPOSED
    max_consecutive_aborts: int = 2     # spec Q12, not PROPOSED


@dataclass(frozen=True)
class AbortSample:
    t_s: float                                   # host monotonic time of this step, s
    age_s: float                                 # time since the last telemetry frame arrived, s
    airborne: bool
    pos_m: tuple[float, float, float]
    ref_m: tuple[float, float, float] | None     # None when there is no position reference
    roll_deg: float
    pitch_deg: float
    rate_err_dps: tuple[float, float, float]     # roll, pitch, yaw rate error
    sat_frac: float                              # fraction of the four motors at a limit, 0..1
    safety_trip: int = 0                         # firmware trip code, 0 = none
    soc_pct: float | None = None                 # None when unknown


@dataclass(frozen=True)
class AbortDecision:
    level: int
    reason: str


NO_ABORT = AbortDecision(0, "")
TRIP_NAMES: dict[int, str] = {
    1: "HEARTBEAT",
    2: "LOW_V",
    3: "AIRBORNE_CAP",
    4: "FENCE",
    5: "CEILING",
    6: "TILT",
}


def _time_reached(elapsed: float, target: float) -> bool:
    """Check if elapsed time has reached target, accommodating floating-point representation."""
    return elapsed >= target or math.isclose(elapsed, target, rel_tol=1e-7, abs_tol=1e-7)


class AbortMonitor:
    def __init__(self, limits: AbortLimits = AbortLimits()) -> None:
        self._limits: AbortLimits = limits
        self._consecutive_aborts: int = 0
        self._decision: AbortDecision = NO_ABORT
        self._last_t_s: float | None = None
        self._tilt_t0: float | None = None
        self._pos_err_t0: float | None = None
        self._osc: collections.deque[tuple[float, tuple[float, float, float]]] = collections.deque()
        self._sat: collections.deque[tuple[float, float]] = collections.deque()
        self._osc_t_first: float | None = None
        self._sat_t_first: float | None = None

    @property
    def decision(self) -> AbortDecision:
        return self._decision

    @property
    def consecutive_aborts(self) -> int:
        return self._consecutive_aborts

    def begin_flight(self) -> None:
        self._decision = NO_ABORT
        self._last_t_s = None
        self._clear_holds_and_windows()

    def end_flight(self) -> None:
        if self._decision.level >= 1:
            self._consecutive_aborts += 1
        else:
            self._consecutive_aborts = 0

    def reset_campaign(self) -> None:
        self._consecutive_aborts = 0
        self.begin_flight()

    def _clear_holds_and_windows(self) -> None:
        self._tilt_t0 = None
        self._pos_err_t0 = None
        self._osc.clear()
        self._sat.clear()
        self._osc_t_first = None
        self._sat_t_first = None

    def _trip_level_1(self, reason: str) -> None:
        if self._decision.level >= 1:
            return
        if self._consecutive_aborts + 1 >= self._limits.max_consecutive_aborts:
            self._decision = AbortDecision(3, f"consecutive_aborts:{reason}")
        else:
            self._decision = AbortDecision(1, reason)

    def _trip_level_3(self, reason: str) -> None:
        if self._decision.level >= 3:
            return
        self._decision = AbortDecision(3, reason)

    def step(self, sample: AbortSample) -> AbortDecision:
        # 1. Latch.
        if self._decision.level >= 3:
            return self._decision

        # 2. Time.
        if not math.isfinite(sample.t_s):
            self._trip_level_1("nonfinite:t_s")
            return self._decision

        if self._last_t_s is not None and not (sample.t_s > self._last_t_s):
            return self._decision

        self._last_t_s = sample.t_s

        # 3. Level-3 checks (evaluated on every accepted sample, airborne or not, in this order):
        # 3a. safety_trip != 0
        if sample.safety_trip != 0:
            trip_str = TRIP_NAMES.get(sample.safety_trip, f"TRIP_{sample.safety_trip}")
            self._trip_level_3(f"firmware:{trip_str}")
            return self._decision

        # 3b. soc_pct is not None and not (soc_pct >= soc_min_pct)
        if sample.soc_pct is not None and not (sample.soc_pct >= self._limits.soc_min_pct):
            self._trip_level_3("battery")
            return self._decision

        # 4. Not airborne: clear the hold timers and the windows, return the latched decision.
        if not sample.airborne:
            self._clear_holds_and_windows()
            return self._decision

        # If level 1 is already latched, later steps return it unchanged unless a level-3
        # condition appears (evaluated in step 3 above).
        if self._decision.level >= 1:
            return self._decision

        # 5. Level-1 checks (airborne only), first hit wins, in this order:
        # 5a. Non-finite input: age_s, pos_m, ref_m (when not None), roll_deg, pitch_deg, rate_err_dps, sat_frac
        if not math.isfinite(sample.age_s):
            self._trip_level_1("nonfinite:age_s")
            return self._decision

        if any(not math.isfinite(v) for v in sample.pos_m):
            self._trip_level_1("nonfinite:pos_m")
            return self._decision

        if sample.ref_m is not None and any(not math.isfinite(v) for v in sample.ref_m):
            self._trip_level_1("nonfinite:ref_m")
            return self._decision

        if not math.isfinite(sample.roll_deg):
            self._trip_level_1("nonfinite:roll_deg")
            return self._decision

        if not math.isfinite(sample.pitch_deg):
            self._trip_level_1("nonfinite:pitch_deg")
            return self._decision

        if any(not math.isfinite(v) for v in sample.rate_err_dps):
            self._trip_level_1("nonfinite:rate_err_dps")
            return self._decision

        if not math.isfinite(sample.sat_frac):
            self._trip_level_1("nonfinite:sat_frac")
            return self._decision

        # 5b. Stale
        if sample.age_s > self._limits.stale_s:
            self._trip_level_1("stale_telemetry")
            return self._decision

        # 5c. Tilt: |roll_deg| > tilt_deg or |pitch_deg| > tilt_deg continuously for tilt_hold_s
        if abs(sample.roll_deg) > self._limits.tilt_deg or abs(sample.pitch_deg) > self._limits.tilt_deg:
            if self._tilt_t0 is None:
                self._tilt_t0 = sample.t_s
            if _time_reached(sample.t_s - self._tilt_t0, self._limits.tilt_hold_s):
                self._trip_level_1("tilt")
                return self._decision
        else:
            self._tilt_t0 = None

        # 5d. Position error: ref_m is not None and distance(pos_m, ref_m) > pos_err_m continuously for pos_err_hold_s
        if sample.ref_m is not None:
            pos_dist = math.dist(sample.pos_m, sample.ref_m)
            if pos_dist > self._limits.pos_err_m:
                if self._pos_err_t0 is None:
                    self._pos_err_t0 = sample.t_s
                if _time_reached(sample.t_s - self._pos_err_t0, self._limits.pos_err_hold_s):
                    self._trip_level_1("position_error")
                    return self._decision
            else:
                self._pos_err_t0 = None
        else:
            self._pos_err_t0 = None

        # Windows maintenance for 5e and 5f:
        if self._osc_t_first is None:
            self._osc_t_first = sample.t_s
        self._osc.append((sample.t_s, sample.rate_err_dps))
        osc_cutoff = sample.t_s - self._limits.osc_window_s
        while self._osc and self._osc[0][0] <= osc_cutoff:
            self._osc.popleft()

        if self._sat_t_first is None:
            self._sat_t_first = sample.t_s
        self._sat.append((sample.t_s, sample.sat_frac))
        sat_cutoff = sample.t_s - self._limits.sat_window_s
        while self._sat and self._sat[0][0] <= sat_cutoff:
            self._sat.popleft()

        # 5e. Oscillation: RMS of rate_err_dps per axis over samples in (t_now - osc_window_s, t_now]
        if _time_reached(sample.t_s - self._osc_t_first, self._limits.osc_window_s) and self._osc:
            n_osc = len(self._osc)
            rms_roll = math.sqrt(sum(pt[1][0] ** 2 for pt in self._osc) / n_osc)
            rms_pitch = math.sqrt(sum(pt[1][1] ** 2 for pt in self._osc) / n_osc)
            rms_yaw = math.sqrt(sum(pt[1][2] ** 2 for pt in self._osc) / n_osc)
            if max(rms_roll, rms_pitch, rms_yaw) > self._limits.osc_rms_dps:
                self._trip_level_1("oscillation")
                return self._decision

        # 5f. Saturation: mean of sat_frac over samples in (t_now - sat_window_s, t_now]
        if _time_reached(sample.t_s - self._sat_t_first, self._limits.sat_window_s) and self._sat:
            n_sat = len(self._sat)
            mean_sat = sum(pt[1] for pt in self._sat) / n_sat
            if mean_sat > self._limits.sat_frac:
                self._trip_level_1("motor_saturation")
                return self._decision

        return self._decision
