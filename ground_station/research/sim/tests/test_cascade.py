import numpy as np
import pytest

from ground_station.research.sim._ccore import build
from ground_station.research.sim.cascade import ROWS_3AE4A23, CPid, PyPid, make_pid


def test_cpid_vs_pypid():
    lib = build.build_pid_lib()
    if lib is None:
        pytest.skip("gcc not available")
        
    np.random.seed(42)
    for name, row in ROWS_3AE4A23.items():
        cpid = make_pid(row, prefer_c=True)
        pypid = make_pid(row, prefer_c=False)
        assert isinstance(cpid, CPid)
        assert isinstance(pypid, PyPid)
        
        for _ in range(2000):
            des = np.random.uniform(-500, 500)
            fb = np.random.uniform(-500, 500)
            cpid.step(des, fb)
            pypid.step(des, fb)
            
            assert np.isclose(cpid.U, pypid.U, atol=1e-4), f"{name}: U {cpid.U} vs {pypid.U}"
            assert np.isclose(cpid.Ui, pypid.Ui, atol=1e-4), f"{name}: Ui {cpid.Ui} vs {pypid.Ui}"
            assert np.isclose(cpid.SumE, pypid.SumE, atol=1e-4), f"{name}: SumE {cpid.SumE} vs {pypid.SumE}"

def test_anti_windup():
    # constant E < EMin -> Ui saturates at min(UiMax, Ki*SumEMax); |E| >= EMin -> SumE unchanged.
    row = ROWS_3AE4A23["rollPID"]
    pid = make_pid(row, prefer_c=False)
    
    # E < EMin: E = 2 (EMin = 3)
    des, fb = 2.0, 0.0
    for _ in range(100):
        pid.step(des, fb)
        
    expected_ui = min(row.UiMax, row.Ki * row.SumEMax)
    assert np.isclose(pid.Ui, expected_ui, atol=1e-4)
    
    # |E| >= EMin: E = 4 (EMin = 3)
    sum_e_before = pid.SumE
    pid.step(4.0, 0.0)
    assert np.isclose(pid.SumE, sum_e_before, atol=1e-4)

def test_steady_error():
    # steady error: angle+rate loop with a constant torque bias B and no other disturbance 
    # settles to the analytic angle error (rate P must supply B - gyro Ui cap, angle P must supply the remaining rate setpoint) within 5%.
    row_ang = ROWS_3AE4A23["rollPID"]
    row_rate = ROWS_3AE4A23["gyroxPID"]
    
    pid_ang = make_pid(row_ang, prefer_c=False)
    pid_rate = make_pid(row_rate, prefer_c=False)
    
    B = 30.0 # constant torque bias
    
    # Simulate a simple plant: torque = gyroxPID.U - B. rate_accel = torque * gain.
    gain = 5.0
    rate = 0.0
    angle = 0.0
    
    dt = 0.005
    for i in range(4000):
        current_B = min(B, i * 0.02)
        # angle loop
        des_rate = pid_ang.step(0.0, angle)
        # rate loop
        u_torque = pid_rate.step(des_rate, rate)
        
        torque_net = u_torque - current_B
        rate_accel = torque_net * gain
        rate += rate_accel * dt
        angle += rate * dt
        
    # analytic error:
    # rate loop needs U = B in steady state. 
    # rate loop max Ui = min(UiMax, Ki*SumEMax) = min(20, 0.01*1000) = 10.
    # So rate P (Kp=5) must supply B - 10 = 30 - 10 = 20.
    # rate P = 20 -> rate E = 20 / 5 = 4.0 deg/s.
    # So rate setpoint must be 4.0 deg/s. (since steady rate = 0).
    # angle loop must output U = 4.0 deg/s.
    # angle loop max Ui = min(UiMax, Ki*SumEMax) = min(10, 0.02*120) = 2.4.
    # So angle P (Kp=3) must supply 4.0 - 2.4 = 1.6.
    # angle P = 1.6 -> angle E = 1.6 / 3 = 0.533 deg.
    # FB = -0.533 deg.
    assert np.isclose(-angle, 0.533, rtol=0.05)


