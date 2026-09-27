import numpy as np

def run_l1(Am, wc_hz):
    DT_C = 0.005
    b = 8.0
    alpha_m = np.exp(-DT_C / 0.05)
    
    w_true = 0.0
    w_hat = 0.0
    u_ad = 0.0
    u_hist = [0.0]*3
    u_lag = 0.0
    
    wc = wc_hz * 2 * np.pi
    exp_Am_Ts = np.exp(Am * DT_C)
    Phi_Ts = (exp_Am_Ts - 1.0) / Am
    
    w_log = []
    
    for k in range(200): # 1.0s
        w = w_true
        
        sigma_hat = - (1.0 / b) * (1.0 / Phi_Ts) * exp_Am_Ts * (w_hat - w)
        alpha = np.exp(-wc * DT_C)
        u_ad = alpha * u_ad + (1 - alpha) * (-sigma_hat)
        
        # Clip
        u_ad = np.clip(u_ad, -50.0, 50.0)
        
        u_pid = -0.2 * (w - 0.0) # Regulation to 0
        u = u_pid + u_ad
        
        w_hat = w + exp_Am_Ts * (w_hat - w) + Phi_Ts * b * (u + sigma_hat)
        
        u_delayed = u_hist.pop(0)
        u_hist.append(u)
        u_lag = alpha_m * u_lag + (1 - alpha_m) * u_delayed
        
        dist = 10.0 if k >= 20 else 0.0
        w_true = w_true + DT_C * (b * u_lag - 0.25 * w_true + dist)
        w_log.append(w_true)
        
    return np.array(w_log)

print("L1 Sanity Check (1-axis model, disturbance injected at t=0.1s)")
w_base = run_l1(Am=-10.0, wc_hz=2.0)
w_fast_adapt = run_l1(Am=-50.0, wc_hz=2.0)
w_high_bw = run_l1(Am=-10.0, wc_hz=5.0)

print(f"Base (Am=-10, wc=2Hz) max error: {np.max(np.abs(w_base[20:])):.3f}")
print(f"Fast adapt (Am=-50, wc=2Hz) max error: {np.max(np.abs(w_fast_adapt[20:])):.3f}")
print(f"High bw (Am=-10, wc=5Hz) max error: {np.max(np.abs(w_high_bw[20:])):.3f}")

if abs(np.max(np.abs(w_fast_adapt[20:])) - np.max(np.abs(w_base[20:]))) < 0.5:
    print("PASS: increasing Am (adaptation rate) does not make the transient worse.")
else:
    print("FAIL: increasing Am changed the transient significantly.")

if np.max(np.abs(w_high_bw[20:])) < np.max(np.abs(w_base[20:])):
    print("PASS: C(s) bandwidth sets the robustness/performance trade (higher bw = smaller transient).")
else:
    print("FAIL: C(s) bandwidth did not improve performance.")
