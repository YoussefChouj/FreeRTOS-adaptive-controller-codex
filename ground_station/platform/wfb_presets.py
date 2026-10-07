"""Onboard program presets: composable builders over the five wfb_prog atoms (API/wfb_prog.h).

A Program is a cursor (x, y, z in m, yaw in deg relative to the START heading) plus the segments so far. Every op
appends segments that start at the cursor and moves the cursor to where they end, so ops chain in any order and
nest through `repeat`:

    atoms       hold, line, move, turn, arc, lissa         one firmware segment each
    composites  home, goto, rect, square, polygon, circle,  built from the atoms only
                helix, fig8, ladder, yaw_sweep, repeat

Shapes that leave the start point (rect, polygon, circle, helix, fig8) extend into -y from it: that half is the one
the camera sees. Speeds are path units (m/s, m/s^2, m/s^3; turn in deg/s, deg/s^2, deg/s^3); lissa converts them
to the firmware's cycles/s. Every op takes motion overrides (profile, v, a, j, yaw_rate, yaw_acc, yaw_jerk) on top
of the program's Motion. The firmware validates the whole program at COMMIT; wfb_program.preview() runs that same
check on the host. Design: docs/workflow-c/onboard-preset-program.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields, replace
from typing import Any, Iterable, Mapping

from ground_station.platform.wfb_program import PROG_MAX_SEGS, Atom, Profile, Segment

PROFILES: dict[str, Profile] = {p.name.lower(): p for p in Profile}


def profile_of(name: str | Profile) -> Profile:
    if isinstance(name, Profile):
        return name
    try:
        return PROFILES[str(name).lower()]
    except KeyError:
        raise ValueError(f"unknown profile {name!r} (known: {', '.join(PROFILES)})") from None


@dataclass(frozen=True)
class Motion:
    """Ramp shape and speeds of an op. Defaults PROPOSED; the caps are WFB_PROG_CAPS_ROW in API/wfb_prog.c."""

    profile: Profile = Profile.SCURVE
    v: float = 0.3  # m/s cruise (lissa: peak path speed)
    a: float = 0.5  # m/s^2 ramp
    j: float = 2.0  # m/s^3 ramp jerk (SCURVE only)
    yaw_rate: float = 30.0  # deg/s turn cruise
    yaw_acc: float = 60.0  # deg/s^2
    yaw_jerk: float = 240.0  # deg/s^3

    def with_(self, **kw: Any) -> Motion:
        bad = set(kw) - MOTION_KEYS
        if bad:
            raise TypeError(f"unknown argument(s) {', '.join(sorted(bad))}")
        kw = {k: v for k, v in kw.items() if v is not None}
        if "profile" in kw:
            kw["profile"] = profile_of(kw["profile"])
        return replace(self, **{k: (v if k == "profile" else float(v)) for k, v in kw.items()})


MOTION_KEYS = frozenset(f.name for f in fields(Motion))


def _wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


class Program:
    """Segments plus the cursor they end at. Ops return self, so they chain: Program(0.5).circle(0.4).home()."""

    def __init__(self, hover_z_m: float, motion: Motion = Motion()) -> None:
        self.hover_z = float(hover_z_m)
        self.motion = motion
        self.segments: list[Segment] = []
        self.x, self.y, self.z, self.yaw = 0.0, 0.0, self.hover_z, 0.0

    @property
    def pose(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.z, self.yaw)

    def _add(self, seg: Segment, x: float, y: float, z: float, yaw: float) -> Program:
        if len(self.segments) >= PROG_MAX_SEGS:
            raise ValueError(f"more than {PROG_MAX_SEGS} segments")
        self.segments.append(seg)
        self.x, self.y, self.z, self.yaw = x, y, z, _wrap180(yaw)
        return self

    # ---------- atoms: one segment each ----------

    def hold(self, s: float) -> Program:
        """Dwell s seconds at the cursor."""
        return self._add(Segment(Atom.HOLD, p=(float(s),)), self.x, self.y, self.z, self.yaw)

    def line(self, x: float | None = None, y: float | None = None, z: float | None = None,
             yaw: float | None = None, v_in: float = 0.0, v_out: float = 0.0, **mot: Any) -> Program:
        """Straight line to an absolute point (None keeps that coordinate); yaw blends to `yaw` along the way."""
        m = self.motion.with_(**mot)
        x = self.x if x is None else float(x)
        y = self.y if y is None else float(y)
        z = self.z if z is None else float(z)
        yaw = self.yaw if yaw is None else float(yaw)
        seg = Segment(Atom.LINE, m.profile, m.v, m.a, m.j, float(v_in), float(v_out), (x, y, z, yaw))
        return self._add(seg, x, y, z, yaw)

    def move(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0, dyaw: float = 0.0, **kw: Any) -> Program:
        """Line by an offset from the cursor."""
        return self.line(self.x + float(dx), self.y + float(dy), self.z + float(dz), self.yaw + float(dyaw), **kw)

    def turn(self, yaw: float | None = None, by: float | None = None, **mot: Any) -> Program:
        """Yaw in place to `yaw` (deg, relative to the START heading) or `by` degrees from the cursor."""
        if (yaw is None) == (by is None):
            raise TypeError("turn needs exactly one of yaw, by")
        m = self.motion.with_(**mot)
        target = float(yaw) if yaw is not None else self.yaw + float(by)  # type: ignore[arg-type]
        seg = Segment(Atom.TURN, m.profile, m.yaw_rate, m.yaw_acc, m.yaw_jerk, p=(target,))
        return self._add(seg, self.x, self.y, self.z, target)

    def arc(self, cx: float, cy: float, sweep: float, dz: float = 0.0, yaw_follow: bool = False,
            v_in: float = 0.0, v_out: float = 0.0, **mot: Any) -> Program:
        """Arc about the absolute centre (cx, cy), radius = distance from the cursor; sweep deg, + = CCW."""
        m = self.motion.with_(**mot)
        cx, cy, sweep, dz = float(cx), float(cy), float(sweep), float(dz)
        r = math.hypot(self.x - cx, self.y - cy)
        th = math.atan2(self.y - cy, self.x - cx) + math.radians(sweep)
        seg = Segment(Atom.ARC, m.profile, m.v, m.a, m.j, float(v_in), float(v_out),
                      (cx, cy, sweep, dz, 1.0 if yaw_follow else 0.0))
        return self._add(seg, cx + r * math.cos(th), cy + r * math.sin(th), self.z + dz,
                         self.yaw + (sweep if yaw_follow else 0.0))

    def lissa(self, amp: Iterable[float], n: Iterable[float], phase: Iterable[float] = (0.0, 0.0, 0.0),
              cycles: float = 1.0, **mot: Any) -> Program:
        """Per axis x0 + amp*(sin(2 pi n s + phase) - sin(phase)), s = 0..cycles. v/a/j are peak path m/s, m/s^2,
        m/s^3, converted to the firmware's cycles/s with the peak path length per cycle."""
        m = self.motion.with_(**mot)
        amp, n, phase = (tuple(float(v) for v in t) for t in (amp, n, phase))
        if not len(amp) == len(n) == len(phase) == 3:
            raise ValueError("lissa amp, n and phase need 3 values (x, y, z)")
        per_cycle = 2.0 * math.pi * math.sqrt(sum((ak * nk) ** 2 for ak, nk in zip(amp, n)))
        if per_cycle <= 0.0:
            raise ValueError("lissa needs a non-zero amp * n")
        cycles = float(cycles)
        end = [c + ak * (math.sin(2.0 * math.pi * nk * cycles + math.radians(pk)) - math.sin(math.radians(pk)))
               for c, ak, nk, pk in zip((self.x, self.y, self.z), amp, n, phase)]
        seg = Segment(Atom.LISSA, m.profile, m.v / per_cycle, m.a / per_cycle, m.j / per_cycle,
                      p=(*amp, *n, *phase, cycles))
        return self._add(seg, end[0], end[1], end[2], self.yaw)

    # ---------- composites: atoms only ----------

    def home(self, **mot: Any) -> Program:
        """Line back to (0, 0, hover_z), then turn back to the START heading."""
        if math.hypot(self.x, self.y, self.z - self.hover_z) > 1e-6:
            self.line(0.0, 0.0, self.hover_z, **mot)
        if abs(self.yaw) > 1e-6:
            self.turn(0.0, **mot)
        return self

    def goto(self, x: float | None = None, y: float | None = None, z: float | None = None, dwell: float = 2.0,
             back: bool = True, **mot: Any) -> Program:
        """Step out to a point and dwell; with back, step back to the start and dwell again."""
        x0, y0, z0 = self.x, self.y, self.z
        self.line(x, y, z, **mot)
        if dwell > 0:
            self.hold(dwell)
        if back:
            self.line(x0, y0, z0, **mot)
            if dwell > 0:
                self.hold(dwell)
        return self

    def rect(self, w: float, h: float, dwell: float = 0.0, ccw: bool = True, **mot: Any) -> Program:
        """w x h rectangle, the cursor at the middle of its +y edge; corners in order, back to the start."""
        x0, y0, hw = self.x, self.y, float(w) / 2.0
        corners = [(x0 - hw, y0), (x0 - hw, y0 - h), (x0 + hw, y0 - h), (x0 + hw, y0)]
        if not ccw:
            corners = [(2.0 * x0 - cx, cy) for cx, cy in corners]
        for cx, cy in corners + [(x0, y0)]:
            self.line(cx, cy, **mot)
            if dwell > 0:
                self.hold(dwell)
        return self

    def square(self, side: float, dwell: float = 0.0, ccw: bool = True, **mot: Any) -> Program:
        return self.rect(side, side, dwell, ccw, **mot)

    def polygon(self, sides: int, r: float, dwell: float = 0.0, ccw: bool = True, **mot: Any) -> Program:
        """Regular polygon inscribed in the circle of radius r centred r below the cursor (in -y)."""
        sides = int(sides)
        if sides < 3:
            raise ValueError("polygon needs at least 3 sides")
        cx, cy, sgn = self.x, self.y - float(r), 1.0 if ccw else -1.0
        for k in range(1, sides + 1):
            th = math.pi / 2.0 + sgn * 2.0 * math.pi * k / sides
            self.line(cx + r * math.cos(th) if k < sides else cx, cy + r * math.sin(th) if k < sides else cy + r,
                      **mot)
            if dwell > 0:
                self.hold(dwell)
        return self

    def circle(self, r: float, laps: float = 1.0, ccw: bool = True, yaw_follow: bool = False,
               **mot: Any) -> Program:
        """Full laps about the centre r below the cursor (in -y); ends where it started."""
        return self.arc(self.x, self.y - float(r), (360.0 if ccw else -360.0) * float(laps), 0.0, yaw_follow, **mot)

    def helix(self, r: float, laps: float, dz: float, ccw: bool = True, back: bool = True,
              **mot: Any) -> Program:
        """Climb dz over laps about the centre r below the cursor; with back, unwind the same way down."""
        cx, cy, sweep = self.x, self.y - float(r), (360.0 if ccw else -360.0) * float(laps)
        self.arc(cx, cy, sweep, dz, **mot)
        if back:
            self.arc(cx, cy, -sweep, -float(dz), **mot)
        return self

    def fig8(self, a: float, b: float, cycles: float = 1.0, **mot: Any) -> Program:
        """Figure eight x = a sin 2t, y = b (cos t - 1): lobes stacked in -y, crossing b below the cursor."""
        return self.lissa((a, b, 0.0), (2.0, 1.0, 0.0), (0.0, 90.0, 0.0), cycles, **mot)

    def ladder(self, dz: float, steps: int = 3, dwell: float = 2.0, back: str = "steps", **mot: Any) -> Program:
        """Climb `steps` steps of dz with a dwell on each; back down by the same steps or in one line."""
        if back not in ("steps", "direct", "none"):
            raise ValueError("ladder back must be steps, direct or none")
        z0 = self.z
        for _ in range(int(steps)):
            self.move(dz=dz, **mot).hold(dwell)
        if back == "steps":
            for _ in range(int(steps)):
                self.move(dz=-dz, **mot).hold(dwell)
        elif back == "direct":
            self.line(z=z0, **mot).hold(dwell)
        return self

    def yaw_sweep(self, angle: float, reps: int = 1, dwell: float = 2.0, **mot: Any) -> Program:
        """Turn to +angle and -angle about the current heading `reps` times, dwelling at each, then back."""
        yaw0 = self.yaw
        for _ in range(int(reps)):
            self.turn(yaw0 + angle, **mot).hold(dwell)
            self.turn(yaw0 - angle, **mot).hold(dwell)
        return self.turn(yaw0, **mot).hold(dwell)

    def repeat(self, n: int, ops: list[Any]) -> Program:
        """Apply a list of ops n times (ops are YAML items, see apply)."""
        for _ in range(int(n)):
            for op in ops:
                self.apply(op)
        return self

    # ---------- YAML ----------

    def apply(self, item: Any) -> Program:
        """One op from YAML: a bare name ("home") or a one-key mapping {name: {kwargs}}."""
        if isinstance(item, str):
            name, kw = item, {}
        elif isinstance(item, Mapping) and len(item) == 1:
            ((name, kw),) = item.items()
            kw = {} if kw is None else kw
        else:
            raise ValueError(f"op must be a name or a one-key mapping, got {item!r}")
        if name not in OPS:
            raise ValueError(f"unknown op {name!r} (known: {', '.join(OPS)})")
        if not isinstance(kw, Mapping):
            raise ValueError(f"{name}: arguments must be a mapping, got {kw!r}")
        try:
            return getattr(self, name)(**kw)
        except TypeError as e:
            raise ValueError(f"{name}: {e}") from None


OPS = ("hold", "line", "move", "turn", "arc", "lissa", "home", "goto", "rect", "square", "polygon", "circle",
       "helix", "fig8", "ladder", "yaw_sweep", "repeat")


def build(ops: Iterable[Any], hover_z_m: float, motion: Motion = Motion(), close: bool = True) -> list[Segment]:
    """Segments of a YAML op list; with close, `home` is appended so the program ends where the firmware wants it."""
    pr = Program(hover_z_m, motion)
    for i, op in enumerate(ops):
        try:
            pr.apply(op)
        except ValueError as e:
            raise ValueError(f"op {i}: {e}") from None
    if close:
        pr.home()
    return pr.segments
