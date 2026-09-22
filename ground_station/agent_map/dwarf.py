"""DWARF extraction over OBJ/JX_FLY.axf.

Independently walks the DWARF debug info to index top-level variables
(``DW_TAG_variable`` with a static address) and functions (``DW_TAG_subprogram``
with a ``DW_AT_low_pc``). Each yields {address, type, size, file, line} so the
build can join firmware names by their C name. This mirrors
``ground_station/livewatch/symbols.py``'s variable resolver without modifying
it (it only covers variables, not functions, and does not expose type names).
"""
from __future__ import annotations

from pathlib import Path

from elftools.elf.elffile import ELFFile

_STRUCT_TAGS = {"DW_TAG_structure_type", "DW_TAG_union_type"}


def _type_name(die, dwarf, seen=None) -> str:
    """Best-effort C type name for a type DIE (typedef shadowing -> name)."""
    seen = seen if seen is not None else set()
    anchor = die
    fallback = None
    while die is not None:
        if id(die.offset) in seen:
            return fallback or "unknown"
        seen.add(id(die.offset))
        tag = die.tag
        if tag == "DW_TAG_typedef":
            if "DW_AT_name" in die.attributes:
                fallback = die.attributes["DW_AT_name"].value.decode()
            die = _attr_type(die, dwarf)
            continue
        if tag in ("DW_TAG_const_type", "DW_TAG_volatile_type",
                   "DW_TAG_restrict_type"):
            die = _attr_type(die, dwarf)
            continue
        if tag == "DW_TAG_pointer_type":
            base = _type_name(die.get_DIE_from_attribute("DW_AT_type")
                              if "DW_AT_type" in die.attributes else None,
                              dwarf, seen)
            return f"{base} *"
        if tag == "DW_TAG_array_type":
            base = _type_name(
                die.get_DIE_from_attribute("DW_AT_type")
                if "DW_AT_type" in die.attributes else None, dwarf, seen)
            return f"{base}[]"
        if "DW_AT_name" in die.attributes:
            return die.attributes["DW_AT_name"].value.decode()
        if tag in _STRUCT_TAGS:
            return "struct"
        if tag == "DW_TAG_base_type":
            return "int" if fallback is None else fallback
        return fallback or tag.split("_")[-1]
    fallback = _type_name(anchor.get_DIE_from_attribute("DW_AT_type")
                          if anchor is not None and
                             "DW_AT_type" in anchor.attributes else None,
                          dwarf, seen)
    return fallback or "unknown"


def _attr_type(die, dwarf):
    if "DW_AT_type" in die.attributes:
        return die.get_DIE_from_attribute("DW_AT_type")
    return None


def _die_size(die, dwarf, seen=None) -> int:
    seen = seen if seen is not None else set()
    if die is None or id(die.offset) in seen:
        return 0
    seen.add(id(die.offset))
    bs = die.attributes.get("DW_AT_byte_size")
    if bs is not None:
        return int(bs.value)
    if "DW_AT_type" in die.attributes:
        return _die_size(die.get_DIE_from_attribute("DW_AT_type"), dwarf, seen)
    if die.tag == "DW_TAG_array_type":
        elem = die.get_DIE_from_attribute("DW_AT_type") if "DW_AT_type" in die.attributes else None
        es = _die_size(elem, dwarf, seen)
        count = 0
        for c in die.iter_children():
            if c.tag == "DW_TAG_subrange_type":
                if "DW_AT_count" in c.attributes:
                    count = max(count, int(c.attributes["DW_AT_count"].value))
                elif "DW_AT_upper_bound" in c.attributes:
                    ub = c.attributes["DW_AT_upper_bound"].value
                    if isinstance(ub, int):
                        count = max(count, ub + 1)
        return es * (count if count else 1)
    return 0


def _static_addr(loc) -> int | None:
    if loc is None:
        return None
    b = bytes(loc.value)
    if b and b[0] == 0x03:  # DW_OP_addr
        return int.from_bytes(b[1:5], "little")
    return None


def read_dwarf(elf_path: str | Path):
    """Return (variables, functions): two dicts name -> {address,type,size,
    file,line}."""
    variables: dict[str, dict] = {}
    functions: dict[str, dict] = {}
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return variables, functions
        dwarf = elf.get_dwarf_info()
        for cu in dwarf.iter_CUs():
            top = cu.get_top_DIE()
            for die in top.iter_children():
                if die.tag == "DW_TAG_variable":
                    name = die.attributes.get("DW_AT_name")
                    if name is None:
                        continue
                    addr = _static_addr(die.attributes.get("DW_AT_location"))
                    if addr is None:
                        continue
                    tdie = _attr_type(die, dwarf)
                    size = _die_size(tdie, dwarf)
                    variables.setdefault(name.value.decode(), {
                        "address": addr,
                        "type": _type_name(tdie, dwarf),
                        "size": size,
                        "file": die.get_full_path(),
                        "line": die.attributes.get(
                            "DW_AT_decl_line").value
                        if "DW_AT_decl_line" in die.attributes else None,
                    })
                elif die.tag == "DW_TAG_subprogram":
                    name = die.attributes.get("DW_AT_name")
                    low = die.attributes.get("DW_AT_low_pc")
                    if name is None or low is None:
                        continue
                    ret = _attr_type(die, dwarf) if "DW_AT_type" in die.attributes else None
                    functions.setdefault(name.value.decode(), {
                        "address": int(low.value),
                        "type": _type_name(ret, dwarf) if ret is not None else "void",
                        "size": 0,
                        "file": die.get_full_path(),
                        "line": die.attributes.get(
                            "DW_AT_decl_line").value
                        if "DW_AT_decl_line" in die.attributes else None,
                    })
    return variables, functions