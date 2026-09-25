import time
import threading

_rtos_cache = None
_rtos_cache_time = 0.0
_rtos_lock = threading.Lock()

def get_rtos_state():
    global _rtos_cache, _rtos_cache_time
    now = time.time()
    
    with _rtos_lock:
        if now - _rtos_cache_time < 1.0 and _rtos_cache is not None:
            return _rtos_cache
            
        try:
            from ground_station.livewatch.reader import LiveReader
            from ground_station.livewatch.transport import SwdCmsisDap
            from ground_station.livewatch.cli import _DEFAULT_ELF
            
            with LiveReader(str(_DEFAULT_ELF), transport=SwdCmsisDap()) as lr:
                vars_to_read = [
                    "g_task_snapshot", "g_task_snapshot_count", "g_task_snapshot_total_time",
                    "g_heap_free", "g_heap_min_free", "g_reset_csr",
                    "g_loop_period_cyc_last", "g_loop_period_cyc_max",
                    "g_loop_period_cyc_min", "g_loop_overrun_count"
                ]
                res1 = lr.read_many(vars_to_read)
                time.sleep(0.5)  # 500ms window for cpu % instead of 1.0s to avoid blocking /api too long
                res2 = lr.read_many(vars_to_read)
                
            # Process and build JSON payload
            t1_total = res1.get("g_task_snapshot_total_time", 0)
            t2_total = res2.get("g_task_snapshot_total_time", 0)
            dt = t2_total - t1_total
            
            t1_tasks = res1.get("g_task_snapshot", [])
            t2_tasks = res2.get("g_task_snapshot", [])
            t1_count = res1.get("g_task_snapshot_count", 0)
            t2_count = res2.get("g_task_snapshot_count", 0)
            
            t1_map = {t.get("pcTaskName", b"").decode("ascii", "ignore").strip("\x00"): t for t in t1_tasks[:t1_count]}
            t2_map = {t.get("pcTaskName", b"").decode("ascii", "ignore").strip("\x00"): t for t in t2_tasks[:t2_count]}
            
            tasks = []
            for name, t2 in sorted(t2_map.items()):
                t1 = t1_map.get(name, t2)
                run1 = t1.get("ulRunTimeCounter", 0)
                run2 = t2.get("ulRunTimeCounter", 0)
                
                cpu = 0.0
                if dt > 0:
                    cpu = (run2 - run1) / dt * 100.0
                
                tasks.append({
                    "name": name,
                    "priority": t2.get("uxCurrentPriority", 0),
                    "state": t2.get("eCurrentState", 0),
                    "cpu_percent": round(cpu, 1),
                    "stack_hwm": t2.get("usStackHighWaterMark", 0)
                })
                
            payload = {
                "available": True,
                "tasks": tasks,
                "heap_free": res2.get("g_heap_free", 0),
                "heap_min_free": res2.get("g_heap_min_free", 0),
                "loop_stats": {
                    "last_cycles": res2.get("g_loop_period_cyc_last", 0),
                    "min_cycles": res2.get("g_loop_period_cyc_min", 0),
                    "max_cycles": res2.get("g_loop_period_cyc_max", 0),
                    "overruns": res2.get("g_loop_overrun_count", 0),
                },
                "reset_csr": res2.get("g_reset_csr", 0)
            }
            _rtos_cache = payload
            _rtos_cache_time = time.time()
            return payload
            
        except Exception as e:
            payload = {"available": False, "reason": str(e)}
            # Do NOT cache failures for a full second, or maybe do cache them so we don't spam the broken probe?
            # Cache them for 1 second.
            _rtos_cache = payload
            _rtos_cache_time = time.time()
            return payload
