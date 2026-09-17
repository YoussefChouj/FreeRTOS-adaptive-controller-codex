"""Generated typed telemetry contract and compatibility checks."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .discovery import load_generated_registry


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = ROOT / "ground_station/generated/telemetry_schema.json"


@dataclass(frozen=True)
class TelemetryStream:
    stream_id: int
    name: str
    wire_type: str
    unit: str
    owner: str
    rate_hz: int
    version: int
    dependencies: int


@dataclass(frozen=True)
class TelemetrySchema:
    format_version: int
    registry_version: int
    schema_version: int
    registry_crc32: int
    streams: tuple[TelemetryStream, ...]

    @property
    def schema_id(self) -> str:
        return f"r{self.registry_version}-s{self.schema_version}-{self.registry_crc32:08X}"

    def compatible_with(self, discovery) -> bool:
        return (
            discovery.registry_version == self.registry_version
            and discovery.schema_version == self.schema_version
            and discovery.registry_crc32 == self.registry_crc32
        )

    def stream(self, stream_id: int) -> TelemetryStream:
        for item in self.streams:
            if item.stream_id == stream_id:
                return item
        raise KeyError(stream_id)


def load_telemetry_schema(path: Path | None = None) -> TelemetrySchema:
    source = Path(path or DEFAULT_SCHEMA)
    raw = json.loads(source.read_text(encoding="utf-8"))
    registry = load_generated_registry()
    expected_crc = int(registry["registry_crc32"], 16)
    actual_crc = int(raw["registry_crc32"], 16)
    if (
        raw["registry_version"] != registry["registry_version"]
        or raw["schema_version"] != registry["schema_version"]
        or actual_crc != expected_crc
    ):
        raise ValueError("typed telemetry schema does not match generated registry")
    streams = tuple(
        TelemetryStream(
            int(row["id"]), row["name"], row["type"], row["unit"],
            row["owner"], int(row["rate_hz"]), int(row["version"]),
            int(row["dependencies"]),
        )
        for row in raw["streams"]
    )
    return TelemetrySchema(
        int(raw["format_version"]), int(raw["registry_version"]),
        int(raw["schema_version"]), actual_crc, streams,
    )

