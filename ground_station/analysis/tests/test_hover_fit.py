import numpy as np

from ground_station.analysis.hover_fit import V0, HoverPoint, fit_model, loocv


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
        vd = V0 - vbat

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
    assert cv[0] > 0

def test_extract_hover_points():
    import numpy as np

    from ground_station.analysis.hover_fit import PROPOSED_DT, extract_hover_points

    # create synthetic log data
    t = np.arange(0, 10, PROPOSED_DT)
    series = {
        "status.arm": (t, np.ones_like(t)),
        "flight_phase": (t, np.ones_like(t)),
        "id.sysid_state": (t, np.zeros_like(t)),

        "Ctrler.Z_ratePID.FB": (t, np.zeros_like(t)),
        "Ctrler.locxsPID.FB": (t, np.zeros_like(t)),
        "Ctrler.locysPID.FB": (t, np.zeros_like(t)),
        "Ctrler.Z_posPID.FB": (t, np.full_like(t, 1.0)),
        "Ctrler.Z_posPID.Des": (t, np.full_like(t, 1.0)),

        "Ctrler.Z_ratePID.U": (t, np.full_like(t, 100.0)),
        "status.vbat": (t, np.full_like(t, 15.0)),
        "status.pitch_deg": (t, np.zeros_like(t)),
        "status.roll_deg": (t, np.zeros_like(t)),
    }

    pts = extract_hover_points(series, "f1", skip_s=2.0, window_s=1.0)
    assert len(pts) == 7 # 10s total, skip 2s = 8s
