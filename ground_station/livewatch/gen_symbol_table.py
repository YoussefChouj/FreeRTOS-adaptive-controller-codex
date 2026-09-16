"""Walk OBJ/JX_FLY.axf's DWARF and emit two catalogs:

  firmware/symbol_table_gen.h   - C header the firmware compiles in
  ground_station/livewatch/symbol_catalog.json - JSON for the host

Both share the same firmware_id (32-bit). The host fetches the live catalog
from the FC at session start (0x25 GET_SYMBOL_TABLE) and verifies the
firmware_id matches the cached JSON; a mismatch means the FC was reflashed
and the host must regenerate.

Default scope (publishable symbols):
  - top-level globals whose type is a scalar or fixed-size array
  - struct members reachable from those globals (recursive, cycle-safe)
  - excludes function pointers, char[] buffers, ranges outside SRAM/CCM

Wire format reference: docs/subscribe-symbol-table-contract.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from elftools.elf.elffile import ELFFile

# ---- shared with symbols.py (duplicated to keep this module standalone) ---
_ENCODING = {
    (0x04, 4): 0x06,  # float  -> SUBSCRIBE_TYPE_FLOAT32
    (0x04, 8): 0xFF,  # double -> reserved (firmware doesn't pack)
    (0x05, 1): 0x03, (0x05, 2): 0x04, (0x05, 4): 0x05, (0x05, 8): 0xFF,
    (0x07, 1): 0x00, (0x07, 2): 0x01, (0x07, 4): 0x02, (0x07, 8): 0xFF,
    (0x06, 1): 0x03, (0x08, 1): 0x00,
}

# SRAM + CCM regions (mirrors SUBSCRIBE_ADDR_SRAM_LO/HI, SUBSCRIBE_ADDR_CCM_LO/HI)
_REGIONS = [
    (0x20000000, 0x2001FFFF, "SRAM"),
    (0x10000000, 0x1000FFFF, "CCM"),
]


# ---- data model ----------------------------------------------------------

@dataclass(frozen=True)
class PublishEntry:
    name: str          # e.g. "Ctrler.rollPID.Des"
    hash: int          # 16-bit FNV-1a
    address: int       # 32-bit
    elem_type: int     # SUBSCRIBE_TYPE_*
    count: int         # array length, 1 for scalar
    size_bytes: int    # count * elem_size(elem_type)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "hash": f"0x{self.hash:04X}",
            "address": f"0x{self.address:08X}",
            "elem_type": self.elem_type,
            "count": self.count,
            "size_bytes": self.size_bytes,
        }


# ---- FNV-1a 16-bit (matches firmware reference) ---------------------------

def name_hash(s: str) -> int:
    h = 0x811C
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x0101) & 0xFFFF
    return h


# ---- DWARF walking -------------------------------------------------------

def _is_addr_location(loc) -> bool:
    return len(loc) >= 1 and loc[0] == 0x03


def _var_address(die) -> int:
    loc = bytes(die.attributes["DW_AT_location"].value)
    return struct.unpack_from("<I", loc, 1)[0]


def _member_offset(val) -> int:
    if isinstance(val, int):
        return val
    b = bytes(val)
    if b and b[0] == 0x23:
        n = 0
        shift = 0
        for byte in b[1:]:
            n |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
        return n
    return b[0] if b else 0


def _array_count(array_die):
    for c in array_die.iter_children():
        if c.tag == "DW_TAG_subrange_type":
            if "DW_AT_count" in c.attributes:
                return c.attributes["DW_AT_count"].value
            if "DW_AT_upper_bound" in c.attributes:
                ub = c.attributes["DW_AT_upper_bound"].value
                if isinstance(ub, int):
                    return ub + 1
    return None


def _resolve_type(die, resolver):
    """Strip typedef/qualifier to the underlying type."""
    while die is not None and die.tag in (
        "DW_TAG_typedef", "DW_TAG_const_type",
        "DW_TAG_volatile_type", "DW_TAG_restrict_type",
    ):
        nxt = die.attributes.get("DW_AT_type")
        if nxt is None:
            return {"kind": "opaque", "size": 0, "fmt": None, "die": None}
        die = die.get_DIE_from_attribute("DW_AT_type")
    if die is None:
        return {"kind": "opaque", "size": 0, "fmt": None, "die": None}
    tag = die.tag
    if tag == "DW_TAG_base_type":
        enc = die.attributes["DW_AT_encoding"].value
        size = die.attributes["DW_AT_byte_size"].value
        return {"kind": "scalar", "size": size, "fmt": _ENCODING.get((enc, size)), "die": die}
    if tag == "DW_TAG_enumeration_type":
        size = die.attributes.get("DW_AT_byte_size")
        size = size.value if size else 4
        return {"kind": "scalar", "size": size, "fmt": {1: 0x03, 2: 0x04, 4: 0x05}.get(size), "die": die}
    if tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
        size = die.attributes.get("DW_AT_byte_size")
        return {"kind": "struct", "size": size.value if size else 0, "fmt": None, "die": die}
    if tag == "DW_TAG_array_type":
        elem = _resolve_type(die.get_DIE_from_attribute("DW_AT_type"), resolver)
        total = elem["size"]
        for c in die.iter_children():
            if c.tag == "DW_TAG_subrange_type":
                if "DW_AT_count" in c.attributes:
                    total *= c.attributes["DW_AT_count"].value
                elif "DW_AT_upper_bound" in c.attributes:
                    ub = c.attributes["DW_AT_upper_bound"].value
                    if isinstance(ub, int):
                        total *= ub + 1
        return {"kind": "array", "size": total, "fmt": None, "die": die}
    if tag == "DW_TAG_pointer_type":
        return {"kind": "pointer", "size": 4, "fmt": None, "die": die}
    return {"kind": "opaque", "size": 0, "fmt": None, "die": die}


def _in_publish_region(addr: int) -> bool:
    return any(lo <= addr <= hi for lo, hi, _ in _REGIONS)


# ---- main walker ---------------------------------------------------------

def _publish_recursive(
    resolver,
    name: str,
    addr: int,
    typ: dict,
    seen_structs: set,
    out: list,
    max_array: int = 4096,
    struct_publish_mode: str = "fields",
):
    """Append one or more PublishEntry to `out` for this type.

    struct_publish_mode:
      "fields" - recurse into struct members, publish each leaf (default)
      "whole"  - publish the whole struct as one entry with size=byte_size,
                 count=1. Use this when the catalog cap is small (<= ~80).
                 The host can read the whole struct and decode by offset.

    - scalar at addr: 1 entry, count=1
    - array of scalar: 1 entry, count=N
    - struct: recurse or publish whole per `struct_publish_mode`
              (avoid cycles via seen_structs)
    - anything else: skip silently
    """
    if typ["kind"] == "scalar" and typ["fmt"] is not None and 1 <= typ["size"] <= 4:
        out.append(PublishEntry(
            name=name,
            hash=name_hash(name),
            address=addr,
            elem_type=typ["fmt"],
            count=1,
            size_bytes=typ["size"],
        ))
        return

    if typ["kind"] == "array":
        elem = _resolve_type(typ["die"].get_DIE_from_attribute("DW_AT_type"), resolver)
        if elem["kind"] != "scalar" or elem["fmt"] is None or not (1 <= elem["size"] <= 4):
            return
        n = _array_count(typ["die"])
        if n is None or n <= 0 or n > max_array:
            return
        out.append(PublishEntry(
            name=name,
            hash=name_hash(name),
            address=addr,
            elem_type=elem["fmt"],
            count=n,
            size_bytes=elem["size"] * n,
        ))
        return

    if typ["kind"] == "struct":
        struct_die = typ["die"]
        # Big structs (>= 8 B) are published as a single byte-array entry:
        # (name, elem_type=UINT8, count=struct_size_bytes). The host decodes
        # the struct bytes using DWARF-derived offsets (see SymbolResolver).
        # This collapses 14-member structs into one entry, keeping the catalog
        # compact (v1 single-frame constraint: <= ~60 entries on the wire).
        if typ["size"] > 0 and struct_publish_mode == "fields" and typ["size"] <= 255:
            out.append(PublishEntry(
                name=name,
                hash=name_hash(name),
                address=addr,
                elem_type=0x00,           # SUBSCRIBE_TYPE_UINT8
                count=typ["size"],
                size_bytes=typ["size"],
            ))
            return
        # Otherwise fall back to whole-blob or recursive depending on mode.
        if struct_publish_mode == "whole":
            out.append(PublishEntry(
                name=name,
                hash=name_hash(name),
                address=addr,
                elem_type=0x00,
                count=typ["size"] if typ["size"] <= 255 else 255,
                size_bytes=typ["size"],
            ))
            return
        if struct_die.offset in seen_structs:
            return
        seen_structs = seen_structs | {struct_die.offset}
        for member in struct_die.iter_children():
            if member.tag != "DW_TAG_member":
                continue
            if "DW_AT_name" not in member.attributes:
                continue
            member_name = member.attributes["DW_AT_name"].value.decode()
            off_at = member.attributes.get("DW_AT_data_member_location")
            off = _member_offset(off_at.value) if off_at else 0
            mtyp = _resolve_type(member.get_DIE_from_attribute("DW_AT_type"), resolver)
            full_name = f"{name}.{member_name}"
            _publish_recursive(resolver, full_name, addr + off, mtyp, seen_structs, out, struct_publish_mode=struct_publish_mode)


def walk_elf(elf_path: Path, include: list, exclude: list) -> list[PublishEntry]:
    """Return all publishable entries, deduped and filtered."""
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            raise SystemExit(f"{elf_path}: no DWARF debug info")
        dwarf = elf.get_dwarf_info()

    out: list[PublishEntry] = []
    seen: set[tuple[str, int]] = set()

    include_pat = [re.compile(p) for p in include]
    exclude_pat = [re.compile(p) for p in exclude]

    for cu in dwarf.iter_CUs():
        top = cu.get_top_DIE()
        for die in top.iter_children():
            if die.tag != "DW_TAG_variable":
                continue
            name_at = die.attributes.get("DW_AT_name")
            loc = die.attributes.get("DW_AT_location")
            if not name_at or not loc:
                continue
            if not _is_addr_location(loc.value):
                continue
            name = name_at.value.decode()
            addr = _var_address(die)
            if not _in_publish_region(addr):
                continue
            if include_pat and not any(p.search(name) for p in include_pat):
                continue
            if exclude_pat and any(p.search(name) for p in exclude_pat):
                continue
            typ = _resolve_type(die.get_DIE_from_attribute("DW_AT_type"), None)
            _publish_recursive(None, name, addr, typ, set(), out)

    # Stable order: by address, then by name
    out.sort(key=lambda e: (e.address, e.name))
    # Final dedup: drop any (name, address) pairs that snuck in twice.
    seen_keys: set = set()
    deduped: list = []
    for e in out:
        key = (e.name, e.address)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(e)
    return deduped


# ---- firmware_id ---------------------------------------------------------

def compute_firmware_id(elf_path: Path) -> int:
    """First 4 bytes of SHA256(elf_path) interpreted big-endian as uint32.

    Mirrors what gen runs would emit identically across hosts because the
    input is the ELF bytes.
    """
    h = hashlib.sha256()
    h.update(elf_path.read_bytes())
    return struct.unpack(">I", h.digest()[:4])[0]


# ---- emit ----------------------------------------------------------------

_C_HEADER_TEMPLATE = """\
/* AUTO-GENERATED -- do not hand-edit.
 * Regenerate with:
 *   python -m ground_station.livewatch.gen_symbol_table \\
 *       --elf OBJ/JX_FLY.axf \\
 *       --out-header firmware/symbol_table_gen.h \\
 *       --out-catalog ground_station/livewatch/symbol_catalog.json
 *
 * Build id (firmware_id): 0x{firmware_id:08X}
 * ELF SHA256: {elf_sha256}
 * Generated:  {timestamp}
 * Entries:    {n_entries}
 */

