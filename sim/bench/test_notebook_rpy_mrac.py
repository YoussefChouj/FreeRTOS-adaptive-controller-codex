"""WP-30: the P3 notebook port runs headless and adaptation beats the non-adaptive baseline."""
import numpy as np

import notebook_rpy_mrac as nb

FT = 30.0


def test_notebook_mrac_beats_adaptation_off_baseline():
    # Notebook default vs its Config.ADAPTATION_ON = False switch, on the notebook's error x - xm.
    res = nb.compare(FT, ("mrac+pr", "pr_only"))
    for r in res.values():
        assert len(r["log"]["t"]) == int(np.ceil(FT / nb.DT))   # ran to the end, no divergence break
        assert np.all(np.isfinite(r["log"]["x"]))
    assert np.all(res["mrac+pr"]["rms_xm"] < res["pr_only"]["rms_xm"])


def test_notebook_mrac_beats_nominal_without_recovery():
    # Performance recovery off: pure K1/K2 nominal drifts under the RBF disturbance, MRAC holds.
    res = nb.compare(FT, ("mrac", "nominal"))
    assert np.all(res["mrac"]["rms_xr"] < 0.5 * res["nominal"]["rms_xr"])
    assert np.all(res["mrac"]["rms_xr"] < 5.0)
