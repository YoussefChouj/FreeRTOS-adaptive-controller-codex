"""Tests for trajectory pipeline module (Task G2).

Verifies shapes, tilt rotation, arc-length resampling, timing profile,
validation against firmware commit checks, and CRC32 computation.
"""

from __future__ import annotations

import math
import struct
import zlib
import pytest

from ground_station.service.trajectory_pipeline import (
    SHAPES,
    Profile,
    TrajLimits,
    TrajPoint,
    crc32,
    generate,
    resample,
    shape_xy,
    tilt,
    time_profile,
    validate,
)


def test_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 1: Shapes are closed, start at (0, 0), and reject invalid parameters."""
    # Test line
    line_pts = SHAPES["line"]({"length_m": 1.0, "heading_deg": 45.0})
    assert line_pts[0] == (0.0, 0.0)
    assert line_pts[-1] == (0.0, 0.0)
    with pytest.raises(ValueError):
        SHAPES["line"]({})
    with pytest.raises(ValueError):
        SHAPES["line"]({"length_m": 1.0})
    with pytest.raises(ValueError):
        SHAPES["line"]({"heading_deg": 0.0})
    with pytest.raises(ValueError):
        SHAPES["line"]({"length_m": 0.0, "heading_deg": 0.0})
    with pytest.raises(ValueError):
        SHAPES["line"]({"length_m": -1.0, "heading_deg": 0.0})

    # Test circle
    circle_pts = SHAPES["circle"]({"radius_m": 0.5})
    assert circle_pts[0] == (0.0, 0.0)
    assert circle_pts[-1] == (0.0, 0.0)
    with pytest.raises(ValueError):
        SHAPES["circle"]({})
    with pytest.raises(ValueError):
        SHAPES["circle"]({"radius_m": 0.0})
    with pytest.raises(ValueError):
        SHAPES["circle"]({"radius_m": -0.5})

    # Test figure8
    fig8_pts = SHAPES["figure8"]({"width_m": 1.0, "height_m": 0.8})
    assert fig8_pts[0] == (0.0, 0.0)
    assert fig8_pts[-1] == (0.0, 0.0)
    with pytest.raises(ValueError):
        SHAPES["figure8"]({})
    with pytest.raises(ValueError):
        SHAPES["figure8"]({"width_m": 1.0})
    with pytest.raises(ValueError):
        SHAPES["figure8"]({"height_m": 0.8})
    with pytest.raises(ValueError):
        SHAPES["figure8"]({"width_m": 0.0, "height_m": 0.8})
    with pytest.raises(ValueError):
        SHAPES["figure8"]({"width_m": 1.0, "height_m": -0.5})

    # Test square
    sq_pts = SHAPES["square"]({"side_m": 0.5})
    assert sq_pts[0] == (0.0, 0.0)
    assert sq_pts[-1] == (0.0, 0.0)
    with pytest.raises(ValueError):
        SHAPES["square"]({})
    with pytest.raises(ValueError):
        SHAPES["square"]({"side_m": 0.0})
    with pytest.raises(ValueError):
        SHAPES["square"]({"side_m": -0.5})

    # Test library with monkeypatched path-library loader
    fake_path = {
        "id": "open_test_path",
        "points": [
            {"x": 100.0, "y": 200.0},
            {"x": 150.0, "y": 250.0},
            {"x": 120.0, "y": 230.0},
        ],
    }

    def fake_get_path(path_id: str) -> dict | None:
        if path_id == "open_test_path":
            return fake_path
        return None

    monkeypatch.setattr("ground_station.service.path_library.get_path", fake_get_path)

    lib_pts = SHAPES["library"]({"path_id": "open_test_path"})
    assert lib_pts[0] == (0.0, 0.0)
    assert lib_pts[-1] == (0.0, 0.0)
    assert lib_pts[1][0] == pytest.approx(0.5)
    assert lib_pts[1][1] == pytest.approx(0.5)
    assert lib_pts[2][0] == pytest.approx(0.2)
    assert lib_pts[2][1] == pytest.approx(0.3)

    # Missing / non-existent library path_id raises ValueError
    with pytest.raises(ValueError):
        SHAPES["library"]({})
    with pytest.raises(ValueError):
        SHAPES["library"]({"path_id": "non_existent"})

    # shape_xy unknown shape raises ValueError
    assert shape_xy("circle", {"radius_m": 0.3})[0] == (0.0, 0.0)
    with pytest.raises(ValueError):
        shape_xy("unknown_shape", {})


def test_tilt() -> None:
    """Test 2: Tilt rotation preserves distances and matches expected z span."""
    hover_z = 0.5
    circle_xy = SHAPES["circle"]({"radius_m": 0.3})

    # 0 deg keeps z == hover_z
    t0 = tilt(circle_xy, hover_z_m=hover_z, tilt_deg=0.0, axis_deg=0.0)
    for _, _, z in t0:
        assert z == pytest.approx(hover_z)

    # Circle of radius 0.3 tilted 30 deg about axis 0
    t30 = tilt(circle_xy, hover_z_m=hover_z, tilt_deg=30.0, axis_deg=0.0)
    zs = [z for _, _, z in t30]
    expected_delta_z = 2.0 * 0.3 * math.sin(math.radians(30.0))
    assert max(zs) - min(zs) == pytest.approx(expected_delta_z, abs=1e-3)

    # Keeps every 3-D distance to the circle centre (0, 0.3, hover_z)
    center_tilted = tilt([(0.0, 0.3)], hover_z_m=hover_z, tilt_deg=30.0, axis_deg=0.0)[0]
    for pt in t30:
        dist = math.sqrt(
            (pt[0] - center_tilted[0]) ** 2
            + (pt[1] - center_tilted[1]) ** 2
            + (pt[2] - center_tilted[2]) ** 2
        )
        assert dist == pytest.approx(0.3, abs=1e-3)


def test_resample() -> None:
    """Test 3: Fixed arc-length resampling."""
    pts = [(0.0, 0.0, 0.5), (3.0, 4.0, 0.5)]
    ds = 0.25
    res = resample(pts, ds_m=ds)
    assert res[0] == pts[0]
    assert res[-1] == pts[-1]

    spacings = [
        math.sqrt(
            (res[i + 1][0] - res[i][0]) ** 2
            + (res[i + 1][1] - res[i][1]) ** 2
            + (res[i + 1][2] - res[i][2]) ** 2
        )
        for i in range(len(res) - 1)
    ]
    for s in spacings:
        assert s == pytest.approx(spacings[0], abs=1e-9)

    with pytest.raises(ValueError):
        resample(pts, ds_m=0.0)
    with pytest.raises(ValueError):
        resample(pts, ds_m=-0.1)


def test_time_profile() -> None:
    """Test 4: Timing profile trapezoid, triangular, monotonicity, and theoretical total time."""
    p_long = Profile(v_cruise_mps=1.0, a_max_mps2=2.0, ds_m=0.05, hover_z_m=0.5)
    L_long = 10.0
    pts_2pt = [(0.0, 0.0, 0.5), (L_long, 0.0, 0.5)]
    out_2pt = time_profile(pts_2pt, p_long)

    # 2-point path total time equals L / v + v / a (abs 1e-6)
    assert out_2pt[0].t == 0.0
    expected_T = (L_long / p_long.v_cruise_mps) + (p_long.v_cruise_mps / p_long.a_max_mps2)
    assert out_2pt[1].t == pytest.approx(expected_T, abs=1e-6)

    # Long line resampled
    resampled_long = resample(pts_2pt, ds_m=0.05)
    out_long = time_profile(resampled_long, p_long)
    assert out_long[0].t == 0.0
    for i in range(len(out_long) - 1):
        assert out_long[i + 1].t > out_long[i].t

    speeds_long = [
        math.sqrt(
            (out_long[i + 1].x - out_long[i].x) ** 2
            + (out_long[i + 1].y - out_long[i].y) ** 2
            + (out_long[i + 1].z - out_long[i].z) ** 2
        )
        / (out_long[i + 1].t - out_long[i].t)
        for i in range(len(out_long) - 1)
    ]
    max_speed_long = max(speeds_long)
    assert max_speed_long <= p_long.v_cruise_mps * (1.0 + 1e-6)
    assert max_speed_long == pytest.approx(p_long.v_cruise_mps, rel=1e-6)

    # Short line triangular profile (peak < cruise)
    p_short = Profile(v_cruise_mps=1.0, a_max_mps2=1.0, ds_m=0.01, hover_z_m=0.5)
    pts_short = [(0.0, 0.0, 0.5), (0.1, 0.0, 0.5)]
    resampled_short = resample(pts_short, ds_m=0.01)
    out_short = time_profile(resampled_short, p_short)
    speeds_short = [
        math.sqrt(
            (out_short[i + 1].x - out_short[i].x) ** 2
            + (out_short[i + 1].y - out_short[i].y) ** 2
            + (out_short[i + 1].z - out_short[i].z) ** 2
        )
        / (out_short[i + 1].t - out_short[i].t)
        for i in range(len(out_short) - 1)
    ]
    max_speed_short = max(speeds_short)
    assert max_speed_short < p_short.v_cruise_mps


def test_generate() -> None:
    """Test 5: Trajectory generation and validation errors."""
    prof_default = Profile(v_cruise_mps=0.3, a_max_mps2=0.5, ds_m=0.02, hover_z_m=0.5)

    # Default limits accept circle radius 0.3
    pts = generate("circle", {"radius_m": 0.3}, prof_default)
    assert len(pts) > 0

    # Circle radius 1.0 raises ValueError containing "BOUNDS"
    with pytest.raises(ValueError) as exc_bounds:
        generate("circle", {"radius_m": 1.0}, prof_default)
    assert "BOUNDS" in str(exc_bounds.value)

    # Profile needing > 600 points raises ValueError containing "COUNT"
    prof_count = Profile(v_cruise_mps=0.3, a_max_mps2=0.5, ds_m=0.002, hover_z_m=0.5)
    with pytest.raises(ValueError) as exc_count:
        generate("circle", {"radius_m": 0.3}, prof_count)
    assert "COUNT" in str(exc_count.value)

    # Profile(1.5, 5.0, 0.02, 0.5) on radius 0.3 circle raises with "SPEED"
    prof_speed = Profile(v_cruise_mps=1.5, a_max_mps2=5.0, ds_m=0.02, hover_z_m=0.5)
    with pytest.raises(ValueError) as exc_speed:
        generate("circle", {"radius_m": 0.3}, prof_speed)
    assert "SPEED" in str(exc_speed.value)


def test_validate() -> None:
    """Test 6: Validation checks for each tag and ordering."""
    limits = TrajLimits()
    hover_z = 0.5
    valid_list = [
        TrajPoint(0.0, 0.0, hover_z, 0.0, 0.0),
        TrajPoint(0.0, 0.0, hover_z, 0.0, 1.0),
    ]

    # Valid list returns []
    assert validate(valid_list, limits, hover_z) == []

    # COUNT: fewer than 2 points or more than limits.max_points
    errs_count_few = validate([valid_list[0]], limits, hover_z)
    assert any(m.startswith("COUNT") for m in errs_count_few)

    many_pts = [TrajPoint(0.0, 0.0, hover_z, 0.0, float(i)) for i in range(limits.max_points + 2)]
    errs_count_many = validate(many_pts, limits, hover_z)
    assert any(m.startswith("COUNT") for m in errs_count_many)

    # RANGE: NaN
    nan_list = [TrajPoint(float("nan"), 0.0, hover_z, 0.0, 0.0), valid_list[1]]
    errs_range = validate(nan_list, limits, hover_z)
    assert any(m.startswith("RANGE") for m in errs_range)

    # TIME: t[0] = 0.1; equal t
    t0_list = [TrajPoint(0.0, 0.0, hover_z, 0.0, 0.1), valid_list[1]]
    errs_time_t0 = validate(t0_list, limits, hover_z)
    assert any(m.startswith("TIME") for m in errs_time_t0)

    teq_list = [TrajPoint(0.0, 0.0, hover_z, 0.0, 0.0), TrajPoint(0.0, 0.0, hover_z, 0.0, 0.0)]
    errs_time_eq = validate(teq_list, limits, hover_z)
    assert any(m.startswith("TIME") for m in errs_time_eq)

    # BOUNDS: x; z below; yaw 180.5
    x_list = [TrajPoint(1.35, 0.0, hover_z, 0.0, 0.0), valid_list[1]]
    errs_bounds_x = validate(x_list, limits, hover_z)
    assert any(m.startswith("BOUNDS") for m in errs_bounds_x)

    z_list = [TrajPoint(0.0, 0.0, 0.25, 0.0, 0.0), valid_list[1]]
    errs_bounds_z = validate(z_list, limits, hover_z)
    assert any(m.startswith("BOUNDS") for m in errs_bounds_z)

    yaw_list = [TrajPoint(0.0, 0.0, hover_z, 180.5, 0.0), valid_list[1]]
    errs_bounds_yaw = validate(yaw_list, limits, hover_z)
    assert any(m.startswith("BOUNDS") for m in errs_bounds_yaw)

    # ENDPOINT: first; last
    ep_first = [TrajPoint(0.15, 0.0, hover_z, 0.0, 0.0), valid_list[1]]
    errs_ep_first = validate(ep_first, limits, hover_z)
    assert any(m.startswith("ENDPOINT") and "point 0" in m for m in errs_ep_first)

    ep_last = [valid_list[0], TrajPoint(0.15, 0.0, hover_z, 0.0, 1.0)]
    errs_ep_last = validate(ep_last, limits, hover_z)
    assert any(m.startswith("ENDPOINT") and "point 1" in m for m in errs_ep_last)

    # SPEED
    speed_list = [TrajPoint(0.0, 0.0, hover_z, 0.0, 0.0), TrajPoint(0.0, 0.5, hover_z, 0.0, 0.1)]
    errs_speed = validate(speed_list, limits, hover_z)
    assert any(m.startswith("SPEED") for m in errs_speed)

    # Two different problems in documented order
    two_prob_list = [
        TrajPoint(0.0, 0.0, hover_z, 0.0, 0.1),
        TrajPoint(0.0, 0.5, hover_z, 0.0, 0.2),
    ]
    errs_two = validate(two_prob_list, limits, hover_z)
    idx_time = next(i for i, m in enumerate(errs_two) if m.startswith("TIME"))
    idx_speed = next(i for i, m in enumerate(errs_two) if m.startswith("SPEED"))
    assert idx_time < idx_speed


def test_crc32() -> None:
    """Test 7: CRC32 of points matches hand-packed 40 bytes and responds to float32 changes."""
    p0 = TrajPoint(0.1, 0.2, 0.3, 10.0, 0.0)
    p1 = TrajPoint(0.4, 0.5, 0.6, 20.0, 1.0)
    pts = [p0, p1]

    raw_40b = struct.pack(
        "<10f",
        0.1, 0.2, 0.3, 10.0, 0.0,
        0.4, 0.5, 0.6, 20.0, 1.0,
    )
    expected_crc = zlib.crc32(raw_40b) & 0xFFFFFFFF
    assert crc32(pts) == expected_crc

    p1_mod = TrajPoint(0.4 + 1e-3, 0.5, 0.6, 20.0, 1.0)
    assert crc32([p0, p1_mod]) != expected_crc


def test_validate_float32_overflow_is_range() -> None:
    """A value finite as a double but beyond float32 is what the firmware receives as inf."""
    pts = [TrajPoint(0.0, 0.0, 0.5, 0.0, 0.0), TrajPoint(1e39, 0.0, 0.5, 0.0, 1.0)]
    errs = validate(pts, TrajLimits(), 0.5)
    assert any(e.startswith("RANGE: point 1") for e in errs)

def test_waypoints_shape() -> None:
    """Explicit waypoints: closed at the hover point, validated, optional corner rounding."""
    wp = SHAPES["waypoints"]
    assert wp({"points_m": [[0.5, 0.0], [0.5, 0.5]]}) == [(0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.0)]
    assert wp({"points_m": [[0, 0], [0.4, 0.0], [0, 0]]}) == [(0.0, 0.0), (0.4, 0.0), (0.0, 0.0)]
    for bad in ({}, {"points_m": []}, {"points_m": [[1.0]]}, {"points_m": [[math.nan, 0.0]]},
                {"points_m": [[True, 0.0]]}, {"points_m": [[0.1, 0.1]] * 101},
                {"points_m": [[0.1, 0.1]], "corner_cut": 4}, {"points_m": [[0.1, 0.1]], "corner_cut": 1.0}):
        with pytest.raises(ValueError):
            wp(bad)

    # Chaikin keeps both ends and stays inside the hull of the waypoints.
    cut = wp({"points_m": [[0.5, 0.0], [0.5, 0.5]], "corner_cut": 2})
    assert cut[0] == (0.0, 0.0) and cut[-1] == (0.0, 0.0)
    assert all(-1e-9 <= x <= 0.5 + 1e-9 and -1e-9 <= y <= 0.5 + 1e-9 for x, y in cut)

    # A dense zig-zag inside the cage generates and validates.
    rows = [[-0.8, y] if i % 2 == 0 else [0.8, y] for i, y in enumerate([-0.9, -0.6, -0.3, 0.0, 0.3, 0.6, 0.9])]
    pts = generate("waypoints", {"points_m": rows, "corner_cut": 2},
                   Profile(v_cruise_mps=0.3, a_max_mps2=0.5, ds_m=0.05, hover_z_m=0.8))
    assert 0 < len(pts) <= 600
