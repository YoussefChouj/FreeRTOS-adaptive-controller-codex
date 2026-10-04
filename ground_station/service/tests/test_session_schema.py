"""WP-42 P3: a recording describes itself (session_schema.py) and reads back."""
from __future__ import annotations

import json
from types import SimpleNamespace

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.platform.firmware_contract import GS_PROTO_VERSION
from ground_station.service import session_schema as ss
from ground_station.service.core import GroundStationService
from ground_station.service.storage import CsvRecorder

SCHEMAS = {
    1: StreamSchema(divider=2, transport=0, total_bytes=12, slot=1, ranges=(
        StreamRange(0x20000100, 4, 1, name="g_thr", fmt="<f"),
        StreamRange(0x20000200, 4, 2, name="att", _names=("att.roll", "att.pitch"), _fmts=("f", "i")),
    )),
    0: StreamSchema(divider=0, transport=0, total_bytes=0, slot=0, ranges=()),
}


def test_variables_one_entry_per_element_with_type_and_rate():
    v = ss.variables(SCHEMAS, units={"g_thr": "N"})
    assert [x["name"] for x in v] == ["g_thr", "att.roll", "att.pitch"]
    assert [x["type"] for x in v] == ["float32", "float32", "int32"]
    assert v[0]["unit"] == "N" and v[1]["unit"] is None              # no unit known: None, not a guess
    assert v[0]["slot"] == 1 and v[0]["divider"] == 2 and v[0]["rate_hz"] == round(SCHEMAS[1].hz, 3)
    assert v[2]["address"] == "0x20000204"


def test_tunables_are_the_firmware_row_cells():
    cells = ss.tunables([ss.REPO / "API" / "prearm.c"])
    vbat = [c for c in cells if c["table"] == "PREARM_LIMITS_ROW" and c["param"] == "vbat_min_v"]
    assert len(vbat) == 1 and vbat[0]["value"] == 14.0 and vbat[0]["unit"] == "V"


def test_round_trip(tmp_path):
    block = ss.describe(build={"commit": "abc123", "dirty": False}, elf=None, variables=ss.variables(SCHEMAS),
                        tunables=ss.tunables([ss.REPO / "API" / "prearm.c"]))
    rec = CsvRecorder(tmp_path, enabled=True)
    assert rec.start(requested_by="agent:test", reason="p3", session_schema=block)
    for lifecycle in ("submitted", "applied"):
        rec.add_event("command", {"id": 0x1E, "idx": 3, "value": 0.25, "transaction_id": 9,
                                  "lifecycle": lifecycle}, source="service")
    rec.add_event("arm_state", {"state": "armed"}, source="service")
    rec.stop()

    back = ss.read(rec.session_dir)
    assert back["schema"] == json.loads(json.dumps(block))
    assert back["schema"]["format"] == "gs-session" and back["schema"]["format_version"] == 2
    assert back["schema"]["contracts"]["gs_proto"] == GS_PROTO_VERSION
    assert [p["data"]["lifecycle"] for p in back["params"]] == ["submitted", "applied"]
    assert all(p["data"]["value"] == 0.25 and p["data"]["idx"] == 3 for p in back["params"])
    assert [e["kind"] for e in back["events"]][0] == "recording_start"


def test_service_builds_the_block_from_the_bridge_schemas():
    svc = SimpleNamespace(bridge=SimpleNamespace(_stream_schemas=SCHEMAS))
    block = GroundStationService._session_schema(svc)
    assert block is not None and len(block["variables"]) == 3
    assert block["build"]["git"] is None or len(block["build"]["git"]["commit"]) == 40
    assert any(c["table"] == "PREARM_LIMITS_ROW" for c in block["tunables"])


def test_recording_without_schema_keeps_the_v1_manifest(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=True)
    rec.start()
    rec.stop()
    assert ss.read(rec.session_dir)["schema"] is None
