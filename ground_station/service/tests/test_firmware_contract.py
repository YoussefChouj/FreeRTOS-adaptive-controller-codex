"""Tests for the firmware contract manifest (WP1) and command lifecycle (WP3)."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from ground_station.platform.firmware_contract import (
    CONTRACT_VERSION, GS_PROTO_VERSION, PLATFORM_COMMAND_VERSION,
    SUBSCRIBE_MAX_SLOTS, SUBSCRIBE_MAX_RANGES, SUBSCRIBE_STREAM_MAX_BYTES,
    frame_size_subscribe_request, frame_size_schema_reply, frame_size_data_frame,
    effective_rate_hz, link_budget_usart3, link_budget_uart5,
    COMMAND_TABLE, FirmwareContract, current as firmware_contract,
)
from ground_station.platform.transactions import Outcome, RejectReason, Result
from ground_station.service.core import (
    GroundStationService, CommandLifecycle, CommandAction,
)
from ground_station.service.storage import SessionStore


# ---------------------------------------------------------------------------
# Firmware contract tests
# ---------------------------------------------------------------------------

def test_contract_version_is_v1():
    assert CONTRACT_VERSION == "v1"


def test_command_table_has_all_expected_ids():
    expected = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
                 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0F,
                 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x1E}
    missing = expected - set(COMMAND_TABLE.keys())
    assert not missing, f"missing command IDs: {missing:x}"


def test_command_0x04_is_dangerous():
    spec = COMMAND_TABLE[0x04]
    assert spec.safety.danger_level == "dangerous"
    assert spec.safety.description  # has a description
    assert "abort" in spec.description.lower()


def test_command_0x06_requires_sdk_mode():
    spec = COMMAND_TABLE[0x06]
    assert spec.safety.requires_sdk_mode


def test_command_0x0F_has_both_mrac_flags_and_telemetry_modes():
    spec = COMMAND_TABLE[0x0F]
    param_indices = {p.index for p in spec.params}
    assert 0 in param_indices   # adaptation_on
    assert 12 in param_indices  # of_frame_on
    assert 100 in param_indices  # telemetry_legacy
    assert 102 in param_indices # telemetry_subscribe_only


def test_command_0x18_requires_disarmed_and_ground_idle():
    spec = COMMAND_TABLE[0x18]
    assert spec.safety.requires_disarmed
    assert spec.safety.requires_ground_idle


def test_subscribe_limits_match_firmware():
    assert SUBSCRIBE_MAX_SLOTS == 4
    assert SUBSCRIBE_MAX_RANGES == 62
    assert SUBSCRIBE_STREAM_MAX_BYTES == 1024


def test_frame_size_subscribe_request_known_values():
    assert frame_size_subscribe_request(0) == 9
    assert frame_size_subscribe_request(1) == 17
    assert frame_size_subscribe_request(30) == 249
    assert frame_size_subscribe_request(31) == 257
    assert frame_size_subscribe_request(54) == 441  # original dashboard plan
    assert frame_size_subscribe_request(62) == 505


def test_frame_size_schema_reply_known_values():
    assert frame_size_schema_reply(0) == 11
    assert frame_size_schema_reply(23) == 195   # S2 live test
    assert frame_size_schema_reply(54) == 443   # original dashboard plan


def test_frame_size_data_frame():
    # total_bytes = 0 (no ranges active)
    assert frame_size_data_frame(0, 0) == 12
    # total_bytes = 32 (one u32 per range)
    assert frame_size_data_frame(4, 32) == 44


def test_effective_rate_hz():
    assert effective_rate_hz(1) == 100.0
    assert effective_rate_hz(2) == 50.0
    assert effective_rate_hz(4) == 25.0
    assert effective_rate_hz(10) == 10.0


def test_link_budget_usart3_within_budget():
    # 54-range plan: 441 bytes request, ~200 Hz
    # Check that a realistic plan is under 100%
    pct = link_budget_usart3(frame_size_data_frame(54, 200), 4)
    assert pct < 100.0  # must fit inside the link


def test_contract_validate_command_unknown():
    ok, reason = firmware_contract.validate_command(0x99, 0, 0.0)
    assert not ok
    assert "unknown command" in reason


def test_contract_validate_command_0x06_requires_sdk_mode():
    ok, reason = firmware_contract.validate_command(0x06, 0, 0.5)
    assert not ok
    assert "SDK mode" in reason
    ok, reason = firmware_contract.validate_command(0x06, 0, 0.5, sdk_mode=True)
    assert ok


def test_contract_validate_command_0x18_requires_disarmed():
    ok, reason = firmware_contract.validate_command(0x18, 0, 0.0)
    assert not ok
    assert "disarmed" in reason


def test_contract_validate_subscribe_plan():
    ok, reason = firmware_contract.subscribe_validate_plan(0, 0, 1, 1)
    assert not ok
    assert "n_ranges must be" in reason

    ok, reason = firmware_contract.subscribe_validate_plan(62, 1024, 1, 1)
    assert ok, reason

    ok, reason = firmware_contract.subscribe_validate_plan(62, 1025, 1, 1)
    assert not ok
    assert "exceeds max" in reason


def test_contract_to_dict():
    d = firmware_contract.to_dict()
    assert d["contract_version"] == "v1"
    assert "commands" in d
    assert "0x04" in d["commands"]
    assert d["commands"]["0x04"]["safety"]["danger_level"] == "dangerous"
    assert "subscribe" in d


# ---------------------------------------------------------------------------
# Command lifecycle tests (WP3)
# ---------------------------------------------------------------------------

def test_command_lifecycle_constants():
    assert CommandLifecycle.UNKNOWN == "unknown"
    assert CommandLifecycle.SUBMITTED == "submitted"
    assert CommandLifecycle.ACKNOWLEDGED == "acknowledged"
    assert CommandLifecycle.APPLIED == "applied"
    assert CommandLifecycle.REJECTED == "rejected"
    assert CommandLifecycle.TIMED_OUT == "timed_out"


def test_command_action_to_dict():
    action = CommandAction(
        transaction_id=42,
        command_id=0x04,
        index=0,
        value=0.0,
        flags=0,
        lifecycle=CommandLifecycle.SUBMITTED,
        outcome_name=None,
        reason_name=None,
        wire_bytes=9,
        submitted_ns=1_000_000_000_000,
        resolved_ns=None,
        detail="",
    )
    d = action.to_dict()
    assert d["transaction_id"] == 42
    assert d["command_id"] == 0x04
    assert d["lifecycle"] == "submitted"
    assert d["wire_bytes"] == 9
    assert d["resolved_ns"] is None


def test_command_action_resolved_to_dict():
    action = CommandAction(
        transaction_id=7,
        command_id=0x0F,
        index=11,
        value=1.0,
        flags=0,
        lifecycle=CommandLifecycle.APPLIED,
        outcome_name="APPLIED",
        reason_name="NONE",
        wire_bytes=9,
        submitted_ns=2_000_000_000_000,
        resolved_ns=2_001_000_000_000,
        detail="",
    )
    d = action.to_dict()
    assert d["lifecycle"] == "applied"
    assert d["outcome_name"] == "APPLIED"
    assert d["resolved_ns"] == 2_001_000_000_000


# ---------------------------------------------------------------------------
# Service action journal and fault log
# ---------------------------------------------------------------------------

def test_service_action_journal_empty_at_start():
    store = SessionStore()
    service = GroundStationService(store=store, source="test")
    service.start()
    journal = service.action_journal()
    assert journal == []
    service.stop()


def test_service_fault_log_empty_at_start():
    store = SessionStore()
    service = GroundStationService(store=store, source="test")
    service.start()
    faults = service.fault_log()
    assert faults == []
    service.stop()


def test_service_action_journal_populated_on_submit():
    """submit_command records an action in the journal."""
    store = SessionStore()
    mock_bridge = MagicMock()
    mock_bridge.send_transaction.return_value = 99
    service = GroundStationService(store=store, bridge=mock_bridge, source="test")
    service.start()

    txid = service.submit_command(0x04, index=0, value=0.0)
    assert txid == 99

    journal = service.action_journal()
    assert len(journal) == 1
    assert journal[0]["transaction_id"] == 99
    assert journal[0]["command_id"] == 0x04
    assert journal[0]["lifecycle"] == "submitted"
    assert journal[0]["wire_bytes"] > 0
    service.stop()


def test_service_events_for_session_records_submit():
    """events_for_session returns the submitted event."""
    store = SessionStore()
    mock_bridge = MagicMock()
    mock_bridge.send_transaction.return_value = 7
    service = GroundStationService(store=store, bridge=mock_bridge, source="test")
    service.start()

    service.submit_command(0x01, index=0, value=1.0)
    events = service.events_for_session(limit=10)
    event_kinds = [e["kind"] for e in events]
    assert "command_submitted" in event_kinds
    service.stop()


def test_service_fault_log_on_rejection():
    """poll_command with a REJECTED result appends to the fault log."""
    store = SessionStore()
    mock_bridge = MagicMock()
    mock_bridge.send_transaction.return_value = 11
    service = GroundStationService(store=store, bridge=mock_bridge, source="test")
    service.start()

    # Simulate a rejected result arriving
    rejected = Result(11, Outcome.REJECTED, 0x06, 0,
                      RejectReason.SAFETY_INTERLOCK, "command rejected")
    service.record_command_result(rejected)

    faults = service.fault_log()
    assert len(faults) == 1
    assert faults[0]["outcome"] == "rejected"
    assert faults[0]["reason"] == "SAFETY_INTERLOCK"
    service.stop()


def test_service_action_journal_updates_on_ack():
    """poll_command with an ACK resolves the submitted action to acknowledged."""
    store = SessionStore()
    mock_bridge = MagicMock()
    mock_bridge.send_transaction.return_value = 5
    service = GroundStationService(store=store, bridge=mock_bridge, source="test")
    service.start()

    service.submit_command(0x01, index=0, value=1.0)
    ack = Result(5, Outcome.ACK, 0x01, 0, RejectReason.NONE)
    service.record_command_result(ack)

    journal = service.action_journal()
    assert len(journal) == 1
    assert journal[0]["lifecycle"] == "acknowledged"
    assert journal[0]["outcome_name"] == "ACK"
    service.stop()


def test_service_action_journal_updates_on_applied():
    """poll_command with APPLIED resolves the action to applied."""
    store = SessionStore()
    mock_bridge = MagicMock()
    mock_bridge.send_transaction.return_value = 3
    service = GroundStationService(store=store, bridge=mock_bridge, source="test")
    service.start()

    # idx=1 (EMA freeze) is not arm-gated; idx=0 (mode) is disarmed-only.
    service.submit_command(0x1E, index=1, value=1.0)
    applied = Result(3, Outcome.APPLIED, 0x1E, 1, RejectReason.NONE)
    service.record_command_result(applied)

    journal = service.action_journal()
    assert journal[0]["lifecycle"] == "applied"
    assert journal[0]["outcome_name"] == "APPLIED"
    service.stop()


def test_service_action_journal_bounded():
    """Action journal drops oldest when maxlen is exceeded."""
    store = SessionStore()
    mock_bridge = MagicMock()
    # Return sequential txids
    txid_counter = [1]
    def next_txid(*a, **kw):
        t = txid_counter[0]
        txid_counter[0] += 1
        return t
    mock_bridge.send_transaction.side_effect = next_txid
    service = GroundStationService(store=store, bridge=mock_bridge, source="test")
    service.start()

    # Submit 70 actions (over ACTION_JOURNAL_MAX=64)
    for i in range(70):
        service.submit_command(0x01, index=0, value=float(i))

    journal = service.action_journal()
    assert len(journal) == 64
    # Oldest transactions should have been dropped
    txids = [a["transaction_id"] for a in journal]
    assert 1 not in txids  # first txid was dropped
    assert txid_counter[0] - 1 in txids  # last txid is present
    service.stop()


def test_service_fault_log_bounded():
    """Fault log drops oldest when maxlen is exceeded."""
    store = SessionStore()
    mock_bridge = MagicMock()
    txid_counter = [1]
    def next_txid(*a, **kw):
        t = txid_counter[0]
        txid_counter[0] += 1
        return t
    mock_bridge.send_transaction.side_effect = next_txid
    service = GroundStationService(store=store, bridge=mock_bridge, source="test")
    service.start()

    # Generate 70 rejections
    for i in range(70):
        r = Result(i + 10, Outcome.REJECTED, 0x06, 0,
                    RejectReason.SAFETY_INTERLOCK)
        service.record_command_result(r)

    faults = service.fault_log()
    assert len(faults) == 64
    service.stop()


# ---------------------------------------------------------------------------
# Service snapshot includes new contract/command fields
# ---------------------------------------------------------------------------

def test_service_snapshot_includes_adapter_version():
    """ServiceState always includes adapter_version."""
    from ground_station.service.core import ADAPTER_PROTOCOL_VERSION
    store = SessionStore()
    service = GroundStationService(store=store, source="test")
    service.start()
    snap = service.snapshot()
    assert snap.adapter_version == ADAPTER_PROTOCOL_VERSION
    assert snap.adapter_version == "v2"
    service.stop()
