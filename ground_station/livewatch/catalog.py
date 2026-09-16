"""Runtime symbol catalog client.

Two paths to load a catalog:
1. Load from a cached JSON file (offline, no hardware).
2. Fetch from the FC by sending 0x25 (online).

The contract is in docs/subscribe-symbol-table-contract.md. The wire format
here mirrors the FC's GET_SYMBOL_TABLE reply exactly.
"""
from __future__ import annotations

import json
import socket
import struct
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from .transport import LiveTransportError, pop_frame

SUBSCRIBE_GET_CATALOG_CMD = 0x25
SUBSCRIBE_FRAME_TYPE_CATALOG = 0x25

# 0x25 reply wire layout (per docs/subscribe-symbol-table-contract.md):
#   [0xAA 0xBB 0x25] [LEN_HI LEN_LO] [firmware_id:uint32 BE] [n_entries:uint16 LE]
#                    [entries × 12 B] [name_table bytes] [CRC8-XOR]
#
# LEN counts everything from firmware_id to name_table inclusive (not the CRC).
ENTRY_SIZE = 12  # hash(2) + name_off(2) + name_len(1) + elem_type(1) + pad(1) + count(1) + address(4)

_ELEM_TYPE_SIZE = {
    0x00: 1,  # uint8
    0x01: 2,  # uint16
    0x02: 4,  # uint32
    0x03: 1,  # int8
    0x04: 2,  # int16
    0x05: 4,  # int32
    0x06: 4,  # float32
}

# Stride uses 'i' since wire is fixed little-endian.
_STRUCT_ENTRY = struct.Struct("<HHBBBBi")


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    hash: int          # 16-bit
    address: int       # 32-bit LE
    elem_type: int     # SUBSCRIBE_TYPE_*
    count: int
    size_bytes: int


