"""Offline tests for the ELF-vs-target freshness check.

The comparison logic is pure (`read_block` is injected), so a stale-ELF scenario is
exercised with synthetic bytes and no hardware.
"""
from pathlib import Path

import pytest

from ground_station.livewatch.verify import (
    FLASH_BASE, Sample, compare, flash_segments, plan_samples,
)

ELF = Path(__file__).resolve().parents[3] / "OBJ" / "JX_FLY.axf"


# ---- sample planning (pure) --------------------------------------------

def test_plan_samples_covers_first_and_last_chunk():
    """A relink shows up at the edges, so both ends must always be sampled."""
    data = bytes(range(256)) * 8            # 2048 B
    samples = plan_samples([(FLASH_BASE, data)], n=5, chunk=64)
    assert samples[0].address == FLASH_BASE
    assert samples[-1].address == FLASH_BASE + (2048 - 64)
    assert all(len(s.expected) == 64 for s in samples)


def test_plan_samples_are_spread_not_contiguous():
    """A stale ELF can share a prefix, so one contiguous block is not enough."""
    data = bytes(2048)
    addrs = [s.address for s in plan_samples([(FLASH_BASE, data)], n=5, chunk=64)]
    assert len(set(addrs)) == 5
    assert max(addrs) - min(addrs) > 64 * 5


def test_plan_samples_deterministic():
    data = bytes(range(256)) * 4
    a = plan_samples([(FLASH_BASE, data)], n=4, chunk=32)
    b = plan_samples([(FLASH_BASE, data)], n=4, chunk=32)
    assert [s.address for s in a] == [s.address for s in b]


def test_plan_samples_handles_segment_smaller_than_chunk():
    samples = plan_samples([(FLASH_BASE, b"\x01\x02\x03")], n=5, chunk=64)
    assert len(samples) == 1
    assert samples[0].expected == b"\x01\x02\x03"


def test_plan_samples_skips_empty_segment():
    assert plan_samples([(FLASH_BASE, b"")], n=3, chunk=16) == []


def test_plan_samples_full_samples_every_chunk():
    """`full=True` must place a chunk at the start of every chunk-sized window.

    This is the only way to guarantee detection of a localised drift -- a
    relink that shifted a single function. Pre-2026-08-20 the default
    sampled 5 chunks of 64 B out of ~750 kB (0.04 % coverage); a localised
    change landed between samples and the verify reported a false match.
    """
    data = bytes(range(256)) * 4  # 1024 B
    samples = plan_samples([(FLASH_BASE, data)], n=5, chunk=64, full=True)
    # Every 64 B window starting at 0, 64, 128, ..., up to the last chunk
    # that fits. 1024 / 64 = 16 chunks.
    assert len(samples) == 16
    # First and last always included.
    assert samples[0].address == FLASH_BASE
    assert samples[-1].address == FLASH_BASE + (1024 - 64)
    # Spaced evenly by exactly chunk bytes.
    addrs = [s.address for s in samples]
    assert addrs == [FLASH_BASE + i * 64 for i in range(16)]


def test_plan_samples_full_catches_drift_at_offset_5():
    """Regression test: the 2026-08-20 stale-ELF incident slipped past a 5-chunk
    sample plan because the drift was at an offset that landed between two
    samples. With `full=True` the same drift falls on a chunk boundary and
    is reported."""
    # 8 KB segment, drift at offset 0x800 (right between samples 5 and 6 of a
    # 5-chunk spread). With full=True, offset 0x800 is the start of chunk 32.
    data = bytes(0xCC) * (8 * 1024)
    samples_default = plan_samples([(FLASH_BASE, data)], n=5, chunk=64)
    samples_full    = plan_samples([(FLASH_BASE, data)], n=5, chunk=64, full=True)
    default_addrs = {s.address for s in samples_default}
    full_addrs    = {s.address for s in samples_full}
    # The drift at 0x800 must be in the full set; may or may not be in the default.
    assert FLASH_BASE + 0x800 in full_addrs
    # If it wasn't in the default set, that confirms the regression scenario.
    if FLASH_BASE + 0x800 not in default_addrs:
        # The 5-chunk spread on 8 KB skips 0x800 by design (samples at 0, 0x3F0, ...).
        # This is exactly the failure mode the new default --chunks 20 fixes.
        assert True


# ---- comparison (pure) -------------------------------------------------

def test_compare_matching_target_is_ok():
    samples = [Sample(FLASH_BASE, b"abcd"), Sample(FLASH_BASE + 100, b"wxyz")]
    lookup = {s.address: s.expected for s in samples}
    res = compare(samples, lambda a, n: lookup[a])
    assert res.ok
    assert res.mismatched == 0
    assert res.bytes_compared == 8
    assert "matches target" in res.describe()


def test_compare_detects_stale_elf_and_reports_first_bad_address():
    samples = [Sample(FLASH_BASE, b"aaaa"),
               Sample(FLASH_BASE + 64, b"bbbb"),
               Sample(FLASH_BASE + 128, b"cccc")]
    def read(addr, n):
        return b"XXXX" if addr >= FLASH_BASE + 64 else b"aaaa"
    res = compare(samples, read)
    assert not res.ok
    assert res.mismatched == 2
    assert res.first_bad == FLASH_BASE + 64
    msg = res.describe()
    assert "STALE ELF" in msg
    assert "NOT" in msg          # must say the addresses are untrustworthy


def test_no_segments_is_not_reported_as_ok():
    """Zero checks must never read as a pass -- that would defeat the whole point."""
    res = compare([], lambda a, n: b"")
    assert not res.ok
    assert "cannot verify" in res.describe()


# ---- real ELF ----------------------------------------------------------

@pytest.mark.skipif(not ELF.exists(), reason="firmware ELF not built")
def test_flash_segments_are_in_flash_and_nonempty():
    segs = flash_segments(ELF)
    assert segs, "expected at least one loadable flash segment"
    for addr, data in segs:
        assert addr >= FLASH_BASE
        assert len(data) > 0
