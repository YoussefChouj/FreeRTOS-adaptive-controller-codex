import pytest
import time
from ground_station.service import rtos_api

def test_api_rtos_success(monkeypatch):
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
                        {"pcTaskName": b"Test\x00", "ulRunTimeCounter": 10}
                    ]
                }
            else:
                return {
                    "g_task_snapshot_total_time": 2000,
                    "g_task_snapshot_count": 1,
                    "g_task_snapshot": [
                        {"pcTaskName": b"Test\x00", "ulRunTimeCounter": 210, "uxCurrentPriority": 1, "eCurrentState": 0, "usStackHighWaterMark": 100}
                    ],
                    "g_heap_free": 1024,
                    "g_reset_csr": (1<<27)
                }

    monkeypatch.setattr("ground_station.livewatch.reader.LiveReader", MockLiveReader)
    monkeypatch.setattr("time.sleep", lambda x: None)
    
    # reset cache
    rtos_api._rtos_cache = None
    rtos_api._rtos_cache_time = 0
    
    res = rtos_api.get_rtos_state()
    assert res["available"] is True
    assert res["tasks"][0]["name"] == "Test"
    assert res["tasks"][0]["cpu_percent"] == 20.0
    assert res["heap_free"] == 1024
    
def test_api_rtos_failure(monkeypatch):
    class MockLiveReaderFail:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self): raise RuntimeError("Probe disconnected")
        def __exit__(self, *a): pass
        
    monkeypatch.setattr("ground_station.livewatch.reader.LiveReader", MockLiveReaderFail)
    rtos_api._rtos_cache = None
    rtos_api._rtos_cache_time = 0
    
    res = rtos_api.get_rtos_state()
    assert res["available"] is False
    assert "Probe disconnected" in res["reason"]
