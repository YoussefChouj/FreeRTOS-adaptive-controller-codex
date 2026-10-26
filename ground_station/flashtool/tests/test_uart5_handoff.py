"""Synthetic handoff tests: no real socket, probe or drone access."""
import socket
import pytest
from ground_station.flashtool import rebuild_and_flash as rf
from ground_station.comm.mrac_param_encoder import build_command_frame


@pytest.fixture
def handoff(monkeypatch, tmp_path):
    elf = tmp_path / 'previous.axf'
    elf.write_bytes(b'verified fixture')
    calls = []
    class Link:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setsockopt(self, *args): pass
        def bind(self, addr): calls.append(('bind', addr))
        def sendto(self, frame, addr): calls.append(('send', frame, addr))
    monkeypatch.setattr(socket, 'socket', lambda *args: Link())
    monkeypatch.setattr(rf, 'elf_matches_target', lambda elf: True)
    monkeypatch.setattr(rf, 'arm_status', lambda elf: (rf.DISARMED, 9, 0))
    monkeypatch.setattr(rf.time, 'sleep', lambda _: None)
    return elf, calls, Link


def test_verified_disarmed_handoff_sends_only_mixed_command(handoff):
    elf, calls, _ = handoff
    assert rf.restore_uart5_telemetry(elf)
    assert calls[0] == ('bind', ('0.0.0.0', 14550))
    assert calls[1:] == [('send', build_command_frame(0x0F, 101, 0.0), ('192.168.4.1', 14550))] * 2


@pytest.mark.parametrize('vote', [(1, 9, 0), (None, 0, 9), (0, 8, 1), (0, 0, 0)])
def test_armed_or_uncertain_disarm_never_sends(handoff, monkeypatch, vote):
    elf, calls, _ = handoff
    monkeypatch.setattr(rf, 'arm_status', lambda elf: vote)
    assert not rf.restore_uart5_telemetry(elf)
    assert calls == []


def test_stale_elf_never_sends(handoff, monkeypatch):
    elf, calls, _ = handoff
    monkeypatch.setattr(rf, 'elf_matches_target', lambda elf: False)
    assert not rf.restore_uart5_telemetry(elf)
    assert calls == []


def test_missing_elf_never_sends(handoff):
    elf, calls, _ = handoff
    elf.unlink()
    assert not rf.restore_uart5_telemetry(elf)
    assert calls == []


def test_active_bridge_port_never_sends(handoff, monkeypatch):
    elf, calls, link = handoff
    def busy(self, addr): raise OSError('port held by 8081')
    monkeypatch.setattr(link, 'bind', busy)
    assert not rf.restore_uart5_telemetry(elf)
    assert calls == []