def test_load_flight_duplicates(tmp_path):
    import pandas as pd

    from ground_station.research.sim.cascade import load_flight
    
    # Create two slots that repeat t_src_ms
    # Slot 0 has 10 unique timestamps, each repeated 3 times
    t0 = np.repeat(np.arange(10) * 5, 3)
    df0 = pd.DataFrame({'t_src_ms': t0, 'val0': np.arange(30)})
    df0.to_csv(tmp_path / "test_flight.slot0.csv", index=False)
    
    # Slot 1 has 5 unique timestamps, each repeated 3 times
    t1 = np.repeat(np.arange(5) * 5, 3)
    df1 = pd.DataFrame({'t_src_ms': t1, 'val1': np.arange(15)})
    df1.to_csv(tmp_path / "test_flight.slot1.csv", index=False)
    
    df_merged = load_flight(tmp_path, "test_flight")
    # longest slot has 10 unique timestamps
    # our load_flight might sub-sample or merge
    # the spec says: "returns no more rows than the longer slot has unique timestamps"
    assert len(df_merged) <= 10

def test_load_pid_lib_invalid(tmp_path, monkeypatch):
    from ground_station.research.sim import cascade
    
    # Mock build_pid_lib to return a non-library file path
    non_lib_file = tmp_path / "not_a_lib.so"
    non_lib_file.write_text("this is not a valid library")
    
    def mock_build():
        return non_lib_file
        
    monkeypatch.setattr("ground_station.research.sim._ccore.build.build_pid_lib", mock_build)
    
    # Reset lazy load state
    monkeypatch.setattr(cascade, "_libpid_attempted", False)
    monkeypatch.setattr(cascade, "_libpid", None)
    
    # Should catch OSError/ImportError and return None without raising
    assert cascade.load_pid_lib() is None
def test_log_targets():
    import pandas as pd

    from ground_station.research.sim.cascade import log_targets
    
    dt = 0.005
    N = 4000
    t = np.arange(N) * dt
    
    # 0.9 Hz sinusoid
    freq = 0.9
    roll_fb = 2.0 * np.sin(2 * np.pi * freq * t)
    roll_des = 1.0 # constant Des-FB
    
    df = pd.DataFrame({
        't_src_ms': t * 1000,
        'Ctrler.Z_posPID.FB': np.ones(N) * 10.0, # hover active
        'Ctrler.rollPID.FB': roll_fb,
        'Ctrler.rollPID.Des': roll_des,
        'Ctrler.pitchPID.FB': np.zeros(N),
        'Ctrler.pitchPID.Des': np.zeros(N),
        'Ctrler.rollPID.U': np.zeros(N),
        'Ctrler.pitchPID.U': np.zeros(N),
        'Ctrler.gyroxPID.U': np.zeros(N),
        'Ctrler.gyroyPID.U': np.zeros(N),
        'Ctrler.locxPID.Des': np.zeros(N),
        'Ctrler.locxPID.FB': np.zeros(N),
        'Ctrler.locyPID.Des': np.zeros(N),
        'Ctrler.locyPID.FB': np.zeros(N),
    })
    
    targets = log_targets(df)
    assert targets is not None
    assert np.isclose(targets["sway_freq"], freq, atol=0.05)
    
    expected_err = roll_des - np.mean(roll_fb[int(N * 0.15):-1])
    assert np.isclose(targets["roll_err"], expected_err, atol=1e-3)

def test_calibration_smoke(tmp_path):
    import pandas as pd

    from ground_station.research.sim.cascade import calibrate
    
    # Create synthetic tiny flights
    df = pd.DataFrame({
        't_src_ms': np.arange(100) * 5,
        'Ctrler.Z_posPID.FB': np.ones(100) * 10.0,
        'Ctrler.rollPID.FB': np.zeros(100),
        'Ctrler.rollPID.Des': np.zeros(100),
        'Ctrler.pitchPID.FB': np.zeros(100),
        'Ctrler.pitchPID.Des': np.zeros(100),
        'Ctrler.rollPID.U': np.zeros(100),
        'Ctrler.pitchPID.U': np.zeros(100),
        'Ctrler.gyroxPID.U': np.zeros(100),
        'Ctrler.gyroyPID.U': np.zeros(100),
        'Ctrler.locxPID.Des': np.zeros(100),
        'Ctrler.locxPID.FB': np.zeros(100),
        'Ctrler.locyPID.Des': np.zeros(100),
        'Ctrler.locyPID.FB': np.zeros(100),
    })
    
    for name in ["f17_hover_shadow14_removed_white_floor_covering_batery_type_2",
                 "f17_hover_shadow4_removed_white_floor_covering_batery_type_2",
                 "f17_hover_active15_removed_white_floor_covering_batery_type_2"]:
        df.to_csv(tmp_path / f"{name}.slot0.csv", index=False)
        
    cfg = calibrate(tmp_path, quick=True)
    assert np.isfinite(cfg["gain_roll"])
    assert np.isfinite(cfg["tau_roll"])
