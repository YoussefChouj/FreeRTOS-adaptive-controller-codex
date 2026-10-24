"""The firmware's x/y reference rows, read from the firmware source (the host mirror of two tables).

Two firmware tables decide what the x/y MRAC reference model is on the ground:

  - ``s_xyrm`` (TASK/StabilizerTask.c, ``XYRM_ROW``): the reference row of each nominal, applied by
    ``Exp_Poll`` when the exp row latches on the ground (ref_type, bw, crm_ell, gamma).
  - ``mrac_xy_ref_rows`` (API/mrac.c, ``MRAC_XY_REF_ROW``): the reference row of each nominal, applied by
    ``MRAC_XyRefSelect`` when the selected nominal changes (type, bw, zeta, delay ticks).

Both are read from the source here, like ``exp_rows`` reads ``EXP_ROW``: the numbers are never typed twice.
A host test compares the parsed rows with the firmware source (test_mrac_rows.py).

The x/y nominal order is the firmware's ``XY_CTRL_*`` enum: PID 0, ADRC 1, LQR 2, MPC 3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STABILIZER = ROOT / "TASK" / "StabilizerTask.c"
MRAC = ROOT / "API" / "mrac.c"

# The firmware's control period (API/mrac.h MRAC_DT): the delay of a row is its ticks times this.
MRAC_DT = 0.005

# The x/y nominal order (API/xy_ctrl.h XY_CTRL_PID / ADRC / LQR / MPC).
XY_KINDS: tuple[str, ...] = ("pid", "adrc", "lqr", "mpc")

_XYRM_RE = re.compile(
    r"XYRM_ROW\(\s*(-?[\d.]+)f\s*,\s*(-?[\d.]+)f\s*,\s*(-?[\d.]+)f\s*,\s*(-?[\d.]+)f\s*\)")
_REFROW_RE = re.compile(
    r"MRAC_XY_REF_ROW\(\s*(-?[\d.]+)f\s*,\s*(-?[\d.]+)f\s*,\s*(-?[\d.]+)f\s*,\s*(\d+)\s*\)")


@dataclass(frozen=True)
class XyRmRow:
    """One row of ``s_xyrm`` (TASK/StabilizerTask.c): what the exp row applies when it latches."""

    ref_type: float
    bw: float
    crm_ell: float
    gamma: float


@dataclass(frozen=True)
class RefRow:
    """One row of ``mrac_xy_ref_rows`` (API/mrac.c): what ``MRAC_XyRefSelect`` applies on a kind change."""

    ref_type: float
    bw: float
    zeta: float
    delay_ticks: int

    @property
    def delay_s(self) -> float:
        """The row's delay in seconds (the firmware's ``ticks * MRAC_DT``)."""
        return self.delay_ticks * MRAC_DT


def _rows(text: str, rx: re.Pattern[str]) -> list[tuple[float, ...]]:
    return [tuple(float(g) for g in m.groups()) for m in rx.finditer(text)]


def s_xyrm_rows(path: Path = STABILIZER) -> dict[str, XyRmRow]:
    """The ``s_xyrm`` table, by nominal name: {kind: (ref_type, bw, crm_ell, gamma)}.

    Raises ValueError when the source does not carry as many rows as the firmware's nominal order.
    """
    rows = _rows(path.read_text(encoding="utf-8"), _XYRM_RE)
    if len(rows) < len(XY_KINDS):
        raise ValueError(f"{path}: {len(rows)} XYRM_ROW rows, expected at least {len(XY_KINDS)}")
    return {kind: XyRmRow(*rows[i]) for i, kind in enumerate(XY_KINDS)}


def xy_ref_rows(path: Path = MRAC) -> dict[str, RefRow]:
    """The ``mrac_xy_ref_rows`` table, by nominal name: {kind: (type, bw, zeta, delay ticks)}.

    Raises ValueError when the source does not carry as many rows as the firmware's nominal order.
    """
    rows = _rows(path.read_text(encoding="utf-8"), _REFROW_RE)
    if len(rows) < len(XY_KINDS):
        raise ValueError(f"{path}: {len(rows)} MRAC_XY_REF_ROW rows, expected at least {len(XY_KINDS)}")
    out = {}
    for i, kind in enumerate(XY_KINDS):
        ref_type, bw, zeta, ticks = rows[i]
        out[kind] = RefRow(ref_type, bw, zeta, int(ticks))
    return out
