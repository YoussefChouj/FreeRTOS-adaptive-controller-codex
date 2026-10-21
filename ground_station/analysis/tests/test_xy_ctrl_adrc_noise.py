"""The committed ADRC gains must not turn position-sensor noise into full-scale commands.

2026-10-21 flight adrc-alone (session 20261021-194603) oscillated: with wo 19.36 rad/s the ESO velocity estimate
was noisier (std 31 cm/s) than the real velocity (18 cm/s), z3 reached p95 386 > AMAX and the output saturated 45%
of the time. xy_tune adds noise to the velocity only, which ADRC never reads, so the tuner drove wo to its bound.
This closes the loop on API/xy_ctrl_gains.h as committed (a line-for-line port of eso_update / XyCtrl_Step and
XyCtrl_Commit), with the loop delay measured on 10-21 (21 ticks, VAR 47) and 0.9 cm position noise (flight
std(z1 - pos) 0.7-0.8 cm).
"""
import re
from pathlib import Path

import numpy as np
import pytest

HEADER = Path(__file__).resolve().parents[3] / "API" / "xy_ctrl_gains.h"
DT, DELAY = 0.01, 21


def gains(text: str) -> dict:
    sc = lambda n: float(re.search(r"#define %s\s+([-\d.e+]+)" % n, text).group(1))
    return {n: sc("XYC_" + n) for n in ("AL", "K", "DRAG", "AMAX", "ADRC_WC", "ADRC_B0", "ADRC_B1", "ADRC_B2",
                                         "ADRC_B3")}


def hold_step(g: dict, seed: int, k_scale: float = 1.0, pos_noise: float = 0.9, t_end: float = 20.0):
    """40 cm step at 1 s, then hold. Returns (rms position error, rms command) over t >= 8 s."""
    rng = np.random.default_rng(seed)
    wc, b0, b1, b2, b3, amax = (g[n] for n in ("ADRC_WC", "ADRC_B0", "ADRC_B1", "ADRC_B2", "ADRC_B3", "AMAX"))
    pipe, plant = np.zeros(DELAY), np.zeros(DELAY)
    head, z1, z2, z3, a, v, p = 0, None, 0.0, 0.0, 0.0, 0.0, 0.0
    err, cmd = [], []
    for i in range(int(t_end / DT)):
        des = 40.0 if i * DT > 1.0 else 0.0
        pm = p + pos_noise * rng.standard_normal()
        z1 = pm if z1 is None else z1
        e = z1 - pm
        z1 += DT * (z2 - b1 * e)
        z2 += DT * (z3 - b2 * e + b0 * pipe[head])
        z3 += DT * (-b3 * e)
        u = float(np.clip((wc * wc * (des - z1) - 2.0 * wc * z2 - z3) / b0, -amax, amax))
        pipe[head] = u
        ua, plant[head] = plant[head], u
        head = (head + 1) % DELAY
        a = g["AL"] * a + (1.0 - g["AL"]) * ua
        v += DT * (g["K"] * k_scale * a - g["DRAG"] * v)
        p += DT * v
        if i * DT >= 8.0:
            err.append(p - des)
            cmd.append(u)
    return float(np.sqrt(np.mean(np.square(err)))), float(np.sqrt(np.mean(np.square(cmd))))


@pytest.mark.parametrize("seed", [1, 2])
def test_adrc_command_noise_stays_well_below_the_tilt_limit(seed):
    g = gains(HEADER.read_text())
    _, urms = hold_step(g, seed)
    assert urms < 0.5 * g["AMAX"], f"rms command {urms:.0f} cm/s^2 >= half of AMAX {g['AMAX']:.0f}"


@pytest.mark.parametrize("k_scale", [0.9, 1.15])
def test_adrc_still_holds_the_step_across_the_measured_gain_range(k_scale):
    """K fitted on 10-21 flights: x0.92-1.14 of the header's K."""
    rms, _ = hold_step(gains(HEADER.read_text()), seed=1, k_scale=k_scale)
    assert rms < 2.0, f"rms hold error {rms:.2f} cm at K x{k_scale}"