@dataclass(frozen=True)
class SymbolCatalog:
    """Loaded catalog of publishable symbols. Use the helpers below."""
    firmware_id: int
    elf_sha256: str
    generated_at: str
    entries: tuple[CatalogEntry, ...]

    def by_name(self, name: str) -> CatalogEntry | None:
        for e in self.entries:
            if e.name == name:
                return e
        return None

    def by_hash(self, h: int) -> list[CatalogEntry]:
        return [e for e in self.entries if e.hash == h]

    def by_prefix(self, prefix: str) -> list[CatalogEntry]:
        return [e for e in self.entries if e.name.startswith(prefix)]

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def matches_firmware_id(self, other_id: int) -> bool:
        return self.firmware_id == other_id

    def save(self, json_path: Path) -> None:
        payload = {
            "firmware_id": f"0x{self.firmware_id:08X}",
            "elf_sha256": self.elf_sha256,
            "generated_at": self.generated_at,
            "entry_count": len(self.entries),
            "entries": [asdict(e) | {
                "hash": f"0x{e.hash:04X}",
                "address": f"0x{e.address:08X}",
            } for e in self.entries],
        }
        tmp = json_path.with_suffix(json_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(json_path)

    @classmethod
    def load(cls, json_path: Path) -> 'SymbolCatalog':
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        entries = []
        for e in raw["entries"]:
            entries.append(CatalogEntry(
                name=e["name"],
                hash=int(e["hash"], 16) if isinstance(e["hash"], str) else e["hash"],
                address=int(e["address"], 16) if isinstance(e["address"], str) else e["address"],
                elem_type=e["elem_type"],
                count=e["count"],
                size_bytes=e["size_bytes"],
            ))
        return cls(
            firmware_id=int(raw["firmware_id"], 16),
            elf_sha256=raw.get("elf_sha256", ""),
            generated_at=raw.get("generated_at", ""),
            entries=tuple(entries),
        )


# ---- wire (online fetch) -------------------------------------------------

def _xor_crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc & 0xFF


def build_get_catalog_request(local_port: int = 14550) -> bytes:
    """Build the 0xCC 0xDD 0x25 0x00 0x00 CRC request.

    Wire: [0xCC 0xDD] [0x25] [LEN_HI=0] [LEN_LO=0] [CRC8 XOR of CMD..LEN].
    """
    body = bytes([0xCC, 0xDD, SUBSCRIBE_GET_CATALOG_CMD, 0x00, 0x00])
    return body + bytes([_xor_crc8(body)])


def fetch_catalog(
    module_ip: str = "192.168.4.1",
    local_port: int = 14550,
    timeout_s: float = 3.0,
) -> SymbolCatalog:
    """Open a UDP socket, send 0x25, await 0xAA 0xBB 0x25 reply, parse it.

    Returns a SymbolCatalog on success. Raises LiveTransportError on any
    parse failure or timeout.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65535)
    sock.bind(("0.0.0.0", local_port))

    try:
        # MicoAir requires a nudge byte to register our source address.
        sock.sendto(b"\x00", (module_ip, 14550))
        time.sleep(0.05)
        sock.sendto(build_get_catalog_request(), (module_ip, 14550))

        rx = bytearray()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                if rx:
                    break
                continue
            rx.extend(data)
            # Look for one complete 0xAA 0xBB 0x25 frame.
            while True:
                frame = pop_frame(rx)
                if frame is None:
                    break
                ftype, byte5, payload = frame
                if ftype == 0x7F:
                    msg = payload.decode("utf-8", errors="replace").rstrip("\x00")
                    raise LiveTransportError(f"FC rejected 0x25: {msg}")
                if ftype != SUBSCRIBE_FRAME_TYPE_CATALOG:
                    continue
                return _parse_catalog_reply(payload, byte5)
        raise LiveTransportError("no 0x25 reply within timeout")
    finally:
        sock.close()


def _parse_catalog_reply(payload: bytes, byte5_unused: int) -> SymbolCatalog:
    """Parse the 0x25 reply payload per the contract.

    Layout: [firmware_id BE:4] [n_entries LE:2] [entries ×12] [name_table] (no CRC).
    byte5 is unused on this frame type (frame uses payload layout, not byte5).
    """
    if len(payload) < 6:
        raise LiveTransportError(
            f"catalog reply payload {len(payload)} B, need >=6 for header")
    firmware_id = struct.unpack_from(">I", payload, 0)[0]
    n_entries = struct.unpack_from("<H", payload, 4)[0]
    entries_start = 6
    entries_end = entries_start + n_entries * ENTRY_SIZE
    if len(payload) < entries_end:
        raise LiveTransportError(
            f"catalog reply: header claims {n_entries} entries "
            f"({entries_end} B) but payload is {len(payload)} B")
    name_table = payload[entries_end:]

    entries: list[CatalogEntry] = []
    for i in range(n_entries):
        rec = _STRUCT_ENTRY.unpack_from(payload, entries_start + i * ENTRY_SIZE)
        hash_, name_off, name_len, elem_type, _pad, count, address = rec
        # Treat as unsigned
        if address < 0:
            address += 1 << 32
        if not (0 <= name_off < len(name_table)):
            raise LiveTransportError(
                f"catalog entry {i}: name_off {name_off} outside "
                f"name_table (len {len(name_table)})")
        end = name_off + name_len
        if end > len(name_table):
            raise LiveTransportError(
                f"catalog entry {i}: name_off+name_len {end} > "
                f"name_table (len {len(name_table)})")
        name = name_table[name_off:end].decode("utf-8")
        size_bytes = _ELEM_TYPE_SIZE.get(elem_type, 0) * count
        entries.append(CatalogEntry(
            name=name, hash=hash_, address=address,
            elem_type=elem_type, count=count, size_bytes=size_bytes,
        ))

    return SymbolCatalog(
        firmware_id=firmware_id,
        elf_sha256="",
        generated_at="",
        entries=tuple(entries),
    )


def fetch_or_load(
    json_path: Path,
    *,
    force_refresh: bool = False,
    module_ip: str = "192.168.4.1",
    timeout_s: float = 3.0,
) -> SymbolCatalog:
    """Smart fetch: prefer cached JSON; fetch from FC and refresh cache on
    firmware_id mismatch (or when JSON is absent)."""
    json_path = Path(json_path)
    if json_path.exists() and not force_refresh:
        cached = SymbolCatalog.load(json_path)
    else:
        cached = None

    live = fetch_catalog(module_ip=module_ip, timeout_s=timeout_s)

    if cached is not None and cached.firmware_id == live.firmware_id:
        return cached

    # Firmware was reflashed (or first fetch). Persist the new catalog.
    live.elf_sha256 = cached.elf_sha256 if cached else ""
    live.generated_at = ""  # filled by save
    json_path.parent.mkdir(parents=True, exist_ok=True)
    # Build a fresh catalog with the live firmware_id and persisted metadata
    refreshed = SymbolCatalog(
        firmware_id=live.firmware_id,
        elf_sha256=cached.elf_sha256 if cached else "",
        generated_at=live.generated_at or "",
        entries=live.entries,
    )
    refreshed.save(json_path)
    return refreshed