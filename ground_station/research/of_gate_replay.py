"""WP-20 offline replay: OF velocity scale x1.25 + vertical gate on logged flights.

Re-integrates the OF hold position from the logged ``of2_*_fix``, ``s_of_bias_*``,
yaw, ``Z_posPID.FB`` and ``Z_ratePID.FB`` with the same constants and gate logic as
``TASK/StabilizerTask.c`` (WP-20 block next to ``OF_HANDHELD_MIN_ALT_CM``), then prints
the three WP-20 acceptance checks.

    python -m ground_station.research.of_gate_replay --logs logs/vofa
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Mirrors of the firmware tunables (TASK/StabilizerTask.c, WP-20 table).
OF_VEL_SCALE = 1.25
OF_GATE_MIN_ALT_M = 0.40
OF_GATE_VZ_MPS = 0.20
OF_GATE_VZ_TAU_S = 0.005 / 0.02         # OF_GATE_VZ_ALPHA per 5 ms tick -> 0.25 s
OF_GATE_MAX_ALT_CM = 500
OF_GATE_RELEASE_S = 60 * 0.005          # OF_GATE_RELEASE_TICKS at 200 Hz
OF_MIN_QUALITY = 50
OF_HANDHELD_MIN_ALT_M = 0.10            # OF_HANDHELD_MIN_ALT_CM (Z FB proxy, see load)
FLIGHT_PHASE_FLYING = 1
FLIGHT_PHASE_LANDING = 2

SCALE_LOG = "of_scale_test_1"
HOVER_LOG = "flight_test_hover_2"
VERTICAL_WINDOWS_S = ((7.0, 8.5), (69.0, 79.0))

_COLS = {
    "imu_data.yaw": "yaw", "DroneStatus.ARM_Status": "arm",
    "Ctrler.Z_posPID.FB": "zpos", "Ctrler.Z_ratePID.FB": "zrate",
    "ano_of.of2_dx_fix": "dx", "ano_of.of2_dy_fix": "dy",
    "s_of_bias_x": "bx", "s_of_bias_y": "by", "ano_of.of_quality": "q",
    "Ctrler.locxPID.FB": "fx", "Ctrler.locyPID.FB": "fy",
    "g_of_hold_active": "hold", "flight_phase": "phase", "ano_of.of_alt_cm": "alt",
}


def load_flight(logs: Path, name: str) -> pd.DataFrame:
    """Merge the slots of one recording onto the fastest slot that has ``of2_*_fix``.

    Columns: t (s from the first base row) plus the short names in ``_COLS`` that exist.
    Other slots are joined with the last value at or before each base row.
    """
    slots = []
    for i in range(4):
        p = logs / f"{name}.slot{i}.csv"
        if p.exists() and p.stat().st_size > 0:
            df = pd.read_csv(p)
            if len(df):
                slots.append(df.sort_values("t_src_ms"))
    have_of = [s for s in slots if "ano_of.of2_dx_fix" in s.columns]
    if not have_of:
        raise ValueError(f"{name}: no slot has ano_of.of2_dx_fix")
    base = max(have_of, key=len)
    out = base[["t_src_ms"] + [c for c in _COLS if c in base.columns]].copy()
    for s in slots:
        if s is base:
            continue
        add = [c for c in _COLS if c in s.columns and c not in out.columns]
        if add:
            out = pd.merge_asof(out, s[["t_src_ms"] + add], on="t_src_ms", direction="backward")
    out = out.rename(columns=_COLS)
    out["t"] = (out["t_src_ms"] - out["t_src_ms"].iloc[0]) / 1000.0
    for c in ("bx", "by", "zrate", "yaw"):
        if c not in out.columns:
            out[c] = 0.0
    return out.fillna({"bx": 0.0, "by": 0.0, "zrate": 0.0, "yaw": 0.0}).reset_index(drop=True)


def vgate(df: pd.DataFrame) -> np.ndarray:
    """g_of_vgate per row: gates at once, releases after OF_GATE_RELEASE_S of clear rows.

    vz is Z_ratePID.FB through the firmware's first-order low-pass (s_of_vz_f), stepped per row.
    """
    t = df["t"].to_numpy()
    zr = df["zrate"].to_numpy()
    vz = np.zeros(len(df))
    acc = 0.0
    for i in range(len(df)):
        if i:
            acc += min((t[i] - t[i - 1]) / OF_GATE_VZ_TAU_S, 1.0) * (zr[i] - acc)
        vz[i] = acc
    raw = (df["zpos"].to_numpy() < OF_GATE_MIN_ALT_M) | (np.abs(vz) > OF_GATE_VZ_MPS)
    if "alt" in df.columns:
        raw |= df["alt"].fillna(0).to_numpy() > OF_GATE_MAX_ALT_CM
    gate = np.ones(len(df), dtype=bool)          # firmware init: g_of_vgate = 1
    clear_since = None
    for i in range(len(df)):
        if raw[i]:
            clear_since = None
            gate[i] = True
        else:
            if clear_since is None:
                clear_since = t[i]
            gate[i] = (t[i] - clear_since) < OF_GATE_RELEASE_S if i else True
    return gate


def base_mask(df: pd.DataFrame) -> np.ndarray:
    """Firmware pos_integrate without the vertical gate.

    Handheld test (disarmed): quality and height band; Z_posPID.FB stands in for of_alt_cm
    where the log lacks it. Flight: armed and FLYING or LANDING.
    """
    q_ok = df["q"].to_numpy() >= OF_MIN_QUALITY
    if "phase" in df.columns and "arm" in df.columns and (df["arm"] == 1).any():
        ph = df["phase"].to_numpy()
        return q_ok & (df["arm"].to_numpy() == 1) & ((ph == FLIGHT_PHASE_FLYING) | (ph == FLIGHT_PHASE_LANDING))
    if "alt" in df.columns:
        alt = df["alt"].fillna(0).to_numpy()
        return q_ok & (alt >= OF_HANDHELD_MIN_ALT_M * 100) & (alt <= OF_GATE_MAX_ALT_CM)
    return q_ok & (df["zpos"].to_numpy() >= OF_HANDHELD_MIN_ALT_M)


def integrate(df: pd.DataFrame, scale: float, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Hold-frame position (locxPID.FB, locyPID.FB) in cm, firmware equations.

    earth_x += (vx cos + vy sin) dt, earth_y += (vy cos - vx sin) dt with cos/sin of -yaw;
    locxPID.FB = earth_y, locyPID.FB = -earth_x. Each row's velocity holds until the next row.
    """
    t = df["t"].to_numpy()
    dt = np.diff(t, append=t[-1])
    vx = (df["dx"].to_numpy() - df["bx"].to_numpy()) * scale
    vy = (df["dy"].to_numpy() - df["by"].to_numpy()) * scale
    yaw = -np.radians(df["yaw"].to_numpy())
    c, s = np.cos(yaw), np.sin(yaw)
    step = np.where(mask, dt, 0.0)
    ex = np.cumsum((vx * c + vy * s) * step)
    ey = np.cumsum((vy * c - vx * s) * step)
    return ey, -ex


