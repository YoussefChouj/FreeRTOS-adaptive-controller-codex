import json
import struct
import time
import urllib.request

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.livewatch.transport import crc16_ccitt
from ground_station.service.api import ApiServer
from ground_station.service.core import GroundStationService
from ground_station.service.replay import SessionReplay
from ground_station.service.storage import SessionStore


def data_frame(slot: int, sequence: int, source_ms: int, value: float) -> bytes:
    payload = struct.pack("<If", source_ms, value)
    frame_type = 0x09 + slot
    header = bytes((0xAA, 0xBB, frame_type, 0, len(payload), sequence))
    return header + payload + crc16_ccitt(header[2:] + payload).to_bytes(2, "big")


def service_fixture():
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    service = GroundStationService(store=SessionStore(), schemas=[schema], source="sim")
    return service


def test_service_persists_decoded_telemetry_and_replays_deterministically():
    service = service_fixture()
    sid = service.start()
    assert service.ingest(data_frame(0, 7, 100, 1.25), time_ns=10) == 1
    assert service.ingest(data_frame(0, 8, 105, 1.5), time_ns=20) == 1
    snapshot = service.snapshot()
    assert snapshot.samples == 2
    assert snapshot.streams[0]["values"]["altitude"] == [1.5]
    replay = list(SessionReplay(service.store, sid).records())
    # Exactly one service_started event and two telemetry samples.
    event_rows = [r for r in replay if r.type == "event"]
    telemetry_rows = [r for r in replay if r.type == "telemetry"]
    assert len(event_rows) == 1
    assert event_rows[0].data.get("schema_id") is not None  # payload from service_started
    assert len(telemetry_rows) == 2
    assert [r.data["source_time_ms"] for r in telemetry_rows] == [100, 105]


def test_http_api_exposes_schema_aware_health_and_state():
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        health = json.loads(opener.open(base + "/health").read())
        state = json.loads(opener.open(base + "/state").read())
        assert health["ok"] is True
        assert health["schema_id"] == service.schema.schema_id
        assert state["session_id"] == service.session_id
    finally:
        api.stop()


def test_store_orders_same_timestamp_by_insert_id():
    store = SessionStore()
    sid = store.start_session("schema", source="test")
    store.append_event(sid, "first", {}, time_ns=1)
    store.append_event(sid, "second", {}, time_ns=1)
    names = [row["kind"] for row in store.iter_records(sid) if row["type"] == "event"]
    assert names == ["first", "second"]
