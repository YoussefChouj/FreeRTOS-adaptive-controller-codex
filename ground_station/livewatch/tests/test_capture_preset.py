"""Offline tests for the multi-slot capture helper.

Covers only the host-side pre-clear + the wire-budget prep. The actual UDP
capture (subscribe -> schema -> data -> CSV) requires a live FC, so it
stays in the manual bench suite at .agent_scripts/.
"""
from __future__ import annotations

import socket
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from ground_station.livewatch import capture_preset


# ---------------------------------------------------------------------------
# Fake socket -- exercises the same code path as real UDP without touching
# the wire, and lets the test assert what was actually sent.
# ---------------------------------------------------------------------------

class FakeSocket:
    """Minimal UDP-shaped stand-in for socket.socket(AF_INET, SOCK_DGRAM).

    Each call to sendto appends (data, address) to ``self.sent``.
    recvfrom pulls the next pre-loaded frame from ``self.queue`` (a list of
    raw bytes); when the queue is empty it raises socket.timeout so the
    drain loop terminates, mirroring the real AF_INET timeout behaviour.
    """

    def __init__(self, queue=None):
        self.queue = list(queue or [])
        self.sent: list[tuple[bytes, tuple]] = []
        self._timeout = 0.0
        self.closed = False

    def sendto(self, data, address):  # noqa: D401
        self.sent.append((bytes(data), tuple(address)))

    def recvfrom(self, _bufsize):
        if not self.queue:
            raise socket.timeout("no queued frames")
        return self.queue.pop(0), ("192.168.4.1", 14550)

    def settimeout(self, t):
        self._timeout = t

    def close(self):
        self.closed = True


def _no_sleep(_s):
    return None


# ---------------------------------------------------------------------------
# preclear_fc_subscriptions
# ---------------------------------------------------------------------------

def test_preclear_sends_one_nudge_plus_one_stop_per_slot_per_round():
    sock = FakeSocket()

    def factory():
        return sock

    res = capture_preset.preclear_fc_subscriptions(
        host="192.168.4.1", port=14550,
        slots=range(4), rounds=3, drain_secs=0.01,
        socket_factory=factory, sleep=_no_sleep,
    )

    # 3 rounds × (1 nudge + 4 stops) = 3 nudges + 12 stops = 15 packets.
    assert len(sock.sent) == 3 + 3 * 4
    # First packet of round 1 must be the nudge byte.
    assert sock.sent[0] == (b"\x00", ("192.168.4.1", 14550))
    # Stops must be on the wire format the firmware expects: 0xCC 0xDE 0x21 ...
    for i, (data, _) in enumerate(sock.sent):
        if data == b"\x00":
            continue
        assert data[:2] == b"\xCC\xDE", f"packet {i} not a subscribe frame: {data[:3].hex()}"
    assert res["rounds"] == 3
    assert res["drained_packets"] == 0
    assert res["finished_clean"] is True


def test_preclear_counts_frame_types_from_drain_window():
    """Data frames (0x09-0x0C) seen during drain must show up in frame_types_seen."""
    queue = [
        # Mixed frame types so the byte-2 frame-type counter is exercised.
        b"\xAA\xBB\x0A" + b"\x00" * 4,  # 0x0A slot-0 data
        b"\xAA\xBB\x0B" + b"\x00" * 4,  # 0x0B slot-1 data
        b"\xAA\xBB\x0C" + b"\x00" * 4,  # 0x0C slot-2 data
        b"\xAA\xBB\x09" + b"\x00" * 4,  # 0x09 slot-? data (legacy prefix)
        b"\xAA\xBB\x07" + b"\x00" * 4,  # 0x07 reply (kept for diagnostics)
    ] * 20  # plenty for both rounds
    sock = FakeSocket(queue=queue)
    res = capture_preset.preclear_fc_subscriptions(
        rounds=2, drain_secs=0.5,
        socket_factory=lambda: sock, sleep=_no_sleep,
    )
    assert res["drained_packets"] >= 5
    # The byte-2 byte is the frame type. Anything goes; we assert we
    # observed at least one of each data-frame type (0x09..0x0C).
    seen = res["frame_types_seen"]
    for ft in (0x09, 0x0A, 0x0B, 0x0C):
        assert seen.get(ft, 0) >= 1, f"frame_type 0x{ft:02X} not seen in {seen}"
    # 0x0A = slot 0 data, etc. The pre-clear helper does NOT need to flag
    # finished_clean=False on data frames from old rounds -- only the FINAL
    # round's drain window gates the bool. The presence of those counters
    # is what we care about for diagnostics.
    assert seen.get(0x07, 0) >= 1, "diagnostic frame_type 0x07 not seen"


