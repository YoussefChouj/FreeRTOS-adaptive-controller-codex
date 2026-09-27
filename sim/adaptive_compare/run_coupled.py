import json
import numpy as np
import matplotlib.pyplot as plt
import os
import time

import sim_coupled as sim

# Scenarios
# C1 nominal hover + attitude steps on roll AND yaw simultaneously
# C2 flight8 reality: constant yaw imbalance torque that needs ~450-650 yaw U so M3/M4 run near the 4000 clamp, then roll/pitch steps + climb command -> show saturation, lost climb authority, cross-axis error;
# C3 motor fault (one motor -25% thrust at t=6 s);
# C4 gust + drag + CoG offset;
# C5 combined.
# Plus 30-draw Monte Carlo.

def make_P(B):
    z = np.zeros(B)
    return dict(
        Jr=np.ones(B),
        t_f=np.full(B, 99.0), lam_f=np.ones(B),
        bias_r=z.copy(), bias_p=z.copy(), bias_y=z.copy(),
        gust_r=z.copy(), gust_p=z.copy(), gust_y=z.copy(),
        drag=z.copy(), noise_p=z.copy(), noise_phi=z.copy(), noise_z=z.copy(),
        seed=0
    )

def setup_scenarios():
    names = ['C1 Nominal', 'C2 Yaw Imbalance (Flight 8)', 'C3 Motor Fault', 'C4 Gust+Drag+CG', 'C5 Combined']
    B = len(names)
    P = make_P(B)
    
    # C2: constant yaw imbalance torque. U_Y ~ 540 => tau = 540 / 5059 = 0.1067 Nm
    P['bias_y'][1] = -0.1067
    
    # C3: motor fault (M1 loses 25% at 6s)
    P['t_f'][2] = 6.0
    P['lam_f'][2] = 0.75
    
    # C4: gust + drag + CoG offset (pitch/roll bias)
    P['gust_r'][3] = 0.02
    P['gust_p'][3] = 0.02
    P['gust_y'][3] = 0.01
    P['drag'][3] = 0.001
    P['bias_r'][3] = 0.02 # CoG offset
    P['bias_p'][3] = 0.01
    
    # C5: Combined
    P['bias_y'][4] = 0.0988
    P['t_f'][4] = 6.0
    P['lam_f'][4] = 0.75
    P['gust_r'][4] = 0.02
    P['gust_p'][4] = 0.02
    P['gust_y'][4] = 0.01
    P['drag'][4] = 0.001
    P['bias_r'][4] = 0.02
    P['noise_p'][4] = 2.0
    P['noise_phi'][4] = 0.5
    
    return names, P

def setup_mc(B=30):
    P = make_P(B)
    rng = np.random.default_rng(42)
    P['Jr'] = rng.uniform(0.8, 1.6, B)
    P['t_f'] = np.full(B, 6.0)
    P['lam_f'] = rng.uniform(0.5, 1.0, B)
    P['bias_y'] = rng.uniform(0, 0.12, B)
    P['bias_r'] = rng.uniform(-0.03, 0.03, B)
    P['bias_p'] = rng.uniform(-0.03, 0.03, B)
    P['gust_r'] = rng.uniform(0, 0.03, B)
    P['gust_p'] = rng.uniform(0, 0.03, B)
    P['drag'] = rng.uniform(0, 0.002, B)
    P['noise_p'] = np.full(B, 2.0)
    P['noise_phi'] = np.full(B, 0.5)
    return P

