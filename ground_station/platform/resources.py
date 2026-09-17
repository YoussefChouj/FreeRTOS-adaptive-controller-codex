"""Generated resource map and bounded RTOS observability metrics."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .discovery import load_generated_registry


@dataclass(frozen=True)
class ResourceEntry:
    kind: str
    name: str
    owner: str
    rate_hz: int
    limit: float
    unit: str
    address: int | None = None
    size: int | None = None


@dataclass(frozen=True)
class RuntimeMetric:
    name: str
    value: float
    limit: float | None = None
    unit: str = ""

    @property
    def utilization(self) -> float | None:
        if self.limit in (None, 0):
            return None
        return self.value / self.limit


class ResourceMap:
    def __init__(self, entries: Iterable[ResourceEntry], registry_crc32: int):
        self.entries = tuple(entries)
        self.registry_crc32 = registry_crc32

    def by_kind(self, kind: str) -> tuple[ResourceEntry, ...]:
        return tuple(item for item in self.entries if item.kind == kind)

    def resolve_symbols(self, resolver) -> "ResourceMap":
        resolved = []
        for item in self.entries:
            try:
                symbol = resolver.resolve(item.name)
            except (KeyError, ValueError):
                resolved.append(item)
            else:
                resolved.append(ResourceEntry(
                    item.kind, item.name, item.owner, item.rate_hz, item.limit,
                    item.unit, symbol.address, symbol.size))
        return ResourceMap(resolved, self.registry_crc32)


def build_resource_map(registry: dict | None = None) -> ResourceMap:
    source = registry or load_generated_registry()
    entries = []
    for row in source["descriptors"]:
        if row["kind"] not in ("task", "resource"):
            continue
        entries.append(ResourceEntry(
            row["kind"], row["name"], row["owner"], int(row["rate_hz"]),
            float(row["max"]), row["unit"],
        ))
    return ResourceMap(entries, int(source["registry_crc32"], 16))


def observe_metrics(values: dict[str, float], limits: dict[str, float] | None = None,
                    units: dict[str, str] | None = None) -> tuple[RuntimeMetric, ...]:
    limits = limits or {}
    units = units or {}
    return tuple(RuntimeMetric(name, float(value), limits.get(name), units.get(name, ""))
                 for name, value in sorted(values.items()))

