"""Tests for manifest layouts: load + frame packing + VoFA+ forwarding map.

The UART5 round-trip (`apply_layout`) needs a real serial port; that test
lives in `livewatch/tests/test_stream.py`. Here we exercise the host-side
shape only: load YAML, pack adjacent scalars, build the (name -> channel)
map the WiFi bridge uses for VoFA+ forwarding.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ground_station.comm.manifest_layer import (
    FrameSpec,
    Layout,
    load_layouts,
    resolve_ranges,
    vofa_channel_map,
)
from ground_station.livewatch.stream import StreamRange


class FakeResolver:
    """Minimal stand-in for SymbolResolver: returns (name, address, size, fmt)
    for any name in `_symbols`. Mirrors the surface manifest_layer uses."""

    def __init__(self, symbols):
        # symbols: list of (name, address, size, fmt) -- in some order.
        self._symbols = list(symbols)
        self._by_name = {s[0]: s for s in self._symbols}

    def resolve(self, name):
        s = self._by_name[name]
        n, a, sz, fmt = s

        class _Sym:
            pass

        sym = _Sym()
        sym.name = n
        sym.address = a
        sym.size = sz
        sym.fmt = fmt
        return sym

    def names(self):
        return sorted(n for n, *_ in self._symbols)


def _make_symbols(n_per_axis: int = 3, n_axes: int = 4) -> list:
    """Build a fake symbol set: mrac_state.<axis>.Theta[0..N-1] on 4 axes,
    contiguous in memory so the range packer can collapse them. Plus some
    PID scalars on the same size lane."""
    syms = []
    base = 0x20001000
    size = 4
    cur = base
    for axis in ("roll", "pitch", "yaw", "z_rate"):
        for i in range(n_per_axis):
            syms.append((f"mrac_state.{axis}.Theta[{i}]", cur, size, "f"))
            cur += size
    # add a couple of PID scalars of the same size on the next gap_merge.
    syms.append(("Ctrler.gyroxPID.FB", cur, size, "f"))
    cur += size
    syms.append(("Ctrler.gyroxPID.U", cur, size, "f"))
    cur += size
    return syms


class TestLoadLayouts(unittest.TestCase):
    def test_load_returns_three_layouts(self):
        layouts = load_layouts()
        self.assertIn("flight-default", layouts)
        self.assertIn("pid-deep-dive", layouts)
        self.assertIn("adaptive-deep-dive", layouts)

    def test_layout_has_two_frames(self):
        lyt = load_layouts()["flight-default"]
        self.assertGreaterEqual(len(lyt.slots), 2)
        for slot in lyt.slots:
            self.assertIsInstance(slot, FrameSpec)
        self.assertGreater(len(lyt.slots[0].vars), 0)
        self.assertGreater(len(lyt.slots[1].vars), 0)

    def test_frame_a_and_b_target_different_slots(self):
        # Critical: FC emits one data frame per slot per cycle. If both
        # frames collide on the same slot, the second never arrives.
        layouts = load_layouts()
        for name, lyt in layouts.items():
            with self.subTest(layout=name):
                slots = lyt.slots
                self.assertGreaterEqual(
                    len(slots), 2,
                    f"{name}: needs at least 2 slots to test uniqueness")
                slot_indices = [s.slot for s in slots]
                self.assertEqual(
                    len(slot_indices), len(set(slot_indices)),
                    f"{name}: duplicate slots: {slot_indices}")


class TestResolveRanges(unittest.TestCase):
    def test_adjacent_scalars_collapse_into_one_range(self):
        syms = _make_symbols()
        resolver = FakeResolver(syms)
        frame = FrameSpec(
            slot=1, divider=1, vofa_tab="MRAC",
            vars=tuple(f"mrac_state.{a}.Theta[{i}]"
                       for a in ("roll", "pitch", "yaw", "z_rate")
                       for i in range(3)))
        lyt = Layout(
            name="t", doc="", transport="usart3",
            slots=(frame, frame))
        ranges = resolve_ranges(lyt, frame, resolver)
        # 4 axes × 3 scalars = 12 names, but every contiguous run of
        # size-4 same-size adjacent scalars collapses into ONE range.
        # In this fixture the 12 are back-to-back, so we get exactly 1 range.
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0].count, 12)
        self.assertEqual(ranges[0].size, 4)

    def test_gap_breaks_the_range(self):
        # First three scalars adjacent, then a 16-byte gap, then two more.
        syms = [
            ("a.theta[0]", 0x20001000, 4, "f"),
            ("a.theta[1]", 0x20001004, 4, "f"),
            ("a.theta[2]", 0x20001008, 4, "f"),
            ("b.fb",       0x20001018, 4, "f"),  # 16-byte gap
            ("b.u",        0x2000101C, 4, "f"),
        ]
        resolver = FakeResolver(syms)
        frame = FrameSpec(slot=1, divider=1, vofa_tab="T",
                          vars=("a.theta[0]", "a.theta[1]", "a.theta[2]",
                                "b.fb", "b.u"))
        lyt = Layout(name="t", doc="", transport="usart3",
                     slots=(frame, frame))
        ranges = resolve_ranges(lyt, frame, resolver)
        self.assertEqual(len(ranges), 2)
        self.assertEqual(ranges[0].count, 3)
        self.assertEqual(ranges[1].count, 2)


class TestVofaChannelMap(unittest.TestCase):
    def test_channel_order_matches_range_order(self):
        syms = [
            ("x[0]", 0x20001000, 4, "f"),
            ("x[1]", 0x20001004, 4, "f"),
            ("y",    0x20002000, 4, "f"),  # different range
        ]
        resolver = FakeResolver(syms)
        frame = FrameSpec(slot=1, divider=1, vofa_tab="T",
                          vars=("x[0]", "x[1]", "y"))
        lyt = Layout(name="t", doc="", transport="usart3",
                     slots=(frame, frame))
        a_map, _b_map = vofa_channel_map(lyt, resolver)
        # Channel 0 -> x[0], 1 -> x[1], 2 -> y.
        # (Packed-range name expansion picks "x[0], x[1]" for the first range;
        # the index slicing happens inside _range_to_symbols.)
        self.assertIn(0, a_map)
        self.assertIn(1, a_map)
        self.assertIn(2, a_map)
        self.assertTrue(a_map[0].startswith("x[0]"))
        self.assertTrue(a_map[1].startswith("x[1]"))
        self.assertEqual(a_map[2], "y")


if __name__ == "__main__":
    unittest.main()