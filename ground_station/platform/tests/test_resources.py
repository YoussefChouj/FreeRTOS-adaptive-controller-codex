from ground_station.platform.resources import build_resource_map, observe_metrics


def test_resource_map_is_registry_bound_and_contains_rtos_resources():
    resource_map = build_resource_map()
    assert resource_map.registry_crc32 == 0x9F32E2EA
    assert {entry.name for entry in resource_map.by_kind("task")} >= {
        "Send_Task", "Stabilizer_Task", "SystemMonitor_Task",
    }
    assert resource_map.by_kind("resource")


def test_runtime_metrics_expose_bounded_utilization():
    metrics = observe_metrics(
        {"queue_depth": 4, "heap_free": 12000},
        {"queue_depth": 16, "heap_free": 20480},
        {"queue_depth": "entries", "heap_free": "bytes"},
    )
    assert metrics[0].name == "heap_free"
    assert metrics[0].utilization == 12000 / 20480
    assert metrics[1].unit == "entries"