def _plateaus(t: np.ndarray, x: np.ndarray, y: np.ndarray, min_s: float = 1.5,
              tol_cm: float = 1.5) -> list[tuple[int, int]]:
    """Index ranges where the logged position stays within tol_cm of its start for >= min_s."""
    out, i, n = [], 0, len(t)
    while i < n:
        j = i
        while j + 1 < n and abs(x[j + 1] - x[i]) <= tol_cm and abs(y[j + 1] - y[i]) <= tol_cm:
            j += 1
        if t[j] - t[i] >= min_s:
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def find_moves(df: pd.DataFrame, min_cm: float = 20.0, max_dz_m: float = 0.15) -> list[dict]:
    """Horizontal moves between consecutive logged plateaus (|d| >= min_cm, height steady)."""
    t, fx, fy, z = (df[c].to_numpy() for c in ("t", "fx", "fy", "zpos"))
    pl = _plateaus(t, fx, fy)
    moves = []
    for (a0, a1), (b0, b1) in zip(pl, pl[1:]):
        d = (fx[b0:b1 + 1].mean() - fx[a0:a1 + 1].mean(), fy[b0:b1 + 1].mean() - fy[a0:a1 + 1].mean())
        dz = z[b0:b1 + 1].mean() - z[a0:a1 + 1].mean()
        if math.hypot(*d) >= min_cm and abs(dz) <= max_dz_m and z[a0:b1 + 1].min() >= OF_GATE_MIN_ALT_M:
            moves.append({"a": (a0, a1), "b": (b0, b1), "axis": 0 if abs(d[0]) >= abs(d[1]) else 1,
                          "t0": t[a1], "t1": t[b0], "logged": d})
    return moves


def _seg_delta(pos: np.ndarray, m: dict) -> float:
    (a0, a1), (b0, b1) = m["a"], m["b"]
    return float(pos[b0:b1 + 1].mean() - pos[a0:a1 + 1].mean())


