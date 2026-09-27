"""Scenarios, Monte Carlo and figures for Pitch, Yaw, and Z axes."""
import os
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import sim_axes as S

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'figures_axes')
os.makedirs(OUT, exist_ok=True)

SURF, INK, INK2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#e4e3df'
COL = {'PID': '#8a8984', 'S6': '#2a78d6', 'S10': '#eb6834',
       'RBF6': '#1baf7a', 'RBF12': '#eda100', 'RBF24': '#e87ba4'}
LABEL = {'PID': 'PID', 'S6': 'S6', 'S10': 'S10', 'RBF6': 'RBF6', 'RBF12': 'RBF12', 'RBF24': 'RBF24'}

plt.rcParams.update({
    'figure.facecolor': SURF, 'axes.facecolor': SURF, 'savefig.facecolor': SURF,
    'axes.edgecolor': INK2, 'axes.labelcolor': INK, 'text.color': INK,
    'xtick.color': INK2, 'ytick.color': INK2, 'axes.grid': True, 'grid.color': GRID,
    'grid.linewidth': 0.6, 'axes.spines.top': False, 'axes.spines.right': False,
    'lines.linewidth': 2.0, 'font.size': 10.5, 'axes.titlesize': 11.5})

def save(fig, name):
    fig.savefig(os.path.join(OUT, f'{name}.png'), dpi=200, bbox_inches='tight')
    plt.close(fig)

def scen(**kw):
    P = S.nominal_params(1)
    for k, v in kw.items():
        P[k] = np.array([v], float) if k != 'seed' else v
    return P

SCEN = {
    'S1 Nominal': scen(seed=11),
    'S2 Mass/Inertia + Bias': scen(Jr=1.4, bias0=0.02, seed=12),
    'S3 Motor fault @ 6s': scen(lam_f=0.6, bias_f=0.015, t_f=6.0, seed=13),
    'S4 Drag + Gusts': scen(drag=0.002, gust=0.01, qr_amp=1.0, seed=14),
    'S5 Combined': scen(Jr=1.3, bias0=0.015, lam_f=0.7, bias_f=0.01, t_f=6.0,
                        drag=0.002, gust=0.008, noise_p=1.5, noise_phi=0.3,
                        qr_amp=1.0, seed=15),
}

# Per-axis mapping of the shared scenario set. Torque-type terms (bias, gust, drag)
# are specified in pitch N*m; each axis sees the same fraction of controller authority,
# so they are rescaled by K_EFF_pitch / K_EFF_axis (controller units per plant unit).
K_P = S.AXES['pitch']['K_EFF']
TORQUE_KEYS = ('bias0', 'bias_f', 'gust', 'drag')
# Flight 8: measured yaw rate-loop output ~450 units against a constant rotor imbalance.
YAW_FLIGHT8_BIAS = 450.0 / S.AXES['yaw']['K_EFF']

def to_axis(P, axis):
    P = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in P.items()}
    if axis == 'pitch':
        return P
    for k in TORQUE_KEYS:
        P[k] = P[k] * (K_P / S.AXES[axis]['K_EFF'])
    if axis == 'z':
        # one motor at lam_f -> collective thrust loses (1 - lam_f)/4
        P['lam_f'] = 1.0 - (1.0 - P['lam_f']) / 4.0
        # mass change bounded by the 31 % collective headroom (Umax 300 of 950 hover)
        P['Jr'] = 1.0 + (P['Jr'] - 1.0) * 0.375
        # sensors: fused vertical speed ~0.1 m/s, altitude ~0.03 m (deg/s, deg for attitude)
        P['noise_p'] = P['noise_p'] * 0.05
        P['noise_phi'] = P['noise_phi'] * 0.1
    return P

AXIS_SCEN = {ax: {k: to_axis(P, ax) for k, P in SCEN.items()} for ax in ('pitch', 'yaw', 'z')}
# Yaw S2 bias is the measured Flight 8 imbalance rather than the generic scaled bias.
AXIS_SCEN['yaw']['S2 Mass/Inertia + Bias']['bias0'] = np.array([YAW_FLIGHT8_BIAS], float)

def mc_params(n, seed, axis):
    r = np.random.default_rng(seed)
    P = dict(Jr=r.uniform(0.8, 1.6, n), bias0=r.uniform(-0.025, 0.025, n),
             bias_f=r.uniform(-0.015, 0.015, n), lam_f=r.uniform(0.5, 1.0, n),
             t_f=r.uniform(4.0, 9.0, n), drag=r.uniform(0, 0.003, n),
             gust=r.uniform(0, 0.012, n), noise_p=r.uniform(0, 2.0, n),
             noise_phi=r.uniform(0, 0.3, n), qr_amp=r.uniform(0, 1.0, n), seed=seed)
    return to_axis(P, axis)

def tile(P, reps):
    return {k: (np.tile(v, reps) if k != 'seed' else v) for k, v in P.items()}

def concat(Ps):
    return {k: (np.concatenate([P[k] for P in Ps]) if k != 'seed' else Ps[0][k]) for k in Ps[0]}

GAMMAS = np.logspace(-1.5, 2.0, 15)
N_TUNE = 16

