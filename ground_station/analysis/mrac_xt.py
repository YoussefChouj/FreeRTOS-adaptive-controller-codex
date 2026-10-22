"""Decode the MRAC experiment telemetry block (API/mrac.h MRAC_Xt_t, filled by MRAC_XtUpdate each tick).

Subscribe it as one range of 81 uint16 words (MULTI build): ``--group "<Hz>:mrac_xt.pat:81"``. stream_log names the
columns ``mrac_xt.pat[0]`` .. ``mrac_xt.pat[80]`` and sends them unsigned; this module restores the signed fields.

Layout (int16 words, in order): pat, phase, period, e[6], u_ad[6], trap[6][3], grp[6][8]. Axes in MRAC order
(pitch, roll, yaw, z, x, y). e and u_ad are x1000 (rad/s, or the axis unit), grp is the EMA (tau 0.01 s, PROPOSED) of
sum theta_i * phi_i * g_phi over each feature group, x1000, rails +-32767. trap bit i%16 of word i/16 is
mrac_l2g_trapped[axis][i]. pat: bit 0 layer (0 outer, 1 inner), bit 1 speed (0 slow, 1 fast), bit 2 pattern running,
bits 3-8 excite axis mask in Excite_Axis_e order (x, y, z, yaw, pitch, roll). phase = tick into the pattern period,
period = number of completed periods.
Spec: docs/design/2026-10-22-mrac-experiment-stack.md item 6.
"""
from __future__ import annotations

import csv
import sys
from typing import Mapping, Sequence

import numpy as np

AXES = ("pitch", "roll", "yaw", "z", "x", "y")                     # MRAC axis order (mrac.h)
EXC_AXES = ("x", "y", "z", "yaw", "pitch", "roll")                 # Excite_Axis_e order, pat mask bits 3-8
GROUPS = ("bias", "rate", "aero", "coupling", "ctrl", "ref", "rbf", "phy")
N_FEATURES = 46                                                    # MRAC_N_STRUCT 6 + 40 ext (MULTI)
TRAP_WORDS = (N_FEATURES + 15) // 16
E_SCALE = 1000.0                                                   # MRAC_XT_E_SCALE
U_SCALE = 1000.0                                                   # MRAC_XT_U_SCALE
RAIL = 32767

OFF_PAT, OFF_PHASE, OFF_PERIOD = 0, 1, 2
OFF_E = 3
OFF_U = OFF_E + len(AXES)
OFF_TRAP = OFF_U + len(AXES)
OFF_GRP = OFF_TRAP + len(AXES) * TRAP_WORDS
WORDS = OFF_GRP + len(AXES) * len(GROUPS)                          # 81 = MRAC_XT_WORDS
SUBSCRIBE = "mrac_xt.pat:%d" % WORDS
COLUMNS = ["mrac_xt.pat[%d]" % i for i in range(WORDS)]


def _s16(w: np.ndarray) -> np.ndarray:
    return w.astype(np.int64).astype(np.uint16).astype(np.int16).astype(np.int64)


def pattern_bits(pat: int) -> dict:
    pat = int(pat) & 0xFFFF
    mask = (pat >> 3) & 0x3F
    return {"layer": "inner" if pat & 1 else "outer", "speed": "fast" if pat & 2 else "slow",
            "running": bool(pat & 4), "axes": [a for k, a in enumerate(EXC_AXES) if mask >> k & 1]}


def decode(words: np.ndarray) -> dict:
    """words: (n, 81) or (81,) raw words, signed or unsigned. Returns arrays in physical units (x/1000)."""
    w = np.atleast_2d(np.asarray(words, dtype=np.int64))
    if w.shape[1] != WORDS:
        raise ValueError("expected %d words per sample, got %d" % (WORDS, w.shape[1]))
    s = _s16(w)
    u = w & 0xFFFF
    na, ng = len(AXES), len(GROUPS)
    trap = u[:, OFF_TRAP:OFF_GRP].reshape(-1, na, TRAP_WORDS)
    bits = np.zeros((w.shape[0], na, N_FEATURES), dtype=bool)
    for i in range(N_FEATURES):
        bits[:, :, i] = (trap[:, :, i // 16] >> (i % 16)) & 1 == 1
    return {"pat": u[:, OFF_PAT], "phase": u[:, OFF_PHASE], "period": u[:, OFF_PERIOD],
            "running": (u[:, OFF_PAT] & 4) != 0,
            "e": s[:, OFF_E:OFF_U] / E_SCALE, "u_ad": s[:, OFF_U:OFF_TRAP] / U_SCALE,
            "trapped": bits, "grp": s[:, OFF_GRP:WORDS].reshape(-1, na, ng) / U_SCALE,
            "railed": np.abs(s[:, OFF_E:WORDS]) >= RAIL}


def from_columns(cols: Mapping[str, Sequence]) -> dict:
    missing = [c for c in COLUMNS if c not in cols]
    if missing:
        raise KeyError("experiment block columns missing (subscribe %s): %s ..." % (SUBSCRIBE, missing[0]))
    return decode(np.column_stack([np.asarray(cols[c], dtype=float).round() for c in COLUMNS]))


def read_csv(path: str) -> dict:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return from_columns({c: [r[c] for r in rows] for c in COLUMNS} if rows and COLUMNS[0] in rows[0] else {})


def summary(d: dict) -> str:
    n = len(d["pat"])
    lines = ["%d samples, pattern running %.0f %%, railed words %d" % (n, 100.0 * d["running"].mean() if n else 0.0,
                                                                     int(d["railed"].sum()))]
    if n:
        lines.append("last pattern: %s" % pattern_bits(d["pat"][-1]))
    for a, name in enumerate(AXES):
        if not n:
            break
        g = np.abs(d["grp"][:, a, :]).mean(axis=0)
        tot = g.sum()
        share = ", ".join("%s %.0f%%" % (GROUPS[k], 100.0 * g[k] / tot) for k in np.argsort(-g)[:3] if g[k] > 0) \
            if tot > 0 else "none"
        lines.append("%-5s e rms %.4g  |u_ad| mean %.4g  trapped(end) %s  top groups: %s" % (
            name, float(np.sqrt((d["e"][:, a] ** 2).mean())), float(np.abs(d["u_ad"][:, a]).mean()),
            np.flatnonzero(d["trapped"][-1, a]).tolist(), share))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m ground_station.analysis.mrac_xt <slot.csv>   (subscribe %s)" % SUBSCRIBE)
        return 2
    print(summary(read_csv(argv[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