#ifndef SUBSCRIBE_SYMBOL_TABLE_GEN_H
#define SUBSCRIBE_SYMBOL_TABLE_GEN_H

#define SUBSCRIBE_SYMBOLS_COUNT     {n_entries}U
#define SUBSCRIBE_FIRMWARE_ID       0x{firmware_id:08X}U
#define SUBSCRIBE_NAMES_POOL_LEN    {pool_len}U

/* The host and firmware agree on this format (see docs/subscribe-symbol-table-contract.md).
 * Each entry is 12 bytes packed, LE:
 *   uint16_t hash;        /* FNV-1a 16-bit of the UTF-8 name */
 *   uint16_t name_off;    /* offset into g_subscribe_symbol_names */
 *   uint8_t  name_len;    /* bytes for this name (excluding NUL) */
 *   uint8_t  elem_type;   /* SUBSCRIBE_TYPE_* */
 *   uint8_t  _pad;
 *   uint8_t  count;       /* array length; 1 for scalar */
 *   uint32_t address;     /* LE */
 */
extern const Subscribe_SymbolEntry_t g_subscribe_symbols[SUBSCRIBE_SYMBOLS_COUNT];
extern const char  g_subscribe_symbol_names[SUBSCRIBE_NAMES_POOL_LEN];
extern const uint32_t g_subscribe_symbol_names_len;

