"""Landing report: one command from a recorder session to the touchdown numbers.

    python -m ground_station.analysis.landing_report logs/sessions/<session> [--json out.json]

Replaces the ad-hoc touchdown scripts of 2026-10-06. It measures, from the LANDING phase to disarm:
the phase timeline (RETURN, SETTLE, DESCEND), the descent segment times against the operator's best manual
landing (M8, docs/workflow-b/manual-landing-reference.md), ground contact, the spool-down (vzDes forced to 0,
motors fading), peak tilt around touchdown and the xy drift per stage. flight_debrief adds the same section to
every debrief. Parameters it relates to: docs/workflow-b/landing-parameters.md.
"""

from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path
from typing import Any, Mapping

from ground_station.livewatch.campaign_capture import MOTORS
from ground_station.service.campaign_outputs import PRIM_STATE, Series, read_telemetry

PHASE = "flight_phase"                  # StabilizerTask: 0 GROUND_IDLE, 1 FLYING, 2 LANDING, 3 LANDED
ARM = "DroneStatus.ARM_Status"
Z, Z_DES = "Ctrler.Z_posPID.FB", "Ctrler.Z_posPID.Des"
VZ_DES, VZ = "Ctrler.Z_ratePID.Des", "Ctrler.Z_ratePID.FB"
X_CM, Y_CM = "Ctrler.locxPID.FB", "Ctrler.locyPID.FB"   # cm
ROLL, PITCH = "imu_data.rol", "imu_data.pit"
PHASE_LANDING = 2
PRIM_RETURN, PRIM_SETTLE, PRIM_DESCEND = 4, 5, 6        # API/wfb_prim.h

# Descent segments (upper, lower) in m, and the operator's best manual landing M8 (2026-10-06, measured).
SEGMENTS = ((1.0, 0.5), (0.5, 0.3), (0.3, 0.13))
M8_SEGMENT_S = {(1.0, 0.5): 0.69, (0.5, 0.3): 0.23, (0.3, 0.13): 0.28}
GROUND_BAND_M = 0.01    # contact = first sample within this of the lowest height before disarm
TILT_PAD_S = 0.3        # peak tilt is taken from contact - pad to disarm + pad
START_TOL_M = 0.10      # see the segment loop
Z_STEP_M = 0.008        # a height change this big counts as a new range-sensor step


def _first(series: Series, sym: str, t_from: float, pred) -> float | None:
    ts, vs = series.get(sym, ([], []))
    i = bisect.bisect_left(ts, t_from)
    return next((t for t, v in zip(ts[i:], vs[i:]) if pred(v)), None)


def _at(series: Series, sym: str, t: float | None) -> float | None:
    ts, vs = series.get(sym, ([], []))
    if t is None or not ts:
        return None
    return vs[max(0, bisect.bisect_right(ts, t) - 1)]


def _window(series: Series, sym: str, t0: float, t1: float) -> list[float]:
    ts, vs = series.get(sym, ([], []))
    return vs[bisect.bisect_left(ts, t0):bisect.bisect_right(ts, t1)]


def _motor_avg(series: Series, t: float | None) -> float | None:
    ms = [_at(series, m, t) for m in MOTORS]
    return None if t is None or None in ms else sum(ms) / len(ms)  # type: ignore[arg-type]


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


def _xy_m(series: Series, t: float | None) -> tuple[float | None, float | None]:
    x, y = _at(series, X_CM, t), _at(series, Y_CM, t)
    return (None if x is None else x / 100.0, None if y is None else y / 100.0)


def _drift(a: tuple[float | None, float | None], b: tuple[float | None, float | None]) -> dict[str, float | None]:
    if None in a or None in b:
        return {"dx_m": None, "dy_m": None}
    return {"dx_m": _r(b[0] - a[0]), "dy_m": _r(b[1] - a[1])}  # type: ignore[operator]


