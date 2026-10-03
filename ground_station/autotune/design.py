"""PID design on a fitted rate plant: loop margins, firmware discretisation, bounded gain steps.

Plant (WP-25): P(s) = k e^{-sT} / (s (tau s + 1)), U in mixer units -> gyro rate in deg/s.
Firmware PID (API/pid.c:118-125, dt 0.005 s): Ui = Ki_fw * sum(E), Ud = Kd_fw * (E - PreE), so
C(z) = Kp + Ki_fw / (1 - z^-1) + Kd_fw (1 - z^-1), Ki_fw = Ki * dt and Kd_fw = Kd / dt.

design_rate searches Kp and Kd (Ki stays at its current value: at Ki_fw 0.01 its zero sits far below crossover)
for the lowest simulated 30 deg/s step ITAE with PM, GM, max|S| and the UMax clamp inside Spec, then clamps each
gain to [1 - step_frac, 1 + step_frac] x current. design_angle picks the angle Kp whose crossover is closest to a
quarter of the rate crossover, on the plant closed rate loop / s.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

DT_S = 0.005
PID_C = Path(__file__).resolve().parents[2] / "API" / "pid.c"
_ROW_RE = re.compile(r"PID_ROW\(([^)]*)\)\s*,?\s*/\*\s*(\w+)")
_W = np.geomspace(0.3, 0.95 * math.pi / DT_S, 800)  # rad/s, up to 0.95 x Nyquist of the 200 Hz loop


@dataclass(frozen=True)
class Spec:
    """Design and acceptance limits. All PROPOSED (WP-25), none measured on this airframe."""

    pm_min_deg: float = 45.0
    pm_buffer_deg: float = 5.0    # design for PM >= pm_min + buffer: a ~10 % k error must not fail the verify
    gm_min_db: float = 6.0
    s_max: float = 2.0            # max |S|, 6 dB
    step_dps: float = 30.0        # rate step for ITAE and the UMax check
    step_frac: float = 0.30       # gain change per iteration, +-30 % of current
    angle_pm_min_deg: float = 50.0
    angle_wc_ratio: float = 0.25  # angle crossover / rate crossover
    hf_gain_ratio: float = 1.5    # |C| at Nyquist <= this x current: D has no filter (pid.c:125), so cap gyro noise gain


@dataclass(frozen=True)
class PidRow:
    """One API/pid.c PID_ROW in firmware units (Ki_fw, Kd_fw)."""

    kp: float
    ki: float
    kd: float
    umax: float = math.inf
    upmax: float = math.inf
    uimax: float = math.inf
    udmax: float = math.inf
    sumemax: float = math.inf
    emin: float = math.inf

    def gains(self) -> dict[str, float]:
        return {"Kp": self.kp, "Ki": self.ki, "Kd": self.kd}

    def continuous(self, dt: float = DT_S) -> dict[str, float]:
        return {"Kp": self.kp, "Ki": self.ki / dt, "Kd": self.kd * dt}


@dataclass(frozen=True)
class Plant:
    k: float
    tau_s: float
    delay_s: float

    def frf(self, w: np.ndarray) -> np.ndarray:
        s = 1j * np.asarray(w)
        return self.k * np.exp(-s * self.delay_s) / (s * (self.tau_s * s + 1.0))


@dataclass(frozen=True)
class Margins:
    pm_deg: float
    gm_db: float
    wc_rps: float
    s_max: float

    def meets(self, pm_min: float, gm_min: float, s_max: float) -> bool:
        return self.pm_deg >= pm_min and self.gm_db >= gm_min and self.s_max <= s_max


@dataclass
class Design:
    """status "proposed" or "refused" (reason says why). Gains in firmware units."""

    status: str
    reason: str = ""
    current: PidRow | None = None
    ideal: PidRow | None = None
    proposed: PidRow | None = None
    margins: dict[str, Margins] | None = None
    itae: dict[str, float] | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status, "reason": self.reason, "note": self.note}
        for name in ("current", "ideal", "proposed"):
            row = getattr(self, name)
            out[name] = row.gains() if row else None
            out[f"{name}_continuous"] = row.continuous() if row else None
        out["margins"] = {k: _rounded(asdict(m)) for k, m in (self.margins or {}).items()}
        out["itae"] = {k: round(v, 5) for k, v in (self.itae or {}).items()}
        return out


def _rounded(d: dict[str, float]) -> dict[str, float | None]:
    return {k: (round(v, 3) if math.isfinite(v) else None) for k, v in d.items()}


def to_firmware(kp: float, ki: float, kd: float, dt: float = DT_S) -> tuple[float, float, float]:
    """Continuous (Kp, Ki 1/s, Kd s) -> firmware (Kp, Ki_fw, Kd_fw)."""
    return kp, ki * dt, kd / dt


def to_continuous(kp: float, ki_fw: float, kd_fw: float, dt: float = DT_S) -> tuple[float, float, float]:
    return kp, ki_fw / dt, kd_fw * dt


def read_pid_rows(path: str | Path = PID_C) -> dict[str, PidRow]:
    """{member: PidRow} from the PID_ROW table, e.g. rows["gyroxPID"]."""
    rows = {}
    for args, name in _ROW_RE.findall(Path(path).read_text(encoding="utf-8", errors="replace")):
        vals = [float(v) for v in args.split(",")]
        if len(vals) == 9:
            rows[name] = PidRow(*vals)
    return rows


def pid_frf(row: PidRow, w: np.ndarray, dt: float = DT_S) -> np.ndarray:
    zi = np.exp(-1j * np.asarray(w) * dt)
    return row.kp + row.ki / (1.0 - zi) + row.kd * (1.0 - zi)


def _crossings(y: np.ndarray, level: float) -> np.ndarray:
    return np.flatnonzero(np.diff(np.sign(y - level)) != 0)


def margins(mag: np.ndarray, phase_deg: np.ndarray, w: np.ndarray = _W) -> Margins:
    """Margins from |L| and its continuous (unwrapped) phase on the grid w. The worst crossing counts."""
    lw = np.log(w)
    pm, wc = math.inf, math.nan
    for i in _crossings(np.log(mag), 0.0):
        a = -math.log(mag[i]) / (math.log(mag[i + 1]) - math.log(mag[i]))
        ph = phase_deg[i] + a * (phase_deg[i + 1] - phase_deg[i])
        p = (ph + 180.0) % 360.0
        p = p - 360.0 if p > 180.0 else p
        if p < pm:
            pm, wc = p, math.exp(lw[i] + a * (lw[i + 1] - lw[i]))
    gm = math.inf
    for level in np.arange(-180.0 - 360.0 * 4, 1.0, 360.0):
        for i in _crossings(phase_deg, level):
            a = (level - phase_deg[i]) / (phase_deg[i + 1] - phase_deg[i])
            m = math.exp(math.log(mag[i]) + a * (math.log(mag[i + 1]) - math.log(mag[i])))
            if m < 1.0:
                gm = min(gm, -20.0 * math.log10(m))
    s_max = float(np.max(1.0 / np.abs(1.0 + mag * np.exp(1j * np.radians(phase_deg)))))
    return Margins(float(pm), float(gm), float(wc), s_max)


def rate_loop(plant: Plant, row: PidRow, w: np.ndarray = _W) -> tuple[np.ndarray, np.ndarray]:
    """|L| and continuous phase (deg) of C*P. Re C > 0 for positive gains, so angle(C) is in (-90, 90)."""
    c = pid_frf(row, w)
    phase = np.degrees(np.angle(c)) - 90.0 - np.degrees(np.arctan(w * plant.tau_s) + w * plant.delay_s)
    return np.abs(c) * np.abs(plant.frf(w)), phase


def angle_loop(plant: Plant, rate: PidRow, angle: PidRow, w: np.ndarray = _W) -> tuple[np.ndarray, np.ndarray]:
    """|L| and phase of C_angle * T_rate / s, T_rate = CP / (1 + CP) on the current rate gains."""
    lr = pid_frf(rate, w) * plant.frf(w)
    t = lr / (1.0 + lr)
    c = pid_frf(angle, w)
    phase = np.degrees(np.angle(c)) + np.degrees(np.unwrap(np.angle(t))) - 90.0
    return np.abs(c) * np.abs(t) / w, phase


def step_itae(plant: Plant, kp: np.ndarray, kd: np.ndarray, row: PidRow, step_dps: float,
              t_end_s: float = 1.0, sub: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Simulated rate step on the firmware PID (clamps, EMin integral gate) for each (kp, kd) column.

    Returns (ITAE, peak |U| before the UMax clamp). Plant integrated at dt/sub with the delay in substeps."""
    kp, kd = np.broadcast_arrays(np.atleast_1d(np.asarray(kp, float)), np.atleast_1d(np.asarray(kd, float)))
    n, h = kp.size, DT_S / sub
    nd = int(round(plant.delay_s / h))
    buf = np.zeros((nd + 1, n))
    v, x, sume, pre_e, upk, itae = (np.zeros(n) for _ in range(6))
    a = 1.0 - math.exp(-h / max(plant.tau_s, 1e-4))
    j = 0
    with np.errstate(over="ignore", invalid="ignore"):
        for i in range(int(round(t_end_s / DT_S))):
            e = step_dps - x
            sume = np.clip(np.where(np.abs(e) < row.emin, sume + e, sume), -row.sumemax, row.sumemax)
            u_pre = (np.clip(kp * e, -row.upmax, row.upmax) + np.clip(row.ki * sume, -row.uimax, row.uimax)
                     + np.clip(kd * (e - pre_e), -row.udmax, row.udmax))
            pre_e = e
            upk = np.maximum(upk, np.abs(u_pre))
            u = np.clip(u_pre, -row.umax, row.umax)
            for _ in range(sub):
                buf[j] = u
                j = (j + 1) % (nd + 1)
                v = v + a * (buf[j] - v)
                x = x + plant.k * v * h
            itae = itae + (i * DT_S) * np.abs(e) * DT_S
    return np.where(np.isfinite(itae), itae, np.inf), upk


