from ground_station.livewatch.tests.test_rtos import NAMES, FakeReader, frame
from ground_station.service import rtos_api


def _run(monkeypatch, frames, clock):
    lr = FakeReader(frames, NAMES)
    monkeypatch.setattr(rtos_api, "_open_reader", lambda: lr)
    monkeypatch.setattr(rtos_api.time, "time", lambda: clock[0])
    monkeypatch.setattr(rtos_api.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    rtos_api.reset()
    return lr


def test_first_call_takes_window_across_refresh(monkeypatch):
    clock = [100.0]
    a = frame(0, [(0x20002000, 2, 0, 5, 0, 100)])
    b = frame(168_000_000, [(0x20002000, 2, 0, 5, 33_600_000, 99)])
    c = frame(336_000_000, [(0x20002000, 2, 0, 5, 50_400_000, 98)])
    _run(monkeypatch, [a, a, b, b, c], clock)
    r1 = rtos_api.get_rtos_state()                            # cold: a, a, b in one call
    assert r1["tasks"][0]["cpu_percent"] == 20.0 and r1["cpu_window_s"] == 1.0
    assert rtos_api.get_rtos_state() is r1                    # cached inside CACHE_S
    clock[0] += 3
    r2 = rtos_api.get_rtos_state()                            # fw not advanced: last CPU % carried
    assert r2["tasks"][0]["cpu_percent"] == 20.0
    clock[0] += 3
    r3 = rtos_api.get_rtos_state()                            # warm: window b -> c
    assert r3["tasks"][0]["cpu_percent"] == 10.0 and r3["cpu_window_s"] == 1.0


def test_stale_prev_is_dropped_before_cyccnt_wrap(monkeypatch):
    clock = [100.0]
    a = frame(0, [(0x20002000, 2, 0, 5, 0, 100)])
    b = frame(168_000_000, [(0x20002000, 2, 0, 5, 33_600_000, 99)])
    c = frame(336_000_000, [(0x20002000, 2, 0, 5, 50_400_000, 98)])
    d = frame(504_000_000, [(0x20002000, 2, 0, 5, 84_000_000, 97)])
    _run(monkeypatch, [a, b, c, d], clock)
    rtos_api.get_rtos_state()
    clock[0] += rtos_api.PREV_MAX_AGE_S + 5                   # prev b is older than the wrap guard
    r = rtos_api.get_rtos_state()                             # fresh window c -> d, not b -> d
    assert r["tasks"][0]["cpu_percent"] == 20.0 and r["cpu_window_s"] == 1.0


def test_probe_failure_is_unavailable(monkeypatch):
    def boom():
        raise RuntimeError("no probe")
    monkeypatch.setattr(rtos_api, "_open_reader", boom)
    rtos_api.reset()
    r = rtos_api.get_rtos_state()
    assert r == {"available": False, "reason": "RuntimeError: no probe"}
    assert rtos_api._lock.acquire(blocking=False)
    rtos_api._lock.release()


def test_busy_lock_returns_without_blocking(monkeypatch):
    rtos_api.reset()
    rtos_api._lock.acquire()
    try:
        assert rtos_api.get_rtos_state() == {"available": False, "reason": "probe busy"}
    finally:
        rtos_api._lock.release()
