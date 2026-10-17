import numpy as np

from ground_station.analysis.hover_fit import PROPOSED_V0, HoverPoint, fit_model, loocv


def test_synthetic_sag_log():
    # True parameters
    true_c0 = 100.0
    true_c1 = 50.0

    # Generate points
    points = []
    np.random.seed(42)
    for i in range(20):
        flight = "f1" if i < 10 else "f2"
        vbat = np.random.uniform(14.0, 16.5)
        tilt = np.random.uniform(0.9, 1.0)
        vd = PROPOSED_V0 - vbat

        # c0 + c1*(V0 - V) = u * tilt
        throttle_ideal = (true_c0 + true_c1 * vd) / tilt

        # add noise
        throttle = throttle_ideal + np.random.normal(0, 1.0)

        points.append(HoverPoint(flight, i, vbat, throttle, tilt, False))

    fit, _ = fit_model(points, use_quad=False)
    assert "c0" in fit
    assert abs(fit["c0"] - true_c0) < 0.02 * true_c0
    assert abs(fit["c1"] - true_c1) < 0.02 * true_c1

    cv = loocv(points, use_quad=False)
    assert cv > 0
