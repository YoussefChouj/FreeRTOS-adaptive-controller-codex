"""CMD 0x2D VAR_WRITE: write one allowlisted firmware variable over WiFi, the twin of a Keil watch-window write.

The allowlist is the VAR_WRITE_ROW table in TASK/AutoflyTask.c, parsed here (single source, no copied list). One frame
writes one row: INDEX = the row's permanent id, value = float32. The name and range are checked here before sending;
the firmware checks them again (REJECTED with RejectReason.UNKNOWN_VARIABLE / OUT_OF_RANGE) and echoes the variable
name as the APPLIED detail. docs/firmware-table-pattern.md has the wire rules.

    client.var_write("traj_p.move_s", 12)      # WfbClient: True when APPLIED
    client.var_write("traj_go", 1)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

CMD_VAR_WRITE: int = 0x2D
SOURCE = Path(__file__).resolve().parents[2] / "TASK" / "AutoflyTask.c"

_NUM = r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)[fF]?"
_ROW = re.compile(r"^[ \t]*VAR_WRITE_ROW\(\s*([\w.]+)\s*,\s*(VW_U8|VW_F32)\s*,\s*(\d+)\s*,\s*%s\s*,\s*%s\s*\)"
                  % (_NUM, _NUM), re.M)


@dataclass(frozen=True)
class VarRow:
    name: str        # the variable as written in the row ("traj_p.move_s")
    id: int          # permanent row id, the command's INDEX
    integer: bool    # VW_U8: integral values only
    min: float
    max: float


class VarWriteError(ValueError):
    """Refused locally: unknown name, value out of range, or not integral for a u8 row. Nothing was sent."""


def load_rows(path: Path = SOURCE) -> dict[str, VarRow]:
    """name -> row of every VAR_WRITE_ROW line of `path`. Raises ValueError on none, or a repeated name or id."""
    rows: dict[str, VarRow] = {}
    ids: set[int] = set()
    for m in _ROW.finditer(path.read_text(encoding="utf-8")):
        row = VarRow(m.group(1), int(m.group(3)), m.group(2) == "VW_U8", float(m.group(4)), float(m.group(5)))
        if row.name in rows or row.id in ids:
            raise ValueError(f"{path.name}: VAR_WRITE_ROW {row.name} id {row.id} repeats a name or id")
        rows[row.name] = row
        ids.add(row.id)
    if not rows:
        raise ValueError(f"{path.name}: no VAR_WRITE_ROW lines")
    return rows


_rows: dict[str, VarRow] | None = None


def rows() -> dict[str, VarRow]:
    """The allowlist of TASK/AutoflyTask.c, parsed once."""
    global _rows
    if _rows is None:
        _rows = load_rows()
    return _rows


def check(name: str, value: float, table: dict[str, VarRow] | None = None) -> VarRow:
    """The row for `name` when `value` is writable to it; VarWriteError otherwise (the firmware's own rule)."""
    table = rows() if table is None else table
    row = table.get(name)
    if row is None:
        raise VarWriteError(f"{name!r} is not in the VAR_WRITE allowlist ({', '.join(sorted(table))})")
    v = float(value)
    if math.isnan(v) or not row.min <= v <= row.max:
        raise VarWriteError(f"{name} = {value} outside [{row.min:g}, {row.max:g}]")
    if row.integer and v != int(v):
        raise VarWriteError(f"{name} = {value}: a u8 variable takes an integral value")
    return row
