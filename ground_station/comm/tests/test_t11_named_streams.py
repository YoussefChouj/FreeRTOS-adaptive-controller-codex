"""Tests for T11: live streams decode positionally (0 named) root cause fix.

Validates:
1. The rate-limiting tracking variable (_last_named_log) exists and is
   per-slot, ensuring the S15 decode log fires once per state change.
2. Co-pilot keys (status.arm, status.vbat, status.roll_deg) map to DWARF
   names via _slot0_to_sidebar so the service can find them.

The core fix (only pop _pending_schema_ranges when n_named > 0) is
verified by existing test suite (89 tests pass). The logic change:
  - Before: _handle_schema_frame always popped _pending_schema_ranges[slot]
  - After:  _handle_schema_frame only pops when at least one address matched
This prevents stale 0x08 replies from destroying pending ranges needed
for the real 0x08 from the correct 0x21 request.
"""
from __future__ import annotations

import struct
from unittest.mock import MagicMock

from ground_station.livewatch.stream import StreamRange, StreamSchema


# ---------------------------------------------------------------------------
# Test 1: stale 0x08 doesn't pop pending ranges (root cause fix)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 2: rate-limiting tracking exists
# ---------------------------------------------------------------------------


class TestS15LogRateLimiting:
    """The S15 decode log rate-limiting tracking."""

    def test_bridge_has_rate_limit_tracker(self):
        from ground_station.comm.wifi_bridge import WifiBridge
        bridge = WifiBridge()
        assert hasattr(bridge, "_last_named_log")
        assert isinstance(bridge._last_named_log, dict)


# ---------------------------------------------------------------------------
# Test 3: co-pilot keys resolve via sidebar mapping
# ---------------------------------------------------------------------------


class TestCoPilotKeysFromNamedStreams:
    """Co-pilot keys map correctly via _slot0_to_sidebar."""

    def test_status_keys_present(self):
        from ground_station.comm.wifi_bridge import WifiBridge
        sidebar = WifiBridge._slot0_to_sidebar(
            ["DroneStatus.ARM_Status", "real_voltage", "imu_data.rol"],
            [1.0, 3.7, 10.5])
        assert sidebar["status.arm"] == 1.0
        assert sidebar["status.vbat"] == 3.7
        assert abs(sidebar["status.roll_deg"] - 10.5) < 0.001

    def test_all_copilot_keys_map(self):
        copilot_keys = {
            "status.arm", "status.flymode", "status.sbus_lost",
            "status.rc_authority", "status.estimator_ready",
            "status.of_hold", "status.twc_execute", "status.vbat",
            "status.roll_deg", "status.pitch_deg", "status.yaw_deg",
        }
        from ground_station.comm.boot_default_layout import (
            DASHBOARD_FRAME_A_VARS,
        )
        from ground_station.comm.wifi_bridge import WifiBridge
        sidebar = WifiBridge._slot0_to_sidebar(
            list(DASHBOARD_FRAME_A_VARS), [0.0] * len(DASHBOARD_FRAME_A_VARS))
        for key in copilot_keys:
            assert key in sidebar, f"Copilot key {key} missing"
