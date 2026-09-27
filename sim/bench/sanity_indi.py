import numpy as np
import scipy.signal as signal

def run_indi(sync_u=True):
    DT_C = 0.005
    b = 8.0
    alpha_m = np.exp(-DT_C / 0.05)
    
    # 2nd order Butterworth at 15 Hz
    b_f, a_f = signal.butter(2, 15.0 / 100.0)
    
    # IIR filter state
    z_gyro = np.zeros(2)
    z_u = np.zeros(2)
    
    def iir(x, z, b_c, a_c):
        y = b_c[0]*x + z[0]
        z[0] = b_c[1]*x + z[1] - a_c[1]*y
        z[1] = b_c[2]*x - a_c[2]*y
        return y
    
    w_true = 0.0
    u_hist = [0.0]*3
    u_motor = 0.0
    u_lag = 0.0
    w_last = 0.0
    u_delayed_ctrl = [0.0]*3
    
    w_log = []
    
    for k in range(200): # 1.0s
        w = w_true
        
        # INDI
        wdot = (w - w_last) / DT_C
        w_last = w
        nu_f = iir(wdot, z_gyro, b_f, a_f)
        
        nu = 15.0 * (0.0 - w) # k_rate = 15
        
        u_d = u_delayed_ctrl[0]
        u_motor = alpha_m * u_motor + (1 - alpha_m) * u_d
        u_f = iir(u_motor, z_u, b_f, a_f)
        
        if sync_u:
            U = u_f + (nu - nu_f) / b
        else:
            U = (nu - nu_f) / b
            
        u_delayed_ctrl.pop(0)
        u_delayed_ctrl.append(U)
        
        # Plant
        u_delayed_plant = u_hist.pop(0)
        u_hist.append(U)
        u_lag = alpha_m * u_lag + (1 - alpha_m) * u_delayed_plant
        
        dist = 10.0 if k >= 20 else 0.0
        w_true = w_true + DT_C * (b * u_lag - 0.25 * w_true + dist)
        w_log.append(w_true)
        
    return np.array(w_log)

w_sync = run_indi(sync_u=True)
w_nosync = run_indi(sync_u=False)

print("INDI Sanity Check (1-axis model, disturbance injected at t=0.1s)")
print(f"With filter sync: rate error at t=0.4s (0.3s after dist): {w_sync[80]:.3f} dps")
print(f"Without filter sync: rate error at t=0.4s: {w_nosync[80]:.3f} dps")
print(f"With filter sync max error: {np.max(np.abs(w_sync[20:])):.3f} dps")
print(f"Without filter sync max error: {np.max(np.abs(w_nosync[20:])):.3f} dps")
if abs(w_sync[-1]) < 0.5 and abs(w_nosync[-1]) > abs(w_sync[-1]):
    print("PASS: INDI rejects disturbance with no steady-state error, and sync filter helps stability/transient.")
else:
    print("FAIL: Did not meet criteria.")
