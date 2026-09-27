"""Scenarios, Gamma tuning, Monte Carlo and figures for the PID vs MRAC comparison."""
import os
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import sim_core as S

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'figures')
os.makedirs(OUT, exist_ok=True)

# ---- style (dataviz reference palette, validated light mode) ---------------------------
SURF, INK, INK2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#e4e3df'
COL = {'PID': '#8a8984', 'S6': '#2a78d6', 'S10': '#eb6834',
       'RBF6': '#1baf7a', 'RBF12': '#eda100', 'RBF24': '#e87ba4'}
LABEL = {'PID': 'Cascaded PID (baseline)', 'S6': 'PID + MRAC structured 6',
         'S10': 'PID + MRAC structured 10', 'RBF6': 'PID + MRAC RBF 6',
         'RBF12': 'PID + MRAC RBF 12', 'RBF24': 'PID + MRAC RBF 24'}
SHORT = {'PID': 'PID', 'S6': 'Struct-6', 'S10': 'Struct-10',
         'RBF6': 'RBF-6', 'RBF12': 'RBF-12', 'RBF24': 'RBF-24'}
plt.rcParams.update({
    'figure.facecolor': SURF, 'axes.facecolor': SURF, 'savefig.facecolor': SURF,
    'axes.edgecolor': INK2, 'axes.labelcolor': INK, 'text.color': INK,
    'xtick.color': INK2, 'ytick.color': INK2, 'axes.grid': True, 'grid.color': GRID,
    'grid.linewidth': 0.6, 'axes.spines.top': False, 'axes.spines.right': False,
    'lines.linewidth': 2.0, 'font.size': 10.5, 'axes.titlesize': 11.5,
    'axes.titleweight': 'semibold', 'legend.frameon': False, 'font.family': 'DejaVu Sans'})


def save(fig, name):
    for ext in ('png', 'pdf'):
        fig.savefig(os.path.join(OUT, f'{name}.{ext}'), dpi=300, bbox_inches='tight')
    plt.close(fig)


# ---- scenarios -------------------------------------------------------------------------
def scen(**kw):
    P = S.nominal_params(1)
    for k, v in kw.items():
        P[k] = np.array([v], float) if k != 'seed' else v
    return P


SCEN = {
    'S1 Nominal': scen(seed=11),
    'S2 Payload + CG offset': scen(Jr=1.4, bias0=0.02, seed=12),
    'S3 Motor loss @ 6 s': scen(lam_f=0.6, bias_f=0.015, t_f=6.0, seed=13),
    'S4 Drag + gusts': scen(drag=0.002, gust=0.01, qr_amp=1.0, seed=14),
    'S5 Combined + noise': scen(Jr=1.3, bias0=0.015, lam_f=0.7, bias_f=0.01, t_f=6.0,
                                drag=0.002, gust=0.008, noise_p=1.5, noise_phi=0.3,
                                qr_amp=1.0, seed=15),
}


def mc_params(n, seed):
    r = np.random.default_rng(seed)
    return dict(Jr=r.uniform(0.8, 1.6, n), bias0=r.uniform(-0.025, 0.025, n),
                bias_f=r.uniform(-0.015, 0.015, n), lam_f=r.uniform(0.5, 1.0, n),
                t_f=r.uniform(4.0, 9.0, n), drag=r.uniform(0, 0.003, n),
                gust=r.uniform(0, 0.012, n), noise_p=r.uniform(0, 2.0, n),
                noise_phi=r.uniform(0, 0.3, n), qr_amp=r.uniform(0, 1.0, n), seed=seed)


def tile(P, reps):
    return {k: (np.tile(v, reps) if k != 'seed' else v) for k, v in P.items()}


def concat(Ps):
    return {k: (np.concatenate([P[k] for P in Ps]) if k != 'seed' else Ps[0][k]) for k in Ps[0]}


# ---- 1. Gamma tuning (same budget for every adaptive layer) ------------------------------
GAMMAS = np.logspace(-1.5, 2.0, 15)
N_TUNE = 16


