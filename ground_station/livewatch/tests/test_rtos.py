import pytest
from ground_station.livewatch.rtos import cmd_rtos
import collections

class MockArgs:
    def __init__(self):
        self.transport = "swd"
        self.swd_no_limit_packets = True
        self.elf = "dummy.axf"

def test_csr_decode(capsys, monkeypatch):
    # We will just patch LiveReader
    class MockLiveReader:
        def __init__(self, *args, **kwargs):
            self.read_count = 0
            
        def __enter__(self): return self
        def __exit__(self, *a): pass
        
        def read_many(self, names):
            self.read_count += 1
            if self.read_count == 1:
                return {
                    "g_task_snapshot_total_time": 1000,
                    "g_task_snapshot_count": 1,
                    "g_task_snapshot": [
                        {"pcTaskName": b"IdleTask\x00", "ulRunTimeCounter": 50, "uxCurrentPriority": 0, "eCurrentState": 1, "usStackHighWaterMark": 100}
                    ]
                }
            else:
                return {
                    "g_task_snapshot_total_time": 2000,
                    "g_task_snapshot_count": 1,
                    "g_task_snapshot": [
                        {"pcTaskName": b"IdleTask\x00", "ulRunTimeCounter": 150, "uxCurrentPriority": 0, "eCurrentState": 0, "usStackHighWaterMark": 90}
                    ],
                    "g_heap_free": 1024,
                    "g_heap_min_free": 512,
                    "g_loop_period_cyc_last": 840000,
                    "g_loop_period_cyc_max": 850000,
                    "g_loop_period_cyc_min": 830000,
                    "g_loop_overrun_count": 0,
                    "g_reset_csr": (1 << 29) | (1 << 27)  # IWDG, POR/PDR
                }

    monkeypatch.setattr("ground_station.livewatch.reader.LiveReader", MockLiveReader)
    monkeypatch.setattr("time.sleep", lambda x: None)
    
    cmd_rtos(MockArgs())
    
    out, _ = capsys.readouterr()
    assert "IdleTask" in out
    assert "10.0%" in out
    assert "Heap Free: 1024" in out
    assert "Reset Cause (RCC_CSR=0x28000000): IWDG, POR/PDR" in out
