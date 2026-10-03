"""Per-sample safety checks for a live-tune window.

    stop  the run must end: link lost (stale or no telemetry), prim_state left HOVER (RC takeover or a firmware
          landing: the tuner never fights them), a firmware safety trip, battery below soc_min_pct, or the real-frame
          position (origin walk + pos) outside the fence box
    trip  the candidate is to blame: |roll| or |pitch| over angle_deg, a rate-error spike, motor saturation held for
          sat_hold_s, or the drone more than hold_radius_m from the hover point (fence proximity)

The session reverts to the baseline gains on both; a trip also marks the candidate infeasible and shrinks sigma.
Samples are AbortSample-shaped (ground_station/service/abort_monitor.py); rate_err_dps is (roll, pitch, yaw).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

PRIM_HOVER = 2  # g_wfb_status.prim_state HOVER (docs/workflow-b/interfaces.md, campaign_runner.PRIM_HOVER)


@dataclass(frozen=True)
class SafetyLimits:
    angle_deg: float = 15.0        # PROPOSED: SysID aborts at 30 (API/sysid.c:24), the abort monitor at 35
    rate_err_dps: float = 100.0    # PROPOSED: one sample, roll or pitch
    sat_frac: float = 0.5          # PROPOSED: fraction of motors at a limit ...
    sat_hold_s: float = 0.3        # PROPOSED: ... held this long
    hold_radius_m: float = 0.5     # PROPOSED: = SysID green zone 50 cm (API/sysid.c:18)
    stale_s: float = 0.5           # = AbortLimits.stale_s
    soc_min_pct: float = 40.0      # PROPOSED: stop tuning above the abort monitor's 30 % floor
    max_consecutive_fail: int = 3  # PROPOSED


@dataclass(frozen=True)
class Verdict:
    kind: str       # "ok", "trip" or "stop"
    code: str = ""  # stop: the session's end status (link_lost, not_hovering, safety_trip, battery, fence)
    reason: str = ""


OK = Verdict("ok")


class Supervisor:
    def __init__(self, limits: SafetyLimits, hover_m: tuple[float, float, float],
                 fence_xy_m: tuple[float, float]) -> None:
        self.limits = limits
        self.hover_m = hover_m
        self.fence_xy_m = fence_xy_m
        self._sat_t0: float | None = None

    def check(self, sample: Any, prim_state: int, walk_xy: tuple[float, float] = (0.0, 0.0)) -> Verdict:
        lim = self.limits
        if sample is None or not sample.age_s <= lim.stale_s:
            return Verdict("stop", "link_lost", f"link lost: telemetry {getattr(sample, 'age_s', math.inf):.2f} s old")
        if prim_state != PRIM_HOVER:
            return Verdict("stop", "not_hovering", f"prim_state {prim_state} is not HOVER (RC takeover or firmware landing)")
        if sample.safety_trip:
            return Verdict("stop", "safety_trip", f"firmware safety_trip {sample.safety_trip}")
        if sample.soc_pct is not None and sample.soc_pct < lim.soc_min_pct:
            return Verdict("stop", "battery", f"battery {sample.soc_pct:.0f} % < {lim.soc_min_pct:.0f} %")
        x, y = walk_xy[0] + sample.pos_m[0], walk_xy[1] + sample.pos_m[1]
        if abs(x) > self.fence_xy_m[0] or abs(y) > self.fence_xy_m[1]:
            return Verdict("stop", "fence", f"real position ({x:.2f}, {y:.2f}) m outside the fence box {self.fence_xy_m}")

        if not (abs(sample.roll_deg) <= lim.angle_deg and abs(sample.pitch_deg) <= lim.angle_deg):
            return Verdict("trip", "", f"angle roll {sample.roll_deg:.1f} pitch {sample.pitch_deg:.1f} deg > {lim.angle_deg}")
        e = max(abs(sample.rate_err_dps[0]), abs(sample.rate_err_dps[1]))
        if not e <= lim.rate_err_dps:
            return Verdict("trip", "", f"rate error {e:.0f} deg/s > {lim.rate_err_dps:.0f}")
        if sample.sat_frac >= lim.sat_frac:
            if self._sat_t0 is None:
                self._sat_t0 = sample.t_s
            elif sample.t_s - self._sat_t0 >= lim.sat_hold_s:
                return Verdict("trip", "", f"motor saturation {sample.sat_frac:.2f} for {lim.sat_hold_s} s")
        else:
            self._sat_t0 = None
        d = math.hypot(sample.pos_m[0] - self.hover_m[0], sample.pos_m[1] - self.hover_m[1])
        if d > lim.hold_radius_m:
            return Verdict("trip", "", f"{d:.2f} m from the hover point > {lim.hold_radius_m} m (fence proximity)")
        return OK

    def reset(self) -> None:
        self._sat_t0 = None
