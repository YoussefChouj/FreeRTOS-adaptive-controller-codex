"""The power gate must fail closed in every direction.

The interesting cases are not "does it see telemetry" but the three ways it
could wrongly say "safe to build": noise mistaken for frames, an unopenable port
treated as silence, and a requirement inverted.
"""
import pytest

from ground_station.flashtool import target_power as tp


def _frames(n=40):
    return (b"\xaa\xbb\x01\x00\x08" + b"\x00" * 8) * n


# ---- classifying a sample -------------------------------------------------

def test_streaming_telemetry_reads_as_powered():
    assert tp.powered_from_sample(_frames()) is True


def test_silence_reads_as_off():
    assert tp.powered_from_sample(b"") is False


def test_noise_without_a_preamble_is_not_powered():
    """A floating line delivers bytes; only a real frame header proves a core."""
    assert tp.powered_from_sample(b"\x00\xff" * 500) is False


def test_a_lone_preamble_is_not_enough():
    """One header in a trickle is more likely a glitch than a running task."""
    assert tp.powered_from_sample(b"\xaa\xbb") is False


# ---- the gate -------------------------------------------------------------

def test_require_off_passes_when_dark(monkeypatch):
    monkeypatch.setattr(tp, "sample", lambda *a, **k: b"")
    assert tp.main(["--require", "off"]) == 0


def test_require_off_refuses_while_powered(monkeypatch):
    """This is the case that halted the core on 2026-07-28."""
    monkeypatch.setattr(tp, "sample", lambda *a, **k: _frames())
    assert tp.main(["--require", "off"]) == 1


def test_require_on_refuses_a_dark_target(monkeypatch):
    monkeypatch.setattr(tp, "sample", lambda *a, **k: b"")
    assert tp.main(["--require", "on"]) == 1


def test_require_on_passes_while_powered(monkeypatch):
    monkeypatch.setattr(tp, "sample", lambda *a, **k: _frames())
    assert tp.main(["--require", "on"]) == 0


def test_an_unopenable_port_is_never_reported_as_off(monkeypatch):
    """No port means no evidence. Evidence-free must not read as 'safe to build'."""
    def boom(*a, **k):
        raise tp.PortUnavailable("COM6: access denied")
    monkeypatch.setattr(tp, "sample", boom)
    assert tp.main(["--require", "off"]) == 2
    assert tp.main(["--require", "on"]) == 2


def test_reporting_mode_never_gates(monkeypatch):
    monkeypatch.setattr(tp, "sample", lambda *a, **k: _frames())
    assert tp.main([]) == 0
