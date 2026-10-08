"""adaptive_review.l2_health on a synthetic 3L-v2 log: the fit is the share of negd_f the weights predict."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ground_station.analysis.adaptive_review import l2_health


def _log(n=900, noise=0.1, seed=0):
    rng = np.random.default_rng(seed)
    cols = {}
    for ax in ("pitch", "roll"):
        p = "mrac_state.%s." % ax
        phi = rng.normal(size=(n, 6))
        th = np.linspace(0.1, 0.6, 6)
        for i in range(6):
            cols[p + "Phi_f[%d]" % i] = phi[:, i]
            cols[p + "Theta[%d]" % i] = np.full(n, th[i])
        cols[p + "pe"] = noise * rng.normal(size=n)
        cols[p + "p_gain"] = np.linspace(1.0, 3.0, n)
    return pd.DataFrame(cols)


def test_fit_matches_the_noise_share_and_lands_in_the_summary():
    df, summ = _log(), {}
    md = l2_health(df, [("MRAC 1", 0, len(df) - 1)], summ)
    r = summ["l2"]["MRAC 1 pitch"]
    var_pred = np.sum(np.linspace(0.1, 0.6, 6) ** 2)          # Theta'Phi_f variance, Phi_f ~ N(0, 1)
    assert abs(r["fit"] - var_pred / (var_pred + 0.01)) < 0.02
    assert r["p_gain"][0] == 1.0 and r["p_gain"][2] == 3.0
    assert any(line.startswith("| MRAC 1 | roll |") for line in md)


def test_silent_without_the_mrac_3l_group():
    assert l2_health(pd.DataFrame({"x": [0.0]}), [("MRAC 1", 0, 0)], {}) == []
