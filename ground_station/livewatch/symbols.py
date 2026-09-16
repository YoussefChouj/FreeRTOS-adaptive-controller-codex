"""DWARF-backed symbol resolver.

Turns a dotted/indexed name like  "s_ekf.x[3]"  or  "mrac_state.roll.What[0]"
into a concrete (address, C type) by walking the firmware ELF's DWARF info.
No hardware needed - this is pure static analysis of OBJ/JX_FLY.axf.

Why DWARF and not the .map: the .map only lists *base* symbols (s_ekf @ 0x...cb4,
572 B). Field offsets and array-element sizes come from the type info, which only
DWARF carries. Resolving from DWARF means the offsets stay correct across rebuilds
automatically - no hand-maintained tables to drift.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from elftools.elf.elffile import ELFFile

# DWARF DW_ATE_* base-type encodings -> struct format char, keyed by (encoding, size)
_ENCODING = {
    (0x02, 1): "?",  # boolean
    (0x04, 4): "f",  # float
    (0x04, 8): "d",  # double
    (0x05, 1): "b", (0x05, 2): "h", (0x05, 4): "i", (0x05, 8): "q",  # signed
    (0x07, 1): "B", (0x07, 2): "H", (0x07, 4): "I", (0x07, 8): "Q",  # unsigned
    (0x06, 1): "b",  # signed char
    (0x08, 1): "B",  # unsigned char
}


@dataclass(frozen=True)
class Symbol:
    """A fully resolved leaf (scalar) or aggregate the watcher can read."""
    name: str          # canonical name as requested, e.g. "s_ekf.x[3]"
    address: int       # absolute target address
    size: int          # bytes
    fmt: str | None    # struct format char for a scalar leaf, else None (aggregate/opaque)

    @property
    def is_scalar(self) -> bool:
        return self.fmt is not None

    def decode(self, raw: bytes) -> float | int | bool:
        if self.fmt is None:
            raise ValueError(f"{self.name} is an aggregate ({self.size} B); read its fields")
        return struct.unpack("<" + self.fmt, raw[: struct.calcsize(self.fmt)])[0]


class _Type:
    """Minimal resolved-type view: total size, and how to interpret it."""
    __slots__ = ("die", "size", "fmt", "kind")

    def __init__(self, die, size, fmt, kind):
        self.die = die      # underlying (typedef/qualifier-stripped) DIE, or None
        self.size = size    # total bytes
        self.fmt = fmt      # scalar format char, else None
        self.kind = kind    # 'scalar' | 'struct' | 'array' | 'pointer' | 'opaque'


class SymbolResolver:
    """Resolves dotted/indexed variable paths against a firmware ELF's DWARF."""

    def __init__(self, elf_path: str | Path):
        self.elf_path = Path(elf_path)
        self._f = open(self.elf_path, "rb")
        elf = ELFFile(self._f)
        if not elf.has_dwarf_info():
            raise ValueError(f"{elf_path} has no DWARF debug info")
        self._dwarf = elf.get_dwarf_info()
        self._var_index: dict[str, object] = {}   # name -> variable DIE
        self._build_var_index()

    # ---- public API -----------------------------------------------------

    def resolve(self, path: str) -> Symbol:
        """Resolve 's_ekf.x[3]' -> Symbol(address, size, fmt)."""
        base, steps = _parse_path(path)
        die = self._var_index.get(base)
        if die is None:
            raise KeyError(f"unknown symbol {base!r} "
                           f"(not a global/static variable in {self.elf_path.name})")
        addr = _var_address(die)
        typ = self._resolve_type(die.get_DIE_from_attribute("DW_AT_type"))
        for step in steps:
            addr, typ = self._apply_step(addr, typ, step, base)
        return Symbol(path, addr, typ.size, typ.fmt)

    def names(self) -> list[str]:
        """All resolvable base variable names (for GUI dropdowns / registry building)."""
        return sorted(self._var_index)

    def fields_of(self, path: str) -> list[str]:
        """Immediate member/element names under a struct or array path (for name pickers)."""
        base, steps = _parse_path(path)
        die = self._var_index.get(base)
        if die is None:
            raise KeyError(base)
        typ = self._resolve_type(die.get_DIE_from_attribute("DW_AT_type"))
        addr = _var_address(die)
        for step in steps:
            addr, typ = self._apply_step(addr, typ, step, base)
        if typ.kind == "struct":
            out = []
            for m in typ.die.iter_children():
                if m.tag == "DW_TAG_member" and "DW_AT_name" in m.attributes:
                    out.append(m.attributes["DW_AT_name"].value.decode())
            return out
        if typ.kind == "array":
            n = _array_count(typ.die)
            return [f"[{i}]" for i in range(n)] if n is not None else []
        return []

    def close(self):
        self._f.close()

    # ---- internals ------------------------------------------------------

    def _build_var_index(self):
        # File-scope variables (globals + file statics) are direct CU children and
        # carry a DW_OP_addr location. Locals on a stack have no static address and
        # are intentionally skipped.
        for cu in self._dwarf.iter_CUs():
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
                # First definition wins; skip duplicate extern declarations.
                self._var_index.setdefault(name, die)

    def _resolve_type(self, die) -> _Type:
        # Strip typedef / const / volatile to the underlying type.
        while die is not None and die.tag in (
            "DW_TAG_typedef", "DW_TAG_const_type",
            "DW_TAG_volatile_type", "DW_TAG_restrict_type",
        ):
            nxt = die.attributes.get("DW_AT_type")
            if nxt is None:  # e.g. 'const void'
                return _Type(die, 0, None, "opaque")
            die = die.get_DIE_from_attribute("DW_AT_type")

        if die is None:
            return _Type(None, 0, None, "opaque")

        tag = die.tag
        if tag == "DW_TAG_base_type":
            enc = die.attributes["DW_AT_encoding"].value
            size = die.attributes["DW_AT_byte_size"].value
            return _Type(die, size, _ENCODING.get((enc, size)), "scalar")
        if tag == "DW_TAG_pointer_type":
            size = die.attributes.get("DW_AT_byte_size")
            size = size.value if size else 4
            return _Type(die, size, "I", "pointer")
        if tag == "DW_TAG_enumeration_type":
            size = die.attributes.get("DW_AT_byte_size")
            size = size.value if size else 4
            return _Type(die, size, {1: "b", 2: "h", 4: "i"}.get(size, "i"), "scalar")
        if tag == "DW_TAG_structure_type" or tag == "DW_TAG_union_type":
            size = die.attributes.get("DW_AT_byte_size")
            return _Type(die, size.value if size else 0, None, "struct")
        if tag == "DW_TAG_array_type":
            return _Type(die, _array_size(die, self), None, "array")
        return _Type(die, 0, None, "opaque")

    def _apply_step(self, addr, typ: _Type, step, base):
        if isinstance(step, str):  # .member
            if typ.kind != "struct":
                raise TypeError(f"{base}: '.{step}' but current type is {typ.kind}")
            member = _find_member(typ.die, step)
            if member is None:
                raise KeyError(f"{base}: no member '{step}'")
            off = member.attributes.get("DW_AT_data_member_location")
            off = _member_offset(off.value) if off else 0
            mtyp = self._resolve_type(member.get_DIE_from_attribute("DW_AT_type"))
            return addr + off, mtyp
        else:  # [index]
            if typ.kind != "array":
                raise TypeError(f"{base}: '[{step}]' but current type is {typ.kind}")
            elem = self._resolve_type(typ.die.get_DIE_from_attribute("DW_AT_type"))
            return addr + step * elem.size, elem


