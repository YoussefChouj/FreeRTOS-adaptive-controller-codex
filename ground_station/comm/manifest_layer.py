"""Subscribe manifest layouts: name -> (WiFi subscribe request, WiFi VoFA+ map).

A layout groups variables into frame families that VoFA+ receives on
separate UDP ports (1347 / 1348). The host packs the variables for each frame
into the FC's 0xCC 0xDE 0x21 subscribe request, sends it over WiFi (USART3),
reads back the 0x08 schema, and forwards the resulting 0x09 stream to VoFA+.

Layering is at the SLOT level, not the layout level: switching layouts means
dropping the previous slot's subscription and adding a new one. The default
manifest ships at slot 1; alternate layouts land on slots 1-3.

Lane policy (2026-08-20: unified_subscribe_lane)
----------------------------------------------------
docs/decisions.md 2026-08-19: the subscribe control plane moved to WiFi
(USART3) on 2026-08-20. WiFi is now the sole subscribe lane; UART5 is
disabled by default (SUBSCRIBE_UART5_ENABLED=0). WiFi carries the data plane
AND the control plane. This module is the boundary — it builds the bytes that
go over WiFi and the (name -> channel-index) map for VoFA+ forwarding.

Public API
----------
- `load_layouts(path=None)` -> dict[str, Layout]
- `resolve_ranges(layout, frame, resolver)` -> tuple[StreamRange, ...]
  Resolves layout.<frame>.vars through a SymbolResolver (DWARF) and packs
  adjacent scalars into single ranges.
- `apply_layout(layout, control_serial, elf_path)` -> tuple[StreamSchema, ...]
  Performs the subscribe round-trip for all slots; returns the 0x08 schemas.
- `vofa_channel_map(layout)` -> dict[int, str]
  Maps channel_index -> variable name, for the WiFi bridge's
  VoFA+ forwarding path.
- `validate_layout(layout)` -> list[str]
  Validates slot indices, dividers, and var presence. Returns error strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

import yaml

from ground_station.livewatch.stream import (
    TRANSPORT_UART5,
    TRANSPORT_USART3,
    StreamRange,
    StreamSchema,
    subscribe,
)

USART3_BAUD_DEFAULT = 921600   # mirrors USART3_BAUD in BSP/usart3.h

_DEFAULT_LAYOUTS = Path(__file__).with_name("manifest_layout.yaml")


@dataclass(frozen=True)
class FrameSpec:
    """One frame family in a layout: slot, divider, VoFA+ tab, variable list."""

    slot: int
    divider: int
    vofa_tab: str
    vars: tuple[str, ...]
    precision: str = "float32"      # "float32" (default) or "float16"


@dataclass(frozen=True)
class Layout:
    """A named manifest layout: zero or more slots, each on its own divider."""

    name: str
    doc: str
    transport: str            # "usart3" or "uart5"
    slots: tuple[FrameSpec, ...] = field(default_factory=tuple)

    @property
    def usart3_transport(self) -> int:
        return TRANSPORT_USART3 if self.transport == "usart3" else TRANSPORT_UART5

    def frames(self) -> Tuple[FrameSpec, ...]:
        """Backward-compat: alias over self.slots."""
        return self.slots


def load_layouts(path: str | Path | None = None) -> dict[str, Layout]:
    """Read manifest_layout.yaml. Missing file is not an error."""
    p = Path(path) if path else _DEFAULT_LAYOUTS
    if not p.exists():
        return {}
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    out: dict[str, Layout] = {}
    for name, spec in (data.get("layouts") or {}).items():
        # New-style: explicit "slots" key
        if "slots" in spec:
            slots = tuple(_parse_frame(s) for s in spec["slots"])
        else:
            # Legacy: frame_a / frame_b keys (backward compat for manifest_layout.yaml)
            slots = tuple(
                f
                for f in (
                    _parse_frame(spec.get("frame_a", {})) if spec.get("frame_a") else None,
                    _parse_frame(spec.get("frame_b", {})) if spec.get("frame_b") else None,
                )
                if f is not None
            )
        out[name] = Layout(
            name=name,
            doc=str(spec.get("doc", "")).strip(),
            transport=str(spec.get("transport", "usart3")).strip().lower(),
            slots=slots,
        )
    return out


def _parse_frame(spec: dict) -> FrameSpec:
    return FrameSpec(
        slot=int(spec.get("slot", 0)),
        divider=int(spec.get("divider", 1)),
        vofa_tab=str(spec.get("vofa_tab", spec.get("tab_name", ""))).strip(),
        vars=tuple(spec.get("vars", []) or []),
        precision=str(spec.get("precision", "float32")).strip(),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_layout(layout: Layout) -> list[str]:
    """Check a layout for structural errors.

    Returns a list of human-readable error strings. Empty list means valid.
    """
    errors: list[str] = []
    slot_indices: set[int] = set()

    for slot_spec in layout.slots:
        # Slot index range
        if not (0 <= slot_spec.slot <= 3):
            errors.append(
                f"slot {slot_spec.slot} out of range 0-3 "
                f"(vofa_tab={slot_spec.vofa_tab!r})"
            )
        else:
            slot_indices.add(slot_spec.slot)

        # Divider
        if slot_spec.divider < 1:
            errors.append(
                f"slot {slot_spec.slot} divider={slot_spec.divider} must be >= 1 "
                f"(vofa_tab={slot_spec.vofa_tab!r})"
            )

        # Precision
        if slot_spec.precision not in ("float32", "float16"):
            errors.append(
                f"slot {slot_spec.slot} precision={slot_spec.precision!r} "
                f"must be 'float32' or 'float16' "
                f"(vofa_tab={slot_spec.vofa_tab!r})"
            )

    # Slot uniqueness
    if len(slot_indices) != len(layout.slots):
        seen: set[int] = set()
        dupes: set[int] = set()
        for s in layout.slots:
            if s.slot in seen:
                dupes.add(s.slot)
            seen.add(s.slot)
        errors.append(f"duplicate slot indices: {sorted(dupes)}")

    # At least one slot has vars
    if not any(slot.vars for slot in layout.slots):
        errors.append("layout has no variables on any slot")

    return errors


# ---------------------------------------------------------------------------
# Range packing
# ---------------------------------------------------------------------------

def resolve_ranges(layout: Layout, frame: FrameSpec,
                   resolver) -> tuple[StreamRange, ...]:
    """Resolve frame.vars through DWARF and pack adjacent scalars.

    Mirrors `livewatch.reader.build_plan`: scalars of the same size whose
    addresses are <gap_merge_bytes apart collapse into one range with
    count > 1. This is what makes 24 PID fields fit in 4 ranges instead
    of 24, which matters for the SUBSCRIBE_MAX_STREAM_RANGES=24 cap.
    """
    syms = [resolver.resolve(name) for name in frame.vars]
    syms.sort(key=lambda s: (s.address, s.size))
    if not syms:
        return ()
    gap_merge_bytes = 4  # conservative; firmware has the same default
    ranges: list[StreamRange] = []
    cur_syms = [syms[0]]
    cur_start = syms[0].address
    cur_end = cur_start + syms[0].size
    cur_size = syms[0].size
    for sym in syms[1:]:
        if sym.size != cur_size:
            ranges.append(_flush(cur_syms))
            cur_syms = [sym]
            cur_start = sym.address
            cur_end = cur_start + sym.size
            cur_size = sym.size
            continue
        gap = sym.address - cur_end
        if 0 <= gap < gap_merge_bytes and sym.size in (1, 2, 4):
            cur_syms.append(sym)
            cur_end = sym.address + sym.size
        else:
            ranges.append(_flush(cur_syms))
            cur_syms = [sym]
            cur_start = sym.address
            cur_end = cur_start + sym.size
            cur_size = sym.size
    ranges.append(_flush(cur_syms))
    return tuple(ranges)


def _flush(syms: list) -> StreamRange:
    syms.sort(key=lambda s: s.address)
    address = syms[0].address
    size = syms[0].size
    count = len(syms)
    name = ", ".join(s.name for s in syms)
    fmt = syms[0].fmt
    return StreamRange(address=address, size=size, count=count, name=name, fmt=fmt)


# ---------------------------------------------------------------------------
# Subscribe round-trip
# ---------------------------------------------------------------------------

def apply_layout(layout: Layout, control_serial, resolver,
                 usart3_baud: int = USART3_BAUD_DEFAULT,
                 timeout: float = 1.0
                 ) -> Tuple[Tuple[StreamSchema, ...], ...]:
    """Resolve layout vars and send each slot's subscription to the FC.

    Returns a tuple-of-tuples: outer index is slot position in layout.slots,
    inner is one StreamSchema per call. Each slot has its own slot/divider;
    the FC accepts each and starts a 0x09+slot data stream on USART3 (WiFi).

    `control_serial` is a live subscribe transport (e.g. Usart3WifiSubscribeTransport
    for WiFi). The FC replies 0x08 on the same transport.
    """
    out: list[tuple[StreamSchema, ...]] = []
    for frame in layout.slots:
        ranges = resolve_ranges(layout, frame, resolver)
        if not ranges:
            # Drop the slot if it has no vars. divider=0 is the documented stop.
            schema = subscribe(control_serial, ranges, divider=0,
                               transport=layout.usart3_transport,
                               usart3_baud=usart3_baud,
                               slot=frame.slot, timeout=timeout)
            out.append((schema,))
            continue
        schema = subscribe(control_serial, ranges, divider=frame.divider,
                           transport=layout.usart3_transport,
                           usart3_baud=usart3_baud,
                           slot=frame.slot, timeout=timeout)
        out.append((schema,))
    return tuple(out)


# ---------------------------------------------------------------------------
# Boot-default subscribe: applies the firmware's built-in boot-default slot
# ---------------------------------------------------------------------------

def apply_boot_default(transport,
                       resolver,
                       timeout: float = 1.0
                       ) -> "StreamSchema":
    """Apply the firmware's built-in boot-default subscription on slot 0.

    This function re-subscribes the FC's hardcoded boot-default (slot 0) so the
    host recovers the 0x08 schema and can set up VoFA+ forwarding.  The
    re-subscribe is idempotent: the FC accepts a second subscribe for the same
    slot without complaint and re-starts the stream with the same parameters.

    Slot 0 runs at divider=20 (10 Hz at 200 Hz Send_Task).  The range list
    must match ``API/subscribe.c Subscribe_BootDefault()`` exactly: imu_data,
    DroneStatus, system_monitor, UA3RxFrameCnt, UA3TxFrames.

    Works with any transport that has a ``subscribe()`` method returning a
    ``StreamSchema`` (e.g. ``Usart3WifiSubscribeTransport``).
    """
    from ground_station.comm.boot_default_layout import BOOT_DEFAULT_VARS
    ranges = list(resolve_ranges_from_names(BOOT_DEFAULT_VARS, resolver))
    return transport.subscribe(
        symbols=ranges,
        divider=20,        # 200 Hz / 20 = 10 Hz (matches firmware's boot-default)
        transport=TRANSPORT_USART3,
        slot=0,
    )


def resolve_ranges_from_names(vars: Tuple[str, ...], resolver
                              ) -> Tuple[StreamRange, ...]:
    """Resolve variable names to StreamRanges, packing adjacent scalars."""
    syms = [resolver.resolve(name) for name in vars]
    syms.sort(key=lambda s: (s.address, s.size))
    if not syms:
        return ()
    gap_merge_bytes = 4
    ranges: list[StreamRange] = []
    cur_syms = [syms[0]]
    cur_start = syms[0].address
    cur_size = syms[0].size
    cur_end = cur_start + cur_size
    for sym in syms[1:]:
        if sym.size != cur_size:
            ranges.append(_flush(cur_syms))
            cur_syms = [sym]
            cur_start = sym.address
            cur_end = cur_start + sym.size
            cur_size = sym.size
            continue
        gap = sym.address - cur_end
        if 0 <= gap < gap_merge_bytes and sym.size in (1, 2, 4):
            cur_syms.append(sym)
            cur_end = sym.address + sym.size
        else:
            ranges.append(_flush(cur_syms))
            cur_syms = [sym]
            cur_start = sym.address
            cur_end = cur_start + sym.size
            cur_size = sym.size
    ranges.append(_flush(cur_syms))
    return tuple(ranges)


# ---------------------------------------------------------------------------
# VoFA+ channel map
# ---------------------------------------------------------------------------

def vofa_channel_map(layout: Layout, resolver
                     ) -> Tuple[dict[int, str], dict[int, str]]:
    """Return (frame_a, frame_b) -> {channel_index: variable_name}.

    Channel index = position in the data frame's payload (range-major,
    within-range address-ascending).  For layouts with >2 slots the
    extra slots are ignored here; callers should use `slot_channel_map`
    which returns one dict per slot.

    The WiFi bridge converts this into JustFloat LE float32s and forwards
    to UDP 1347 (Frame A) / 1348 (Frame B).
    """
    # Build one channel map per slot
    all_maps: list[dict[int, str]] = []
    for frame in layout.slots:
        ranges = resolve_ranges(layout, frame, resolver)
        cmap: dict[int, str] = {}
        idx = 0
        for rng in ranges:
            for sym in _range_to_symbols(rng, resolver):
                cmap[idx] = sym.name
                idx += 1
        all_maps.append(cmap)

    # Return (frame_a, frame_b) for backward compat; fill empty for missing slots
    return (all_maps[0] if len(all_maps) > 0 else {},
            all_maps[1] if len(all_maps) > 1 else {})


def slot_channel_map(layout: Layout, resolver
                     ) -> dict[int, dict[int, str]]:
    """Return slot_index -> channel_index -> variable_name for all slots."""
    result: dict[int, dict[int, str]] = {}
    for frame in layout.slots:
        ranges = resolve_ranges(layout, frame, resolver)
        cmap: dict[int, str] = {}
        idx = 0
        for rng in ranges:
            for sym in _range_to_symbols(rng, resolver):
                cmap[idx] = sym.name
                idx += 1
        result[frame.slot] = cmap
    return result


def slot_precision_map(layout: Layout) -> dict[int, dict[int, str]]:
    """Return slot_index -> channel_index -> precision for all slots.

    Each frame's precision applies to every channel in that slot. Channels are
    counted directly from the YAML var lists (one channel per var, since the
    YAML lists individual scalars). No DWARF resolver needed.

    Example usage::

        ch_map = slot_channel_map(layout, resolver)   # needs ELF
        prec_map = slot_precision_map(layout)           # pure YAML, no ELF

        # Build global channel-index -> precision map:
        global_prec = {}
        idx = 0
        for slot_idx, slot_frame in enumerate(layout.slots):
            n_ch = len(slot_frame.vars)   # one channel per YAML var
            for ch in range(n_ch):
                global_prec[idx] = slot_frame.precision
                idx += 1
    """
    result: dict[int, dict[int, str]] = {}
    idx = 0
    for frame in layout.slots:
        cmap: dict[int, str] = {}
        for i in range(len(frame.vars)):
            cmap[idx] = frame.precision
            idx += 1
        result[frame.slot] = cmap
    return result


def _range_to_symbols(rng: StreamRange, resolver) -> list:
    """Recover the per-element names a packed range covers.

    When the resolver packs adjacent scalars into one range, `_flush` joins
    the names with ", ". To expand back into one name per channel index,
    split on that comma and assign in order. If the range has no joined
    name (e.g. from a hand-built StreamRange), fall back to a placeholder.
    """
    if rng.name and "," in rng.name:
        names = [n.strip() for n in rng.name.split(",")]
        if len(names) == rng.count:
            return [_Stub(n) for n in names]
    if rng.name and rng.count == 1:
        return [_Stub(rng.name)]
    return [_Stub(f"{rng.name or f'0x{rng.address:08X}'}[{i}]")
            for i in range(rng.count)]


class _Stub:
    """Minimal Symbol-shaped object for name lookup."""
    __slots__ = ("name",)
    def __init__(self, name):
        self.name = name


# ---------------------------------------------------------------------------
# Per-experiment manifests (ground_station/comm/manifests.yaml)
# ---------------------------------------------------------------------------

_DEFAULT_MANIFESTS = Path(__file__).with_name("manifests.yaml")


def load_manifests(path: str | Path | None = None
                   ) -> dict[str, Layout]:
    """Read manifests.yaml: per-experiment slot profiles.

    Unlike ``load_layouts`` (manifest_layout.yaml, operator-facing), this
    file defines full experiment sessions. Each entry in ``manifests:``
    is a Layout with explicit slots 0-3.

    Returns:
        dict of name -> Layout. Skips entries whose name starts with '_'
        (these are internal base profiles).
    """
    p = Path(path) if path else _DEFAULT_MANIFESTS
    if not p.exists():
        return {}
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    manifests = data.get("manifests") or {}
    out: dict[str, Layout] = {}
    for name, spec in manifests.items():
        if name.startswith("_"):
            continue  # internal base profile
        slot_specs = spec.get("slots") or []
        slots = tuple(_parse_frame(s) for s in slot_specs)
        out[name] = Layout(
            name=name,
            doc=str(spec.get("doc", "")).strip(),
            transport=str(spec.get("transport", "usart3")).strip().lower(),
            slots=slots,
        )
    return out


def get_manifest(name: str,
                 manifests: dict[str, Layout] | None = None
                 ) -> Layout | None:
    """Look up one manifest by name, loading from disk if needed."""
    if manifests is None:
        manifests = load_manifests()
    return manifests.get(name)


# ---------------------------------------------------------------------------
# Manifest CLI: python -m ground_station.comm.manifest_layer <name>
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="ground_station.comm.manifest_layer: list and inspect subscribe manifests.\n\n"
                    "Usage:\n"
                    "  python -m ground_station.comm.manifest_layer            # list all manifests\n"
                    "  python -m ground_station.comm.manifest_layer <name>      # show one manifest\n"
                    "  python -m ground_station.comm.manifest_layer --apply <name> [--elf OBJ/JX_FLY.axf]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("name", nargs="?", help="Manifest name to inspect")
    ap.add_argument("--apply", metavar="MANIFEST",
                    help="Print the apply command for this manifest")
    ap.add_argument("--elf", default="OBJ/JX_FLY.axf",
                    help="ELF path for DWARF symbol resolution (default: OBJ/JX_FLY.axf)")
    ap.add_argument("--resolve", action="store_true",
                    help="Resolve variable names to DWARF addresses (requires --apply)")
    args = ap.parse_args()

    manifests = load_manifests()
    if not manifests:
        print("No manifests found in ground_station/comm/manifests.yaml", file=sys.stderr)
        sys.exit(1)

    # --apply implies --resolve and also prints the apply command
    if args.apply:
        args.name = args.apply
        args.resolve = True

    if args.name:
        layout = manifests.get(args.name)
        if layout is None:
            available = ", ".join(sorted(manifests.keys()))
            print(f"Manifest {args.name!r} not found. Available: {available}", file=sys.stderr)
            sys.exit(1)
        print(f"# Manifest: {args.name}")
        print(f"# {layout.doc}")
        print(f"transport: {layout.transport}")
        print(f"slots: {len(layout.slots)}")
        for slot in layout.slots:
            n_bytes = len(slot.vars) * 4
            hz = 200 // slot.divider
            print(f"  slot {slot.slot}: divider={slot.divider} ({hz} Hz), "
                  f"vars={len(slot.vars)}, ~{n_bytes} B/frame, "
                  f"precision={slot.precision}, vofa={slot.vofa_tab!r}")
            for v in slot.vars:
                print(f"    - {v}")
        if args.apply:
            print()
            print(f"# Apply command (paste into livewatch):")
            print(f"python -m ground_station.livewatch manifest apply {args.name}")
            if args.resolve:
                from ground_station.livewatch.symbols import SymbolResolver
                from pathlib import Path
                elf = Path(args.elf)
                if elf.exists():
                    resolver = SymbolResolver(elf)
                    from ground_station.comm.manifest_layer import resolve_ranges
                    for slot in layout.slots:
                        ranges = resolve_ranges(layout, slot, resolver)
                        print(f"\n# Slot {slot.slot} ({slot.vofa_tab}) ranges:")
                        for r in ranges:
                            print(f"  0x{r.address:08X} size={r.size} count={r.count}  {r.name}")
                else:
                    print(f"\n# ELF not found: {elf}  (DWARF resolution skipped)")
    else:
        print("# Available manifests")
        for name, layout in sorted(manifests.items()):
            print(f"  {name}")
            print(f"    {layout.doc.split(chr(10))[0][:70]}")
            slot_hz = [(s.slot, 200 // s.divider) for s in layout.slots]
            print(f"    slots: {slot_hz}")
