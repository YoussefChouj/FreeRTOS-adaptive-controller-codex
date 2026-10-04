"""The ground station's subscribe limits vs the firmware header API/subscribe.h (WP-37).

firmware_contract.py mirrors the subscribe protocol limits; livewatch/stream.py has its own copy of the slot
count. A slot count that differs between them and the firmware makes the GS build requests the drone refuses
("E:bad slot") or ignore frames it sends, so all three are pinned to the header here.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

from ground_station.livewatch import stream

# the package re-exports an object named firmware_contract, so take the module itself
fc = importlib.import_module("ground_station.platform.firmware_contract")

HEADER = Path(__file__).resolve().parents[3] / "API" / "subscribe.h"

# Frame types the FC sends on the same links besides subscribe data (TASK/send_data.c, API/subscribe.c,
# firmware/platform_registry.h, API/fw_identity.h, the transaction results 0x30..0x32).
OTHER_FC_FRAMES = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x22, 0x23, 0x24, 0x25, 0x30, 0x31, 0x32, 0x7F}


def _define(name: str) -> int:
    text = HEADER.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"#define\s+%s\s+(0x[0-9A-Fa-f]+|\d+)U?\b" % name, text)
    assert m, f"{name} missing from {HEADER}"
    return int(m.group(1), 0)


def test_limits_match_the_firmware_header():
    assert fc.SUBSCRIBE_MAX_SLOTS == _define("SUBSCRIBE_MAX_SLOTS")
    assert fc.SUBSCRIBE_MAX_RANGES == _define("SUBSCRIBE_MAX_STREAM_RANGES")
    assert fc.SUBSCRIBE_STREAM_MAX_BYTES == _define("SUBSCRIBE_STREAM_MAX_BYTES")
    assert fc.SUBSCRIBE_STREAM_FRAME_OVERHEAD == _define("SUBSCRIBE_STREAM_FRAME_OVERHEAD")
    assert fc.SUBSCRIBE_BUDGET_PCT_USART3 == _define("SUBSCRIBE_BUDGET_PCT_USART3")
    assert fc.SUBSCRIBE_BUDGET_PCT_UART5 == _define("SUBSCRIBE_BUDGET_PCT_UART5")
    assert fc.SUBSCRIBE_DATA_FRAME_BASE == _define("SUBSCRIBE_FRAME_TYPE_DATA")


def test_the_two_host_copies_of_the_slot_count_agree():
    assert fc.SUBSCRIBE_MAX_SLOTS == stream.MAX_SLOTS


def test_data_frame_types_are_one_per_slot():
    assert list(fc.SUBSCRIBE_DATA_FRAMES) == [fc.SUBSCRIBE_DATA_FRAME_BASE + s for s in range(fc.SUBSCRIBE_MAX_SLOTS)]


def test_eight_slots_would_not_collide_with_another_frame_type():
    """The claim in API/subscribe.h: 0x09..0x10 are free, so 8 slots need no new frame-type block."""
    eight = set(range(fc.SUBSCRIBE_DATA_FRAME_BASE, fc.SUBSCRIBE_DATA_FRAME_BASE + 8))
    assert not eight & OTHER_FC_FRAMES
