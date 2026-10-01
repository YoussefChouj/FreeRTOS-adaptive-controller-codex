import pytest
import numpy as np
from ground_station.research.sim.cascade import ROWS_3AE4A23, make_pid, PyPid, CPid
from ground_station.research.sim._ccore import build

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
            u_c = cpid.step(des, fb)
            u_p = pypid.step(des, fb)
            
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

def test_calibration_smoke(tmp_path):
    pass # to do later

def test_f2_oscillation():
    pass # to do later