# ---- module-level DWARF helpers ----------------------------------------

def _parse_path(path: str):
    """'s_ekf.x[3]' -> ('s_ekf', ['x', 3])."""
    import re
    tokens = path.strip().split(".")
    base_tok = tokens[0]
    steps: list = []

    def emit(tok):
        m = re.match(r"^([A-Za-z_]\w*)((?:\[\d+\])*)$", tok)
        if not m:
            raise ValueError(f"bad path token {tok!r} in {path!r}")
        if m.group(1):
            steps.append(m.group(1))
        for idx in re.findall(r"\[(\d+)\]", m.group(2)):
            steps.append(int(idx))

    # base may itself carry indices, e.g. "buf[2].x"
    bm = re.match(r"^([A-Za-z_]\w*)((?:\[\d+\])*)$", base_tok)
    if not bm:
        raise ValueError(f"bad base symbol {base_tok!r}")
    base = bm.group(1)
    for idx in re.findall(r"\[(\d+)\]", bm.group(2)):
        steps.append(int(idx))
    for tok in tokens[1:]:
        emit(tok)
    return base, steps


def _is_addr_location(loc) -> bool:
    return len(loc) >= 1 and loc[0] == 0x03  # DW_OP_addr


def _var_address(die) -> int:
    loc = bytes(die.attributes["DW_AT_location"].value)
    if not _is_addr_location(loc):
        raise ValueError(f"{die.attributes['DW_AT_name'].value!r} has no static address")
    return struct.unpack_from("<I", loc, 1)[0]


def _member_offset(val) -> int:
    # DW_AT_data_member_location is usually a constant; occasionally a DW_OP_plus_uconst expr.
    if isinstance(val, int):
        return val
    b = bytes(val)
    if b and b[0] == 0x23:  # DW_OP_plus_uconst
        return _uleb(b, 1)
    return b[0] if b else 0


def _uleb(b, i=0) -> int:
    res = shift = 0
    while i < len(b):
        byte = b[i]; i += 1
        res |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    return res


def _find_member(struct_die, name):
    for m in struct_die.iter_children():
        if m.tag == "DW_TAG_member" and "DW_AT_name" in m.attributes:
            if m.attributes["DW_AT_name"].value.decode() == name:
                return m
    return None


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


def _array_size(array_die, resolver: SymbolResolver) -> int:
    elem = resolver._resolve_type(array_die.get_DIE_from_attribute("DW_AT_type"))
    total = elem.size
    for c in array_die.iter_children():
        if c.tag == "DW_TAG_subrange_type":
            if "DW_AT_count" in c.attributes:
                total *= c.attributes["DW_AT_count"].value
            elif "DW_AT_upper_bound" in c.attributes:
                ub = c.attributes["DW_AT_upper_bound"].value
                if isinstance(ub, int):
                    total *= ub + 1
    return total
