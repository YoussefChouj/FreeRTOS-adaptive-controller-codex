"""Assemble docs/agent-map/agent_map.json (step 1 generator).

Each record is one firmware symbol (function, global or struct member):
name, kind, file, line, module, tier, dwarf{address,type,size}|null,
streamable[{preset,slot}]|null, panels[], glossary|null, writers[]|null.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .dwarf import read_dwarf
from .firmware import scan_firmware
from .glossary import load_glossary
from .modules import Modules
from .panels import load_panels
from .streamable import load_streamable
from .writers import _dedupe, load_writers
from .paths import ELF_PATH, OUT_PATH


def _dwarf_overlay(vars_: dict, funcs: dict) -> dict[str, dict]:
    out = {}
    for d in (funcs, vars_):
        for name, info in d.items():
            out[name] = info
    return out


def build() -> dict[str, Any]:
    symbols = scan_firmware()                       # name -> {kind,file,line}
    modules = Modules()
    glossary = load_glossary()
    streamable = load_streamable()
    panels = load_panels()
    panel_map: dict[str, list[str]] = {
        name: panels.panels_for(name) for name in symbols}
    writers = _dedupe(load_writers())

    dwarf_vars, dwarf_funcs = ({}, {})
    if ELF_PATH.exists():
        dwarf_vars, dwarf_funcs = read_dwarf(str(ELF_PATH))
    dwarf = _dwarf_overlay(dwarf_vars, dwarf_funcs)

    records = []
    for name in sorted(symbols):
        s = symbols[name]
        rel_file = s["file"]
        line = s["line"]
        dinfo = dwarf.get(name)

        dwarf_field = None
        if dinfo is not None:
            dwarf_field = {
                "address": dinfo.get("address"),
                "type": dinfo.get("type"),
                "size": dinfo.get("size"),
            }

        meta = modules.lookup(rel_file)
        rec = {
            "name": name,
            "kind": s["kind"],
            "file": rel_file,
            "line": line,
            "module": meta.get("module"),
            "tier": modules.symbol_tier(name, meta.get("tier")),
            "dwarf": dwarf_field,
            "streamable": streamable.get(name) or None,
            "panels": panel_map.get(name) or [],
            "glossary": glossary.get(name),
            "writers": writers.get(name) or None,
        }
        records.append(rec)
    return {
        "format": "agent-map-v1",
        "modules_confirmed": modules.confirmed,
        "symbols": records,
    }


def write(path: Path = OUT_PATH) -> None:
    payload = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def summarize(payload: dict[str, Any]) -> str:
    recs = payload["symbols"]
    n = len(recs)
    d = sum(1 for r in recs if r["dwarf"] is not None)
    s = sum(1 for r in recs if r["streamable"])
    p = sum(1 for r in recs if r["panels"])
    return f"{n} symbols, {d} with dwarf, {s} streamable, {p} with panels"