"""Read-only identity, capability, and registry-digest discovery over MicoAir."""
from __future__ import annotations

import json
import socket
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from ground_station.livewatch.transport import LiveTransportError, pop_frame

DISCOVERY_CMD = 0x22
REGISTRY_DIGEST_CMD = 0x23
DISCOVERY_FRAME = 0x22
REGISTRY_DIGEST_FRAME = 0x23
FORMAT_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]

CAPABILITIES = (
    "wifi_command", "wifi_subscribe", "multi_slot_stream", "static_registry",
    "build_identity", "swd_probe", "legacy_telemetry", "typed_discovery",
)
KIND_NAMES = ("variable", "enum", "parameter", "command", "telemetry", "event", "plugin", "task", "resource")


def _xor(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def build_request(command: int) -> bytes:
    body = bytes((command, 0, 0, 0))
    return b"\xCC\xDE" + body + bytes((_xor(body),))


@dataclass(frozen=True)
class Discovery:
    protocol_version: int
    format_version: int
    schema_version: int
    registry_version: int
    hardware_id: int
    capability_bits: int
    registry_crc32: int
    build_id: tuple[int, int, int, int]
    device_uid: tuple[int, int, int]
    reset_cause: int
    max_ranges: int
    max_slots: int
    max_stream_bytes: int
    send_task_hz: int
    descriptor_count: int

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(name for bit, name in enumerate(CAPABILITIES) if self.capability_bits & (1 << bit))

    def compatible_with(self, registry: dict) -> bool:
        return (
            self.format_version == FORMAT_VERSION
            and self.schema_version == int(registry["schema_version"])
            and self.registry_version == int(registry["registry_version"])
            and self.registry_crc32 == int(registry["registry_crc32"], 16)
            and self.descriptor_count == int(registry["descriptor_count"])
        )


@dataclass(frozen=True)
class RegistryDigest:
    registry_version: int
    schema_version: int
    registry_crc32: int
    kind_counts: dict[str, int]


def parse_discovery(payload: bytes, byte5: int) -> Discovery:
    if byte5 != FORMAT_VERSION or len(payload) != 62 or payload[:4] != b"UAVR":
        raise LiveTransportError("invalid platform discovery reply")
    fields = struct.unpack_from("<BBHHHII4I3II5H", payload, 4)
    return Discovery(
        protocol_version=fields[0], format_version=fields[1], schema_version=fields[2],
        registry_version=fields[3], hardware_id=fields[4], capability_bits=fields[5],
        registry_crc32=fields[6], build_id=tuple(fields[7:11]), device_uid=tuple(fields[11:14]),
        reset_cause=fields[14], max_ranges=fields[15], max_slots=fields[16],
        max_stream_bytes=fields[17], send_task_hz=fields[18], descriptor_count=fields[19],
    )


def parse_registry_digest(payload: bytes, byte5: int) -> RegistryDigest:
    if byte5 != len(KIND_NAMES) or len(payload) != 12 + 4 * byte5 or payload[:4] != b"REG1":
        raise LiveTransportError("invalid registry digest reply")
    version, schema, crc = struct.unpack_from("<HHI", payload, 4)
    counts: dict[str, int] = {}
    for offset in range(12, len(payload), 4):
        kind, _reserved, count = struct.unpack_from("<BBH", payload, offset)
        if not 1 <= kind <= len(KIND_NAMES):
            raise LiveTransportError(f"unknown registry kind {kind}")
        counts[KIND_NAMES[kind - 1]] = count
    return RegistryDigest(version, schema, crc, counts)


def load_generated_registry(path: Path | None = None) -> dict:
    target = path or ROOT / "ground_station/generated/platform_registry.json"
    return json.loads(Path(target).read_text(encoding="utf-8"))


def _request(command: int, wanted: int, module_ip: str, local_port: int, timeout_s: float):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.25)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", local_port))
    try:
        sock.sendto(b"\x00", (module_ip, 14550))
        time.sleep(0.03)
        sock.sendto(build_request(command), (module_ip, 14550))
        rx = bytearray()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(4096)
                rx.extend(data)
            except socket.timeout:
                pass
            while True:
                frame = pop_frame(rx)
                if frame is None:
                    break
                if frame[0] == wanted:
                    return frame[2], frame[1]
                if frame[0] == 0x7F:
                    raise LiveTransportError(frame[2].decode("utf-8", errors="replace").rstrip("\x00"))
        raise LiveTransportError(f"no 0x{wanted:02X} discovery reply within {timeout_s:.1f}s")
    finally:
        sock.close()


def discover(module_ip: str = "192.168.4.1", local_port: int = 14550, timeout_s: float = 3.0) -> tuple[Discovery, RegistryDigest]:
    payload, byte5 = _request(DISCOVERY_CMD, DISCOVERY_FRAME, module_ip, local_port, timeout_s)
    identity = parse_discovery(payload, byte5)
    payload, byte5 = _request(REGISTRY_DIGEST_CMD, REGISTRY_DIGEST_FRAME, module_ip, local_port, timeout_s)
    return identity, parse_registry_digest(payload, byte5)
