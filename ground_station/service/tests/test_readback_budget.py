"""Telemetry readback must stay cheap on the ingest thread (10-18 P15: per-frame readback stalled ingest)."""
from ground_station.service import core
from ground_station.service.core import CommandAction, CommandLifecycle, GroundStationService


def _applied(txid: int, value: float) -> CommandAction:
    return CommandAction(
        transaction_id=txid, command_id=15, index=10, value=value, flags=0,
        lifecycle=CommandLifecycle.APPLIED, outcome_name="APPLIED", reason_name=None,
        wire_bytes=12, submitted_ns=0, resolved_ns=1,
    )


def test_txids_gone_from_journal_are_pruned():
    svc = GroundStationService()
    svc._action_journal.append(_applied(900, 1.0))
    svc._pending_verifications[(15, 10)] = list(range(500)) + [900]
    svc._check_readback()
    assert svc._pending_verifications == {(15, 10): [900]}


def test_matching_value_is_verified():
    svc = GroundStationService()
    svc._action_journal.append(_applied(7, 2.5))
    svc._pending_verifications[(15, 10)] = [7]
    svc._streams[0] = {"values": {"inj": 2.5}}
    svc._check_readback()
    assert svc._pending_verifications == {}
    assert svc._action_journal[0].lifecycle == CommandLifecycle.VERIFIED


def test_readback_runs_at_most_once_per_interval(monkeypatch):
    svc = GroundStationService()
    svc._action_journal.append(_applied(7, 2.5))
    svc._pending_verifications[(15, 10)] = [7]
    clock = [10 * core.READBACK_MIN_INTERVAL_NS]
    monkeypatch.setattr(core.time, "monotonic_ns", lambda: clock[0])
    svc._check_readback()  # runs: no match yet
    svc._streams[0] = {"values": {"inj": 2.5}}
    clock[0] += core.READBACK_MIN_INTERVAL_NS - 1
    svc._check_readback()  # inside the interval: skipped
    assert svc._pending_verifications == {(15, 10): [7]}
    clock[0] += 1
    svc._check_readback()
    assert svc._pending_verifications == {}
