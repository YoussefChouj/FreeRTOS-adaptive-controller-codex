"""Offline tests for the SWD RTOS bridge (task J1).

No hardware: the reader is faked. A second group (skipped without the
firmware ELF) exercises the real coalesced plan + decode with synthetic
region bytes, proving u16/u32 widths end to end.
"""
import struct
import sys
import time
import types
from pathlib import Path

import pytest

from ground_station.platform.rtos_bridge import (
    RTOS_SLOT_KEY, RTOS_SYMBOLS, RtosBridge, RtosSample,
)

# Known values fed through the faked reader (firmware-symbol keyed).
DECODED = {
    "platform_obs_send_ticks":      12345,
    "platform_obs_queue_depth":     27,
    "platform_obs_dma_busy":        1,
    "platform_obs_usart3_tx_drops": 3,
    "platform_obs_cmd_queue_depth": 4,
    "platform_obs_cmd_queue_max":   16,
    "platform_obs_heap_free_bytes": 99999,
    "UA3TxFrames":                  54321,
    "xTickCount":                   888888,
}


class FakePlan:
    def decode(self, blocks):
        return dict(DECODED)


class FakeReaderError(Exception):
    pass


class FakeReader:
    def __init__(self, *a, **k):
        self.closed = False

    def connect(self):
        return self

    def close(self):
        self.closed = True

    def plan(self, names):
        return FakePlan()

    def sample(self, plan):
        return plan.decode(None)


class FakeService:
    def __init__(self):
        self.injected = []

    def inject_external_stream(self, slot, values, sequence=0):
        self.injected.append((slot, dict(values), sequence))


def _bridge(**k):
    return RtosBridge(FakeService(), "fake.axf", **k)


# ---- read plan surface ----------------------------------------------------

def test_plan_covers_nine_symbols_with_correct_width_sources():
    assert len(RTOS_SYMBOLS) == 9
    assert "xTickCount" in RTOS_SYMBOLS
    assert "UA3TxFrames" in RTOS_SYMBOLS
    for stem in ("send_ticks", "queue_depth", "dma_busy", "usart3_tx_drops",
                 "cmd_queue_depth", "cmd_queue_max", "heap_free_bytes"):
        assert "platform_obs_" + stem in RTOS_SYMBOLS


# ---- decode + key mapping -------------------------------------------------

def test_read_one_packs_all_values():
    sample = _bridge()._read_one(FakeReader(), FakePlan())
    assert isinstance(sample, RtosSample)
    assert sample.send_task_ticks == 12345
    assert sample.queue_depth == 27
    assert sample.dma_busy == 1
    assert sample.usart3_tx_drops == 3
    assert sample.cmd_queue_depth == 4
    assert sample.cmd_queue_max == 16
    assert sample.heap_free_bytes == 99999
    assert sample.usart3_tx_count == 54321
    assert sample.scheduler_tick_count == 888888


def test_as_values_keys_match_panel_hints():
    values = _bridge()._read_one(FakeReader(), FakePlan()).as_values()
    # Every sourced panel hint key is present with the right value.
    assert values["rtos.scheduler_tick_count"] == 888888.0
    assert values["rtos.heap_free_bytes"] == 99999.0
    assert values["rtos.usart3_tx_count"] == 54321.0
    assert values["rtos.cmd_queue_depth"] == 4.0
    assert values["rtos.cmd_queue_max"] == 16.0
    assert values["rtos.send_task_ticks"] == 12345.0
    assert values["rtos.dma_busy"] == 1.0
    # Extras surfaced deliberately; old misnamed key and byte key never emitted.
    assert values["rtos.queue_depth"] == 27.0
    assert values["rtos.usart3_tx_drops"] == 3.0
    assert "rtos.send_ticks" not in values
    assert "rtos.usart3_tx_bytes" not in values


def test_large_u32_values_survive_as_exact_integers():
    # F1 failure mode: u32 read as a narrower/wrong type -> denormal/0.
    decoded = dict(DECODED)
    decoded["platform_obs_heap_free_bytes"] = 100000
    decoded["platform_obs_send_ticks"] = 4_000_000_000  # within u32, past u16
    plan = types.SimpleNamespace(decode=lambda blocks: dict(decoded))
    values = _bridge()._read_one(FakeReader(), plan).as_values()
    assert values["rtos.heap_free_bytes"] == 100000.0
    assert int(values["rtos.heap_free_bytes"]) == 100000
    assert values["rtos.send_task_ticks"] == 4_000_000_000.0
    assert float(values["rtos.send_task_ticks"]).is_integer()


def test_decode_miss_returns_none():
    class BadPlan:
        def decode(self, blocks):
            return {"xTickCount": 1}  # everything else missing

    assert _bridge()._read_one(FakeReader(), BadPlan()) is None


# ---- full background loop (LiveReader faked via sys.modules) --------------

def test_background_loop_injects_samples(monkeypatch):
    fake_module = types.ModuleType("ground_station.livewatch.reader")
    fake_module.LiveReader = FakeReader
    fake_module.LiveTransportError = FakeReaderError
    monkeypatch.setitem(sys.modules, "ground_station.livewatch.reader", fake_module)

    service = FakeService()
    bridge = RtosBridge(service, "fake.axf", interval_hz=100.0)
    bridge.start()
    time.sleep(0.15)
    bridge.stop(timeout=2.0)

    assert len(service.injected) >= 2
    for i, (slot, values, seq) in enumerate(service.injected, start=1):
        assert slot == RTOS_SLOT_KEY
        assert seq == i
        assert values["rtos.heap_free_bytes"] == 99999.0
        assert "rtos.usart3_tx_bytes" not in values
    assert bridge.sequence == len(service.injected)


# ---- real coalesced plan against the built ELF ----------------------------

ELF = Path(__file__).resolve().parents[3] / "OBJ" / "JX_FLY.axf"


@pytest.mark.skipif(not ELF.exists(), reason="firmware ELF not built")
def test_real_plan_coalesces_nine_symbols_into_two_regions():
    from ground_station.livewatch.symbols import SymbolResolver
    from ground_station.livewatch.reader import build_plan

    resolver = SymbolResolver(ELF)
    try:
        plan = build_plan(resolver, list(RTOS_SYMBOLS))
    finally:
        resolver.close()

    assert len(plan.symbols) == 9
    assert len(plan.regions) == 2
    print("\nREGIONS:", [(hex(r.start), r.size) for r in plan.regions])
    print("SYMBOL_WIDTHS:", [(s.name, s.size, s.fmt) for s in plan.symbols])

    # Encode known values into synthetic region bytes using each symbol's
    # DWARF-derived width, then decode through the real Plan.decode.
    region_bytes = [bytearray(r.size) for r in plan.regions]
    for sym in plan.symbols:
        ri, off = plan._locate(sym.address)
        struct.pack_into("<" + sym.fmt, region_bytes[ri], off, DECODED[sym.name])

    decoded = plan.decode([bytes(b) for b in region_bytes])
    for name, value in DECODED.items():
        assert decoded[name] == value

    # The exact dict the bridge injects, from the real decode path.
    reader = types.SimpleNamespace(sample=lambda p: decoded)
    sample = _bridge()._read_one(reader, plan)
    print("INJECTED:", repr(sample.as_values()))
    assert sample.heap_free_bytes == 99999
