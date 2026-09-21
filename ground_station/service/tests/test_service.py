import json
import socket
import struct
import time
import urllib.request
from unittest.mock import MagicMock

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.livewatch.transport import crc16_ccitt
from ground_station.platform.transactions import Outcome, RejectReason, Result
from ground_station.service.api import ApiServer
from ground_station.service.core import GroundStationService
from ground_station.service.replay import SessionReplay
from ground_station.service.storage import SessionStore

# Bypass Windows proxy for localhost
_orig_gai = socket.getaddrinfo


def _bypass_gai(host, port, *args, **kwargs):
    if host in ("127.0.0.1", "localhost"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]
    return _orig_gai(host, port, *args, **kwargs)


socket.getaddrinfo = _bypass_gai


def _get_json(url: str) -> tuple[int, dict]:
    import http.client
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    conn = http.client.HTTPConnection(host, port)
    conn.connect()
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    try:
        body_json = json.loads(body)
    except Exception:
        body_json = {"raw": body.decode(errors="replace")}
    return resp.status, body_json


def _post_json(url: str, body: dict) -> tuple[int, dict]:
    import http.client
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port or 80
    path = parsed.path or "/"
    data = json.dumps(body).encode()
    conn = http.client.HTTPConnection(host, port)
    conn.connect()
    conn.request("POST", path, data, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    try:
        body_json = json.loads(body)
    except Exception:
        body_json = {"raw": body.decode(errors="replace")}
    return resp.status, body_json


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
    # snapshot() converts integer slot keys to strings for JSON compatibility
    assert snapshot.streams["0"]["values"]["altitude"] == [1.5]
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
        # Startup identity fields are always published; commit may be
        # honestly null, started_at must be a real epoch timestamp.
        assert health["started_at"] == service.started_at
        assert isinstance(health["started_at"], float)
        assert health["started_commit"] == service.started_commit
        assert health["started_commit"] is None or isinstance(
            health["started_commit"], str)
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


# ── New S14 tests ──────────────────────────────────────────────────────────────


def test_service_state_includes_command_result_fields():
    """ServiceState dataclass carries last_transaction_result and command_results."""
    service = service_fixture()
    service.start()
    state = service.snapshot()
    # Default state has no results yet
    assert state.last_transaction_result is None
    assert state.command_results == []


def test_record_command_result_populates_service_state():
    """record_command_result() populates snapshot fields without a real bridge."""
    service = service_fixture()
    service.start()

    # Inject two synthetic results via the test helper
    r1 = Result(1, Outcome.APPLIED, 0x01, 0, detail="applied")
    r2 = Result(2, Outcome.REJECTED, 0x02, 1,
                RejectReason.SAFETY_INTERLOCK, "safety interlock")
    service.record_command_result(r1)
    service.record_command_result(r2)

    state = service.snapshot()
    assert state.last_transaction_result is not None
    assert state.last_transaction_result["transaction_id"] == 2
    assert state.last_transaction_result["status"] == "rejected"
    assert state.last_transaction_result["reason"] == "SAFETY_INTERLOCK"
    assert len(state.command_results) == 2
    assert state.command_results[0]["transaction_id"] == 1
    assert state.command_results[0]["status"] == "applied"


def test_command_results_respects_maxlen():
    """_command_results deque drops oldest when maxlen is exceeded."""
    service = service_fixture()
    service.start()

    for i in range(15):
        r = Result(i, Outcome.ACK, 0, 0)
        service.record_command_result(r)

    state = service.snapshot()
    # Only the last 10 are kept
    assert len(state.command_results) == 10
    # Oldest entries were dropped
    assert state.command_results[0]["transaction_id"] == 5
    assert state.command_results[-1]["transaction_id"] == 14


def test_http_state_includes_command_result_fields():
    """GET /state JSON response includes last_transaction_result and command_results."""
    service = service_fixture()
    service.start()

    # Seed a synthetic result
    r = Result(99, Outcome.APPLIED, 0x0E, 0, detail="authority granted")
    service.record_command_result(r)

    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        state = json.loads(opener.open(base + "/state").read())
        assert "last_transaction_result" in state
        assert state["last_transaction_result"]["transaction_id"] == 99
        assert state["last_transaction_result"]["status"] == "applied"
        assert "command_results" in state
        assert isinstance(state["command_results"], list)
        assert len(state["command_results"]) == 1
    finally:
        api.stop()


def test_http_artifacts_endpoint_returns_list():
    """GET /artifacts returns 200 with an 'artifacts' key (list, may be empty)."""
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(base + "/artifacts")
        assert status == 200
        assert "artifacts" in body
        assert isinstance(body["artifacts"], list)
    finally:
        api.stop()


def test_http_analysis_compare_returns_400_on_missing_params():
    """GET /analysis/compare returns 400 when required query params are absent."""
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        # No params at all
        status, _ = _get_json(base + "/analysis/compare")
        assert status == 400
        # Missing 'stream'
        sid_a = service.session_id
        status, _ = _get_json(base + "/analysis/compare?a=" + sid_a + "&b=" + sid_a + "&key=altitude")
        assert status == 400
    finally:
        api.stop()


def test_http_analysis_compare_returns_valid_diff_structure():
    """GET /analysis/compare returns 200 with session stats and diff for valid params."""
    service = service_fixture()
    service.start()

    # Seed two sessions with telemetry so compare_sessions can compute stats
    sid_a = service.session_id
    service.store.append_telemetry(sid_a, 0, 0, {"altitude": 10.0},
                                   source_time_ms=100, time_ns=1_000_000_000)
    service.store.append_telemetry(sid_a, 0, 1, {"altitude": 20.0},
                                   source_time_ms=105, time_ns=1_010_000_000)

    sid_b = service.store.start_session(service.schema.schema_id, "test")
    service.store.append_telemetry(sid_b, 0, 0, {"altitude": 15.0},
                                   source_time_ms=100, time_ns=1_000_000_000)
    service.store.append_telemetry(sid_b, 0, 1, {"altitude": 25.0},
                                   source_time_ms=105, time_ns=1_010_000_000)

    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(
            base + "/analysis/compare?a=" + sid_a + "&b=" + sid_b
            + "&stream=0&key=altitude"
        )
        assert status == 200
        # Response must contain both session stats
        assert sid_a in body
        assert sid_b in body
        assert "diff_mean" in body
        assert "diff_max" in body
        assert "better_session" in body
        # diff_mean: a has mean 15, b has mean 20, diff = -5
        assert body["diff_mean"] == -5.0
        assert body["better_session"] == "a"
    finally:
        api.stop()


# ── S15 tests ──────────────────────────────────────────────────────────────


def test_ingest_decoded_routes_a_to_slot_0():
    """tag 'a' (sidebar) routes to slot 0, not slot 2 (S15 Fix 1)."""
    service = service_fixture()
    service.start()

    # Sidebar payload with all the dashboard named keys.
    sidebar = {
        "status.pitch_deg": -1.0,
        "status.roll_deg":  -0.5,
        "status.yaw_deg":   16.6,
        "status.arm":        1.0,
        "status.vbat":       12.4,
        "mrac.pitch.e":      0.05,
    }
    service.ingest_decoded("a", sidebar, time_ns=1)

    state = service.snapshot()
    assert "0" in state.streams, "sidebar 'a' must land in slot 0"
    assert "2" not in state.streams, "sidebar 'a' must NOT land in slot 2 anymore"
    # Named keys are preserved verbatim.
    assert state.streams["0"]["values"]["status.arm"] == 1.0
    assert state.streams["0"]["values"]["mrac.pitch.e"] == 0.05


def test_ingest_decoded_routes_b_c_id_to_expected_slots():
    """tag 'b'->1, 'c'->3, 'id'->1; typed s0..s3 map via int(tag[1:])."""
    service = service_fixture()
    service.start()

    service.ingest_decoded("b", {"pid.pitch.FB": 0.1}, time_ns=1)
    service.ingest_decoded("c", {"c.altitude": 1.2},   time_ns=2)
    service.ingest_decoded("id", {"id.counter": 7.0},   time_ns=3)
    service.ingest_decoded("s0", {"slot0.x": 1.0},      time_ns=4)
    service.ingest_decoded("s2", {"slot2.y": 2.0},      time_ns=5)

    state = service.snapshot()
    assert "1" in state.streams and "pid.pitch.FB" in state.streams["1"]["values"]
    assert "3" in state.streams and "c.altitude" in state.streams["3"]["values"]
    assert state.streams["1"]["values"]["id.counter"] == 7.0  # merged with b
    assert "0" in state.streams and "slot0.x" in state.streams["0"]["values"]
    assert "2" in state.streams and "slot2.y" in state.streams["2"]["values"]


def test_ingest_decoded_merges_sidebar_into_existing_slot():
    """Sidebar (tag 'a') merges into the slot 0 values dict alongside raw subscribe keys (S15 Fix 3)."""
    service = service_fixture()
    service.start()

    # First, simulate the raw subscribe path on slot 0 (tag 's0').
    raw_subscribe = {
        "slot0.imu_data.rol":        -0.5,
        "slot0.imu_data.pit":        -1.0,
        "slot0.DroneStatus.ARM_Status": 0.0,
        "slot0.t_ms": 1234,
        "slot0.seq":  17,
        "slot0.received": 17,
        "slot0.dropped":  0,
        "slot0.loss_pct": 0.0,
    }
    service.ingest_decoded("s0", raw_subscribe, time_ns=10)

    # Then the sidebar payload arrives on tag 'a'. Should merge, not overwrite.
    sidebar = {
        "status.pitch_deg":  -1.05,
        "status.roll_deg":   -0.79,
        "status.arm":        1.0,
        "status.vbat":       12.34,
    }
    service.ingest_decoded("a", sidebar, time_ns=20)

    state = service.snapshot()
    assert "0" in state.streams
    values = state.streams["0"]["values"]

    # Raw subscribe keys survive.
    assert values["slot0.imu_data.rol"] == -0.5
    assert values["slot0.t_ms"] == 1234
    # Sidebar keys are merged in alongside them.
    assert values["status.arm"] == 1.0
    assert values["status.vbat"] == 12.34
    # Stream metadata at top level is populated from the embedded slot0.* keys.
    assert state.streams["0"]["sequence"] == 17
    assert state.streams["0"]["received"] == 17
    assert state.streams["0"]["source_time_ms"] == 1234
    # last_update_ns reflects the latest ingest (sidebar arrival).
    assert state.streams["0"]["last_update_ns"] == 20


def test_stream_metadata_present_on_every_path():
    """All top-level stream metadata fields are present, even when the
    incoming payload has no embedded slot0.* keys (S15 Fix 2)."""
    service = service_fixture()
    service.start()

    # Sidebar with NO embedded slotN.* metadata.
    service.ingest_decoded("a", {"status.arm": 1.0}, time_ns=42)

    state = service.snapshot()
    entry = state.streams["0"]
    # All metadata fields are present even though the payload didn't carry them.
    for field in ("sequence", "source_time_ms", "received",
                  "dropped", "loss_pct", "last_update_ns"):
        assert field in entry, f"missing top-level stream metadata field: {field}"
    # Defaults are zero/None for fields the payload didn't carry.
    assert entry["sequence"] == 0
    assert entry["received"] == 0
    assert entry["last_update_ns"] == 42  # always set on ingest


def test_inject_external_stream_publishes_to_snapshot():
    """inject_external_stream publishes a synthetic slot into the snapshot (S15 Fix 5)."""
    service = service_fixture()
    service.start()

    service.inject_external_stream(
        "rtos",
        {"rtos.send_ticks": 123.0, "rtos.queue_depth": 4.0,
         "rtos.dma_busy": 0.0, "rtos.scheduler_tick_count": 9000.0},
        sequence=7,
    )

    state = service.snapshot()
    assert "rtos" in state.streams
    entry = state.streams["rtos"]
    assert entry["tag"] == "external"
    assert entry["sequence"] == 7
    assert entry["received"] == 1
    assert entry["values"]["rtos.send_ticks"] == 123.0
    assert entry["values"]["rtos.queue_depth"] == 4.0

    # Second call should merge, not overwrite, and increment received counter.
    service.inject_external_stream(
        "rtos",
        {"rtos.queue_depth": 6.0},
        sequence=8,
    )
    state = service.snapshot()
    assert state.streams["rtos"]["received"] == 2
    assert state.streams["rtos"]["values"]["rtos.send_ticks"] == 123.0  # preserved
    assert state.streams["rtos"]["values"]["rtos.queue_depth"] == 6.0   # updated


def test_inject_external_stream_no_op_when_service_not_started():
    """inject_external_stream silently ignores calls before start()."""
    service = service_fixture()  # NOT started
    service.inject_external_stream("rtos", {"rtos.x": 1.0})
    # snapshot() does NOT require a started service (it just returns
    # an empty state) — so the right invariant to check is that no
    # stream was injected into self._streams.
    assert "rtos" not in service._streams, (
        "inject_external_stream must be a no-op when service.session_id is None"
    )
    # And snapshot() reflects that — no stream was added.
    state = service.snapshot()
    assert "rtos" not in state.streams


def test_state_stream_metadata_visible_over_http():
    """GET /state surfaces top-level stream metadata after ingest_decoded."""
    service = service_fixture()
    service.start()

    # Feed both paths and verify both surface through /state.
    service.ingest_decoded("s0", {
        "slot0.imu_data.rol":  0.1,
        "slot0.t_ms": 5000,
        "slot0.seq":  42,
        "slot0.received": 42,
        "slot0.dropped":  1,
        "slot0.loss_pct": 2.3,
    }, time_ns=1000)
    service.ingest_decoded("a", {
        "status.arm":   1.0,
        "status.vbat": 12.0,
    }, time_ns=2000)

    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(base + "/state")
        assert status == 200
        s0 = body["streams"]["0"]
        assert s0["sequence"] == 42
        assert s0["received"] == 42
        assert s0["dropped"] == 1
        assert abs(s0["loss_pct"] - 2.3) < 1e-6
        assert s0["last_update_ns"] == 2000
        # Both raw subscribe keys and sidebar keys coexist in values.
        assert s0["values"]["slot0.imu_data.rol"] == 0.1
        assert s0["values"]["status.arm"] == 1.0
        assert s0["values"]["status.vbat"] == 12.0
    finally:
        api.stop()


# ── S16 tests ──────────────────────────────────────────────────────────────


def test_http_health_slots_exposes_status_field():
    """GET /health/slots returns a 'status' field per slot: live/mixed/stale/dead.

    S16 adds the derived status classification (Pattern 2 from PLANNING_PROMPT
    §8) so the shell can color-code rows without re-computing the rule.
    """
    service = service_fixture()
    service.start()

    # Slot 0: fresh data (all keys recent).
    service.ingest_decoded("a", {"status.arm": 1.0}, time_ns=1_000_000_000_000)

    # Slot 'rtos': external stream with no freshness data (treated as dead-ish).
    service.inject_external_stream("rtos", {"rtos.x": 1.0}, sequence=1)

    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        report = json.loads(opener.open(base + "/health/slots").read())
        assert "slots" in report
        slots = report["slots"]
        assert "0" in slots
        # status field must be present on every slot.
        for slot_key, data in slots.items():
            assert "status" in data, f"slot {slot_key} missing 'status' field"
            assert data["status"] in ("live", "mixed", "stale", "dead"), \
                f"slot {slot_key} has invalid status: {data['status']}"
    finally:
        api.stop()


def test_http_health_slots_has_ttl_ns():
    """GET /health/slots response includes ttl_ns so the JS knows the
    freshness threshold without hardcoding it."""
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        report = json.loads(opener.open(base + "/health/slots").read())
        assert "ttl_ns" in report
        assert isinstance(report["ttl_ns"], int)
        assert report["ttl_ns"] == 30 * 10**9  # default TTL
    finally:
        api.stop()


def test_http_health_slots_stream_health_signal():
    """/health/slots exposes a session-lifetime stall signal even after slots
    are TTL-evicted, so the Diagnostics tab can distinguish "stalled link"
    from "nothing ever subscribed" (both show as slots=={})."""
    service = service_fixture()
    service.start()
    # No frames yet: must be truthfully "not published", not 0.
    assert service._last_update_ns is None
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        report = json.loads(opener.open(base + "/health/slots").read())
        sh = report["stream_health"]
        assert sh["telemetry_seen"] is False
        assert sh["last_frame_age_ns"] is None
        assert sh["stalled"] is False
        # A frame far older than the TTL => stalled, but still reported.
        past = time.time_ns() - 100 * 10**9
        service.ingest_decoded("a", {"status.arm": 1.0}, time_ns=past)
        report = json.loads(opener.open(base + "/health/slots").read())
        sh = report["stream_health"]
        assert sh["telemetry_seen"] is True
        assert sh["stalled"] is True
        assert sh["last_frame_age_ns"] is not None
        assert sh["last_frame_age_ns"] > report["ttl_ns"]
    finally:
        api.stop()


def test_http_subscribe_preview_endpoint():
    """POST /subscribe/preview returns DWARF resolution preview without
    touching the WiFi socket. Regression test for S16 subscribe_preview()
    integration into api.py."""
    service = service_fixture()
    service.start()
    # Give it a mock bridge that has subscribe_preview so the endpoint can call it.
    service.bridge = MagicMock()
    service.bridge.subscribe_preview = lambda **kw: service.bridge._impl.subscribe_preview(**kw)
    service.bridge._impl = MagicMock()
    service.bridge._impl.subscribe_preview = lambda **kw: service.bridge._preview_impl(**kw)
    # Wire the real bridge impl so we test real resolver logic.
    from ground_station.comm.wifi_bridge import WifiBridge
    _real_bridge = WifiBridge(vofa_enabled=False)
    service.bridge = _real_bridge  # replace mock with real bridge

    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _post_json(base + "/subscribe/preview", {
            "slot": 0, "divider": 4, "ranges": [],
        })
        assert status == 200, f"expected 200, got {status}: {body}"
        assert "ranges" in body
        assert "unresolved" in body
        assert "expected_rate_hz" in body
        assert "var_count" in body
        # Slot 0 divider=4 → 100/4 = 25 Hz expected.
        assert body["expected_rate_hz"] == 25.0
    finally:
        api.stop()


def test_http_subscribe_preview_unknown_slot_rejected():
    """POST /subscribe/preview with an invalid slot returns 400."""
    service = service_fixture()
    service.start()
    # Use a real bridge for the preview call.
    from ground_station.comm.wifi_bridge import WifiBridge
    service.bridge = WifiBridge(vofa_enabled=False)
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _post_json(base + "/subscribe/preview", {
            "slot": 99, "divider": 1, "ranges": [],
        })
        assert status == 400
        assert "error" in body
    finally:
        api.stop()


# ── S17 tests — WP6 / WP3 API endpoints ────────────────────────────────────


def test_http_api_routes_endpoint():
    """GET /api/routes lists routes; every GET route without params answers."""
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(base + "/api/routes")
        assert status == 200
        assert "/replay/<id>/play" in body["POST"]
        assert "tab-<workspace>" in body["ui_testids"]
        for route in body["GET"]:
            if "<" in route or route.startswith("/analysis/"):
                continue
            code, _ = _get_json(base + route)
            # The fixture has no experiment runtime, so /experiments is 503.
            assert code == (503 if route == "/experiments" else 200), route
    finally:
        api.stop()


def test_http_api_contract_endpoint():
    """GET /api/contract returns the firmware contract manifest."""
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(base + "/api/contract")
        assert status == 200
        assert body["contract_version"] == "v1"
        assert "commands" in body
        assert "0x04" in body["commands"]
        assert body["commands"]["0x04"]["safety"]["danger_level"] == "dangerous"
        assert "subscribe" in body
        assert body["subscribe"]["max_slots"] == 4
    finally:
        api.stop()


def test_http_api_manifest_endpoint():
    """GET /api/manifest returns the full system capability manifest."""
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(base + "/api/manifest")
        assert status == 200
        assert body["manifest_version"] == "v1"
        assert "elf_identity" in body
        assert body["elf_identity"]["exists"] is True
        assert "firmware_symbols" in body
        assert body["firmware_symbols"]["count"] > 300
        assert "commands" in body
        assert "0x01" in body["commands"]["commands"]
        assert "telemetry" in body
        assert body["telemetry"]["verified_published_keys_total"] > 100
        assert "panels" in body
        assert len(body["panels"]) == 17
        assert "routes" in body
        assert "/api/manifest" in body["routes"]["GET"]
    finally:
        api.stop()


def test_http_api_view_model_endpoint():
    """GET /api/view-model returns a rich state snapshot with slot freshness."""
    service = service_fixture()
    service.start()

    # Use a wall-clock timestamp (near current time) so keys fall within
    # the 30-second TTL window. A fixed 1970 timestamp (1_000_000_000_000 ns)
    # makes every key permanently stale since age_ns >> TTL.
    now_ns = time.time_ns()
    service.ingest_decoded("a", {"status.arm": 1.0, "status.vbat": 12.4},
                            time_ns=now_ns)

    # 15 journal entries, oldest first: view-model must return the newest 10.
    service.action_journal = lambda: [{"i": i} for i in range(15)]
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(base + "/api/view-model")
        assert status == 200
        assert [a["i"] for a in body["actions"]] == list(range(14, 4, -1))
        # session_stats scans the whole session, so it is opt-in via ?stats=1.
        assert body["session_stats"] == {}
        status, _ = _get_json(base + "/api/view-model?stats=1")
        assert status == 200
        assert "slots" in body
        assert "adapter_version" in body
        assert "fault_count" in body
        assert "actions" in body
        assert "schema_id" in body
        # Slot 0 should be present with freshness info.
        assert "0" in body["slots"]
        assert body["slots"]["0"]["status"] in ("live", "mixed", "stale", "dead")
        # Fresh keys should be non-zero for the ingested values.
        assert body["slots"]["0"]["fresh_keys"] > 0
    finally:
        api.stop()


def test_http_api_events_endpoint():
    """GET /api/events returns the session event journal."""
    service = service_fixture()
    service.start()

    # Feed something that generates an event. Use a wall-clock timestamp
    # so the ingest is not treated as permanently stale.
    service.ingest_decoded("a", {"status.arm": 1.0}, time_ns=time.time_ns())

    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(base + "/api/events")
        assert status == 200
        assert "events" in body
        assert "count" in body
        assert isinstance(body["events"], list)
    finally:
        api.stop()


def test_http_api_faults_endpoint():
    """GET /api/faults returns the fault log."""
    service = service_fixture()
    service.start()

    # Simulate a rejection.
    from ground_station.platform.transactions import Outcome, RejectReason, Result
    r = Result(7, Outcome.REJECTED, 0x06, 0,
               RejectReason.SAFETY_INTERLOCK, "command rejected")
    service.record_command_result(r)

    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(base + "/api/faults")
        assert status == 200
        assert "faults" in body
        assert body["count"] == 1
        assert body["faults"][0]["outcome"] == "rejected"
        assert body["faults"][0]["reason"] == "SAFETY_INTERLOCK"
    finally:
        api.stop()


def test_http_api_actions_endpoint():
    """GET /api/actions returns the command action journal."""
    service = service_fixture()
    service.start()

    # Submit a command. set service.gateway (not just bridge) so submit_command
    # finds a usable gateway. The gateway holds a reference to bridge at init
    # time; reassigning bridge afterwards does NOT update gateway.
    mock_gateway = MagicMock()
    mock_gateway.submit.return_value = 42
    service.gateway = mock_gateway
    service.submit_command(0x04, index=0, value=0.0)

    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(base + "/api/actions")
        assert status == 200
        assert "actions" in body
        assert body["count"] == 1
        assert body["actions"][0]["transaction_id"] == 42
        assert body["actions"][0]["command_id"] == 0x04
        assert body["actions"][0]["lifecycle"] == "submitted"
    finally:
        api.stop()


def test_http_replay_endpoint():
    """GET /replay/<session_id> returns session records."""
    service = service_fixture()
    service.start()
    sid = service.session_id

    # Add some telemetry.
    service.ingest_decoded("a", {"status.arm": 1.0})

    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, body = _get_json(base + "/replay/" + sid)
        assert status == 200
        assert body["session_id"] == sid
        assert "records" in body
        assert body["count"] > 0
    finally:
        api.stop()


def test_http_replay_unknown_session_returns_404():
    """GET /replay/<unknown> returns 404."""
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, _ = _get_json(base + "/replay/nonexistent")
        assert status == 404
    finally:
        api.stop()


def test_submit_command_motor_bench_rejected_when_not_disarmed():
    """Motor bench command (0x16) is rejected when drone is not disarmed."""
    import pytest
    service = service_fixture()
    service.start()
    service.gateway = MagicMock()
    service.gateway.submit.return_value = 42

    # Ingest telemetry indicating armed
    service.ingest_decoded("a", {"status.arm": 1.0})
    assert not service.is_disarmed()

    with pytest.raises(ValueError, match="rejected: drone is not disarmed"):
        service.submit_command(0x16, index=0, value=100.0)

    # Fault log must contain the rejection
    faults = service.fault_log()
    assert any(f.get("reason") == "SAFETY_INTERLOCK" and f.get("command_id") == 0x16 for f in faults)

    # Now ingest telemetry indicating disarmed
    service.ingest_decoded("a", {"status.arm": 0.0})
    assert service.is_disarmed()

    txid = service.submit_command(0x16, index=0, value=100.0)
    assert txid == 42



def test_submit_command_motor_bench_rejected_when_arm_unknown_or_stale():
    """No or stale arm telemetry fails closed for motor bench (0x16)."""
    import pytest
    service = service_fixture()
    service.start()
    service.gateway = MagicMock()
    service.gateway.submit.return_value = 7
    assert service.arm_state() == "unknown"
    with pytest.raises(ValueError, match="arm state unknown"):
        service.submit_command(0x16, index=0, value=100.0)
    stale = time.time_ns() - 3 * service.ARM_STALE_NS
    service.ingest_decoded("a", {"status.arm": 0.0}, time_ns=stale)
    assert not service.is_disarmed()
    service.ingest_decoded("a", {"status.arm": 0.0})
    assert service.submit_command(0x16, index=0, value=100.0) == 7


def test_motor_bench_click_acknowledged_and_applied_via_fake_bridge():
    """Bug 6: a disarmed bench click transmits cmd 0x16 over the (fake) bridge
    and the firmware ACK then APPLIED result advances the command lifecycle
    that the dashboard surface, without any network."""
    import struct as _struct
    from ground_station.platform.transactions import Command, build_command

    service, bridge = _bridge_service()
    service.start()
    service.ingest_decoded("a", {"status.arm": 0.0})
    assert service.is_disarmed()

    # The exact click sequence the panel issues: select M1, CCR=2000, enable=1.
    for idx, val in ((1, 1.0), (2, 2000.0), (0, 1.0)):
        txid = service.submit_command(0x16, index=idx, value=val)
        # The tx propagated to the bridge (the wire frame the panel produced).
        assert txid == bridge.next_txid
        # The action journal logged it as SUBMITTED with a 15-byte wire frame.
        action = next(a for a in service.action_journal()
                     if a["command_id"] == 0x16 and a["index"] == idx)
        assert action["lifecycle"] == "submitted"
        assert action["wire_bytes"] == 15
        # The exact 0xCC 0xDF frame the click produces parses as cmd 0x16.
        frame = build_command(Command(transaction_id=0, command_id=0x16,
                                      index=idx, value=val, flags=0))
        assert frame[:2] == b"\xCC\xDF"
        assert frame[6] == 0x16 and frame[7] == idx
        assert _struct.unpack("<f", frame[10:14])[0] == val

    # Firmware ACKs the first select, then APPLIES it.
    acks = [(t, Result(t, Outcome.ACK, 0x16, 1, RejectReason.NONE, "queued"))
            for t in range(1, 4)]
    applied = Result(1, Outcome.APPLIED, 0x16, 1, RejectReason.NONE, "applied")
    for _, r in acks:
        bridge.results.append(r)
    bridge.results.append(applied)
    for _ in range(4):
        service.poll_command()

    state = service.snapshot()
    # ACK + APPLIED both surfaced so the operator sees the firmware heard it.
    statuses = [c["status"] for c in state.command_results]
    assert "ack" in statuses and "applied" in statuses
    # The panel correlates via /state command_results, which keeps every result:
    assert any(c["transaction_id"] == 1 and c["status"] == "applied"
               for c in service.snapshot().command_results)

    # A rejected bench command surfaces its safety reason.
    rej_tx = service.submit_command(0x16, index=0, value=1.0)
    bridge.results.append(Result(rej_tx, Outcome.REJECTED, 0x16, 0,
                                 RejectReason.SAFETY_INTERLOCK,
                                 "arm state unknown"))
    service.poll_command()
    rej_state = service.snapshot()
    assert rej_state.last_transaction_result["status"] == "rejected"
    assert rej_state.last_transaction_result["reason"] == "SAFETY_INTERLOCK"


def test_expire_commands_marks_timed_out_once():
    """Commands with no firmware result after 1.0 s become timed_out, one fault each."""
    from ground_station.service.core import COMMAND_TIMEOUT_NS
    service = service_fixture()
    service.start()
    service.gateway = MagicMock()
    service.gateway.submit.return_value = 9
    service.submit_command(0x04, index=0, value=1.0)
    submitted = service.action_journal()[-1]["submitted_ns"]
    assert service.expire_commands(submitted + COMMAND_TIMEOUT_NS // 2) == 0
    assert service.expire_commands(submitted + COMMAND_TIMEOUT_NS) == 1
    assert service.action_journal()[-1]["lifecycle"] == "timed_out"
    assert service.expire_commands(submitted + 5 * COMMAND_TIMEOUT_NS) == 0
    faults = [f for f in service._fault_log if f["transaction_id"] == 9]
    assert len(faults) == 1 and faults[0]["outcome"] == "timed_out"


def test_replay_to_bus_updates_streams_without_persisting():
    """Replayed telemetry reaches the live streams but is not stored again."""
    service = service_fixture()
    service.start()
    service.gateway = MagicMock()
    sid = service.session_id
    service.ingest_decoded("s1", {"slot1.x": 3.5}, time_ns=10)
    service.ingest_decoded("s1", {"slot1.x": -1.0}, time_ns=20)

    def rows():
        return [r for r in service.store.iter_records(sid) if r["type"] == "telemetry"]

    assert len(rows()) == 2
    assert service.replay_to_bus(sid) == 2
    assert service.snapshot().streams["1"]["values"]["slot1.x"] == -1.0
    assert len(rows()) == 2
    service.gateway.submit.assert_not_called()


def test_http_routes_ignore_query_string():
    """Exact-match GET routes still resolve when a query string is attached."""
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, _ = _get_json(base + "/state?x=1")
        assert status == 200
        status, body = _get_json(base + "/api/events?limit=1")
        assert status == 200
        assert body["count"] <= 1
    finally:
        api.stop()


def test_http_compare_non_integer_stream_returns_400():
    """GET /analysis/compare with a non-integer stream is a client error, not 500."""
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, _ = _get_json(base + "/analysis/compare?a=x&b=y&stream=telemetry&key=k")
        assert status == 400
    finally:
        api.stop()


def test_http_static_rejects_path_traversal(tmp_path):
    """Encoded ../ segments cannot escape the static root."""
    (tmp_path / "shell").mkdir()
    (tmp_path / "shell" / "index.html").write_text("ok")
    (tmp_path / "secret.txt").write_text("secret")
    service = service_fixture()
    service.start()
    api = ApiServer(service, static_root=tmp_path / "shell")
    api.start()
    try:
        base = "http://127.0.0.1:%d" % api.address[1]
        status, _ = _get_json(base + "/%2e%2e/secret.txt")
        assert status in (403, 404)
    finally:
        api.stop()


def _api_for(service):
    api = ApiServer(service)
    api.start()
    return api, "http://127.0.0.1:%d" % api.address[1]


def test_http_post_commands_accepts_and_rejects():
    """POST /commands -> 202 with txid; malformed body -> 400. Mocked gateway."""
    service = service_fixture()
    service.start()
    service.gateway = MagicMock()
    service.gateway.submit.return_value = 42
    api, base = _api_for(service)
    try:
        status, body = _post_json(base + "/commands", {"command_id": 1, "value": 0.5})
        assert status == 202
        assert body["transaction_id"] == 42
        service.gateway.submit.assert_called_once_with(1, 0, 0.5, 0)
        status, body = _post_json(base + "/commands", {"index": 0})
        assert status == 400
        # Motor bench without fresh arm telemetry fails closed.
        status, body = _post_json(base + "/commands", {"command_id": 0x16})
        assert status == 400
        assert "arm state unknown" in body["error"]
        assert service.gateway.submit.call_count == 1
    finally:
        api.stop()


def test_http_post_subscribe_without_bridge_returns_503():
    service = service_fixture()
    service.start()
    api, base = _api_for(service)
    try:
        status, _ = _post_json(base + "/subscribe", {"slot": 1, "divider": 2})
        assert status == 503
    finally:
        api.stop()


def test_http_post_subscribe_calls_bridge_subscribe_slot():
    service = service_fixture()
    service.start()
    service.bridge = MagicMock()
    api, base = _api_for(service)
    try:
        status, body = _post_json(base + "/subscribe",
                                  {"slot": 2, "divider": 5, "ranges": ["mrac_state"]})
        assert status == 202
        assert body == {"slot": 2, "divider": 5, "ranges": ["mrac_state"]}
        service.bridge.subscribe_slot.assert_called_once_with(
            slot=2, divider=5, ranges=["mrac_state"])
        service.bridge.subscribe_slot.side_effect = KeyError("no such symbol")
        status, body = _post_json(base + "/subscribe", {"slot": 1, "ranges": ["bogus"]})
        assert status == 400
    finally:
        api.stop()


def test_http_replay_play_unknown_and_known_session():
    service = service_fixture()
    sid = service.start()
    service.ingest(data_frame(0, 1, 100, 2.0), time_ns=10)
    api, base = _api_for(service)
    try:
        status, _ = _post_json(base + "/replay/nope/play", {})
        assert status == 404
        status, body = _post_json(base + "/replay/%s/play" % sid, {})
        assert status == 200
        assert body["session_id"] == sid
        assert body["replayed"] >= 1
    finally:
        api.stop()


def test_http_analysis_and_diagnostics_routes():
    service = service_fixture()
    sid = service.start()
    service.ingest(data_frame(0, 1, 100, 1.0), time_ns=10)
    service.ingest(data_frame(0, 2, 105, 1.1), time_ns=20)
    api, base = _api_for(service)
    try:
        for name in ("jitter", "gaps", "effective-rate"):
            status, _ = _get_json(base + "/analysis/%s?session_id=%s&stream=0" % (name, sid))
            assert status == 200, name
            status, _ = _get_json(base + "/analysis/%s" % name)
            assert status == 400, name
        status, body = _get_json(base + "/api/diagnostics/bundle")
        assert status == 200
        assert isinstance(body, dict)
        status, _ = _get_json(base + "/api/contract")
        assert status == 200
    finally:
        api.stop()


def _fill_records(service, count):
    """Ingest ``count`` telemetry frames into a fresh session; return its id."""
    sid = service.start()
    for i in range(count):
        service.ingest(data_frame(0, i % 256, i, float(i)), time_ns=1000 + i)
    return sid


def test_records_route_pages_and_caps_by_default():
    """A long session must not be served in one unbounded body.

    Regression: /sessions/<id>/records used to stream every row, which on a
    real flight session produced a 180 MB response that stalled the shell.
    """
    from ground_station.service.api import RECORDS_DEFAULT_LIMIT

    service = service_fixture()
    total = RECORDS_DEFAULT_LIMIT + 50  # + 1 service_started event
    sid = _fill_records(service, total)
    api, base = _api_for(service)
    try:
        status, body = _get_json("%s/sessions/%s/records" % (base, sid))
        assert status == 200
        assert body["count"] == RECORDS_DEFAULT_LIMIT
        assert len(body["records"]) == RECORDS_DEFAULT_LIMIT
        assert body["limit"] == RECORDS_DEFAULT_LIMIT
        assert body["offset"] == 0
        assert body["truncated"] is True

        # Explicit window: 5 rows starting at 10, contiguous with the full list.
        status, page = _get_json("%s/sessions/%s/records?limit=5&offset=10" % (base, sid))
        assert status == 200
        assert page["count"] == 5 and page["offset"] == 10 and page["limit"] == 5
        assert page["records"] == body["records"][10:15]

        # limit=0 means unlimited and is therefore never marked truncated.
        status, allrecs = _get_json("%s/sessions/%s/records?limit=0" % (base, sid))
        assert status == 200
        assert allrecs["count"] == total + 1
        assert allrecs["limit"] is None
        assert allrecs["truncated"] is False

        # A garbage limit falls back to the default instead of erroring.
        status, junk = _get_json("%s/sessions/%s/records?limit=abc" % (base, sid))
        assert status == 200 and junk["limit"] == RECORDS_DEFAULT_LIMIT
    finally:
        api.stop()


def test_replay_route_pages_like_records_route():
    from ground_station.service.api import RECORDS_DEFAULT_LIMIT

    service = service_fixture()
    sid = _fill_records(service, RECORDS_DEFAULT_LIMIT + 10)
    api, base = _api_for(service)
    try:
        status, body = _get_json("%s/replay/%s" % (base, sid))
        assert status == 200
        assert body["count"] == RECORDS_DEFAULT_LIMIT
        assert body["truncated"] is True

        status, tail = _get_json("%s/replay/%s?limit=4&offset=2" % (base, sid))
        assert status == 200
        assert tail["count"] == 4 and tail["offset"] == 2
        assert tail["records"] == body["records"][2:6]

        status, allrecs = _get_json("%s/replay/%s?limit=0" % (base, sid))
        assert allrecs["count"] == RECORDS_DEFAULT_LIMIT + 11
        assert allrecs["truncated"] is False
    finally:
        api.stop()


def test_store_iter_records_offset_without_limit():
    """offset alone must skip rows and still return the rest (SQL LIMIT -1)."""
    service = service_fixture()
    sid = _fill_records(service, 6)
    everything = list(service.store.iter_records(sid))
    assert list(service.store.iter_records(sid, offset=3)) == everything[3:]
    assert list(service.store.iter_records(sid, limit=2, offset=1)) == everything[1:3]


class _FakeCommandBridge:
    """Minimal bridge for Bug 1: sends transactions, queues 0x30/0x32-style
    results for the gateway polling thread to drain (the production path
    that publishes snapshots into the hub)."""

    def __init__(self):
        self.next_txid = 0
        self.results = []

    def start(self, auto_subscribe_boot_default=True):
        pass

    def stop(self):
        pass

    def send_transaction(self, command_id, index, value, flags):
        self.next_txid += 1
        return self.next_txid

    def poll_transaction_result(self, timeout=0.0):
        return self.results.pop(0) if self.results else None


def _bridge_service():
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    bridge = _FakeCommandBridge()
    service = GroundStationService(bridge=bridge, store=SessionStore(),
                                   schemas=[schema], source="sim")
    return service, bridge


def test_state_stays_fresh_after_command_result_publish():
    """Bug 1 regression (AUDIT_2026-09-21 §Bug 1).

    /state used to prefer hub.latest_state(), a cache written only by the
    gateway result-polling thread. After the first command result was
    published, every /state poll returned that one frozen snapshot, so the
    dashboard stopped updating while /health (computed live) stayed green.
    /state must always reflect the live service state, and the exact
    /commands + command-result shapes the shell polls must survive.
    """
    service, bridge = _bridge_service()
    service.start()
    api, base = _api_for(service)
    try:
        now = time.time_ns()
        # Telemetry flows before the command.
        assert service.ingest(data_frame(0, 7, 100, 1.25), time_ns=now) == 1
        status, state = _get_json(base + "/state")
        assert status == 200
        assert state["samples"] == 1

        # The operator's command click — exact /commands response shape.
        status, resp = _post_json(base + "/commands",
                                  {"command_id": 0x0E, "index": 0, "value": 1})
        assert status == 202
        assert isinstance(resp["transaction_id"], int)

        # Firmware applied it: the gateway polling thread picks the result
        # up off the bridge and publishes a snapshot into the hub.
        bridge.results.append(Result(resp["transaction_id"], Outcome.APPLIED,
                                     0x0E, 0, detail="applied"))
        deadline = time.time() + 5.0
        while (api.hub.latest_state() is None
               or api.hub.latest_state().get("last_transaction_result") is None):
            if time.time() > deadline:
                raise AssertionError("gateway polling thread never published")
            time.sleep(0.05)
        published = api.hub.latest_state()["last_transaction_result"]
        assert published["transaction_id"] == resp["transaction_id"]
        assert published["status"] == "applied"

        # More telemetry arrives AFTER the hub publish. Pre-fix, /state
        # kept serving the frozen cached snapshot (samples == 1 forever).
        assert service.ingest(data_frame(0, 8, 105, 1.5),
                              time_ns=now + 40_000_000) == 1
        status, state = _get_json(base + "/state")
        assert status == 200
        assert state["samples"] == 2, (
            "/state served a stale snapshot after the command publish "
            "(Bug 1 regression)")
        # Command feedback is still visible in the fresh snapshot.
        assert state["last_transaction_result"]["transaction_id"] == \
            resp["transaction_id"]
        assert state["last_transaction_result"]["status"] == "applied"
        assert any(r["transaction_id"] == resp["transaction_id"]
                   for r in state["command_results"])
        # Telemetry stream data still present alongside command fields.
        assert "0" in state["streams"]
    finally:
        api.stop()


def test_command_arm_gate_fails_closed_over_http():
    """0x16 (MOTOR_BENCH) stays blocked while ARM state is unknown (fail closed)."""
    service, _bridge = _bridge_service()
    service.start()
    api, base = _api_for(service)
    try:
        # No status.arm telemetry ingested -> arm_state() is "unknown".
        status, resp = _post_json(base + "/commands",
                                  {"command_id": 0x16, "index": 0, "value": 1})
        assert status == 400
        assert "rejected" in str(resp.get("error", "")).lower()
        # Nothing was transmitted to the drone.
        assert service.gateway._pending == set()
        assert not any(a["command_id"] == 0x16
                      for a in service.action_journal())
    finally:
        api.stop()
