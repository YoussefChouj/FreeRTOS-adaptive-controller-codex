import numpy as np
from scipy import signal

from ground_station.analysis.att_fit import AttFitter, theil_u2, vaf


def test_synthetic_closed_loop():
    # Simulate y' = w, w' = -a*w + b*m, m' = (u_d - m)/tau
    b = 8.5
    a = 2.0
    tau = 0.03
    d = 2
    dt = 0.01

    # Generate random u
    np.random.seed(42)
    n = 1000
    u = np.random.normal(0, 10, n)

    # Add gap
    u1 = u[:400]
    u2 = u[500:]

    # Simulate
    A = np.array([[-a, b], [0, -1/tau]])
    B = np.array([[0], [1/tau]])
    C = np.array([[1, 0]])
    D = np.array([[0]])
    sys = signal.StateSpace(A, B, C, D)
    sys_d = sys.to_discrete(dt)
    b_d, a_d = signal.ss2tf(sys_d.A, sys_d.B, sys_d.C, sys_d.D)

    def sim(u_arr):
        u_d = np.concatenate([np.full(d, u_arr[0]), u_arr[:-d]])
        return signal.lfilter(b_d[0], a_d, u_d) + np.random.normal(0, 0.1, len(u_arr))

    w1 = sim(u1)
    w2 = sim(u2)

    segs = [{"u": u1, "w": w1}, {"u": u2, "w": w2}]
    d_grid = [0, 1, 2, 3, 4]
    fitter = AttFitter(segs, dt, d_grid)
    b_fit, a_fit, tau_fit, d_fit, _biases = fitter.fit()

    assert d_fit == 4
    assert abs(b_fit - b) < 0.05 * b
    assert abs(a_fit - a) < 0.05 * a
    assert abs(tau_fit - tau) < 0.05 * tau

def test_tic_and_vaf():
    # TIC and VAF match hand-computed values on a tiny example.
    y = np.array([1.0, 2.0, 3.0])
    y_pred = np.array([1.1, 1.9, 3.2])

    # num = sqrt(mean([0.01, 0.01, 0.04])) = sqrt(0.06/3) = sqrt(0.02) = 0.1414
    # den = sqrt(mean([1, 4, 9])) + sqrt(mean([1.21, 3.61, 10.24]))
    # = sqrt(14/3) + sqrt(15.06/3) = sqrt(4.666) + sqrt(5.02) = 2.16 + 2.24 = 4.4
    # tic = 0.1414 / 4.4 = 0.032

    tic = theil_u2(y, y_pred)
    assert abs(tic - 0.032) < 0.005

    # VAF
    # var(y) = var([1, 2, 3]) = 2/3 = 0.666
    # var(y - y_pred) = var([-0.1, 0.1, -0.2])
    # mean = -0.0666, var = mean([0.0011, 0.027, 0.0177]) = 0.0155
    # vaf = 100 * (1 - 0.0155/0.666) = 97.6%

    v_val = vaf(y, y_pred)
    assert abs(v_val - 97.6) < 1.0


def test_json_schema(tmp_path):
    import json

    import numpy as np

    # Create dummy csv logs
    import pandas as pd

    from ground_station.analysis.att_fit import main

    dt = 0.01
    n = 300
    t = np.arange(n) * dt

    for i in range(2):
        df = pd.DataFrame({
            "t": t,
            "Ctrler.gyroxPID.U": np.random.normal(0, 1, n),
            "Ctrler.gyroxPID.FB": np.random.normal(0, 1, n),
            "seq": np.arange(n) % 256,
            "status.arm": np.ones(n),
            "flight_phase": np.ones(n),
            "Ctrler.Z_ratePID.FB": np.zeros(n),
            "Ctrler.locxsPID.FB": np.zeros(n),
            "Ctrler.locysPID.FB": np.zeros(n),
            "Ctrler.Z_posPID.FB": np.ones(n),
            "Ctrler.Z_posPID.Des": np.ones(n),
            "Ctrler.Z_ratePID.U": np.ones(n) * 100.0,
            "status.vbat": np.ones(n) * 15.0,
            "status.pitch_deg": np.zeros(n),
            "status.roll_deg": np.zeros(n),
        })
        # Add sysid flag to second one
        if i == 1:
            df["id.sysid_state"] = np.ones(n)
        df.to_csv(tmp_path / f"log{i}.csv", index=False)

    out_file = tmp_path / "out.json"
    args = [str(tmp_path / "log0.csv"), str(tmp_path / "log1.csv"), "--axes", "roll", "--out", str(out_file)]

    main(args)

    out = json.loads(out_file.read_text())
    expected_keys = {"git_sha", "sessions", "models", "hover"}
    model_keys = {"b", "a", "tau", "D_ticks_200hz", "D_s", "bounds", "model", "skipped_segments", "lofo_per_flight", "sysid_flights_summary", "traj_flights_summary", "pass"}

    assert set(out.keys()) == expected_keys
    assert set(out["models"]["roll"].keys()) == model_keys
