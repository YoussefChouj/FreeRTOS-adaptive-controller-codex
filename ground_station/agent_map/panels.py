"""Map firmware symbols to the dashboard panels that render them.

Source: ``ground_station/platform/capability_manifest.py::get_panels`` (the
same panel inventory /api/manifest serves) plus ``get_telemetry_keys`` for the
"keys read but not published" audit. These are module functions on static
files — the live service on :8081 is never started or called.

A symbol ``X`` is linked to a panel when any key the panel reads:
  * equals ``X`` or holds ``X`` as one of its dotted segments;
  * belongs to a ``pid`` family whose loop matches ``X`` as a PID struct
    (``<loop>PID``), e.g. ``pid.gyrox.*`` <=> ``gyroxPID``;
  * belongs to a telemetry namespace comments in ``boot_default_layout.py``
    assign to ``X`` (e.g. ``s_ekf`` -> ``ekf`` -> ``ekf.vel_x``);
  * is ``*`` (auto-discover panels such as Telemetry Explorer read all keys).
"""
from __future__ import annotations

import re
from pathlib import Path

from .paths import ROOT

_BOOT_LAYOUT = ROOT / "ground_station" / "comm" / "boot_default_layout.py"
# "raw.path[..]",  # telemetry.namespace.leaf ...
_COMMENT_KEY = re.compile(r'"([A-Za-z_]\w*(?:\[[^"]*\])?[^"]*)"[,;]\s*#\s*([\w.]+)')
_PID = re.compile(r"^(.+)PID$")


def _load_panels() -> list[dict]:
    """Panel inventory (name + keys_read) without starting the service."""
    from ground_station.platform.capability_manifest import get_panels
    return get_panels()


def _load_extra_keys() -> dict[str, set[str]]:
    """Telemetry keys read by panels but not published (key -> panels)."""
    from ground_station.platform.capability_manifest import get_telemetry_keys
    telemetry = get_telemetry_keys()
    out: dict[str, set[str]] = {}
    for entry in telemetry.get("unverified_keys_in_panels") or []:
        for p in entry.get("panels") or []:
            out.setdefault(entry.get("key", ""), set()).add(p)
    return out


def _load_boot_namespaces(path: Path = _BOOT_LAYOUT) -> dict[str, set[str]]:
    """raw DWARF base -> telemetry namespaces, from boot_default_layout.py."""
    try:
        txt = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, set[str]] = {}
    for m in _COMMENT_KEY.finditer(txt):
        base = m.group(1).split(".", 1)[0].split("[", 1)[0]
        ns = m.group(2).split(".", 1)[0]
        if base and ns:
            out.setdefault(base, set()).add(ns)
    return out


class PanelIndex:
    """Efficient symbol -> panels behind one parsed manifest."""

    def __init__(self):
        self.panels = _load_panels()
        self.boot_ns = _load_boot_namespaces()
        self.extra = _load_extra_keys()
        # token (dotted segment) -> panel names
        self._token_panels: dict[str, set[str]] = {}
        self._auto_panels: set[str] = set()
        self._seed_all()

    def _seed_all(self):
        for p in self.panels:
            name = p["name"]
            for key in p.get("keys_read") or []:
                if key == "*":
                    self._auto_panels.add(name)
                    continue
                for tok in key.split("."):
                    if tok.isidentifier():
                        self._token_panels.setdefault(tok, set()).add(name)
                # pid family: "pid.<loop>.<leaf>" and "@pid.<loop>" aliases
                parts = key.split(".")
                if parts[0] == "pid" and len(parts) >= 2 and parts[1]:
                    loop_id = parts[1] + "PID"
                    if loop_id.isidentifier():
                        self._token_panels.setdefault(loop_id, set()).add(name)

    def _hits(self, symbol: str) -> set[str]:
        out = set(self._token_panels.get(symbol, ()))
        # PID struct family
        m = _PID.match(symbol)
        if m:
            pidpanels = set()
            for p in self.panels:
                for key in p.get("keys_read") or []:
                    parts = key.split(".")
                    if len(parts) >= 2 and parts[0] == "pid" and \
                            parts[1].lower() == m.group(1).lower():
                        pidpanels.add(p["name"])
            out |= pidpanels
        # telemetry-namespace prefixes from boot comments
        for ns in self.boot_ns.get(symbol, ()):
            for p in self.panels:
                for key in p.get("keys_read") or []:
                    if key.startswith(ns + ".") or key == ns:
                        out.add(p["name"])
        return out

    def panels_for(self, symbol: str) -> list[str]:
        out = self._hits(symbol)
        # Auto-discover panels ("*") genuinely read every key, but listing them
        # on every symbol would make the panel field meaningless, so only
        # explicit key matches count here.
        # + extra/unpublished keys that embed the symbol as a token
        for key, ps in self.extra.items():
            if symbol in key.split(".") or symbol == key:
                out |= ps
        return sorted(out)


def load_panels() -> "PanelIndex":
    return PanelIndex()


def resolve_panels(symbol: str, index: "PanelIndex") -> list[str]:
    return index.panels_for(symbol)