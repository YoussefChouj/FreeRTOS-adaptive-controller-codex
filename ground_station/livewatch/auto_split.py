"""Auto-split large subscribe manifests across multiple slots.

When a subscribe request times out waiting for 0x08 schema reply, the firmware
likely rejected it due to link budget constraints but sent no error frame.

This module splits the manifest into smaller chunks, tries each on a separate
slot, and merges the resulting streams into one CSV on the host side.

Industry-standard approach:
1. Error frames with reason codes (firmware sends 0x7F with "link budget exceeded")
2. Capability negotiation (firmware advertises max ranges/bandwidth on boot)
3. Incremental subscribe with acks (add ranges one at a time, get ack/error per range)

This is the **interim fix** (no firmware change needed):
- Try the full manifest first
- If timeout, binary-split and retry on multiple slots
- Merge streams on host side

Once firmware gains 0x7F error frames, this becomes a "split on explicit reject"
path instead of "split on timeout guess".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from .stream import (
    MAX_SLOTS, MAX_STREAM_RANGES, StreamRange, SEND_TASK_MEASURED_HZ,
    SEND_TASK_HZ, FRAME_OVERHEAD, BUDGET_PCT, MultiStreamDecoder,
    build_stream_request, decode_schema, TRANSPORT_UART5, TRANSPORT_USART3,
)
from .transport import LiveTransportError, pop_frame


@dataclass
class SlotPlan:
    """One slot's worth of ranges."""
    slot: int
    ranges: List[StreamRange]
    divider: int


def estimate_frame_size(ranges: List[StreamRange]) -> int:
    """Frame overhead + payload bytes."""
    FRAME_OVERHEAD = 6 + 4 + 2  # header + timestamp + CRC16
    total = sum(r.size * r.count for r in ranges)
    return FRAME_OVERHEAD + total


def estimate_bps(ranges: List[StreamRange], divider: int) -> int:
    """Bytes/sec for this slot."""
    frame_bytes = estimate_frame_size(ranges)
    return (frame_bytes * SEND_TASK_MEASURED_HZ) // divider