def landing(series: Series) -> dict[str, Any] | None:
    """Touchdown metrics of the first LANDING phase in ``series``, or None if the flight never landed."""
    t_land = _first(series, PHASE, 0.0, lambda v: int(round(v)) == PHASE_LANDING)
    if t_land is None:
        return None
    t_end = series[PHASE][0][-1]
    t_disarm = _first(series, ARM, t_land, lambda v: int(round(v)) == 0)
    t_stop = t_disarm if t_disarm is not None else t_end

    # WFB timeline before the descent: RETURN start, SETTLE start, DESCEND start.
    t_return = _first(series, PRIM_STATE, 0.0, lambda v: int(round(v)) == PRIM_RETURN)
    t_settle = _first(series, PRIM_STATE, t_return or 0.0, lambda v: int(round(v)) == PRIM_SETTLE)
    t_descend = _first(series, PRIM_STATE, t_settle or 0.0, lambda v: int(round(v)) == PRIM_DESCEND)

    # Descent segments: first crossing of each height after LANDING starts.
    # A LANDING that starts up to START_TOL_M below a segment's top times that segment from LANDING.
    z0 = _at(series, Z, t_land)
    segments = []
    for hi, lo in SEGMENTS:
        if z0 is None or z0 < hi - START_TOL_M:
            t_hi = None
        else:
            t_hi = t_land if z0 < hi else _first(series, Z, t_land, lambda v, h=hi: v <= h)
        t_lo = None if t_hi is None else _first(series, Z, t_hi, lambda v, h=lo: v <= h)
        dt = None if t_hi is None or t_lo is None else t_lo - t_hi
        segments.append({"from_m": hi, "to_m": lo, "s": _r(dt), "m8_s": M8_SEGMENT_S[(hi, lo)],
                         "x_m8": _r(dt / M8_SEGMENT_S[(hi, lo)], 2) if dt else None})

    # The height estimate steps at the range-sensor rate: that rate sets the resolution of every time above.
    # Each sample arrives twice (slot0.X full precision, bare X rounded to 0.01), so count jumps of >= Z_STEP_M only.
    zd = _window(series, Z, t_land, t_stop)
    steps = sum(1 for a, b in zip(zd, zd[1:]) if abs(b - a) >= Z_STEP_M)
    z_step_hz = steps / (t_stop - t_land) if t_stop > t_land else None

    # Contact: first sample within GROUND_BAND_M of the lowest height before disarm.
    zs = _window(series, Z, t_land, t_stop)
    t_contact = None
    if zs:
        z_min = min(zs)
        t_contact = _first(series, Z, t_land, lambda v: v <= z_min + GROUND_BAND_M)
    # Spool: vzDes forced to exactly 0 inside LANDING (f81da19).
    t_spool = _first(series, VZ_DES, t_land, lambda v: v == 0.0)
    if t_spool is not None and t_spool > t_stop:
        t_spool = None

    t0 = (t_contact if t_contact is not None else t_stop) - TILT_PAD_S
    rol = _window(series, ROLL, t0, t_stop + TILT_PAD_S)
    pit = _window(series, PITCH, t0, t_stop + TILT_PAD_S)
    xy_land, xy_contact, xy_stop = _xy_m(series, t_land), _xy_m(series, t_contact), _xy_m(series, t_stop)

    return {
        "t_return_s": _r(t_return), "t_settle_s": _r(t_settle), "t_descend_s": _r(t_descend),
        "t_landing_s": _r(t_land), "t_contact_s": _r(t_contact), "t_spool_s": _r(t_spool),
        "t_disarm_s": _r(t_disarm),
        "settle_s": _r(t_descend - t_settle) if t_descend is not None and t_settle is not None else None,
        "landing_to_disarm_s": _r(t_stop - t_land),
        "return_to_disarm_s": _r(t_stop - t_return) if t_return is not None else None,
        "z_start_m": _r(z0),
        "z_step_hz": _r(z_step_hz, 1),
        "segments": segments,
        "contact_to_spool_s": _r(t_spool - t_contact) if t_spool is not None and t_contact is not None else None,
        "spool_s": _r(t_stop - t_spool) if t_spool is not None else None,
        "motor_avg_contact": _r(_motor_avg(series, t_contact), 0),
        "motor_avg_spool_start": _r(_motor_avg(series, t_spool), 0),
        "motor_avg_before_disarm": _r(_motor_avg(series, t_stop - 0.01), 0),
        "vz_des_at_contact": _r(_at(series, VZ_DES, t_contact)),
        "roll_peak_deg": _r(max(rol, key=abs), 1) if rol else None,
        "pitch_peak_deg": _r(max(pit, key=abs), 1) if pit else None,
        "drift_descent": _drift(xy_land, xy_contact),
        "drift_ground": _drift(xy_contact, xy_stop),
        "drift_total": _drift(xy_land, xy_stop),
    }


