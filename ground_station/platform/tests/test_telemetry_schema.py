import json

import pytest

from ground_station.platform.telemetry import DEFAULT_SCHEMA, load_telemetry_schema


def test_generated_telemetry_schema_matches_registry():
    schema = load_telemetry_schema()
    assert schema.schema_id == "r1-s1-9F32E2EA"
    assert schema.stream(9).name == "typed_stream"
    assert schema.stream(9).wire_type == "frame_0x09"


def test_telemetry_schema_rejects_registry_drift(tmp_path):
    source = tmp_path / "schema.json"
    raw = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    raw["registry_crc32"] = "0x00000000"
    source.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        load_telemetry_schema(source)
