import numpy as np
from ctrl_mrac import RefModel, MRAC_S6
from fwpid import FwPID
import sys
sys.path.append('../adaptive_compare')
from sim_coupled import reference_model, DT_P, M_mass, g

def test_reference_model():
    print("Testing (a) Online vs Offline Reference Model...")
    N = 1000
    des = np.zeros((N, 3))
    des[100:500, 0] = 30.0
    
    L = reference_model(np.arange(N)*0.005, des[:, 0], des[:, 1], des[:, 2], np.zeros(N))
    rm_off = np.rad2deg(L['phi'][:, 0])
    
    rm_on = np.zeros(N)
    ref_model = RefModel(1)
    
    for i in range(N):
        step_des = np.array([des[i, 0]])
        step_rm = ref_model.step(step_des, np.zeros(1), np.zeros(1), np.zeros(1))
        rm_on[i] = step_rm['phi'][0]
        
    diff = np.abs(rm_off - rm_on)
    peak = np.max(np.abs(rm_off))
    max_diff = np.max(diff)
    print(f"Peak: {peak:.2f}, Max Diff: {max_diff:.2f}")
    if max_diff < 0.01 * peak:
        print("PASS")
    else:
        print("FAIL")

def test_lyapunov():
    print("Testing (b) MRAC parameter boundedness and tracking improvement...")
    
    def run_1axis(gamma):
        # We will use MRAC_S6 as a FwPID + MRAC.
        ctrl = MRAC_S6(1)
        ctrl.p['gamma'] = np.array([gamma])
        
        dt = 0.005
        N = 2000
        cmd = np.zeros(N)
        cmd[100:1000] = 30.0
        
        phi = 0.0
        p = 0.0
        
        err_sum = 0.0
        max_W = 0.0
        
        # We need a baseline PID for roll
        # FwPID uses ANG_PR and RATE_PR
        from sim_coupled import ANG_PR, RATE_PR
        from fwpid import PID
        ang_r = PID(ANG_PR, 1)
        rate_r = PID(RATE_PR, 1)
        
        for i in range(N):
            c = np.array([cmd[i]])
            phm = np.array([phi])
            pm = np.array([p])
            
            w_d = ang_r.step(c - np.rad2deg(phm))
            u_nom = rate_r.step(w_d - np.rad2deg(pm))
            
            # mock o
            o = {
                'k': i, 'pos': np.zeros((1, 3)), 'vel': np.zeros((1, 3)),
                'rpy': np.array([[np.rad2deg(phi), 0.0, 0.0]]), 
                'gyro': np.array([[np.rad2deg(p), 0.0, 0.0]]), 
                'vbat': np.array([15.4]),
                'ref': {'p': np.zeros((1, 3)), 'v': np.zeros((1, 3)), 'a': np.zeros((1, 3)), 'yaw': np.zeros(1)}
            }
            ctrl.des = np.array([[cmd[i], 0.0]])
            ctrl.tz = np.zeros(1)
            
            U = ctrl.controller_update(o, np.array([[u_nom[0], 0.0, 0.0]]), np.array([[w_d[0], 0.0, 0.0]]))
            U_roll = U[0, 0]
            
            accel = U_roll * 0.14 * 0.5 # 50% loss of effectiveness = payload
            p += accel * dt
            phi += p * dt
            
            err_sum += abs(cmd[i] - np.rad2deg(phi))
            max_W = max(max_W, np.linalg.norm(ctrl.mrac_r.Th))
            
        return err_sum, max_W

    err0, W0 = run_1axis(0.0)
    errS, WS = run_1axis(1.0)
    
    print(f"Error gamma=0: {err0:.2f}")
    print(f"Error gamma=1.0: {errS:.2f}")
    print(f"Max W norm: {WS:.4f}")
    
    if errS < err0 and WS < 10.0:
        print("PASS")
    else:
        print("FAIL")

if __name__ == '__main__':
    print("Sanity scripts for MRAC:")
    test_reference_model()
    test_lyapunov()