def tune(axis):
    Pt = mc_params(N_TUNE, 101, axis)
    res = {}
    for c in S.CONTROLLERS[1:]:
        P = tile(Pt, len(GAMMAS))
        g = np.repeat(GAMMAS, N_TUNE)
        m = S.metrics(S.simulate(axis, c, P, g), axis)
        J = m['rms_ref'].reshape(len(GAMMAS), N_TUNE)
        cost = np.where(np.isinf(J).any(1), np.inf, J.mean(1))
        res[c] = float(GAMMAS[np.argmin(cost)])
    return res

def plot_axis(axis, names, logs, tab, mc):
    t = logs['PID']['t']
    phim = logs['PID']['phi_m']
    i = names.index('S5 Combined')
    
    # 1. Time-response figure (combined scenario)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(t, logs['PID']['cmd'], color=INK2, lw=1.2, ls=':', label='Command')
    ax.plot(t, phim, color=INK, lw=1.4, ls='--', label='Nominal')
    for c in S.CONTROLLERS:
        ax.plot(t, logs[c]['phi'][:, i], color=COL[c], label=LABEL[c])
    ax.set_ylabel(f'{axis.capitalize()} (deg/m)')
    ax.set_xlabel('Time (s)')
    ax.legend(ncol=3, fontsize=9)
    ax.set_title(f'{axis.capitalize()} Combined Scenario', loc='left')
    save(fig, f'{axis}_time_response')
    
    # 2. Bar chart of RMS deviation per scenario
    fig, ax = plt.subplots(figsize=(10, 4))
    w = 0.12
    x = np.arange(len(names))
    for k, c in enumerate(S.CONTROLLERS):
        rms = tab[c]['rms_ref']
        ax.bar(x + k*w, rms, w, label=LABEL[c], color=COL[c])
    ax.set_xticks(x + 2.5*w)
    ax.set_xticklabels(names)
    ax.set_ylabel('RMS Deviation')
    ax.legend(ncol=6)
    ax.set_title(f'{axis.capitalize()} RMS Deviation per Scenario', loc='left')
    save(fig, f'{axis}_rms_bar')
    
    # 3. Monte Carlo box plot
    fig, ax = plt.subplots(figsize=(8, 4))
    data = [np.where(np.isinf(mc[c]['rms_ref']), np.nan, mc[c]['rms_ref']) for c in S.CONTROLLERS]
    for k, (c, d) in enumerate(zip(S.CONTROLLERS, data)):
        d = d[~np.isnan(d)]
        if len(d) > 0:
            bp = ax.boxplot(d, positions=[k], widths=0.5, patch_artist=True, showfliers=False)
            bp['boxes'][0].set_facecolor(COL[c])
    ax.set_xticks(range(len(S.CONTROLLERS)))
    ax.set_xticklabels([LABEL[c] for c in S.CONTROLLERS])
    ax.set_ylabel('RMS Deviation')
    ax.set_title(f'{axis.capitalize()} Monte Carlo RMS Deviation', loc='left')
    save(fig, f'{axis}_mc_box')

def main():
    results = {}
    mc_all = {}
    
    for axis in ('pitch', 'yaw', 'z'):
        print(f'Running {axis}...')
        tuned = tune(axis)
        gam = {'PID': 0.0, **tuned}
        
        names = list(SCEN)
        Pall = concat([AXIS_SCEN[axis][n] for n in names])
        
        logs, tab = {}, {}
        for c in S.CONTROLLERS:
            L = S.simulate(axis, c, Pall, np.full(len(names), gam[c]))
            logs[c] = L
            tab[c] = {k: v.tolist() for k, v in S.metrics(L, axis).items()}
            
        N_MC = 40
        Pm = mc_params(N_MC, 202, axis)
        mc = {}
        for c in S.CONTROLLERS:
            mc[c] = S.metrics(S.simulate(axis, c, Pm, np.full(N_MC, gam[c])), axis)
            
        results[axis] = {
            'scenarios': tab,
            'mc_medians': {c: {k: float(np.nanmedian(np.where(np.isinf(mc[c][k]), np.nan, mc[c][k]))) for k in mc[c]} for c in mc}
        }
        mc_all[axis] = mc
        
        plot_axis(axis, names, logs, tab, mc)
        
    # Summary figure across 3 axes
    fig, axs = plt.subplots(1, 3, figsize=(15, 4))
    for k, axis in enumerate(('pitch', 'yaw', 'z')):
        mc = mc_all[axis]
        data = [np.where(np.isinf(mc[c]['rms_ref']), np.nan, mc[c]['rms_ref']) for c in S.CONTROLLERS]
        for j, (c, d) in enumerate(zip(S.CONTROLLERS, data)):
            d = d[~np.isnan(d)]
            if len(d) > 0:
                bp = axs[k].boxplot(d, positions=[j], widths=0.5, patch_artist=True, showfliers=False)
                bp['boxes'][0].set_facecolor(COL[c])
        axs[k].set_xticks(range(len(S.CONTROLLERS)))
        axs[k].set_xticklabels([LABEL[c] for c in S.CONTROLLERS])
        axs[k].set_title(f'{axis.capitalize()} MC RMS')
    save(fig, 'summary_all_axes')
    
    json.dump(results, open(os.path.join(OUT, 'results_axes.json'), 'w'), indent=1)

if __name__ == '__main__':
    main()