#endif /* SUBSCRIBE_SYMBOL_TABLE_GEN_H */
"""


def emit_header(entries: list[PublishEntry], firmware_id: int, elf_sha256: str,
                timestamp: str, path: Path) -> None:
    pool = bytearray()
    for e in entries:
        # ensure NUL-terminated
        pool.extend(e.name.encode("utf-8"))
        pool.append(0)
    body = _C_HEADER_TEMPLATE.format(
        firmware_id=firmware_id,
        elf_sha256=elf_sha256,
        timestamp=timestamp,
        n_entries=len(entries),
        pool_len=len(pool),
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def emit_c_data(entries: list[PublishEntry], path: Path) -> None:
    """Emit the symbol_table_gen.c with the actual data arrays.

    The host runs gen to refresh both .h and .c. The .c is what the firmware
    links; the .h is the contract.

    Format: 12-byte packed structs in flash, contiguous NUL-terminated names.
    """
    pool = bytearray()
    # Pre-compute offsets and ensure entries reference them correctly
    rows = []
    for e in entries:
        name_off = len(pool)
        pool.extend(e.name.encode("utf-8"))
        pool.append(0)
        rows.append((e, name_off, len(e.name.encode("utf-8"))))

    lines = [
        "/* AUTO-GENERATED -- see firmware/symbol_table_gen.h for the contract. */",
        f"/* {len(entries)} entries, name pool {len(pool)} B. */",
        "#include \"subscribe.h\"",
        "#include \"symbol_table_gen.h\"",
        "",
        f"const Subscribe_SymbolEntry_t g_subscribe_symbols[SUBSCRIBE_SYMBOLS_COUNT] = {{",
    ]
    for (e, off, nlen) in rows:
        lines.append(
            f"    {{ 0x{e.hash:04X}U, 0x{off:04X}U, {nlen}U, "
            f"0x{e.elem_type:02X}U, 0U, {e.count}U, 0x{e.address:08X}U }},"
        )
    lines.append("};")
    lines.append("")
    lines.append(
        f"const uint32_t g_subscribe_symbol_names_len = {len(pool)}U;"
    )
    lines.append(
        f"const char g_subscribe_symbol_names[SUBSCRIBE_NAMES_POOL_LEN] = {{"
    )
    # Emit the pool as a comma-separated hex byte list (split into 16-B rows
    # for readability).
    for i in range(0, len(pool), 16):
        chunk = pool[i:i + 16]
        lines.append("    " + ", ".join(f"0x{b:02X}" for b in chunk) + ",")
    lines.append("};")
    lines.append("")

    body = "\n".join(lines)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def emit_catalog_json(entries: list[PublishEntry], firmware_id: int,
                      elf_sha256: str, timestamp: str,
                      path: Path) -> None:
    payload = {
        "firmware_id": f"0x{firmware_id:08X}",
        "elf_sha256": elf_sha256,
        "generated_at": timestamp,
        "entry_count": len(entries),
        "entries": [e.to_json() for e in entries],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


# ---- CLI -----------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--elf", default="OBJ/JX_FLY.axf", type=Path)
    p.add_argument("--out-header", default="firmware/symbol_table_gen.h", type=Path)
    p.add_argument("--out-data", default="firmware/symbol_table_gen.c", type=Path)
    p.add_argument("--out-catalog", default="ground_station/livewatch/symbol_catalog.json", type=Path)
    p.add_argument("--include", action="append", default=[],
                   help="regex; symbol name must match one (default: any)")
    p.add_argument("--exclude", action="append", default=[],
                   help="regex; skip symbol if name matches")
    p.add_argument("--max-entries", type=int, default=60,
                   help="firmware-side cap; see SUBSCRIBE_CATALOG_MAX")
    p.add_argument("--dry-run", action="store_true",
                   help="print summary; don't write files")
    args = p.parse_args(argv)

    if not args.elf.exists():
        print(f"ELF not found: {args.elf}", file=sys.stderr)
        return 2

    entries = walk_elf(args.elf, args.include, args.exclude)
    if len(entries) > args.max_entries:
        print(
            f"warning: {len(entries)} entries exceed --max-entries {args.max_entries}; "
            "truncating by (region, name). Re-run with --include to narrow.",
            file=sys.stderr,
        )
        entries = entries[: args.max_entries]

    firmware_id = compute_firmware_id(args.elf)
    elf_sha256 = hashlib.sha256(args.elf.read_bytes()).hexdigest()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if args.dry_run:
        print(f"firmware_id: 0x{firmware_id:08X}")
        print(f"elf_sha256:  {elf_sha256}")
        print(f"entries:     {len(entries)}")
        for e in entries[:20]:
            print(f"  {e.name:48s} 0x{e.address:08X} type=0x{e.elem_type:02X} count={e.count}")
        if len(entries) > 20:
            print(f"  ... ({len(entries) - 20} more)")
        return 0

    args.out_header.parent.mkdir(parents=True, exist_ok=True)
    args.out_data.parent.mkdir(parents=True, exist_ok=True)
    args.out_catalog.parent.mkdir(parents=True, exist_ok=True)

    emit_header(entries, firmware_id, elf_sha256, timestamp, args.out_header)
    emit_c_data(entries, args.out_data)
    emit_catalog_json(entries, firmware_id, elf_sha256, timestamp, args.out_catalog)

    print(f"wrote {args.out_header} ({args.out_header.stat().st_size} B)")
    print(f"wrote {args.out_data} ({args.out_data.stat().st_size} B)")
    print(f"wrote {args.out_catalog} ({args.out_catalog.stat().st_size} B)")
    print(f"firmware_id: 0x{firmware_id:08X}, entries: {len(entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())