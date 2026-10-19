"""Parser for the EXP_ROW table in TASK/StabilizerTask.c (single source of truth)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "TASK" / "StabilizerTask.c"

_ROW_RE = re.compile(
    r"^[ \t]*EXP_ROW\(\s*(\w+)\s*,\s*(\d+)U?\s*,\s*(\d+)U?\s*,\s*(\d+)U?\s*,\s*(\d+)U?\s*,\s*(\d+)U?\s*,\s*(-?\d+)\s*,\s*(\d+)U?\s*\)",
    re.M,
)


@dataclass(frozen=True)
class ExpRow:
    id: int
    name: str
    xy: int
    kp: int
    vp: int
    lim: int
    mdec: int
    xyrm: int
    layer: int


def load_exp_rows(path: Path = SOURCE) -> list[ExpRow]:
    text = path.read_text(encoding="utf-8")
    rows: list[ExpRow] = []
    names: set[str] = set()
    for m in _ROW_RE.finditer(text):
        name = m.group(1)
        if name in names:
            raise ValueError(f"Duplicate exp name: {name}")
        names.add(name)
        row = ExpRow(
            id=len(rows),
            name=name,
            xy=int(m.group(2)),
            kp=int(m.group(3)),
            vp=int(m.group(4)),
            lim=int(m.group(5)),
            mdec=int(m.group(6)),
            xyrm=int(m.group(7)),
            layer=int(m.group(8)),
        )
        rows.append(row)
    if not rows:
        raise ValueError(f"No EXP_ROW lines found in {path}")
    return rows


_rows: list[ExpRow] | None = None


def exp_rows() -> list[ExpRow]:
    global _rows
    if _rows is None:
        _rows = load_exp_rows()
    return _rows


def exp_by_name(name: str) -> ExpRow | None:
    for r in exp_rows():
        if r.name == name:
            return r
    return None


def exp_by_id(id_: int) -> ExpRow | None:
    for r in exp_rows():
        if r.id == id_:
            return r
    return None
