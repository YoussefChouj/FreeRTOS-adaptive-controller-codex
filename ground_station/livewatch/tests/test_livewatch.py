"""Offline tests for livewatch: DWARF resolution + coalescing + decode.

No hardware required. The resolver runs against the real firmware ELF; the reader
logic is exercised with synthetic region bytes via the pure build_plan/decode path.
"""
import re
import struct
from pathlib import Path

import pytest

from ground_station.livewatch.symbols import SymbolResolver, Symbol, _parse_path
from ground_station.livewatch.reader import LiveReader, build_plan, Plan, Region
from ground_station.livewatch.transport import SwdCmsisDap
from ground_station.livewatch.registry import Registry

ELF = Path(__file__).resolve().parents[3] / "OBJ" / "JX_FLY.axf"
MAP = ELF.with_suffix(".map")

pytestmark = pytest.mark.skipif(not ELF.exists(), reason="firmware ELF not built")

# "    s_ekf     0x20000ca8   Data   572  send_data.o(.bss)"
_MAP_SYM = re.compile(r"^\s+(\S+)\s+0x([0-9a-fA-F]{8})\s+Data\s+\d+", re.M)


def _map_addresses() -> dict:
    if not MAP.exists():
        pytest.skip(f"{MAP.name} not present")
    return {n: int(a, 16) for n, a in _MAP_SYM.findall(MAP.read_text(errors="replace"))}


@pytest.fixture(scope="module")
def r():
    res = SymbolResolver(ELF)
    yield res
    res.close()


# ---- path parser (pure) ------------------------------------------------

def test_parse_path_dotted_indexed():
    assert _parse_path("s_ekf.x[3]") == ("s_ekf", ["x", 3])
    assert _parse_path("a") == ("a", [])
    assert _parse_path("buf[2].y[0]") == ("buf", [2, "y", 0])


# ---- DWARF resolution against real firmware ----------------------------

def test_known_addresses(r):
    """DWARF resolution must agree with the linker map, symbol for symbol.

    Read from the map rather than hardcoded: absolute addresses shift on any
    rebuild that changes .bss layout, so pinned constants fail for a reason that
    has nothing to do with the resolver being wrong.
    """
    m = _map_addresses()
    for name in ("s_ekf", "imu_data", "system_monitor", "mrac_state"):
        assert name in m, f"{name} missing from {MAP.name}"
        assert r.resolve(name).address == m[name], f"{name} disagrees with the linker map"


def test_field_and_array_offsets(r):
    base = r.resolve("s_ekf").address
    assert r.resolve("s_ekf.x[0]").address == base           # first array elem == base
    assert r.resolve("s_ekf.x[3]").address == base + 12      # float32 stride
    assert r.resolve("s_ekf.nis").address == base + 408
    assert r.resolve("s_ekf.active").address == base + 424


def test_scalar_typing(r):
    assert r.resolve("s_ekf.x[0]").fmt == "f"
    assert r.resolve("s_ekf.x[0]").size == 4
    active = r.resolve("s_ekf.active")
    assert active.fmt == "B" and active.size == 1
    assert r.resolve("s_ekf").fmt is None  # aggregate, not a scalar leaf


def test_unknown_symbol_raises(r):
    with pytest.raises(KeyError):
        r.resolve("does_not_exist_xyz")


def test_names_and_fields(r):
    assert "s_ekf" in r.names()
    assert set(["x", "P", "nis", "active"]).issubset(set(r.fields_of("s_ekf")))


# ---- coalescing (pure, no hardware) ------------------------------------

def test_live_reader_defaults_to_swd():
    reader = LiveReader(ELF)
    try:
        assert isinstance(reader.transport, SwdCmsisDap)
    finally:
        reader.close()


def test_explicit_swd_transport_is_preserved():
    transport = SwdCmsisDap()
    reader = LiveReader(ELF, transport=transport)
    try:
        assert reader.transport is transport
    finally:
        reader.close()


def test_build_plan_coalesces_adjacent_but_splits_holes(r):
    # x[0..3] are contiguous -> merge. nis is 392 B past x[3] (unwatched P[81]
    # hole) -> beyond the 48 B break-even -> split. active is 12 B past nis -> merge.
    # Net: two regions, NOT one. This is the bandwidth-optimal plan for the probe.
    names = ["s_ekf.x[0]", "s_ekf.x[3]", "s_ekf.nis", "s_ekf.active"]
    plan = build_plan(r, names)
    base = r.resolve("s_ekf.x[0]").address
    assert len(plan.regions) == 2
    assert plan.regions[0].start == base                 # x[0..3] block
    assert plan.regions[0].end == base + 16
    assert plan.regions[1].start == r.resolve("s_ekf.nis").address  # nis+active block


def test_gap_override_forces_single_region(r):
    # A wired/latency-limited probe would raise the threshold; 1024 bridges the hole.
    names = ["s_ekf.x[0]", "s_ekf.nis"]
    assert len(build_plan(r, names, gap_merge_bytes=1024).regions) == 1
    assert len(build_plan(r, names, gap_merge_bytes=48).regions) == 2


def test_build_plan_splits_distant_symbols(r):
    # s_ekf (0x...0CB4) and mrac_state (0x...4B98) are ~81 KB apart -> far beyond
    # the merge gap -> two separate regions.
    plan = build_plan(r, ["s_ekf.active", "mrac_state.pitch"])
    assert len(plan.regions) == 2


# ---- decode round-trip with synthetic bytes ----------------------------

def test_decode_roundtrip(r):
    names = ["s_ekf.x[0]", "s_ekf.x[3]", "s_ekf.nis", "s_ekf.active"]
    plan = build_plan(r, names)

    # Build a synthetic image per region with known values at the right offsets.
    bufs = [bytearray(reg.size) for reg in plan.regions]

    def locate(addr):
        for i, reg in enumerate(plan.regions):
            if reg.start <= addr < reg.end:
                return i, addr - reg.start
        raise AssertionError("addr not in any region")

    def put_f(name, val):
        i, off = locate(r.resolve(name).address)
        struct.pack_into("<f", bufs[i], off, val)

    put_f("s_ekf.x[0]", 1.25)
    put_f("s_ekf.x[3]", -0.5)
    put_f("s_ekf.nis", 3.0)
    i, off = locate(r.resolve("s_ekf.active").address)
    bufs[i][off] = 1

    out = plan.decode([bytes(b) for b in bufs])
    assert out["s_ekf.x[0]"] == pytest.approx(1.25)
    assert out["s_ekf.x[3]"] == pytest.approx(-0.5)
    assert out["s_ekf.nis"] == pytest.approx(3.0)
    assert out["s_ekf.active"] == 1


# ---- registry ----------------------------------------------------------

def test_registry_groups_resolve(r):
    reg = Registry()
    assert "ekf" in reg.group_names()
    # Every var in every group must resolve against the real firmware.
    for g in reg.group_names():
        for v in reg.vars(g):
            r.resolve(v)  # raises if a registry entry has gone stale


def test_registry_expand():
    reg = Registry()
    expanded = reg.expand(["group:ekf", "imu_data"])
    assert "s_ekf.active" in expanded
    assert "imu_data" in expanded
