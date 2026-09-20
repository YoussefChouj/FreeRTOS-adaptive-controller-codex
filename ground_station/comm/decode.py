"""Host-side decode pipeline: raw 0x09 frame bytes → decoded Float32 values.

Layers applied in order:
  1. Unpack raw bytes per range (struct.unpack, little-endian)
  2. Float16 bit-cast when precision_map says "float16"
  3. Delta decode: decoded = prev + raw_value
  4. RLE: track consecutive run length per channel
  5. Counter: skip delta decode for monotonically increasing counters
"""
from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ground_station.livewatch.stream import StreamSchema


# ---------------------------------------------------------------------------
# Float16 helpers (no struct 'e' — may not be available on all builds)
# ---------------------------------------------------------------------------

def _float16_to_float32(bits: int) -> float:
    """Reinterpret a 16-bit unsigned integer as IEEE-754 float16."""
    sign = (bits >> 15) & 0x1
    exp16 = (bits >> 10) & 0x1f
    frac16 = bits & 0x3ff

    if exp16 == 0:
        if frac16 == 0:
            value = 0.0
        else:
            value = (frac16 / 1024.0) * (2 ** -14)
    elif exp16 == 31:
        if frac16 == 0:
            value = float('inf')
        else:
            value = float('nan')
    else:
        value = (1.0 + frac16 / 1024.0) * (2 ** (exp16 - 15))

    return -value if sign else value


def _uint16_to_float32_le(raw: bytes) -> float:
    """Convert 2-byte little-endian uint16 bit-pattern to float32."""
    bits = int.from_bytes(raw, "little")
    return _float16_to_float32(bits)


# ---------------------------------------------------------------------------
# DecodePipeline
# ---------------------------------------------------------------------------

class DecodePipeline:
    """Host-side decode: raw bytes → Float32, with delta/RLE/precision layers."""

    def __init__(
        self,
        schema: "StreamSchema",
        precision_map: dict[int, str] | None = None,
    ):
        """
        Args:
            schema: StreamSchema from the 0x08 reply (has ranges with addresses + sizes + counts).
            precision_map: channel_index -> "float32" | "float16" | "counter".
                          From the manifest's precision field, per frame.
        """
        self.schema = schema
        self.precision_map = precision_map or {}
        self._delta_state: dict[int, float] = {}
        self._rle_state: dict[int, tuple[float, int]] = {}

    def decode_frame(
        self, raw_payload: bytes
    ) -> tuple[dict[int, float], dict[int, int]]:
        """Decode one 0x09 frame payload.

        Returns:
            values: channel_index -> decoded Float32 value.
            rle_counts: channel_index -> consecutive run length (1 = unique).

        Delta encoding is NOT active in v1. The firmware sends full Float32 values
        over the wire, not deltas. Each frame decodes to its raw value as-is.
        The _delta_state dict is present for future use when firmware is updated
        to encode slowly-changing signals (e.g. MRAC weights) as deltas, which
        would halve wire cost for those signals. When that firmware lands, add
        a wire_delta=True flag to DecodePipeline.__init__ and re-enable
        decoded = self._delta_state[ch] + float(raw_val) here.
        """
        values: dict[int, float] = {}
        rle_counts: dict[int, int] = {}

        ch = 0
        offset = 0
        for rng in self.schema.ranges:
            # nbytes = wire frame bytes per range (8 * count, always).
            # The actual data is rng.size * rng.count bytes (e.g. 4 for float32).
            # Slice only the data portion; advance offset by the wire frame size.
            data_bytes = rng.size * rng.count
            raw = raw_payload[offset:offset + data_bytes]
            offset += rng.nbytes  # wire frame advance

            # Unpack per-element: struct doesn't expand count in format strings.
            # For 4-byte elements we use '<f' per element to avoid ambiguity with
            # the struct count-prefix convention (e.g. '<4f' is 4 floats, not 1).
            if rng.size == 4:
                vals: list[float] = []
                for i in range(rng.count):
                    vals.append(struct.unpack("<f", raw[i * 4:(i + 1) * 4])[0])
            elif rng.size == 2:
                raw_vals = struct.unpack(f"<{rng.count}H", raw)
                vals = [float(v) for v in raw_vals]
            else:
                raw_vals = struct.unpack(f"<{rng.count}B", raw)
                vals = [float(v) for v in raw_vals]

            for i in range(rng.count):
                raw_val = vals[i]
                precision = self.precision_map.get(ch, "float32")

                # Apply precision cast for float16: uint16 bit-pattern -> IEEE-754 float16
                if precision == "float16":
                    raw_val = _float16_to_float32(int(raw_val))

                # Counter: unpacked as uint32 in wire format (4 bytes, unsigned little-endian).
                # Skip float conversion entirely for these — raw_val from '<f' unpack is garbage.
                if precision == "counter":
                    decoded = float(int.from_bytes(
                        raw[i * 4:(i + 1) * 4], "little"))
                else:
                    decoded = float(raw_val)

                # RLE tracking — prev_val always comes from _rle_state, never ch not in dict
                prev_val: float
                prev_count: int
                entry = self._rle_state.get(ch)
                if entry is not None:
                    prev_val, prev_count = entry
                else:
                    prev_val, prev_count = decoded, 0
                if abs(decoded - prev_val) < 1e-9:
                    prev_count += 1
                else:
                    prev_count = 1
                    prev_val = decoded
                self._rle_state[ch] = (prev_val, prev_count)
                rle_counts[ch] = prev_count

                values[ch] = decoded
                ch += 1

        return values, rle_counts

    def reset(self) -> None:
        """Reset delta and RLE state. Call on session start."""
        self._delta_state.clear()
        self._rle_state.clear()