def hf_gain(row: PidRow) -> float:
    """|C| at the 100 Hz Nyquist frequency (z = -1): the gain from gyro noise to U."""
    return abs(row.kp + row.ki / 2.0 + 2.0 * row.kd)


def _round(v: float) -> float:
    return float(f"{v:.3g}")


def clamp_step(new: float, cur: float, frac: float) -> float:
    """new limited to [1 - frac, 1 + frac] x cur (a zero gain stays zero)."""
    return round(float(np.clip(new, cur * (1.0 - frac), cur * (1.0 + frac))), 6)


def design_rate(plant: Plant, cur: PidRow, spec: Spec = Spec()) -> Design:
    """Best (Kp, Kd) on a grid around the current row, then one bounded step toward it."""
    if not (plant.k > 0 and plant.tau_s >= 0 and plant.delay_s >= 0):
        return Design("refused", f"fitted plant is not a stable integrator+lag (k {plant.k:.4g})", current=cur)
    kd_wide = cur.kd * np.geomspace(1 / 3, 3, 15) if cur.kd > 0 else np.zeros(1)
    lo, hi = 1.0 - spec.step_frac, 1.0 + spec.step_frac
    wide = [replace(cur, kp=_round(kp), kd=_round(kd)) for kp in cur.kp * np.geomspace(1 / 3, 3, 21) for kd in kd_wide]
    box = [replace(cur, kp=clamp_step(cur.kp * a, cur.kp, spec.step_frac), kd=clamp_step(cur.kd * b, cur.kd,
                                                                                          spec.step_frac))
           for a in np.linspace(lo, hi, 13) for b in np.linspace(lo, hi, 13)]
    m_cur = margins(*rate_loop(plant, cur))
    j_cur = float(step_itae(plant, cur.kp, cur.kd, cur, spec.step_dps)[0][0])
    ideal = _best_rate(plant, cur, wide, spec)
    if ideal is None:
        return Design("refused", f"no Kp/Kd in [1/3, 3] x current meets PM >= {spec.pm_min_deg} deg, "
                      f"GM >= {spec.gm_min_db} dB, max|S| <= {spec.s_max} within the UMax and noise-gain caps",
                      current=cur,
                      margins={"current": m_cur}, itae={"current": j_cur})
    prop, note = _best_rate(plant, cur, box, spec), ""
    if prop is None:  # nothing inside the step box meets the spec: take the box point with the best PM
        ms = [margins(*rate_loop(plant, r)) for r in box]
        i = max(range(len(box)), key=lambda n: ms[n].pm_deg if ms[n].gm_db >= spec.gm_min_db else -math.inf)
        if not ms[i].pm_deg > m_cur.pm_deg:
            return Design("refused", f"no gains within +-{spec.step_frac:.0%} of current meet the spec or improve "
                          f"PM over current ({m_cur.pm_deg:.1f} deg)", current=cur, ideal=ideal,
                          margins={"current": m_cur}, itae={"current": j_cur})
        prop, note = box[i], "best PM within the step box; it does not meet the spec yet, iterate"
    elif (prop.kp, prop.kd) != (ideal.kp, ideal.kd):
        note = f"step limited to +-{spec.step_frac:.0%} of current; fly it, then iterate toward ideal"
    rows = {"current": cur, "ideal": ideal, "proposed": prop}
    return Design("proposed", current=cur, ideal=ideal, proposed=prop, note=note,
                  margins={k: margins(*rate_loop(plant, r)) for k, r in rows.items()},
                  itae={k: float(step_itae(plant, r.kp, r.kd, cur, spec.step_dps)[0][0]) for k, r in rows.items()})


