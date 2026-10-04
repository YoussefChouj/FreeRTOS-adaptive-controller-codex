"""Trajectory pipeline for autonomous tuning flights (Workflow B, Task G2).

Turns a geometric shape into a timed list of 3-D trajectory points accepted
by firmware CMD 0x1B, and predicts the firmware commit verdict.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class TrajPoint:
    x: float
    y: float
    z: float
    yaw_deg: float
    t: float


@dataclass(frozen=True)
class TrajLimits:
    x_abs_m: float = 1.3   # WFB_TRAJ_LIMITS_ROW in API/wfb_traj.c: 0.3 m inside the fence/ceiling
    y_abs_m: float = 1.7
    z_min_m: float = 0.3
    z_max_m: float = 1.4
    v_max_mps: float = 1.0
    endpoint_tol_m: float = 0.10
    yaw_abs_deg: float = 180.0
    max_points: int = 600


@dataclass(frozen=True)
class Profile:
    v_cruise_mps: float
    a_max_mps2: float
    ds_m: float
    hover_z_m: float
    yaw_deg: float = 0.0


def _shape_line(params: dict) -> list[tuple[float, float]]:
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")
    if "length_m" not in params:
        raise ValueError("length_m parameter is missing")
    length_m = params["length_m"]
    if not isinstance(length_m, (int, float)) or length_m <= 0.0 or not math.isfinite(length_m):
        raise ValueError("length_m must be positive and finite")

    if "heading_deg" not in params:
        raise ValueError("heading_deg parameter is missing")
    heading_deg = params["heading_deg"]
    if not isinstance(heading_deg, (int, float)) or not math.isfinite(heading_deg):
        raise ValueError("heading_deg must be finite")

    rad = math.radians(float(heading_deg))
    x_far = float(length_m) * math.cos(rad)
    y_far = float(length_m) * math.sin(rad)
    return [(0.0, 0.0), (x_far, y_far), (0.0, 0.0)]


def _shape_circle(params: dict) -> list[tuple[float, float]]:
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")
    if "radius_m" not in params:
        raise ValueError("radius_m parameter is missing")
    radius_m = params["radius_m"]
    if not isinstance(radius_m, (int, float)) or radius_m <= 0.0 or not math.isfinite(radius_m):
        raise ValueError("radius_m must be positive and finite")

    r = float(radius_m)
    # Circle starts at the origin (0, 0), tangent to the x axis, center at (0, r).
    # Parameterization: x(phi) = r * sin(phi), y(phi) = r * (1 - cos(phi))
    # Segment count ensures chord error <= 1 mm (0.001 m).
    # Sagitta = r * (1 - cos(d_phi / 2)) <= 0.001 -> cos(d_phi / 2) >= 1 - 0.001 / r.
    if r > 0.001:
        d_phi_max = 2.0 * math.acos(max(-1.0, 1.0 - 0.001 / r))
        n_pts = max(100, int(math.ceil(2.0 * math.pi / d_phi_max)))
    else:
        n_pts = 100

    points: list[tuple[float, float]] = []
    for i in range(n_pts):
        phi = 2.0 * math.pi * i / n_pts
        points.append((r * math.sin(phi), r * (1.0 - math.cos(phi))))
    points.append((0.0, 0.0))
    return points


def _shape_figure8(params: dict) -> list[tuple[float, float]]:
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")
    if "width_m" not in params:
        raise ValueError("width_m parameter is missing")
    width_m = params["width_m"]
    if not isinstance(width_m, (int, float)) or width_m <= 0.0 or not math.isfinite(width_m):
        raise ValueError("width_m must be positive and finite")

    if "height_m" not in params:
        raise ValueError("height_m parameter is missing")
    height_m = params["height_m"]
    if not isinstance(height_m, (int, float)) or height_m <= 0.0 or not math.isfinite(height_m):
        raise ValueError("height_m must be positive and finite")

    w = float(width_m)
    h = float(height_m)
    # Lemniscate of Gerono (Lissajous 1:2) with crossing point at (0, 0).
    # x(t) = (w / 2) * sin(2t), y(t) = (h / 2) * sin(t) for t in [0, 2pi].
    # Spans: x in [-w/2, w/2] (span w), y in [-h/2, h/2] (span h).
    # Dense sampling so chord error <= 1 mm.
    n_pts = max(500, int(math.ceil(2.0 * math.pi * math.hypot(w, h) / 0.005)))
    points: list[tuple[float, float]] = []
    for i in range(n_pts):
        t = 2.0 * math.pi * i / n_pts
        points.append(((w / 2.0) * math.sin(2.0 * t), (h / 2.0) * math.sin(t)))
    points.append((0.0, 0.0))
    return points


def _shape_square(params: dict) -> list[tuple[float, float]]:
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")
    if "side_m" not in params:
        raise ValueError("side_m parameter is missing")
    side_m = params["side_m"]
    if not isinstance(side_m, (int, float)) or side_m <= 0.0 or not math.isfinite(side_m):
        raise ValueError("side_m must be positive and finite")

    s = float(side_m)
    # Square with one corner at (0, 0)
    return [(0.0, 0.0), (s, 0.0), (s, s), (0.0, s), (0.0, 0.0)]


def _shape_library(params: dict) -> list[tuple[float, float]]:
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")
    if "path_id" not in params:
        raise ValueError("path_id parameter is missing")
    path_id = params["path_id"]
    if not isinstance(path_id, str) or not path_id:
        raise ValueError("path_id must be a non-empty string")

    from ground_station.service.path_library import get_path

    data = get_path(path_id)
    if data is None or not isinstance(data, dict) or "points" not in data or not data["points"]:
        raise ValueError(f"path_id {path_id} not found in path library")

    raw_points = data["points"]
    if len(raw_points) < 1:
        raise ValueError(f"path_id {path_id} has no points")

    first = raw_points[0]
    x0 = float(first["x"] if isinstance(first, dict) else first[0])
    y0 = float(first["y"] if isinstance(first, dict) else first[1])

    # Convert cm to m, shift first point to (0, 0)
    points: list[tuple[float, float]] = []
    for pt in raw_points:
        px = float(pt["x"] if isinstance(pt, dict) else pt[0])
        py = float(pt["y"] if isinstance(pt, dict) else pt[1])
        points.append(((px - x0) / 100.0, (py - y0) / 100.0))

    # Append closing point when the path is open
    if math.hypot(points[-1][0], points[-1][1]) > 1e-6:
        points.append((0.0, 0.0))

    return points


WAYPOINTS_MAX = 100          # waypoints per path; the resampled path still has to fit TrajLimits.max_points
WAYPOINTS_CORNER_CUT_MAX = 3  # Chaikin passes; each one cuts every corner at 1/4 and 3/4 of its segments


def _shape_waypoints(params: dict) -> list[tuple[float, float]]:
    """Explicit waypoints in metres, in the ground-centre frame (the hover point is (0, 0)).

    params: points_m = [[x, y], ...] (1..WAYPOINTS_MAX), corner_cut = 0..3 (optional, default 0).
    The path starts and ends at the hover point: (0, 0) is added in front and at the back when missing.
    corner_cut > 0 rounds the corners (Chaikin), so the reference velocity does not jump at a corner; the
    rounded path passes near the waypoints, not through them.
    """
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")
    raw = params.get("points_m")
    if not isinstance(raw, list) or not 1 <= len(raw) <= WAYPOINTS_MAX:
        raise ValueError(f"points_m must be a list of 1..{WAYPOINTS_MAX} [x, y] pairs")
    cut = params.get("corner_cut", 0)
    if not isinstance(cut, int) or isinstance(cut, bool) or not 0 <= cut <= WAYPOINTS_CORNER_CUT_MAX:
        raise ValueError(f"corner_cut must be an int 0..{WAYPOINTS_CORNER_CUT_MAX}")

    points: list[tuple[float, float]] = []
    for i, pt in enumerate(raw):
        if (not isinstance(pt, (list, tuple)) or len(pt) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in pt)):
            raise ValueError(f"points_m[{i}] must be a finite [x, y] pair")
        points.append((float(pt[0]), float(pt[1])))
    if math.hypot(*points[0]) > 1e-6:
        points.insert(0, (0.0, 0.0))
    if math.hypot(*points[-1]) > 1e-6:
        points.append((0.0, 0.0))

    for _ in range(cut):
        cut_pts = [points[0]]
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            cut_pts.append((0.75 * x1 + 0.25 * x2, 0.75 * y1 + 0.25 * y2))
            cut_pts.append((0.25 * x1 + 0.75 * x2, 0.25 * y1 + 0.75 * y2))
        cut_pts.append(points[-1])
        points = cut_pts
    return points


SHAPES: dict[str, Callable[[dict], list[tuple[float, float]]]] = {
    "line": _shape_line,
    "circle": _shape_circle,
    "figure8": _shape_figure8,
    "square": _shape_square,
    "library": _shape_library,
    "waypoints": _shape_waypoints,
}


def shape_xy(shape: str, params: dict) -> list[tuple[float, float]]:
    """Step 1: Returns a closed list of (x, y) coordinates in metres."""
    if shape not in SHAPES:
        raise ValueError(f"Unknown shape: '{shape}'")
    return SHAPES[shape](params)


def tilt(
    points_xy: list[tuple[float, float]],
    hover_z_m: float,
    tilt_deg: float,
    axis_deg: float,
) -> list[tuple[float, float, float]]:
    """Step 2: Rotates the flat shape about the horizontal axis through the hover point."""
    z_hover = float(hover_z_m)
    if tilt_deg == 0.0:
        return [(float(x), float(y), z_hover) for x, y in points_xy]

    alpha = math.radians(float(tilt_deg))
    theta_a = math.radians(float(axis_deg))
    ux = math.cos(theta_a)
    uy = math.sin(theta_a)
    cos_a = math.cos(alpha)
    sin_a = math.sin(alpha)

    # Rodrigues' rotation about unit axis u = (ux, uy, 0) through hover point (0, 0, z_hover)
    out: list[tuple[float, float, float]] = []
    for x, y in points_xy:
        xf = float(x)
        yf = float(y)
        dot = ux * xf + uy * yf
        rx = xf * cos_a + ux * dot * (1.0 - cos_a)
        ry = yf * cos_a + uy * dot * (1.0 - cos_a)
        rz = (ux * yf - uy * xf) * sin_a
        out.append((rx, ry, z_hover + rz))

    return out


def resample(
    points_xyz: list[tuple[float, float, float]],
    ds_m: float,
) -> list[tuple[float, float, float]]:
    """Step 3: Fixed arc-length resample along the 3-D polyline."""
    if ds_m <= 0.0 or not math.isfinite(ds_m):
        raise ValueError("ds_m must be positive and finite")
    if len(points_xyz) < 2:
        return [(float(x), float(y), float(z)) for x, y, z in points_xyz]

    cum_dist = [0.0]
    for i in range(len(points_xyz) - 1):
        x1, y1, z1 = points_xyz[i]
        x2, y2, z2 = points_xyz[i + 1]
        seg_d = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)
        cum_dist.append(cum_dist[-1] + seg_d)

    total_len = cum_dist[-1]
    if total_len == 0.0:
        return [points_xyz[0], points_xyz[-1]]

    n_segments = math.ceil(total_len / ds_m)
    if n_segments < 1:
        n_segments = 1
    step = total_len / n_segments

    out: list[tuple[float, float, float]] = [points_xyz[0]]
    seg_idx = 0
    num_pts = len(points_xyz)

    for k in range(1, n_segments):
        target_s = k * step
        while seg_idx < num_pts - 2 and cum_dist[seg_idx + 1] < target_s:
            seg_idx += 1
        d_seg = cum_dist[seg_idx + 1] - cum_dist[seg_idx]
        if d_seg <= 0.0:
            frac = 0.0
        else:
            frac = (target_s - cum_dist[seg_idx]) / d_seg
        x1, y1, z1 = points_xyz[seg_idx]
        x2, y2, z2 = points_xyz[seg_idx + 1]
        out.append((
            x1 + frac * (x2 - x1),
            y1 + frac * (y2 - y1),
            z1 + frac * (z2 - z1),
        ))

    out.append(points_xyz[-1])
    return out


def time_profile(
    points_xyz: list[tuple[float, float, float]],
    profile: Profile,
) -> list[TrajPoint]:
    """Step 4: Assigns timestamps along arc length via trapezoidal / triangular speed profile."""
    if profile.v_cruise_mps <= 0.0 or not math.isfinite(profile.v_cruise_mps):
        raise ValueError("v_cruise_mps must be positive and finite")
    if profile.a_max_mps2 <= 0.0 or not math.isfinite(profile.a_max_mps2):
        raise ValueError("a_max_mps2 must be positive and finite")
    if len(points_xyz) < 2:
        if len(points_xyz) == 1:
            x, y, z = points_xyz[0]
            return [TrajPoint(float(x), float(y), float(z), float(profile.yaw_deg), 0.0)]
        return []

    cum_dist = [0.0]
    for i in range(len(points_xyz) - 1):
        x1, y1, z1 = points_xyz[i]
        x2, y2, z2 = points_xyz[i + 1]
        seg_d = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)
        cum_dist.append(cum_dist[-1] + seg_d)

    L = cum_dist[-1]
    v = float(profile.v_cruise_mps)
    a = float(profile.a_max_mps2)
    yaw = float(profile.yaw_deg)

    s_acc = (v * v) / (2.0 * a)
    is_trapezoidal = L > (v * v) / a

    if is_trapezoidal:
        t_acc = v / a
        s_cruise_end = L - s_acc
        T = (L / v) + (v / a)

        def t_at_s(s: float) -> float:
            if s <= 0.0:
                return 0.0
            if s >= L:
                return T
            if s <= s_acc:
                return math.sqrt(2.0 * s / a)
            if s <= s_cruise_end:
                return t_acc + (s - s_acc) / v
            d_rem = max(0.0, L - s)
            return T - math.sqrt(2.0 * d_rem / a)

    else:
        v_peak = math.sqrt(a * L) if L > 0.0 else 0.0
        t_acc = v_peak / a if a > 0.0 else 0.0
        T = 2.0 * t_acc
        s_half = L / 2.0

        def t_at_s(s: float) -> float:
            if s <= 0.0:
                return 0.0
            if s >= L:
                return T
            if s <= s_half:
                return math.sqrt(2.0 * s / a)
            d_rem = max(0.0, L - s)
            return T - math.sqrt(2.0 * d_rem / a)

    out: list[TrajPoint] = []
    for i, (x, y, z) in enumerate(points_xyz):
        s = cum_dist[i]
        t = t_at_s(s)
        out.append(TrajPoint(float(x), float(y), float(z), yaw, t))

    return out


def _round32(v: float) -> float:
    """Value as the firmware sees it; a magnitude beyond float32 becomes infinite."""
    try:
        return struct.unpack("<f", struct.pack("<f", float(v)))[0]
    except OverflowError:
        return math.copysign(math.inf, v)


def validate(
    points: list[TrajPoint],
    limits: TrajLimits,
    hover_z: float,
) -> list[str]:
    """Validates points against firmware commit checks using float32 rounding."""
    errors: list[str] = []
    n = len(points)

    # Check 1: COUNT
    if n < 2:
        errors.append(f"COUNT: point count {n} is fewer than 2")
    elif n > limits.max_points:
        errors.append(f"COUNT: point count {n} exceeds limits.max_points {limits.max_points}")

    pts_f32: list[tuple[float, float, float, float, float]] = []
    for pt in points:
        pts_f32.append((
            _round32(pt.x),
            _round32(pt.y),
            _round32(pt.z),
            _round32(pt.yaw_deg),
            _round32(pt.t),
        ))

    # Check 2: RANGE (any field non-finite once rounded to float32)
    for i, fields in enumerate(pts_f32):
        if not all(math.isfinite(v) for v in fields):
            errors.append(f"RANGE: point {i} contains non-finite field")

    # Check 3: TIME (t[0] != 0, or t not strictly increasing)
    if n > 0:
        if pts_f32[0][4] != 0.0 or math.isnan(pts_f32[0][4]):
            errors.append(f"TIME: point 0 t ({pts_f32[0][4]}) != 0.0")
        for i in range(1, n):
            t_prev = pts_f32[i - 1][4]
            t_curr = pts_f32[i][4]
            if math.isnan(t_curr) or math.isnan(t_prev) or t_curr <= t_prev:
                errors.append(f"TIME: point {i} t ({t_curr}) not strictly greater than point {i - 1} t ({t_prev})")

    # Check 4: BOUNDS (|x| > x_abs_m, |y| > y_abs_m, z outside z_min_m..z_max_m, |yaw_deg| > yaw_abs_deg)
    x_abs_lim = _round32(limits.x_abs_m)
    y_abs_lim = _round32(limits.y_abs_m)
    z_min_lim = _round32(limits.z_min_m)
    z_max_lim = _round32(limits.z_max_m)
    yaw_abs_lim = _round32(limits.yaw_abs_deg)

    for i, (x, y, z, yaw, _) in enumerate(pts_f32):
        if math.isfinite(x) and abs(x) > x_abs_lim:
            errors.append(f"BOUNDS: point {i} |x| ({abs(x):.4f}) > {x_abs_lim}")
        if math.isfinite(y) and abs(y) > y_abs_lim:
            errors.append(f"BOUNDS: point {i} |y| ({abs(y):.4f}) > {y_abs_lim}")
        if math.isfinite(z) and (z < z_min_lim or z > z_max_lim):
            errors.append(f"BOUNDS: point {i} z ({z:.4f}) outside [{z_min_lim}, {z_max_lim}]")
        if math.isfinite(yaw) and abs(yaw) > yaw_abs_lim:
            errors.append(f"BOUNDS: point {i} |yaw_deg| ({abs(yaw):.4f}) > {yaw_abs_lim}")

    # Check 5: ENDPOINT (3-D distance from the first or the last point to (0, 0, hover_z) > endpoint_tol_m)
    tol_lim = _round32(limits.endpoint_tol_m)
    hover_z_f32 = _round32(hover_z)

    if n > 0:
        x0, y0, z0, _, _ = pts_f32[0]
        if math.isfinite(x0) and math.isfinite(y0) and math.isfinite(z0):
            d0 = math.sqrt(x0 * x0 + y0 * y0 + (z0 - hover_z_f32) ** 2)
            if d0 > tol_lim:
                errors.append(f"ENDPOINT: point 0 distance to hover point ({d0:.4f}) > {tol_lim}")

    if n > 1:
        x_end, y_end, z_end, _, _ = pts_f32[-1]
        if math.isfinite(x_end) and math.isfinite(y_end) and math.isfinite(z_end):
            d_end = math.sqrt(x_end * x_end + y_end * y_end + (z_end - hover_z_f32) ** 2)
            if d_end > tol_lim:
                errors.append(f"ENDPOINT: point {n - 1} distance to hover point ({d_end:.4f}) > {tol_lim}")

    # Check 6: SPEED (3-D distance between consecutive points / their time difference > v_max_mps)
    v_max_lim = _round32(limits.v_max_mps)
    if n > 1:
        for i in range(n - 1):
            x1, y1, z1, _, t1 = pts_f32[i]
            x2, y2, z2, _, t2 = pts_f32[i + 1]
            if (
                math.isfinite(x1) and math.isfinite(y1) and math.isfinite(z1) and math.isfinite(t1)
                and math.isfinite(x2) and math.isfinite(y2) and math.isfinite(z2) and math.isfinite(t2)
            ):
                dt = t2 - t1
                if dt > 0.0:
                    dist = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)
                    speed = dist / dt
                    if speed > v_max_lim:
                        errors.append(f"SPEED: point {i + 1} segment speed ({speed:.4f}) > {v_max_lim}")

    return errors


def generate(
    shape: str,
    params: dict,
    profile: Profile,
    limits: TrajLimits = TrajLimits(),
) -> list[TrajPoint]:
    """Generates trajectory points through steps 1-4 and validates them."""
    pts_xy = shape_xy(shape, params)
    tilt_deg = float(params.get("tilt_deg", 0.0))
    axis_deg = float(params.get("axis_deg", 0.0))
    pts_xyz = tilt(pts_xy, hover_z_m=profile.hover_z_m, tilt_deg=tilt_deg, axis_deg=axis_deg)
    resampled_xyz = resample(pts_xyz, ds_m=profile.ds_m)
    points = time_profile(resampled_xyz, profile=profile)

    errs = validate(points, limits=limits, hover_z=profile.hover_z_m)
    if errs:
        raise ValueError("; ".join(errs))

    return points


def crc32(points: list[TrajPoint]) -> int:
    """Computes IEEE 802.3 CRC32 matching firmware CMD 0x1B COMMIT check."""
    n = len(points)
    if n == 0:
        return 0
    floats: list[float] = []
    for pt in points:
        floats.extend([pt.x, pt.y, pt.z, pt.yaw_deg, pt.t])
    packed = struct.pack(f"<{5 * n}f", *floats)
    return zlib.crc32(packed) & 0xFFFFFFFF