def _f(x: Any, unit: str = "") -> str:
    return "-" if x is None else f"{x}{unit}"


def render(m: Mapping[str, Any] | None) -> str:
    """Markdown landing section (empty-flight note if ``m`` is None)."""
    if m is None:
        return "## Landing\n\nNo LANDING phase in this session.\n"
    lines = [
        "## Landing",
        "",
        f"LANDING at {_f(m['t_landing_s'], ' s')} from z {_f(m['z_start_m'], ' m')}, contact {_f(m['t_contact_s'], ' s')}, "
        f"spool {_f(m['t_spool_s'], ' s')}, disarm {_f(m['t_disarm_s'], ' s')}. SETTLE {_f(m['settle_s'], ' s')}, "
        f"LANDING to disarm {_f(m['landing_to_disarm_s'], ' s')}, RETURN to disarm {_f(m['return_to_disarm_s'], ' s')}.",
        "",
        "| segment | time | M8 manual | x M8 |",
        "|---|---|---|---|",
    ]
    for s in m["segments"]:
        lines.append(f"| {s['from_m']} -> {s['to_m']} m | {_f(s['s'], ' s')} | {s['m8_s']} s | {_f(s['x_m8'])} |")
    d, g, t = m["drift_descent"], m["drift_ground"], m["drift_total"]
    lines += [
        "",
        "| touchdown | value |",
        "|---|---|",
        f"| vzDes at contact | {_f(m['vz_des_at_contact'], ' m/s')} |",
        f"| contact -> spool start | {_f(m['contact_to_spool_s'], ' s')} |",
        f"| spool length | {_f(m['spool_s'], ' s')} |",
        f"| motor avg contact / spool start / before disarm | {_f(m['motor_avg_contact'])} / "
        f"{_f(m['motor_avg_spool_start'])} / {_f(m['motor_avg_before_disarm'])} |",
        f"| peak roll / pitch around touchdown | {_f(m['roll_peak_deg'], ' deg')} / {_f(m['pitch_peak_deg'], ' deg')} |",
        f"| xy drift, descent (estimate) | {_f(d['dx_m'], ' m')} / {_f(d['dy_m'], ' m')} |",
        f"| xy drift, on the ground (estimate) | {_f(g['dx_m'], ' m')} / {_f(g['dy_m'], ' m')} |",
        f"| xy drift, total (estimate) | {_f(t['dx_m'], ' m')} / {_f(t['dy_m'], ' m')} |",
        "",
        f"The height estimate changes value about {_f(m['z_step_hz'])} times a second during the descent, so the "
        "segment and contact times are only as fine as one step. It freezes on the ground, so a hop does not show "
        "in z; the drift is the estimate, not a tape measure. Knobs: docs/workflow-b/landing-parameters.md.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("session", help="a recorder session dir with telemetry.csv")
    p.add_argument("--json", help="also write the metrics here")
    a = p.parse_args(argv)
    m = landing(read_telemetry(a.session))
    print(render(m), end="")
    if a.json:
        Path(a.json).write_text(json.dumps(m, indent=2), encoding="utf-8")
    return 0 if m is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