def _best_rate(plant: Plant, cur: PidRow, rows: list[PidRow], spec: Spec) -> PidRow | None:
    """Lowest step ITAE among rows that meet the margins, the noise-gain cap and do not saturate UMax; None if none."""
    hf_max = spec.hf_gain_ratio * hf_gain(cur)
    ok = [r for r in rows if hf_gain(r) <= hf_max
          and margins(*rate_loop(plant, r)).meets(spec.pm_min_deg + spec.pm_buffer_deg, spec.gm_min_db, spec.s_max)]
    if not ok:
        return None
    itae, upk = step_itae(plant, np.array([r.kp for r in ok]), np.array([r.kd for r in ok]), cur, spec.step_dps)
    itae = np.where(upk <= cur.umax, itae, np.inf)
    return ok[int(np.argmin(itae))] if np.isfinite(itae).any() else None


def design_angle(plant: Plant, rate: PidRow, cur: PidRow, spec: Spec = Spec()) -> Design:
    """Angle Kp (Ki, Kd kept) for crossover ~ spec.angle_wc_ratio x the rate crossover, PM >= angle_pm_min."""
    m_rate = margins(*rate_loop(plant, rate))
    if not m_rate.meets(spec.pm_min_deg, spec.gm_min_db, spec.s_max):
        return Design("refused", f"rate loop misses its spec (PM {m_rate.pm_deg:.1f} deg, GM {m_rate.gm_db:.1f} "
                      f"dB, max|S| {m_rate.s_max:.2f}); tune the rate loop first", current=cur)
    target = spec.angle_wc_ratio * m_rate.wc_rps
    best, best_err = None, math.inf
    for kp in cur.kp * np.geomspace(1 / 3, 3, 61):
        m = margins(*angle_loop(plant, rate, replace(cur, kp=kp)))
        if m.meets(spec.angle_pm_min_deg, spec.gm_min_db, spec.s_max) and abs(math.log(m.wc_rps / target)) < best_err:
            best, best_err = kp, abs(math.log(m.wc_rps / target))
    m_cur = margins(*angle_loop(plant, rate, cur))
    if best is None:
        return Design("refused", f"no angle Kp in [1/3, 3] x current meets PM >= {spec.angle_pm_min_deg} deg, "
                      f"GM >= {spec.gm_min_db} dB, max|S| <= {spec.s_max}", current=cur, margins={"current": m_cur})
    ideal = replace(cur, kp=_round(best))
    prop = replace(cur, kp=clamp_step(ideal.kp, cur.kp, spec.step_frac))
    m_prop = margins(*angle_loop(plant, rate, prop))
    d = Design("proposed", current=cur, ideal=ideal, proposed=prop,
               margins={"rate": m_rate, "current": m_cur, "ideal": margins(*angle_loop(plant, rate, ideal)),
                        "proposed": m_prop})
    if prop != ideal:
        d.note = f"step clamped to +-{spec.step_frac:.0%} of current; fly it, then iterate"
    if not m_prop.meets(spec.angle_pm_min_deg, spec.gm_min_db, spec.s_max) and m_prop.pm_deg <= m_cur.pm_deg:
        d.status, d.reason = "refused", (f"clamped angle step misses the spec (PM {m_prop.pm_deg:.1f} deg) and does "
                                         "not improve on current")
    return d
