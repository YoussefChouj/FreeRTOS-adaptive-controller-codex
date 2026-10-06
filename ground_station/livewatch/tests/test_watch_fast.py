"""watch --fast / --stop-file: WiFi rate switch frames and the open-ended stop."""
import struct
from types import SimpleNamespace

from ground_station.livewatch import cli
from ground_station.livewatch.transport import Usart3WifiSubscribeTransport


class _Sock:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(data)


class _Wifi(Usart3WifiSubscribeTransport):
    def __init__(self):
        self.module_ip, self.port, self.sock = "192.168.4.1", 14550, _Sock()

    def _port(self):
        return SimpleNamespace(_sock=self.sock)


class _Reader:
    def __init__(self, transport, stop):
        self.transport, self.stop = transport, stop

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, names, hz, duration):
        for i in range(10_000):
            if i == 50:
                self.stop.write_text("")
            yield {"t": i / 200, "x": i}


def _args(tmp_path, **kw):
    a = dict(names=["x"], hz=200, secs=None, csv=str(tmp_path / "w.csv"), fast=True,
             stop_file=str(tmp_path / "w.stop"), quiet=True)
    a.update(kw)
    return SimpleNamespace(**a)


def test_fast_sends_mode_and_of_frame_then_restores(tmp_path, monkeypatch):
    wifi = _Wifi()
    monkeypatch.setattr(cli, "_expand", lambda a: a.names)
    monkeypatch.setattr(cli, "_live_reader", lambda a: _Reader(wifi, tmp_path / "w.stop"))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    cli.cmd_watch(_args(tmp_path))
    frames = [(f[3], struct.unpack("<f", f[4:8])[0]) for f in wifi.sock.sent]
    assert frames == [(102, 0.0)] * 2 + [(12, 1.0)] * 2 + [(12, 0.0)] * 2 + [(101, 0.0)] * 2
    f = wifi.sock.sent[0]
    assert f[:3] == b"\xcc\xdd\x0f"
    crc = 0
    for b in f[2:8]:
        crc ^= b
    assert f[8] == crc


def test_stop_file_ends_capture_and_keeps_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_expand", lambda a: a.names)
    monkeypatch.setattr(cli, "_live_reader", lambda a: _Reader(_Wifi(), tmp_path / "w.stop"))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    cli.cmd_watch(_args(tmp_path, fast=False))
    rows = (tmp_path / "w.csv").read_text().splitlines()
    assert 50 < len(rows) - 1 <= 80          # stopped within one 20-row check after the file appeared
    assert not (tmp_path / "w.stop").exists()
