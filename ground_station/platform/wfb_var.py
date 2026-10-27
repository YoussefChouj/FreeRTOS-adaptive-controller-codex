"""CMD 0x2D VAR_WRITE: write one allowlisted firmware variable over WiFi, the twin of a Keil watch-window write.

The allowlist is the VAR_WRITE_ROW table in TASK/AutoflyTask.c, parsed here (single source, no copied list). One frame
writes one row: INDEX = the row's permanent id, value = float32. The name and range are checked here before sending;
the firmware checks them again (REJECTED with RejectReason.UNKNOWN_VARIABLE / OUT_OF_RANGE) and echoes the variable
name as the APPLIED detail. docs/firmware-table-pattern.md has the wire rules.

    client.var_write("traj_p.move_s", 12)      # WfbClient: True when APPLIED
    client.var_write("traj_go", 1)

The Keil trajectory presets (traj_id, traj_go) read the traj_p struct; traj_defaults() parses its boot values from the
same file, so a flight script that names a few fields flies the rest at the firmware's own defaults.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

CMD_VAR_WRITE: int = 0x2D
SOURCE = Path(__file__).resolve().parents[2] / "TASK" / "AutoflyTask.c"

_NUM = r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)[fF]?"
# traj_id values (TASK/AutoflyTask.c traj_id comment)
PRESETS: dict[str, int] = {"step": 1, "zigzag": 2, "circle": 3, "figure8": 4}
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


def armed_locked(name: str, table: dict[str, VarRow] | None = None) -> bool:
    """True when VarWrite_Apply refuses `name` while armed (REJECT_BUSY): u8 ids 47 and 60-63, f32 ids 75-83
    (TASK/AutoflyTask.c). These are written on the ground before the RC arm (campaign_fly.preapply_armed_locked)."""
    row = (rows() if table is None else table).get(name)
    return row is not None and (row.id == 47 or 60 <= row.id <= 63 or 75 <= row.id <= 83)


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def traj_defaults(path: Path = SOURCE) -> dict[str, float]:
    """"traj_p.<field>" -> boot value: the TrajParams_t field order zipped with the traj_p initializer's numbers.
    Raises ValueError when the two counts differ (a field without an initializer value, or a value parsed twice)."""
    text = _strip_comments(path.read_text(encoding="utf-8"))
    body = re.search(r"typedef struct \{([^{}]*)\} TrajParams_t;", text)
    init = re.search(r"\bTrajParams_t traj_p = \{([^{}]*)\};", text)
    if body is None or init is None:
        raise ValueError(f"{path.name}: TrajParams_t or its traj_p initializer not found")
    fields = re.findall(r"\b(?:uint8_t|float)\s+(\w+)\s*;", body.group(1))
    values = [float(v) for v in re.findall(r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)[fFuU]?", init.group(1))]
    if not fields or len(fields) != len(values):
        raise ValueError(f"{path.name}: {len(fields)} TrajParams_t fields but {len(values)} traj_p initializer values")
    return {f"traj_p.{f}": v for f, v in zip(fields, values)}


def traj_total_max_s(path: Path = SOURCE) -> float:
    """TRAJ_TOTAL_MAX_S: the move_s + ret_s + hold_s ceiling traj_go refuses above (status 0xEE)."""
    m = re.search(r"#define TRAJ_TOTAL_MAX_S\s+%s" % _NUM, path.read_text(encoding="utf-8"))
    if m is None:
        raise ValueError(f"{path.name}: TRAJ_TOTAL_MAX_S not found")
    return float(m.group(1))


def preset_duration_s(params: dict[str, float]) -> float:
    """Planned preset time from traj_go to back at the start point: goto and settle when start_fixed, then move,
    return and hold. The firmware stretches goto_s when the limit mode's speed needs it, so this is a lower bound."""
    p = params
    lead = p["traj_p.goto_s"] + p["traj_p.settle_s"] if p["traj_p.start_fixed"] else 0.0
    return lead + p["traj_p.move_s"] + p["traj_p.ret_s"] + p["traj_p.hold_s"]
