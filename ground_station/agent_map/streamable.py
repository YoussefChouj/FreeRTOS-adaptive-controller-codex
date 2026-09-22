"""Streamable variables: which subscribe presets+sockets log a symbol.

Joins ``multi_slot_presets.yaml`` (preset -> slots -> {manifest, hz}) with
``manifests.yaml`` (manifest -> vars[]). A symbol is streamable under
``{preset, slot}`` when that preset's slot uses a manifest whose var list
contains the symbol as the base of a DWARF path.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .paths import MANIFESTS_YAML, PRESETS_YAML


def _base(path) -> str:
    # manifests.yaml vars may be a plain DWARF path or {"dwarf": path, "key": ...}
    if isinstance(path, dict):
        path = path.get("dwarf") or ""
    if not isinstance(path, str):
        return ""
    # first dotted/indexed segment = the DWARF base symbol
    return path.strip().lstrip("-").strip().split(".", 1)[0].split("[", 1)[0]


def load_streamable(manifests_path: Path = MANIFESTS_YAML,
                    presets_path: Path = PRESETS_YAML) -> dict[str, list[dict]]:
    """Return {symbol: [{preset, slot}, ...]} for every symbol any preset logs."""
    man_raw = yaml.safe_load(manifests_path.read_text(encoding="utf-8"))
    pre_raw = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
    manifests = man_raw.get("manifests") or {}
    presets = pre_raw.get("presets") or {}

    # symbol set per manifest (base names only)
    manifest_syms: dict[str, set[str]] = {}
    for name, m in manifests.items():
        base_set = set()
        for v in m.get("vars") or []:
            base_set.add(_base(v))
        manifest_syms[name] = base_set

    out: dict[str, list[dict]] = {}
    for preset_name, preset in presets.items():
        for slot in preset.get("slots") or []:
            man = (slot or {}).get("manifest")
            syms = manifest_syms.get(man or "", set())
            slot_idx = (slot or {}).get("slot", 0)
            for s in syms:
                out.setdefault(s, []).append(
                    {"preset": preset_name, "slot": slot_idx})
    for v in out.values():
        # dedupe identical (preset, slot) pairs, keep preset order
        seen = set()
        ded = []
        for item in v:
            key = (item["preset"], item["slot"])
            if key not in seen:
                seen.add(key)
                ded.append(item)
        v[:] = ded
    return out