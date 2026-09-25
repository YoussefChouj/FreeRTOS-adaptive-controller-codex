"""FreeRTOS health through the probe: per-task CPU %, stack HWM, heap, loop timing, reset cause.

Firmware side (USER/main.c): SystemMonitor_Task fills ``g_task_snapshot[]`` via uxTaskGetSystemState
at 1 Hz; the run-time counter is raw DWT CYCCNT (168 MHz, wraps every 25.6 s), so every delta here
is a 32-bit modular subtraction. CPU % needs two snapshots with different ``g_task_snapshot_total_time``.
"""
from __future__ import annotations

import time

CPU_HZ = 168_000_000
LOOP_NOMINAL_HZ = 200
TASK_NAME_LEN = 16          # configMAX_TASK_NAME_LEN
MASK32 = 0xFFFFFFFF

TASK_FIELDS = ("pcTaskName", "xTaskNumber", "eCurrentState", "uxCurrentPriority",
               "ulRunTimeCounter", "usStackHighWaterMark")
SCALARS = ("g_task_snapshot_count", "g_task_snapshot_total_time", "g_heap_free", "g_heap_min_free",
           "g_reset_csr", "g_loop_period_cyc_last", "g_loop_period_cyc_max",
           "g_loop_period_cyc_min", "g_loop_overrun_count")
STATES = ("Run", "Rdy", "Blk", "Sus", "Del")
# RCC_CSR reset flags (RM0090 7.3.21), bit -> name
CSR_FLAGS = ((31, "LPWR"), (30, "WWDG"), (29, "IWDG"), (28, "SFT"), (27, "POR"), (26, "PIN"), (25, "BOR"))


def decode_csr(csr: int) -> list[str]:
    return [name for bit, name in CSR_FLAGS if int(csr) & (1 << bit)]


def _cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace")


def read_snapshot(lr, max_tasks: int = 16, name_cache: dict | None = None) -> dict:
    """One probe pass: scalars, then the ``count`` valid task entries, then their names."""
    sc = lr.sample(lr.plan(list(SCALARS)))
    count = max(0, min(int(sc["g_task_snapshot_count"]), max_tasks))
    names = [f"g_task_snapshot[{i}].{f}" for i in range(count) for f in TASK_FIELDS]
    row = lr.sample(lr.plan(names)) if names else {}
    cache = {} if name_cache is None else name_cache
    tasks = []
    for i in range(count):
        t = {f: int(row[f"g_task_snapshot[{i}].{f}"]) for f in TASK_FIELDS}
        ptr = t.pop("pcTaskName")
        if ptr not in cache:
            cache[ptr] = _cstr(lr.read_raw(ptr, TASK_NAME_LEN)) if ptr else f"task{t['xTaskNumber']}"
        t["name"] = cache[ptr]
        tasks.append(t)
    snap = {k: int(v) for k, v in sc.items()}
    snap["tasks"] = tasks
    snap["t_host"] = time.time()
    return snap


def build_report(cur: dict, prev: dict | None = None) -> dict:
    """JSON-ready view. CPU % is None unless ``prev`` is an older, different firmware snapshot."""
    dt = None
    if prev is not None:
        d = (cur["g_task_snapshot_total_time"] - prev["g_task_snapshot_total_time"]) & MASK32
        dt = d or None
    before = {t["xTaskNumber"]: t for t in (prev or {}).get("tasks", [])}
    tasks = []
    for t in sorted(cur["tasks"], key=lambda x: -x["uxCurrentPriority"]):
        cpu = None
        p = before.get(t["xTaskNumber"])
        if dt and p is not None:
            cpu = round(((t["ulRunTimeCounter"] - p["ulRunTimeCounter"]) & MASK32) * 100.0 / dt, 2)
        st = t["eCurrentState"]
        tasks.append({"name": t["name"], "number": t["xTaskNumber"], "priority": t["uxCurrentPriority"],
                      "state": STATES[st] if 0 <= st < len(STATES) else str(st),
                      "cpu_percent": cpu, "stack_hwm_words": t["usStackHighWaterMark"]})
    to_us = 1e6 / CPU_HZ
    mn = cur["g_loop_period_cyc_min"]
    loop = {"nominal_us": 1e6 / LOOP_NOMINAL_HZ,
            "last_us": round(cur["g_loop_period_cyc_last"] * to_us, 1),
            "max_us": round(cur["g_loop_period_cyc_max"] * to_us, 1),
            "min_us": None if mn == MASK32 else round(mn * to_us, 1),
            "overruns": cur["g_loop_overrun_count"]}
    csr = cur["g_reset_csr"]
    return {"available": True, "tasks": tasks, "cpu_window_s": None if dt is None else round(dt / CPU_HZ, 3),
            "heap_free": cur["g_heap_free"], "heap_min_free": cur["g_heap_min_free"], "loop": loop,
            "reset_csr": f"0x{csr:08X}", "reset_causes": decode_csr(csr)}


def _snapshot_retry(lr, cache: dict, tries: int = 3) -> dict:
    """The wireless SWD link drops single DAP transfers (live: 1 of 3 runs); a re-read succeeds."""
    for attempt in range(tries):
        try:
            return read_snapshot(lr, name_cache=cache)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(0.1)


def read_report(lr, wait_s: float = 2.5) -> dict:
    """Two snapshots across a firmware refresh (1 Hz) so CPU % is defined."""
    cache: dict = {}
    first = _snapshot_retry(lr, cache)
    cur = first
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(0.25)
        cur = _snapshot_retry(lr, cache)
        if cur["g_task_snapshot_total_time"] != first["g_task_snapshot_total_time"]:
            break
    return build_report(cur, first)


def format_report(rep: dict) -> str:
    out = [f"{'task':<16} {'prio':>4} {'state':<5} {'cpu%':>7} {'stack_hwm_w':>11}"]
    for t in rep["tasks"]:
        cpu = "-" if t["cpu_percent"] is None else f"{t['cpu_percent']:.2f}"
        out.append(f"{t['name']:<16} {t['priority']:>4} {t['state']:<5} {cpu:>7} {t['stack_hwm_words']:>11}")
    lp = rep["loop"]
    out.append(f"cpu window {rep['cpu_window_s']} s | heap free {rep['heap_free']} B, min ever {rep['heap_min_free']} B")
    out.append(f"loop us: last {lp['last_us']} min {lp['min_us']} max {lp['max_us']} "
               f"(nominal {lp['nominal_us']:.0f}) overruns {lp['overruns']}")
    out.append(f"reset {rep['reset_csr']}: {', '.join(rep['reset_causes']) or 'none'}")
    return "\n".join(out)


def cmd_rtos(args):
    from .cli import _live_reader
    with _live_reader(args) as lr:
        print(format_report(read_report(lr)))
    return 0
