"""motor_lag: the IV fit recovers a known first-order lag + dead time under RPM noise; OLS is biased low."""
import numpy as np

from ground_station.analysis.motor_lag import fit, segments


def _plant(tau=0.05, d=1, ts=0.02, n=6000, noise=150.0, seed=0):
    rng = np.random.default_rng(seed)
    cmd = 3000 + np.cumsum(rng.normal(0, 20, n)) * 0.2 + rng.normal(0, 60, n)
    a = np.exp(-ts / tau)
    rpm = np.full(n, 5000.0)
    for k in range(1, n):
        rpm[k] = a * rpm[k - 1] + (1 - a) * (1.6 * cmd[max(k - d, 0)])
    return cmd, rpm + rng.normal(0, noise, n), a


def test_iv_recovers_tau_and_ols_is_biased():
    cmd, rpm, a = _plant()
    seg = np.zeros(len(cmd), int)
    fly = np.ones(len(cmd), bool)
    tau_iv = -0.02 / np.log(fit(cmd, rpm, seg, fly, d=1, iv=True)['a'])
    tau_ls = -0.02 / np.log(fit(cmd, rpm, seg, fly, d=1, iv=False)['a'])
    assert abs(tau_iv - 0.05) < 0.01
    assert tau_ls < 0.8 * tau_iv


def test_segments_split_on_reset_and_gap():
    t = np.array([0, 20, 40, 10, 30, 200, 220], float)
    assert segments(t, 60).tolist() == [0, 0, 0, 1, 1, 2, 2]
