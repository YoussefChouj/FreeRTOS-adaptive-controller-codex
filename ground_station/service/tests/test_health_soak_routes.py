"""/health/ingest_lag and /health/store: what the soak reads per flight (the lag histogram, queue depth, store rows)."""
from __future__ import annotations

import time

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.service.api import ApiServer
from ground_station.service.tests.test_service import _get_json, service_fixture


def test_ingest_lag_route_serves_the_bridge_histogram():
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        assert _get_json(base + "/health/ingest_lag") == (200, {"bridge_available": False})
        service.bridge = WifiBridge(vofa_enabled=False)
        now = time.time_ns()
        service.bridge.ingest_lag_hist.add(3_000_000, now)
        service.bridge.ingest_lag_hist.add(250_000_000, now)
        code, body = _get_json(base + f"/health/ingest_lag?since_s={now // 10**9}")
        assert code == 200 and body["bridge_available"] is True
        assert body["total"] == 2 and body["over_limit"] == 1 and body["max_ms"] == 250.0
        assert body["seconds"] == [[now // 10**9, 250.0]]
        assert body["link"]["ingest_queue"] == 0 and body["link"]["ingest_dropped"] == 0
        assert _get_json(base + "/health/ingest_lag?since_s=x")[0] == 400
    finally:
        api.stop()


def test_store_route_counts_rows_against_the_cap():
    service = service_fixture()
    sid = service.start()
    for i in range(5):
        service.store.append_telemetry(sid, 0, i, {"altitude": [1.0]})
    api = ApiServer(service)
    api.start()
    try:
        code, body = _get_json("http://127.0.0.1:%d/health/store" % api.address[1])
        assert code == 200 and body["max_rows"] == 10_000 and body["path"] == ":memory:" and body["prune_every"] == 1000
        assert body["rows"]["telemetry"] == 5 and body["rows"]["raw_frames"] == 0
        assert body["rows"]["events"] >= 1
    finally:
        api.stop()
