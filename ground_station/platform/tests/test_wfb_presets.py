"""Preset builders: the cursor chains ops, YAML ops map to builders, and every composite passes the firmware COMMIT."""

from __future__ import annotations

import math
import shutil

import pytest

from ground_station.platform import wfb_presets as ps
from ground_station.platform.wfb_program import Atom, Profile, preview

HZ = 0.5


def test_cursor_tracks_atoms():
    pr = ps.Program(HZ).line(0.2, -0.3).move(dz=0.1).turn(by=30)
    assert pr.pose == pytest.approx((0.2, -0.3, 0.6, 30.0))
    pr.arc(0.2, -0.6, 180.0)  # half circle about (0.2, -0.6) from its +y point
    assert pr.pose[:2] == pytest.approx((0.2, -0.9))
    pr.turn(-170).turn(by=-30)
    assert pr.yaw == pytest.approx(160.0)  # wrapped to [-180, 180)


def test_motion_overrides_and_profiles():
    pr = ps.Program(HZ, ps.Motion(profile=Profile.TRAP)).line(y=-0.4, profile="quintic", v=0.2)
    seg = pr.segments[0]
    assert seg.atom == Atom.LINE and seg.profile == Profile.QUINTIC and seg.v == 0.2
    assert seg.a == ps.Motion().a  # unspecified fields keep the program's motion
    with pytest.raises(ValueError, match="unknown profile"):
        pr.line(y=0.0, profile="bezier")


def test_lissa_speed_is_path_speed():
    seg = ps.Program(HZ).fig8(0.4, 0.3, v=0.25).segments[0]
    per_cycle = 2 * math.pi * math.hypot(0.4 * 2, 0.3 * 1)
    assert seg.v * per_cycle == pytest.approx(0.25)
    assert seg.p[:9] == (0.4, 0.3, 0.0, 2.0, 1.0, 0.0, 0.0, 90.0, 0.0)


def test_yaml_ops_build_and_close_home():
    ops = ["hold", {"circle": {"r": 0.3}}, {"repeat": {"n": 2, "ops": [{"move": {"dy": -0.2}}, {"hold": {"s": 1}}]}}]
    with pytest.raises(ValueError, match="op 0: hold"):  # hold needs s
        ps.build(ops, HZ)
    segs = ps.build(ops[1:], HZ)
    assert [s.atom for s in segs] == [Atom.ARC, Atom.LINE, Atom.HOLD, Atom.LINE, Atom.HOLD, Atom.LINE]
    assert segs[-1].p[:3] == (0.0, 0.0, HZ)
    with pytest.raises(ValueError, match="unknown op 'spiral'"):
        ps.build(["spiral"], HZ)
    with pytest.raises(ValueError, match="unknown argument"):
        ps.build([{"line": {"y": -0.2, "speed": 1}}], HZ)


def test_segment_cap():
    pr = ps.Program(HZ)
    for _ in range(64):
        pr.hold(0.1)
    with pytest.raises(ValueError, match="more than 64"):
        pr.hold(0.1)


COMPOSITES = {
    "circle": lambda p: p.circle(0.4),
    "circle_cw_yaw": lambda p: p.circle(0.3, laps=2, ccw=False, yaw_follow=True),
    "fig8": lambda p: p.fig8(0.4, 0.4),
    "square": lambda p: p.square(0.6, dwell=1.0),
    "polygon": lambda p: p.polygon(6, 0.4),
    "helix": lambda p: p.helix(0.3, 2, 0.4),
    "ladder": lambda p: p.ladder(0.2, 3),
    "goto": lambda p: p.goto(0.0, -0.5),
    "yaw_sweep": lambda p: p.yaw_sweep(45.0),
    "profiles": lambda p: p.line(y=-0.6, profile="sine").line(y=0.0, profile="quintic"),
}


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
@pytest.mark.parametrize("name", sorted(COMPOSITES))
def test_composites_pass_commit_in_the_camera_half(name):
    pr = COMPOSITES[name](ps.Program(HZ)).home()
    r = preview(pr.segments, HZ)
    assert r.ok, r.error
    assert max(p[2] for p in r.points) <= 1e-3  # never into +y (camera sees only -y)
    assert r.points[-1][1:] == pytest.approx((0.0, 0.0, HZ, 0.0), abs=1e-3)


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
def test_firmware_rejects_what_the_builder_lets_through():
    assert preview(ps.Program(HZ).line(y=-0.5).segments, HZ).error.startswith("endpoint")
    assert preview(ps.Program(HZ).circle(1.0).home().segments, HZ).error == "bounds at segment 0"  # y -2.0 > fence
