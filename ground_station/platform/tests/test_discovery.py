import struct

from ground_station.platform.discovery import (
    CAPABILITIES, KIND_NAMES, build_request, load_generated_registry,
    parse_discovery, parse_registry_digest,
)


def test_discovery_request_contract():
    assert build_request(0x22) == bytes.fromhex("cc de 22 00 00 00 22")


def test_generated_registry_is_self_consistent():
    registry = load_generated_registry()
    assert registry["descriptor_count"] == len(registry["descriptors"])
    assert sum(registry["kind_counts"].values()) == registry["descriptor_count"]
    assert len(registry["capabilities"]) == len(CAPABILITIES)


def test_parse_discovery_and_compatibility():
    registry = load_generated_registry()
    payload = b"UAVR" + struct.pack(
        "<BBHHHII4I3II5H", 14, 1, registry["schema_version"], registry["registry_version"],
        registry["hardware_id"], (1 << len(CAPABILITIES)) - 1,
        int(registry["registry_crc32"], 16), 0xB10DCAFE, 7, 8, 9,
        1, 2, 3, 4, 62, 4, 1024, 80, registry["descriptor_count"])
    parsed = parse_discovery(payload, 1)
    assert parsed.compatible_with(registry)
    assert parsed.capabilities == CAPABILITIES


def test_parse_registry_digest():
    registry = load_generated_registry()
    payload = b"REG1" + struct.pack("<HHI", registry["registry_version"], registry["schema_version"], int(registry["registry_crc32"], 16))
    for ident, kind in enumerate(KIND_NAMES, 1):
        payload += struct.pack("<BBH", ident, 0, registry["kind_counts"].get(kind, 0))
    digest = parse_registry_digest(payload, len(KIND_NAMES))
    assert digest.kind_counts == registry["kind_counts"]