def check_scale(df: pd.DataFrame) -> tuple[bool, list[str]]:
    gate = vgate(df)
    mask = base_mask(df)
    old = integrate(df, 1.0, mask)
    new = integrate(df, OF_VEL_SCALE, mask & ~gate)
    lines, mags = [], []
    for k, m in enumerate(find_moves(df), 1):
        ax = m["axis"]
        lg, ro, rn = m["logged"][ax], _seg_delta(old[ax], m), _seg_delta(new[ax], m)
        mags.append(abs(rn))
        lines.append(f"  move {k}: t {m['t0']:5.1f}-{m['t1']:5.1f} s  {'xy'[ax]}  logged {lg:+6.1f}  "
                     f"replay x1.00 {ro:+6.1f}  replay x{OF_VEL_SCALE:.2f}+gate {rn:+6.1f} cm")
    mean = float(np.mean(mags)) if mags else float("nan")
    ok = len(mags) == 8 and 47.0 <= mean <= 53.0
    lines.append(f"  moves found {len(mags)} (expect 8), mean |scaled| {mean:.1f} cm (need 47-53)")
    return ok, lines


def check_vertical(df: pd.DataFrame) -> tuple[bool, list[str]]:
    gate = vgate(df)
    mask = base_mask(df)
    t = df["t"].to_numpy()
    old = integrate(df, 1.0, mask)
    new = integrate(df, OF_VEL_SCALE, mask & ~gate)
    ok, lines = True, []
    for w0, w1 in VERTICAL_WINDOWS_S:
        i0, i1 = int(np.searchsorted(t, w0)), int(np.searchsorted(t, w1)) - 1
        d_old = [old[a][i1] - old[a][i0] for a in (0, 1)]
        d_new = [new[a][i1] - new[a][i0] for a in (0, 1)]
        share = float(gate[i0:i1 + 1].mean())
        ok &= max(abs(v) for v in d_new) <= 1.0
        lines.append(f"  t {w0:4.1f}-{w1:4.1f} s: ungated x1.00 {d_old[0]:+5.1f}/{d_old[1]:+5.1f}  "
                     f"gated x{OF_VEL_SCALE:.2f} {d_new[0]:+5.1f}/{d_new[1]:+5.1f} cm (x/y, need <=1.0)  "
                     f"gated {share:.0%} of window")
    return ok, lines


def check_hover(df: pd.DataFrame) -> tuple[bool, list[str]]:
    gate = vgate(df)
    t, ph = df["t"].to_numpy(), df["phase"].to_numpy()
    flying = (df["arm"].to_numpy() == 1) & (ph == FLIGHT_PHASE_FLYING)
    if not flying.any():
        return False, ["  no FLYING rows"]
    t_to = t[np.argmax(flying)]
    sel = flying & (df["hold"].to_numpy() == 1) & (t >= t_to + 3.0)
    dt = np.diff(t, append=t[-1])
    share = float((dt * (sel & gate)).sum() / max((dt * sel).sum(), 1e-9))
    ok = share <= 0.10
    lines = [f"  takeoff t {t_to:.1f} s, hold-active FLYING time {float((dt * sel).sum()):.1f} s, "
             f"gated {share:.1%} (need <=10%)"]
    land = (ph == FLIGHT_PHASE_LANDING) & (df["arm"].to_numpy() == 1)
    if land.any():
        mask = base_mask(df)
        old = integrate(df, 1.0, mask)
        new = integrate(df, OF_VEL_SCALE, mask & ~gate)
        i0, i1 = int(np.argmax(land)), int(len(land) - 1 - np.argmax(land[::-1]))
        lines.append(f"  info: landing descent t {t[i0]:.1f}-{t[i1]:.1f} s, OF position change "
                     f"old {old[0][i1] - old[0][i0]:+.1f}/{old[1][i1] - old[1][i0]:+.1f}, "
                     f"new {new[0][i1] - new[0][i0]:+.1f}/{new[1][i1] - new[1][i0]:+.1f} cm (x/y; "
                     f"OF's own reading, not ground truth; new = x{OF_VEL_SCALE:.2f} on the ungated rows)")
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logs", type=Path, default=Path("logs/vofa"))
    args = ap.parse_args(argv)
    scale = load_flight(args.logs, SCALE_LOG)
    hover = load_flight(args.logs, HOVER_LOG)
    results = []
    for title, fn, df in (("1. scale, 8 horizontal 50 cm moves", check_scale, scale),
                          ("2. vertical segments, gated position change", check_vertical, scale),
                          ("3. hover_2 gated share of hold time", check_hover, hover)):
        ok, lines = fn(df)
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {title}")
        print("\n".join(lines))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