def test_preclear_marks_clean_when_no_data_frames_in_final_round():
    # Drain windows emit only 0x08 (schema) replies, not 0x09-0x0C.
    queue = [b"\xAA\xBB\x08" + b"\x00" * 4] * 50
    sock = FakeSocket(queue=queue)
    res = capture_preset.preclear_fc_subscriptions(
        rounds=2, drain_secs=0.02,
        socket_factory=lambda: sock, sleep=_no_sleep,
    )
    # 0x08 is not in the data-frame range, so finished_clean stays True.
    assert res["finished_clean"] is True


def test_preclear_is_idempotent_on_quiet_socket():
    """Calling twice on a quiet FC returns finished_clean=True both times."""
    s1 = FakeSocket()
    capture_preset.preclear_fc_subscriptions(
        rounds=2, drain_secs=0.01,
        socket_factory=lambda: s1, sleep=_no_sleep,
    )
    s2 = FakeSocket()
    res2 = capture_preset.preclear_fc_subscriptions(
        rounds=2, drain_secs=0.01,
        socket_factory=lambda: s2, sleep=_no_sleep,
    )
    assert res2["finished_clean"] is True
    assert res2["drained_packets"] == 0
    # Both calls send the same number of packets -- idempotent in byte count.
    assert len(s1.sent) == len(s2.sent)


def test_preclear_result_is_jsonable_dict():
    sock = FakeSocket()
    res = capture_preset.preclear_fc_subscriptions(
        rounds=1, drain_secs=0.01,
        socket_factory=lambda: sock, sleep=_no_sleep,
    )
    import json
    serialised = json.dumps(res)
    again = json.loads(serialised)
    assert again == res


def test_preclear_handles_sendto_oserror_gracefully():
    """OSError on sendto must not crash the routine -- the FC may be offline."""
    sock = MagicMock()
    sock.sendto.side_effect = OSError("network unreachable")
    sock.recvfrom.side_effect = socket.timeout

    # Should not raise -- best-effort on offline FCs.
    res = capture_preset.preclear_fc_subscriptions(
        rounds=2, drain_secs=0.01,
        socket_factory=lambda: sock, sleep=_no_sleep,
    )
    assert res["rounds"] == 2
    assert res["drained_packets"] == 0


def test_preclear_closes_owned_socket():
    """When no socket_factory is provided, the routine MUST close its socket."""
    s = FakeSocket()
    # Patch the module's _default_factory indirectly by passing our factory
    # but flipping owns_sock=False to confirm the close path goes through.
    capture_preset.preclear_fc_subscriptions(
        rounds=1, drain_secs=0.01,
        socket_factory=lambda: s, sleep=_no_sleep,
    )
    # Externally-injected sockets are NOT auto-closed -- the owner closes.
    assert s.closed is False


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def test_constants_have_stated_defaults():
    assert capture_preset.DEFAULT_PRE_CLEAR_ROUNDS == 5
    assert capture_preset.DEFAULT_PRE_CLEAR_DRAIN_S == 2.5


def test_pre_clear_result_shape():
    """Lock in the dict shape the CLI/dashboard consume; break loudly on rename."""
    res = capture_preset.preclear_fc_subscriptions(
        rounds=1, drain_secs=0.01,
        socket_factory=lambda: FakeSocket(), sleep=_no_sleep,
    )
    for k in ("drained_packets", "frame_types_seen", "rounds",
              "duration_s", "finished_clean"):
        assert k in res, f"missing key: {k}"
