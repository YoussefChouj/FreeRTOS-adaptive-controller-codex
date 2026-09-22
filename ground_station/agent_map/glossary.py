"""Parse docs/glossary.md into term -> definition line."""
from __future__ import annotations

from pathlib import Path

import re

from .paths import GLOSSARY_MD

_TERM_RE = re.compile(r"^\*\*([^*]+)\*\*")


def load_glossary(path: Path = GLOSSARY_MD) -> dict[str, dict]:
    """Return {term: {'definition': text, 'line': int}}."""
    out: dict[str, dict] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    current: tuple[str, list[str]] | None = None
    for idx, line in enumerate(lines, start=1):
        m = _TERM_RE.match(line.strip())
        if m:
            if current is not None:
                out[current[0]] = {
                    "definition": " ".join(x.strip() for x in current[1]).strip(),
                    "line": current[2],
                }
            current = (m.group(1).strip(), [line], idx)
        elif current is not None:
            s = line.strip()
            if s:
                current[1].append(s)
    if current is not None:
        out[current[0]] = {
            "definition": " ".join(x.strip() for x in current[1]).strip(),
            "line": current[2],
        }
    return out


def lookup(glossary: dict[str, dict], name: str) -> dict | None:
    """Exact term lookup; case-insensitive fallback on the bare term."""
    if name in glossary:
        return glossary[name]
    low = name.lower()
    for term, info in glossary.items():
        if term.lower() == low:
            return info
    return None