def tune():
    Pt = mc_params(N_TUNE, 101)
    res = {}
    for c in S.CONTROLLERS[1:]:
        P = tile(Pt, len(GAMMAS))
        g = np.repeat(GAMMAS, N_TUNE)
        m = S.metrics(S.simulate(c, P, g))
        J = m['rms_ref'].reshape(len(GAMMAS), N_TUNE)
        # cost: mean RMS error; any divergence disqualifies that Gamma
        cost = np.where(np.isinf(J).any(1), np.inf, J.mean(1))
        res[c] = dict(cost=cost.tolist(), best=float(GAMMAS[np.argmin(cost)]))
        print(f'tune {c}: best Gamma {res[c]["best"]:.1f}  cost {np.min(cost):.3f}')
    return res


def main():
    tuned = tune()
    gam = {'PID': 0.0, **{c: tuned[c]['best'] for c in tuned}}

    # ---- 2. deterministic scenarios ------------------------------------------------------
    names = list(SCEN)
    Pall = concat([SCEN[n] for n in names])
    logs, tab = {}, {}
    for c in S.CONTROLLERS:
        # one batch per controller; lanes = scenarios (gusts are per-lane seeded by batch seed)
        L = S.simulate(c, Pall, np.full(len(names), gam[c]))
        logs[c] = L
        tab[c] = {k: v.tolist() for k, v in S.metrics(L).items()}

    # ---- 3. Monte Carlo ---------------------------------------------------------------------
    N_MC = 80
    Pm = mc_params(N_MC, 202)
    mc = {}
    for c in S.CONTROLLERS:
        mc[c] = S.metrics(S.simulate(c, Pm, np.full(N_MC, gam[c])))
    json.dump(dict(gamma=gam, tuning=tuned, scenarios=names, table=tab,
                   mc={c: {k: v.tolist() for k, v in mc[c].items()} for c in mc}),
              open(os.path.join(OUT, 'results.json'), 'w'), indent=1)

    figures(names, logs, tab, mc, tuned, gam)