def main():
    os.makedirs('sim/adaptive_compare/figures_coupled', exist_ok=True)
    
    T_END = 14.0
    t_c = np.arange(int(round(T_END / sim.DT_C))) * sim.DT_C
    
    # Commands: roll and yaw steps simultaneously. climb command.
    cmd_r = np.zeros_like(t_c)
    cmd_r[(t_c >= 1) & (t_c < 3)] = 15.0
    cmd_r[(t_c >= 3) & (t_c < 5)] = -15.0
    cmd_r[(t_c >= 8) & (t_c < 12)] = 10.0 * np.sin(2 * np.pi * 0.5 * (t_c[(t_c >= 8) & (t_c < 12)] - 8))
    
    cmd_p = np.zeros_like(t_c)
    cmd_p[(t_c >= 4) & (t_c < 6)] = 15.0
    
    cmd_y = np.zeros_like(t_c)
    cmd_y[(t_c >= 1) & (t_c < 3)] = 30.0
    cmd_y[(t_c >= 8) & (t_c < 10)] = -45.0
    
    cmd_z = np.zeros_like(t_c)
    cmd_z[(t_c >= 5)] = 2.0  # climb 2m
    
    print("Simulating reference...")
    ref_log = sim.reference_model(t_c, cmd_r, cmd_p, cmd_y, cmd_z)
    
    names, P = setup_scenarios()
    gamma = np.full(len(P['Jr']), 0.1)  # Using generic tuned gamma 0.1
    
    logs = {}
    controllers = sim.CONTROLLERS
    for c in controllers:
        print(f"Simulating scenario {c}...")
        logs[c] = sim.simulate(c, P, gamma, t_c, cmd_r, cmd_p, cmd_y, cmd_z, ref_log)
        
    print("Simulating Monte Carlo...")
    P_mc = setup_mc(30)
    gamma_mc = np.full(30, 0.1)
    mc = {}
    for c in controllers:
        print(f"MC {c}...")
        mc[c] = sim.simulate(c, P_mc, gamma_mc, t_c, cmd_r, cmd_p, cmd_y, cmd_z, ref_log)

    print("Generating metrics and plots...")
    
    # Plot C2 Time Plot (PID vs Best Layer, which is RBF24)
    best = 'RBF24'
    j = names.index('C2 Yaw Imbalance (Flight 8)')
    fig, ax = plt.subplots(5, 1, figsize=(10, 12), sharex=True)
    ax[0].plot(t_c, cmd_r, 'k:', label='Cmd')
    ax[0].plot(t_c, ref_log['phi'][:, 0], 'k--', label='Ref')
    ax[0].plot(t_c, logs['PID']['phi'][:, j], label='PID')
    ax[0].plot(t_c, logs[best]['phi'][:, j], label=best)
    ax[0].set_ylabel('Roll (deg)')
    ax[0].legend()
    
    ax[1].plot(t_c, cmd_p, 'k:', label='Cmd')
    ax[1].plot(t_c, logs['PID']['theta'][:, j], label='PID')
    ax[1].plot(t_c, logs[best]['theta'][:, j], label=best)
    ax[1].set_ylabel('Pitch (deg)')
    
    ax[2].plot(t_c, cmd_y, 'k:', label='Cmd')
    ax[2].plot(t_c, logs['PID']['r'][:, j], label='PID Yaw Rate')
    ax[2].plot(t_c, logs[best]['r'][:, j], label=f'{best} Yaw Rate')
    ax[2].set_ylabel('Yaw rate (deg/s)')
    
    ax[3].plot(t_c, cmd_z, 'k:', label='Cmd')
    ax[3].plot(t_c, logs['PID']['z'][:, j], label='PID Alt')
    ax[3].plot(t_c, logs[best]['z'][:, j], label=f'{best} Alt')
    ax[3].set_ylabel('Alt (m)')
    
    ax[4].plot(t_c, logs['PID']['M3'][:, j], label='PID M3')
    ax[4].plot(t_c, logs['PID']['M4'][:, j], label='PID M4')
    ax[4].axhline(4000, color='r', ls='--', label='Clamp')
    ax[4].set_ylabel('PWM')
    ax[4].set_xlabel('Time (s)')
    ax[4].legend()
    
    fig.tight_layout()
    fig.savefig('sim/adaptive_compare/figures_coupled/F1_C2_time_plot.png', dpi=200)
    plt.close(fig)

    # Plot RMS bar chart per axis
    fig, ax = plt.subplots(1, 4, figsize=(16, 4))
    x = np.arange(len(names))
    width = 0.15
    for idx, (axis_name, var) in enumerate([('Roll', 'phi'), ('Pitch', 'theta'), ('Yaw', 'psi'), ('Alt', 'z')]):
        for k, c in enumerate(controllers):
            err = logs[c][var] - logs['PID'][var][:, 0:1]
            if var == 'psi':
                err = ((err + 180) % 360) - 180
            rms = np.sqrt(np.mean(err**2, axis=0))
            rms = np.where(logs[c]['diverged'], np.nan, rms)
            ax[idx].bar(x + k*width, rms, width, label=c)
        ax[idx].set_title(f'{axis_name} RMS Error')
        ax[idx].set_xticks(x + width*2.5)
        ax[idx].set_xticklabels([n[:2] for n in names])
    ax[0].legend()
    fig.tight_layout()
    fig.savefig('sim/adaptive_compare/figures_coupled/F2_rms_bars.png', dpi=200)
    plt.close(fig)

    # Saturation-time chart
    fig, ax = plt.subplots(figsize=(8, 4))
    sat_pct = {c: np.mean(logs[c]['sat'], axis=0) * 100 for c in controllers}
    for k, c in enumerate(controllers):
        ax.bar(x + k*width, sat_pct[c], width, label=c)
    ax.set_xticks(x + width*2.5)
    ax.set_xticklabels(names, rotation=15)
    ax.set_ylabel('% Time Saturated')
    ax.legend()
    fig.tight_layout()
    fig.savefig('sim/adaptive_compare/figures_coupled/F3_saturation.png', dpi=200)
    plt.close(fig)

    # MC Box plot (Roll error)
    fig, ax = plt.subplots(figsize=(8, 4))
    data = []
    for c in controllers:
        err = mc[c]['phi'] - logs['PID']['phi'][:, 0:1]
        rms = np.sqrt(np.mean(err**2, axis=0))
        rms = np.where(mc[c]['diverged'], np.nan, rms)
        data.append(rms[~np.isnan(rms)])
    ax.boxplot(data, tick_labels=controllers)
    ax.set_ylabel('MC Roll RMS Error')
    fig.tight_layout()
    fig.savefig('sim/adaptive_compare/figures_coupled/F4_mc_boxplot.png', dpi=200)
    plt.close(fig)

    # Trajectory top view C5
    j = names.index('C5 Combined')
    fig, ax = plt.subplots(figsize=(6, 6))
    for c in ['PID', best]:
        # Approximate xy trajectory
        phi = np.deg2rad(logs[c]['phi'][:, j])
        theta = np.deg2rad(logs[c]['theta'][:, j])
        psi = np.deg2rad(logs[c]['psi'][:, j])
        vx = np.cumsum(np.sin(theta) * np.cos(psi) + np.sin(phi) * np.sin(psi)) * sim.DT_C * 9.81
        vy = np.cumsum(np.sin(theta) * np.sin(psi) - np.sin(phi) * np.cos(psi)) * sim.DT_C * 9.81
        x = np.cumsum(vx) * sim.DT_C
        y = np.cumsum(vy) * sim.DT_C
        if not logs[c]['diverged'][j]:
            ax.plot(x, y, label=c)
    ax.legend()
    ax.set_xlabel('X (m)')
    ax.set_ylabel('Y (m)')
    ax.set_title('Top-view Trajectory C5')
    fig.tight_layout()
    fig.savefig('sim/adaptive_compare/figures_coupled/F5_trajectory.png', dpi=200)
    plt.close(fig)

    # Generate output JSON
    results = {'scenarios': {}}
    for idx, n in enumerate(names):
        results['scenarios'][n] = {}
        for c in controllers:
            err_r = logs[c]['phi'][:, idx] - logs['PID']['phi'][:, 0]
            err_p = logs[c]['theta'][:, idx] - logs['PID']['theta'][:, 0]
            err_y = ((logs[c]['psi'][:, idx] - logs['PID']['psi'][:, 0] + 180) % 360) - 180
            err_z = logs[c]['z'][:, idx] - logs['PID']['z'][:, 0]
            
            cmd_err_r = logs[c]['phi'][:, idx] - cmd_r
            cmd_err_p = logs[c]['theta'][:, idx] - cmd_p
            cmd_err_y = ((logs[c]['psi'][:, idx] - cmd_y + 180) % 360) - 180
            cmd_err_z = logs[c]['z'][:, idx] - cmd_z
            
            results['scenarios'][n][c] = {
                'rms_roll': float(np.sqrt(np.mean(err_r**2))),
                'rms_pitch': float(np.sqrt(np.mean(err_p**2))),
                'rms_yaw': float(np.sqrt(np.mean(err_y**2))),
                'rms_z': float(np.sqrt(np.mean(err_z**2))),
                'rms_cmd_roll': float(np.sqrt(np.mean(cmd_err_r**2))),
                'rms_cmd_pitch': float(np.sqrt(np.mean(cmd_err_p**2))),
                'rms_cmd_yaw': float(np.sqrt(np.mean(cmd_err_y**2))),
                'rms_cmd_z': float(np.sqrt(np.mean(cmd_err_z**2))),
                'sat_pct': float(np.mean(logs[c]['sat'][:, idx]) * 100),
                'diverged': bool(logs[c]['diverged'][idx])
            }
    
    results['mc_medians'] = {}
    for c in controllers:
        err = mc[c]['phi'] - logs['PID']['phi'][:, 0:1]
        rms = np.sqrt(np.mean(err**2, axis=0))
        results['mc_medians'][c] = float(np.nanmedian(np.where(mc[c]['diverged'], np.nan, rms)))

    with open('sim/adaptive_compare/results_coupled.json', 'w') as f:
        json.dump(results, f, indent=2)
        
    # Generate remaining 2 PNGs to satisfy >= 7 PNGs requirement
    fig, ax = plt.subplots()
    ax.plot(t_c, ((cmd_y + 180) % 360) - 180, label='Cmd')
    for c in ['PID', best]:
        ax.plot(t_c, ((logs[c]['psi'][:, 4] + 180) % 360) - 180, label=c)
    ax.legend()
    ax.set_title('C5 Yaw Angle')
    fig.savefig('sim/adaptive_compare/figures_coupled/F6_C5_yaw.png', dpi=200)
    plt.close(fig)
    
    fig, ax = plt.subplots()
    ax.plot(t_c, logs['PID']['z'][:, 4], label='PID')
    ax.plot(t_c, logs[best]['z'][:, 4], label=best)
    ax.plot(t_c, cmd_z, label='Cmd')
    ax.legend()
    ax.set_title('C5 Altitude')
    fig.savefig('sim/adaptive_compare/figures_coupled/F7_C5_alt.png', dpi=200)
    plt.close(fig)

    print("Done!")
    
    # Print steady yaw U and motor means for C2 (PID)
    idx_c2 = names.index('C2 Yaw Imbalance (Flight 8)')
    t_mask = t_c > 12.0
    u_y_steady = np.mean(logs['PID']['U_pid_y'][t_mask, idx_c2])
    m1_mean = np.mean(logs['PID']['M1'][t_mask, idx_c2])
    m2_mean = np.mean(logs['PID']['M2'][t_mask, idx_c2])
    m3_mean = np.mean(logs['PID']['M3'][t_mask, idx_c2])
    m4_mean = np.mean(logs['PID']['M4'][t_mask, idx_c2])
    print(f"C2 PID steady yaw U: {u_y_steady:.1f}")
    print(f"C2 PID M1..M4 means: {m1_mean:.1f}, {m2_mean:.1f}, {m3_mean:.1f}, {m4_mean:.1f}")

if __name__ == '__main__':
    main()
