"""``python -m ground_station.agent_map explain <name>``

Compact answer (<= 25 lines) about a firmware symbol. This is the universal
interface for WSL workers (no MCP): definition file:line, module/tier, DWARF,
streamable preset, panels, glossary, and writers.
"""
from __future__ import annotations

import difflib
import json
from typing import Any

from .paths import OUT_PATH, ROOT
from .build import build, summarize


def _load() -> dict[str, Any]:
    if OUT_PATH.exists():
        return json.loads(OUT_PATH.read_text(encoding="utf-8"))
    return build()


def _by_name(payload: dict) -> dict[str, dict]:
    return {r["name"]: r for r in payload["symbols"]}


def _fmt(r: dict, name: str) -> list[str]:
    lines: list[str] = []
    lines.append(f"{name} — {r['kind']} @ {r['file']}:{r['line']}")
    mod = r.get('module') or '—'
    t = r.get('tier')
    tier = t if t is not None else '—'
    lines.append(f"  module: {mod}   tier: {tier}")
    d = r.get("dwarf")
    if d:
        lines.append(f"  dwarf: {d['type']} @ 0x{d['address']:08x} "
                     f"({d['size']} B)")
    else:
        lines.append("  dwarf: (none / not in ELF)")
    st = r.get("streamable")
    if st:
        for item in st:
            lines.append(f"  streamable: preset={item['preset']} slot={item['slot']}")
    else:
        lines.append("  streamable: (none)")
    pan = r.get("panels") or []
    lines.append("  panels: " + (", ".join(pan) if pan else "(none)"))
    w = r.get("writers")
    if w:
        lines.append("  writers:")
        for entry in w[:12]:
            lines.append(f"    {entry}")
        if len(w) > 12:
            lines.append(f"    … {len(w) - 12} more")
    else:
        lines.append("  writers: (none)")
    g = r.get("glossary")
    if g and g.get("definition"):
        lines.append(f"  glossary: {g['definition'][:200]}")
    else:
        lines.append("  glossary: (none)")
    return lines


def format_explain(name: str, *, _max: int = 25) -> tuple[str, bool]:
    payload = _load()
    records = _by_name(payload)
    if name in records:
        out = _fmt(records[name], name)
        return "\n".join(out[: _max]), True

    names = list(records)
    close = difflib.get_close_matches(name, names, n=5, cutoff=0.0)
    lines = [f"unknown symbol: {name!r}"]
    if close:
        lines.append("did you mean:")
        for c in close:
            rec = records[c]
            lines.append(f"  {c}  ({rec['kind']} @ {rec['file']}:{rec['line']})")
    else:
        lines.append("(no close matches)")
    return "\n".join(lines[: _max]), False


def explain(name: str, *, _max: int = 25) -> int:
    text, found = format_explain(name, _max=_max)
    print(text)
    return 0 if found else 1


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="python -m ground_station.agent_map",
                                description="deterministic firmware agent map")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="regenerate agent_map.json")
    b.set_defaults(func=_cmd_build)

    e = sub.add_parser("explain", help="explain a firmware symbol")
    e.add_argument("name")
    e.set_defaults(func=_cmd_explain)

    args = p.parse_args(argv)
    return args.func(args)


def _cmd_build(args) -> int:
    from .build import write, build, summarize
    import sys
    payload = build()
    write()
    line = summarize(payload)
    print(line)
    return 0


def _cmd_explain(args) -> int:
    return explain(args.name)