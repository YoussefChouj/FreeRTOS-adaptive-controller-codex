"""Shared fixtures. Owned by the supervisor (WP0); workers may use these fixtures but must not edit this file.

hover_log (26 s, deterministic, seed 0) - ground truth that worker tests may assert against:
  DroneStatus.ARM_Status   100 Hz  0 before t=1.0 s, 1 from 1.0 to 25.0 s, 0 after
  flight_phase             100 Hz  0 before 2.0, 1 on [2.0, 22.0), 2 on [22.0, 24.0), 3 after
  Ctrler.gyroxPID.*        100 Hz  Des 0; FB = 3.0*sin(2*pi*6*t) + N(0, 0.2) while flying -> 6 Hz oscillation
  Ctrler.gyroyPID.*        100 Hz  Des 0; FB = 1.5 constant + N(0, 0.2) while flying -> bias, no oscillation
  (U = -5*FB, SumE = cumulative -FB clipped to +-1000, Kp/Ki/Kd are NOT logged)
  mymotor.motor1..4        100 Hz  2000 on ground; flying: M1=M2=3000, M3=M4=3100 (yaw pair +100), M4 hits 4000
                                   on [10.0, 10.5)
  Throttle_out             100 Hz  0 on ground, 3050 flying
  real_voltage              10 Hz  16.4 V armed on ground, linear 16.2 -> 15.6 while flying, 15.9 after
  mrac_flags.adaptation_on  25 Hz  1 throughout       mrac_flags.output_injection_on 25 Hz 0 throughout (shadow)
  mrac_state.roll.*         25 Hz  e = FB of gyrox resampled, u_nom = -5*e, u_ad = 0.1*u_nom,
                                   Theta[0] = 1 - exp(-t/3)  (converges), Theta[1] = 0.05*t (drifts)
"""
from __future__ import annotations

import numpy as np
import pytest

from ground_station.analysis.flightlab.model import FlightLog, Signal, SlotInfo
from ground_station.analysis.flightlab.pipeline import load_config


def _slot_info(index: int, rate: float, t: np.ndarray, names: list[str]) -> SlotInfo:
    dt = np.diff(t) * 1000.0 if t.size > 1 else np.array([np.nan])
    dur = float(t[-1] - t[0]) if t.size > 1 else 0.0
    return SlotInfo(index=index, rate_hz=rate, n_rows=int(t.size), duration_s=dur,
                    rate_measured_hz=(t.size - 1) / dur if dur > 0 else float("nan"),
                    seq_drops=0, tsrc_gaps=0, drop_pct=0.0, dt_median_ms=float(np.median(dt)),
                    dt_p99_ms=float(np.percentile(dt, 99)), dt_max_ms=float(np.max(dt)),
                    tsrc_backsteps=0, host_latency_std_ms=0.0, vars=sorted(names))


def build_log(sigs: dict, rates: dict, name: str = "synthetic", meta: dict | None = None) -> FlightLog:
    """sigs: {var: (t, v)} arrays; rates: {var: nominal Hz}. One slot per distinct rate, highest rate = slot 0."""
    slot_of = {r: i for i, r in enumerate(sorted(set(rates.values()), reverse=True))}
    signals, slot_t, slot_vars = {}, {}, {}
    for var, (t, v) in sigs.items():
        t = np.asarray(t, dtype=np.float64)
        v = np.asarray(v, dtype=np.float64)
        r = float(rates[var])
        signals[var] = Signal(var, t, v, r, slot_of[rates[var]])
        slot_t.setdefault(slot_of[rates[var]], t)
        slot_vars.setdefault(slot_of[rates[var]], []).append(var)
    slots = [_slot_info(i, r, slot_t[i], slot_vars[i]) for r, i in sorted(slot_of.items(), key=lambda kv: kv[1])]
    t_all = [s.t for s in signals.values() if s.t.size]
    duration = float(max(t[-1] for t in t_all) - min(t[0] for t in t_all)) if t_all else 0.0
    return FlightLog(name=name, source_format="synthetic", source_paths=[], meta=meta or {},
                     t0_src_ms=0.0, duration_s=duration, signals=signals, slots=slots)


@pytest.fixture
def make_log():
    """Factory: make_log(sigs, rates, name="synthetic", meta=None) -> FlightLog."""
    return build_log


@pytest.fixture(scope="session")
def cfg() -> dict:
    return load_config()


def _hover() -> FlightLog:
    rng = np.random.default_rng(0)
    T = 26.0
    t100 = np.arange(0.0, T, 0.01)
    t25 = np.arange(0.0, T, 0.04)
    t10 = np.arange(0.0, T, 0.1)
    fly = (t100 >= 2.0) & (t100 < 22.0)
    arm = ((t100 >= 1.0) & (t100 < 25.0)).astype(float)
    phase = np.select([t100 < 2.0, t100 < 22.0, t100 < 24.0], [0.0, 1.0, 2.0], 3.0)

    s, r = {}, {}
    def add(name, t, v, rate):
        s[name], r[name] = (t, v), rate

    add("DroneStatus.ARM_Status", t100, arm, 100)
    add("flight_phase", t100, phase, 100)
    fbx = np.where(fly, 3.0 * np.sin(2 * np.pi * 6.0 * t100) + rng.normal(0, 0.2, t100.size), 0.0)
    fby = np.where(fly, 1.5 + rng.normal(0, 0.2, t100.size), 0.0)
    for pfx, fb in (("Ctrler.gyroxPID", fbx), ("Ctrler.gyroyPID", fby)):
        add(pfx + ".Des", t100, np.zeros_like(t100), 100)
        add(pfx + ".FB", t100, fb, 100)
        add(pfx + ".U", t100, -5.0 * fb, 100)
        add(pfx + ".SumE", t100, np.clip(np.cumsum(-fb), -1000, 1000), 100)
    base = {1: 3000.0, 2: 3000.0, 3: 3100.0, 4: 3100.0}
    for k, lvl in base.items():
        m = np.where(fly, lvl, 2000.0)
        if k == 4:
            m[(t100 >= 10.0) & (t100 < 10.5)] = 4000.0
        add(f"mymotor.motor{k}", t100, m, 100)
    add("Throttle_out", t100, np.where(fly, 3050.0, 0.0), 100)

    volt = np.where(t10 < 2.0, 16.4, np.where(t10 < 22.0, 16.2 - 0.6 * (t10 - 2.0) / 20.0, 15.9))
    add("real_voltage", t10, volt, 10)

    add("mrac_flags.adaptation_on", t25, np.ones_like(t25), 25)
    add("mrac_flags.output_injection_on", t25, np.zeros_like(t25), 25)
    e = np.interp(t25, t100, fbx)
    add("mrac_state.roll.e", t25, e, 25)
    add("mrac_state.roll.u_nom", t25, -5.0 * e, 25)
    add("mrac_state.roll.u_ad", t25, -0.5 * e, 25)
    add("mrac_state.roll.Theta[0]", t25, 1.0 - np.exp(-t25 / 3.0), 25)
    add("mrac_state.roll.Theta[1]", t25, 0.05 * t25, 25)
    meta = {"started_at": "2026-01-01 00:00:00", "git": "synthetic", "preset": {"name": "hover_synthetic"}}
    return build_log(s, r, name="hover_synthetic", meta=meta)


@pytest.fixture
def hover_log() -> FlightLog:
    return _hover()
