"""Transport-agnostic, read-only live variable reader.

Safety contract (do not weaken): transport implementations expose block-read paths
only. The default SWD transport preserves ``connect_mode=attach``,
``target_override=cortex_m`` and ``resume_on_disconnect=False``; neither transport
has a halt, reset, core-memory write, arm, or motor path. UART5 sends only the
confirmed benign ID-frame handshake and observes replies.

The coalescing (`build_plan`) and decoding (`Plan.decode`) are pure functions with
no hardware dependency, so they are unit-tested offline against synthetic bytes.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .symbols import Symbol, SymbolResolver
from .transport import LiveTransport, LiveTransportError, SwdCmsisDap

# Merge two symbols into one block read only when the gap between them is smaller
# than the cost of a second transaction, expressed in bytes.
#
# Measured on the wireless CMSIS-DAP probe (2026-07-24): it is BANDWIDTH-limited,
# not latency-limited -- ~1.7 ms fixed per transaction PLUS only ~25-30 KB/s of
# transfer (a 4 B read = 1.72 ms; a 425 B read = 16.7 ms). So bridging an unused
# gap of G bytes costs ~G/28 ms, while a second transaction costs ~1.7 ms:
# merging wins only when  G/28 ms < 1.7 ms  ->  G < ~48 bytes. Hence 48.
#
# Contrast a *wired* CMSIS-DAP probe (latency-limited): there a much larger
# threshold would be optimal. Only affects efficiency, never correctness; retune
# per probe. Override via LiveReader(..., gap_merge_bytes=N) if you switch probes.
_GAP_MERGE_BYTES = 48


@dataclass
class Region:
    start: int
    size: int

    @property
    def end(self) -> int:
        return self.start + self.size


@dataclass
class Plan:
    """A resolved, coalesced read plan: which blocks to read, and how to slice them."""
    symbols: list[Symbol]
    regions: list[Region]

    def decode(self, region_bytes: list[bytes]) -> dict[str, object]:
        """Decode a full sample. `region_bytes[i]` is the raw read of `regions[i]`."""
        out: dict[str, object] = {}
        for sym in self.symbols:
            ri, off = self._locate(sym.address)
            raw = region_bytes[ri][off: off + sym.size]
            out[sym.name] = sym.decode(raw) if sym.is_scalar else raw
        return out

    def _locate(self, addr: int) -> tuple[int, int]:
        for i, r in enumerate(self.regions):
            if r.start <= addr < r.end:
                return i, addr - r.start
        raise KeyError(f"address 0x{addr:08X} not covered by plan")


def build_plan(resolver: SymbolResolver, names: list[str],
               gap_merge_bytes: int = _GAP_MERGE_BYTES) -> Plan:
    """Resolve names and coalesce their memory into minimal contiguous read regions."""
    syms = [resolver.resolve(n) for n in names]
    ordered = sorted(syms, key=lambda s: s.address)
    regions: list[Region] = []
    for s in ordered:
        if regions and s.address <= regions[-1].end + gap_merge_bytes:
            last = regions[-1]
            new_end = max(last.end, s.address + s.size)
            last.size = new_end - last.start
        else:
            regions.append(Region(s.address, s.size))
    return Plan(symbols=syms, regions=regions)


class LiveReader:
    """Resolves symbols and samples them through a read-only transport."""

    def __init__(self, elf_path: str | Path, transport: LiveTransport | None = None,
                 gap_merge_bytes: int | None = None,
                 *, swd_limit_packets: bool = True):
        self.resolver = SymbolResolver(elf_path)
        if transport is None:
            from .transport import SwdCmsisDap
            transport = SwdCmsisDap(limit_packets=swd_limit_packets)
        self.transport = transport
        self.gap_merge_bytes = (self.transport.gap_merge_bytes
                                if gap_merge_bytes is None else gap_merge_bytes)
        self._target = None

    # ---- connection (lazy; keeps offline tests hardware-free) ----------

    def connect(self):
        self.transport.connect()
        self._target = getattr(self.transport, "target", None)
        return self

    def close(self):
        self.transport.close()
        self._target = None
        self.resolver.close()

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # ---- sampling ------------------------------------------------------

    def plan(self, names: list[str]) -> Plan:
        return build_plan(self.resolver, names, self.gap_merge_bytes)

    def sample(self, plan: Plan) -> dict[str, object]:
        try:
            blocks = self.transport.sample(plan)
        except LiveTransportError:
            raise
        return plan.decode(blocks)

    # ---- retry-aware raw read (used by verify and raw callers) --------------

    def read_raw(self, address: int, size: int) -> bytes:
        """Read a raw memory region with the same retry policy as sample()."""
        return self.transport._read_region_with_retry(Region(address, size))

    def stream(self, names: list[str], hz: float = 20.0,
               duration: float | None = None) -> Iterator[dict]:
        """Yield {'t': elapsed_s, <name>: value, ...} samples at ~hz until duration."""
        plan = self.plan(names)
        period = 1.0 / hz
        t0 = time.perf_counter()
        next_t = t0
        while True:
            now = time.perf_counter()
            row = {"t": now - t0}
            row.update({k: v for k, v in self.sample(plan).items()
                        if not isinstance(v, (bytes, bytearray))})
            yield row
            if duration is not None and (now - t0) >= duration:
                return
            next_t += period
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.perf_counter()  # fell behind; resync, don't spiral
