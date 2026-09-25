from types import SimpleNamespace

from ground_station.livewatch import rtos


class FakeReader:
    """Implements the LiveReader surface rtos.py uses: plan / sample / read_raw."""

    def __init__(self, frames, names):
        self.frames = list(frames)   # each: dict of scalar + g_task_snapshot[i].field values
        self.names = names           # ptr -> bytes
        self.i = 0
        self.raw_reads = 0

    def plan(self, names):
        return list(names)

    def sample(self, plan):
        f = self.frames[min(self.i, len(self.frames) - 1)]
        if "g_task_snapshot_count" in plan:
            pass
        elif plan and plan[-1].startswith("g_task_snapshot["):
            self.i += 1      # task block closes a snapshot
        return {n: f[n] for n in plan}

    def read_raw(self, addr, size):
        self.raw_reads += 1
        return self.names[addr][:size].ljust(size, b"\x00")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def frame(total, tasks, csr=1 << 27 | 1 << 26, loop=(837_000, 833_000, 841_000, 0), heap=(9000, 8500)):
    f = {"g_task_snapshot_count": len(tasks), "g_task_snapshot_total_time": total & 0xFFFFFFFF,
         "g_heap_free": heap[0], "g_heap_min_free": heap[1], "g_reset_csr": csr,
         "g_loop_period_cyc_last": loop[0], "g_loop_period_cyc_min": loop[1],
         "g_loop_period_cyc_max": loop[2], "g_loop_overrun_count": loop[3]}
    for i, (ptr, num, st, prio, rt, hwm) in enumerate(tasks):
        for k, v in zip(rtos.TASK_FIELDS, (ptr, num, st, prio, rt & 0xFFFFFFFF, hwm)):
            f[f"g_task_snapshot[{i}].{k}"] = v
    return f


NAMES = {0x20001000: b"IDLE\x00junk", 0x20002000: b"Stabilizer\x00"}


def test_decode_csr():
    assert rtos.decode_csr(0) == []
    assert rtos.decode_csr((1 << 27) | (1 << 26)) == ["POR", "PIN"]
    assert rtos.decode_csr(0xFE000000) == ["LPWR", "WWDG", "IWDG", "SFT", "POR", "PIN", "BOR"]


def test_report_cpu_and_names():
    a = frame(1_000, [(0x20001000, 1, 1, 0, 100, 50), (0x20002000, 2, 2, 5, 200, 120)])
    b = frame(1_000 + 168_000_000, [(0x20001000, 1, 1, 0, 100 + 126_000_000, 50),
                                    (0x20002000, 2, 0, 5, 200 + 42_000_000, 118)])
    rep = rtos.build_report(rtos.read_snapshot(FakeReader([b], NAMES)), rtos.read_snapshot(FakeReader([a], NAMES)))
    by = {t["name"]: t for t in rep["tasks"]}
    assert by["IDLE"]["cpu_percent"] == 75.0 and by["Stabilizer"]["cpu_percent"] == 25.0
    assert rep["tasks"][0]["name"] == "Stabilizer"          # sorted by priority, highest first
    assert by["Stabilizer"]["state"] == "Run" and by["Stabilizer"]["stack_hwm_words"] == 118
    assert rep["cpu_window_s"] == 1.0
    assert rep["reset_causes"] == ["POR", "PIN"] and rep["reset_csr"] == "0x0C000000"
    assert rep["loop"]["last_us"] == round(837_000 / 168, 1) and rep["loop"]["overruns"] == 0


def test_cpu_survives_cyccnt_wrap():
    t0 = 0xFFFFFFFF - 10_000_000          # both counters wrap inside the window
    a = frame(t0, [(0x20001000, 1, 1, 0, t0, 0)])
    b = frame(t0 + 168_000_000, [(0x20001000, 1, 1, 0, t0 + 84_000_000, 0)])
    rep = rtos.build_report(rtos.read_snapshot(FakeReader([b], NAMES)), rtos.read_snapshot(FakeReader([a], NAMES)))
    assert rep["tasks"][0]["cpu_percent"] == 50.0 and rep["cpu_window_s"] == 1.0


def test_no_window_and_unset_min():
    a = frame(5, [(0, 7, 9, 1, 0, 0)], loop=(0, 0xFFFFFFFF, 0, 0))
    rep = rtos.build_report(rtos.read_snapshot(FakeReader([a], NAMES)), None)
    t = rep["tasks"][0]
    assert t["cpu_percent"] is None and t["name"] == "task7" and t["state"] == "9"
    assert rep["loop"]["min_us"] is None and rep["cpu_window_s"] is None


def test_names_cached_by_pointer():
    a = frame(1, [(0x20001000, 1, 1, 0, 0, 0), (0x20002000, 2, 1, 1, 0, 0)])
    lr, cache = FakeReader([a, a], NAMES), {}
    rtos.read_snapshot(lr, name_cache=cache)
    rtos.read_snapshot(lr, name_cache=cache)
    assert lr.raw_reads == 2


def test_cmd_rtos_waits_for_refresh(monkeypatch, capsys):
    a = frame(1_000, [(0x20002000, 2, 0, 5, 0, 100)])
    b = frame(1_000 + 168_000_000, [(0x20002000, 2, 0, 5, 16_800_000, 99)])
    lr = FakeReader([a, a, b], NAMES)
    import ground_station.livewatch.cli as cli
    monkeypatch.setattr(cli, "_live_reader", lambda args: lr)
    monkeypatch.setattr(rtos.time, "sleep", lambda s: None)
    assert rtos.cmd_rtos(SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "Stabilizer" in out and "10.00" in out and "POR, PIN" in out


class FlakyReader(FakeReader):
    def __init__(self, frames, names, fail_at):
        super().__init__(frames, names)
        self.calls, self.fail_at = 0, set(fail_at)

    def sample(self, plan):
        self.calls += 1
        if self.calls in self.fail_at:
            raise RuntimeError("DAP_TRANSFER response error")
        return super().sample(plan)


def test_read_report_retries_transfer_error(monkeypatch):
    a = frame(1_000, [(0x20002000, 2, 0, 5, 0, 100)])
    b = frame(1_000 + 168_000_000, [(0x20002000, 2, 0, 5, 16_800_000, 99)])
    monkeypatch.setattr(rtos.time, "sleep", lambda s: None)
    rep = rtos.read_report(FlakyReader([a, b], NAMES, fail_at=[1]))
    assert rep["tasks"][0]["cpu_percent"] == 10.0
    try:
        rtos.read_report(FlakyReader([a, b], NAMES, fail_at=[1, 2, 3]))
    except RuntimeError:
        pass
    else:
        raise AssertionError("persistent failure must raise")