def split_manifest(ranges: List[StreamRange], divider: int, transport: int,
                  usart3_baud: int = 921600) -> List[SlotPlan]:
    """Split ranges across multiple slots to fit link budget.
    
    Binary split: if N ranges exceed budget, try N/2 on slot 0, N/2 on slot 1.
    If a half still exceeds budget, split again.
    
    Returns list of SlotPlans, one per slot needed.
    """
    # Link budget caps (conservative 70% utilization)
    BUDGET_PCT_UART5 = 70
    BUDGET_PCT_USART3 = 70
    UART5_BAUD = 115200
    
    if transport == TRANSPORT_USART3:
        cap_bps = (usart3_baud // 10) * BUDGET_PCT_USART3 // 100
    else:
        cap_bps = (UART5_BAUD // 10) * BUDGET_PCT_UART5 // 100
    
    # Check if the full manifest fits
    full_bps = estimate_bps(ranges, divider)
    if full_bps <= cap_bps:
        return [SlotPlan(slot=0, ranges=ranges, divider=divider)]
    
    # Binary split until each chunk fits
    plans = []
    queue = [(ranges, 0)]  # (ranges_chunk, slot_index)
    
    while queue:
        chunk, slot = queue.pop(0)
        if slot >= MAX_SLOTS:
            raise LiveTransportError(
                f"auto-split: manifest needs >{MAX_SLOTS} slots to fit link budget")
        
        chunk_bps = estimate_bps(chunk, divider)
        if chunk_bps <= cap_bps:
            plans.append(SlotPlan(slot=slot, ranges=chunk, divider=divider))
        else:
            # Split in half
            mid = len(chunk) // 2
            if mid == 0:
                raise LiveTransportError(
                    f"auto-split: single range {chunk[0].name} exceeds link budget "
                    f"({chunk_bps} bps > {cap_bps} bps cap)")
            queue.append((chunk[:mid], slot))
            queue.append((chunk[mid:], slot + 1))
    
    # Renumber slots sequentially
    for i, plan in enumerate(plans):
        plan.slot = i
    
    return plans


def try_subscribe_with_split(control, ranges: List[StreamRange], divider: int,
                            transport: int, usart3_baud: int = 921600,
                            timeout: float = 5.0, quiet: bool = False):
    """Try subscribing with auto-split on timeout.
    
    Returns:
        List of (slot, schema) tuples, one per slot actually subscribed.
    
    Raises:
        LiveTransportError if all slots fail.
    """
    control.reset_input_buffer()
    
    # Try full manifest first
    request = build_stream_request(ranges, divider, transport, usart3_baud,
                                   slot=0, other_bps=0)
    control.write(request)
    control.flush()
    
    schema = _await_schema_or_timeout(control, ranges, timeout)
    if schema is not None:
        if not quiet:
            print(f"subscribed: slot 0, {len(ranges)} ranges, no split needed")
        return [(0, schema)]
    
    # Timeout — split and retry
    if not quiet:
        print(f"timeout on {len(ranges)} ranges — splitting across slots...")
    
    plans = split_manifest(ranges, divider, transport, usart3_baud)
    if not quiet:
        print(f"split into {len(plans)} slot(s):")
        for plan in plans:
            print(f"  slot {plan.slot}: {len(plan.ranges)} ranges, "
                  f"{estimate_frame_size(plan.ranges)} B/frame, "
                  f"{estimate_bps(plan.ranges, plan.divider)} bps")
    
    results = []
    for plan in plans:
        control.reset_input_buffer()
        request = build_stream_request(plan.ranges, plan.divider, transport,
                                       usart3_baud, slot=plan.slot, other_bps=0)
        control.write(request)
        control.flush()
        
        schema = _await_schema_or_timeout(control, plan.ranges, timeout)
        if schema is None:
            raise LiveTransportError(
                f"auto-split: slot {plan.slot} timed out even after splitting")
        
        if schema.slot != plan.slot:
            raise LiveTransportError(
                f"auto-split: asked for slot {plan.slot}, got {schema.slot}")
        
        results.append((plan.slot, schema))
    
    return results


def _await_schema_or_timeout(control, ranges: List[StreamRange],
                             timeout: float):
    """Wait for 0x08 schema reply, return None on timeout."""
    rx = bytearray()
    deadline = time.monotonic() + timeout
    
    while time.monotonic() < deadline:
        frame = pop_frame(rx)
        if frame is not None:
            frame_type, byte5, payload = frame
            if frame_type == 0x08:
                return decode_schema(byte5, payload, ranges)
            if frame_type == 0x7F:
                # Error frame — future firmware will send this instead of silent timeout
                reason = payload.decode("utf-8", "replace").rstrip("\x00")
                raise LiveTransportError(f"firmware rejected: {reason}")
            continue
        
        waiting = getattr(control, "in_waiting", 0)
        chunk = control.read(waiting or 1)
        if chunk:
            rx.extend(chunk)
        else:
            time.sleep(0.01)
    
    return None  # Timeout


def auto_split_and_log(control_port, data_port, ranges, divider, transport,
                       seconds, out_path, elf="OBJ/JX_FLY.axf",
                       usart3_baud=921600, quiet=False):
    """Log a range list across slots when only the range count is too large.

    Slot splitting does not increase link capacity. Reject an over-budget
    request before opening the controller and explain the required divider.
    """
    import csv
    import serial

    from .stream import BUDGET_PCT, FRAME_OVERHEAD, SEND_TASK_HZ
    from .stream_log import _open_data, _slot_path, columns_for

    plans = split_manifest_by_count(ranges, divider)
    baud = usart3_baud if transport == TRANSPORT_USART3 else 115200
    budget = (baud // 10) * BUDGET_PCT[transport] // 100
    for plan in plans:
        bps = (FRAME_OVERHEAD + sum(r.nbytes for r in plan.ranges)) * SEND_TASK_HZ // plan.divider
        if bps > budget:
            required = ((FRAME_OVERHEAD + sum(r.nbytes for r in plan.ranges)) * SEND_TASK_HZ + budget - 1) // budget
            raise LiveTransportError(
                "auto-split: slot %d needs %d B/s, above the %d B/s budget; "
                "use divider >= %d (about %.1f Hz) or log fewer values"
                % (plan.slot, bps, budget, required,
                   SEND_TASK_MEASURED_HZ / required))

    control = serial.Serial(control_port, 115200, timeout=0.05)
    data = _open_data(data_port, control, control_port)
    schemas = []
    handles = []
    writers = {}
    rows = {}
    decoder = None
    started = time.monotonic()
    try:
        control.reset_input_buffer()
        for plan in plans:
            request = build_stream_request(
                plan.ranges, plan.divider, transport, usart3_baud,
                slot=plan.slot, other_bps=0)
            control.write(request)
            control.flush()
            schema = _await_schema_or_timeout(control, plan.ranges, 1.5)
            if schema is None:
                raise LiveTransportError(
                    "auto-split: no schema reply for slot %d" % plan.slot)
            schemas.append(schema)

        out_path = Path(out_path)
        decoder = MultiStreamDecoder(schemas)
        for schema in schemas:
            path = _slot_path(out_path, schema.slot)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("w", newline="", encoding="utf-8")
            handles.append(handle)
            writer = csv.writer(handle)
            writer.writerow(["t_src_ms", "t_host_s", "seq"] + columns_for(schema))
            writers[schema.slot] = writer
            rows[schema.slot] = 0

        data.reset_input_buffer()
        while time.monotonic() - started < seconds:
            waiting = data.in_waiting
            if not waiting:
                time.sleep(0.002)
                continue
            for slot, seq, t_ms, values in decoder.feed(data.read(waiting)):
                schema = decoder.decoders[slot].schema
                flat = []
                for rng in schema.ranges:
                    got = values[rng.name or "r%d" % len(flat)]
                    flat.extend(got if isinstance(got, list) else [got])
                writers[slot].writerow(
                    [t_ms, "%.4f" % (time.monotonic() - started), seq] + flat)
                rows[slot] += 1
    finally:
        stop = build_stream_request([], 0, transport, usart3_baud)
        for plan in plans:
            try:
                control.write(build_stream_request(
                    [], 0, transport, usart3_baud, slot=plan.slot))
                control.flush()
                time.sleep(0.25)
            except Exception:
                pass
        for handle in handles:
            handle.close()
        control.close()
        if data is not control:
            data.close()

    elapsed = time.monotonic() - started
    return [{
        "slot": schema.slot,
        "rows": rows[schema.slot],
        "hz": rows[schema.slot] / elapsed if elapsed else 0.0,
        "dropped": decoder.decoders[schema.slot].dropped,
        "loss_pct": decoder.decoders[schema.slot].loss_pct,
        "malformed": decoder.decoders[schema.slot].crc_errors,
        "path": str(_slot_path(Path(out_path), schema.slot)),
    } for schema in schemas]


def split_manifest_by_count(ranges, divider):
    """Split ranges into valid slot plans without changing total bandwidth."""
    if not ranges:
        raise LiveTransportError("auto-split: manifest has no ranges")
    plans = []
    for start in range(0, len(ranges), MAX_STREAM_RANGES):
        slot = len(plans)
        if slot >= MAX_SLOTS:
            raise LiveTransportError(
                "auto-split: manifest needs more than %d slots" % MAX_SLOTS)
        plans.append(SlotPlan(slot, ranges[start:start + MAX_STREAM_RANGES], divider))
    return plans
