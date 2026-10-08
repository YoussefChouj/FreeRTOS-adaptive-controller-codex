"""feature_id: the virtual rope pendulum rings at its tuning and settles to zero relative swing in a steady lean, and
the leave-one-segment-out score finds a feature that is the target."""
from __future__ import annotations

import numpy as np

from ground_station.analysis import adaptive_review as ar
from ground_station.analysis.feature_id import Data, pendulum


def test_pendulum_rings_at_f0_and_hangs_straight_in_a_steady_lean():
    t = np.arange(int(20 * ar.FS)) / ar.FS
    tilt = np.where(t > 1.0, 0.1, 0.0)                     # 0.1 rad lean step at 1 s
    sw, swq = pendulum(tilt, 0.5, zeta=0.02)
    ring = sw[(t > 1.5) & (t < 9.5)]
    crossings = np.sum(np.diff(np.sign(ring)) != 0)
    assert abs(crossings / 2 / 8.0 - 0.5) < 0.07           # half-period crossings over 8 s -> about 0.5 Hz
    sw_d, swq_d = pendulum(tilt, 0.5, zeta=0.7)            # well damped: settles
    assert abs(sw_d[-1]) < 1e-3 and abs(swq_d[-1]) < 1e-3
    assert abs(sw[0]) < 1e-12                               # starts at rest on the first sample


def test_loso_scores_a_feature_that_is_the_target():
    rng = np.random.default_rng(1)
    items = []
    for k in range(4):
        y = rng.normal(size=500)
        items.append(("log", "seg%d" % k, {"pitch": (y, {"good": y + 0.05 * rng.normal(size=500),
                                                          "noise": rng.normal(size=500)})}))
    d = Data(items, "pitch")
    good, noise = d.names.index("good"), d.names.index("noise")
    assert d.loso([good]) > 0.99
    assert d.loso([noise]) < 0.01
