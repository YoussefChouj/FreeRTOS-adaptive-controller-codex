"""GET /api/rtos: probe-backed FreeRTOS health, cached, never blocking.

One SWD snapshot per refresh (no sleep); CPU % is computed against the previous refresh once the firmware's
1 Hz snapshot has advanced. A concurrent caller gets the cached payload instead of waiting on the probe.
"""
import threading
import time

CACHE_S = 2.0
PREV_MAX_AGE_S = 20.0   # CYCCNT wraps every 2^32/168 MHz = 25.6 s: an older prev gives a wrong modular delta
COLD_WAIT_S = 2.5      # > one 1 Hz firmware refresh

_lock = threading.Lock()
_cache = None          # last payload
_cache_time = 0.0
_prev_snap = None      # last raw snapshot, for the CPU % window
_name_cache = {}


def _open_reader():
    from ground_station.livewatch.cli import _DEFAULT_ELF
    from ground_station.livewatch.reader import LiveReader
    from ground_station.livewatch.transport import SwdCmsisDap
    return LiveReader(str(_DEFAULT_ELF), transport=SwdCmsisDap())


def reset():
    global _cache, _cache_time, _prev_snap
    _cache, _cache_time, _prev_snap = None, 0.0, None
    _name_cache.clear()


def get_rtos_state():
    global _cache, _cache_time, _prev_snap
    from ground_station.livewatch import rtos
    now = time.time()
    if _cache is not None and now - _cache_time < CACHE_S:
        return _cache
    if not _lock.acquire(blocking=False):
        return _cache if _cache is not None else {"available": False, "reason": "probe busy"}
    try:
        prev = _prev_snap
        if prev is not None and now - prev["t_host"] > PREV_MAX_AGE_S:
            prev = None
        with _open_reader() as lr:
            if prev is None:
                # cold start or stale prev: take the window inside this call, across one firmware refresh
                prev = rtos._snapshot_retry(lr, _name_cache)
                deadline = time.time() + COLD_WAIT_S
                snap = prev
                while time.time() < deadline and snap["g_task_snapshot_total_time"] == prev["g_task_snapshot_total_time"]:
                    time.sleep(0.2)
                    snap = rtos._snapshot_retry(lr, _name_cache)
            else:
                snap = rtos._snapshot_retry(lr, _name_cache)
        payload = rtos.build_report(snap, prev)
        if payload["cpu_window_s"] is None and _cache is not None:
            # firmware 1 Hz snapshot has not advanced since the last refresh: keep prev, reuse the last CPU %
            last = {t["number"]: t["cpu_percent"] for t in _cache.get("tasks", [])}
            for t in payload["tasks"]:
                t["cpu_percent"] = last.get(t["number"])
            payload["cpu_window_s"] = _cache.get("cpu_window_s")
        if prev is None or snap["g_task_snapshot_total_time"] != prev["g_task_snapshot_total_time"]:
            _prev_snap = snap
        _cache, _cache_time = payload, time.time()
        return payload
    except Exception as e:  # probe absent, busy in another process, stale ELF...
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}
    finally:
        _lock.release()