# ---- figures ---------------------------------------------------------------------------------
def figures(names, logs, tab, mc, tuned, gam):
    t = logs['PID']['t']
    cmd = logs['PID']['cmd']
    phim = logs['PID']['phi_m']

    # F1 hero: combined scenario, attitude + deviation from nominal
    i = names.index('S5 Combined + noise')
    show = ['PID', 'S6', 'S10', 'RBF24']
    fig, ax = plt.subplots(2, 1, figsize=(10, 6.2), sharex=True,
                           gridspec_kw=dict(height_ratios=[1.35, 1]))
    ax[0].plot(t, cmd, color=INK2, lw=1.2, ls=':', label='Roll command')
    ax[0].plot(t, phim, color=INK, lw=1.4, ls='--', label='Nominal reference (healthy drone)')
    for c in show:
        ax[0].plot(t, logs[c]['phi'][:, i], color=COL[c], label=LABEL[c])
        ax[1].plot(t, logs[c]['phi'][:, i] - phim, color=COL[c], label=LABEL[c])
    for a in ax:
        a.axvline(6.0, color=INK2, lw=0.8)
    ax[0].text(6.08, 17.2, 'motor loss (70 %)', color=INK2, fontsize=9, va='top')
    ax[0].set_ylabel('Roll angle (deg)')
    ax[1].set_ylabel('Deviation from nominal (deg)')
    ax[1].set_xlabel('Time (s)')
    ax[0].set_title('Combined scenario: +30 % inertia, CG offset, motor loss, drag, gusts, sensor noise',
                    loc='left')
    ax[0].legend(ncol=3, loc='lower left', fontsize=9, bbox_to_anchor=(0, 1.08))
    ax[1].axhline(0, color=INK2, lw=0.6)
    fig.align_ylabels(ax)
    save(fig, 'F1_hero_combined')

    # F2 small multiples: deviation from nominal, every scenario, every controller
    fig, axs = plt.subplots(len(names), 1, figsize=(10, 11), sharex=True)
    for j, (a, n) in enumerate(zip(axs, names)):
        for c in S.CONTROLLERS:
            a.plot(t, logs[c]['phi'][:, j] - phim, color=COL[c], lw=1.6, label=SHORT[c])
        a.axhline(0, color=INK2, lw=0.6)
        a.set_title(n, loc='left', fontsize=10.5)
        a.set_ylabel('deg')
    axs[0].legend(ncol=6, loc='lower left', bbox_to_anchor=(0, 1.25), fontsize=9)
    axs[-1].set_xlabel('Time (s)')
    fig.suptitle('Roll deviation from the nominal (healthy) closed loop', x=0.125, ha='left',
                 y=0.995, fontweight='semibold')
    fig.tight_layout()
    save(fig, 'F2_scenarios_deviation')

    # F3 RMS heatmap (sequential blue) with numbers in text ink
    M = np.array([tab[c]['rms_ref'] for c in S.CONTROLLERS]).T
    fig, a = plt.subplots(figsize=(9.2, 3.9))
    from matplotlib.colors import LinearSegmentedColormap
    cm = LinearSegmentedColormap.from_list('blue', ['#f3f7fd', '#9ec5f4', '#2a78d6', '#104281'])
    im = a.imshow(M, cmap=cm, aspect='auto')
    for (r, k), v in np.ndenumerate(M):
        a.text(k, r, f'{v:.2f}', ha='center', va='center', fontsize=10,
               color='#ffffff' if v > 0.6 * M.max() else INK)
    a.set_xticks(range(len(S.CONTROLLERS)), [SHORT[c] for c in S.CONTROLLERS])
    a.set_yticks(range(len(names)), names)
    a.grid(False)
    for s_ in a.spines.values():
        s_.set_visible(False)
    a.tick_params(length=0)
    cb = fig.colorbar(im, ax=a, fraction=0.03, pad=0.02)
    cb.set_label('RMS deviation (deg)', color=INK2)
    cb.outline.set_visible(False)
    a.set_title('RMS roll deviation from nominal per scenario (lower is better)', loc='left')
    save(fig, 'F3_rms_heatmap')

    # F4 Monte Carlo distribution
    fig, a = plt.subplots(figsize=(9, 4.6))
    data = [np.where(np.isinf(mc[c]['rms_ref']), np.nan, mc[c]['rms_ref']) for c in S.CONTROLLERS]
    for k, (c, d) in enumerate(zip(S.CONTROLLERS, data)):
        d = d[~np.isnan(d)]
        bp = a.boxplot(d, positions=[k], widths=0.5, patch_artist=True, showfliers=False,
                       medianprops=dict(color=INK, lw=1.6), whiskerprops=dict(color=INK2),
                       capprops=dict(color=INK2), boxprops=dict(edgecolor=INK2))
        bp['boxes'][0].set_facecolor(COL[c])
        bp['boxes'][0].set_alpha(0.85)
        jit = np.random.default_rng(k).uniform(-0.18, 0.18, len(d))
        a.scatter(k + jit, d, s=9, color=INK2, alpha=0.45, lw=0, zorder=3)
        a.text(k, np.nanpercentile(d, 100) * 1.04, f'median {np.median(d):.2f}', ha='center',
               fontsize=8.5, color=INK2)
    n_mc = len(data[0])
    a.set_xticks(range(len(S.CONTROLLERS)), [SHORT[c] for c in S.CONTROLLERS])
    a.set_ylabel('RMS deviation from nominal (deg)')
    a.set_ylim(0, a.get_ylim()[1] * 1.1)
    a.set_title(f'Monte Carlo robustness ({n_mc} random drones: inertia 0.8-1.6x, CG offset, '
                'motor loss 0-50 %, gusts, noise)', loc='left', fontsize=10.5)
    a.grid(axis='x', visible=False)
    save(fig, 'F4_monte_carlo')

    # F5 what the adaptive layer learns: u_ad vs matched disturbance
    fig, axs = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for a, n in zip(axs, ['S2 Payload + CG offset', 'S3 Motor loss @ 6 s']):
        j = names.index(n)
        d = logs['S6']['d_eq'][:, j]
        k = np.ones(40) / 40
        ideal = -np.convolve(d, k, 'same')
        ideal[(t < 0.15) | (t > t[-1] - 0.15)] = np.nan   # drop moving-average edge effects
        a.plot(t, ideal, color=INK, lw=1.4, ls='--',
               label='Ideal cancellation (-matched disturbance, 0.2 s avg)')
        for c in ['S6', 'S10', 'RBF6', 'RBF24']:
            a.plot(t, logs[c]['u_ad'][:, j], color=COL[c], lw=1.6, label=LABEL[c])
        a.set_title(n, loc='left')
        a.set_xlabel('Time (s)')
        a.axhline(0, color=INK2, lw=0.6)
    axs[0].set_ylabel('Adaptive command u_ad (PID units)')
    fig.suptitle('What the adaptive layer learns', x=0.07, ha='left', fontweight='semibold')
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    h, l = axs[0].get_legend_handles_labels()
    fig.legend(h, l, ncol=3, fontsize=8.5, loc='upper left', bbox_to_anchor=(0.07, 0.93))
    save(fig, 'F5_uad_vs_disturbance')

    # F6 feature count vs performance (two families, one axis)
    fig, a = plt.subplots(figsize=(7.5, 4.4))
    med = {c: np.nanmedian(np.where(np.isinf(mc[c]['rms_ref']), np.nan, mc[c]['rms_ref']))
           for c in S.CONTROLLERS}
    fam = {'Structured (physics basis)': ['S6', 'S10'], 'RBF (unstructured)': ['RBF6', 'RBF12', 'RBF24']}
    for (lab, cs), col in zip(fam.items(), ['#2a78d6', '#1baf7a']):
        x = [S.n_features(c) for c in cs]
        y = [med[c] for c in cs]
        a.plot(x, y, color=col, marker='o', ms=8, label=lab)
        for c, xx, yy in zip(cs, x, y):
            a.annotate(SHORT[c], (xx, yy), textcoords='offset points', xytext=(6, 6),
                       fontsize=9, color=INK2)
    a.axhline(med['PID'], color=COL['PID'], lw=1.4, ls='--', label='PID baseline')
    a.set_xlabel('Number of adaptive parameters (features)')
    a.set_ylabel('Median MC RMS deviation (deg)')
    a.set_title('More features is not automatically better', loc='left')
    a.legend(loc='upper right', fontsize=9)
    a.set_ylim(bottom=0)
    save(fig, 'F6_features_vs_error')

    # F7 Gamma sensitivity (tuning robustness)
    fig, a = plt.subplots(figsize=(7.5, 4.4))
    for c in S.CONTROLLERS[1:]:
        cost = np.array(tuned[c]['cost'])
        a.plot(GAMMAS[np.isfinite(cost)], cost[np.isfinite(cost)], color=COL[c], marker='o',
               ms=4, label=SHORT[c])
    a.set_xscale('log')
    a.set_xlabel('Adaptation gain Gamma')
    a.set_ylabel('Mean RMS deviation, tuning set (deg)')
    a.set_title('Sensitivity to adaptation gain (same tuning budget for all)', loc='left')
    a.legend(fontsize=9, ncol=2)
    save(fig, 'F7_gamma_sensitivity')

    # F8 control effort and smoothness in the combined scenario
    fig, axs = plt.subplots(2, 1, figsize=(10, 5.6), sharex=True)
    for c in ['PID', 'S6', 'RBF24']:
        u = logs[c]['U_pid'][:, i] + logs[c]['u_ad'][:, i]
        axs[0].plot(t, u, color=COL[c], lw=1.2, label=LABEL[c])
        axs[1].plot(t, logs[c]['u_ad'][:, i], color=COL[c], lw=1.6, label=LABEL[c])
    axs[0].set_ylabel('Total rate command (PID units)')
    axs[1].set_ylabel('Adaptive part u_ad')
    axs[1].set_xlabel('Time (s)')
    axs[0].legend(ncol=3, fontsize=9, loc='lower left', bbox_to_anchor=(0, 1.02))
    fig.align_ylabels(axs)
    save(fig, 'F8_control_effort')


if __name__ == '__main__':
    main()
