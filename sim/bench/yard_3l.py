"""3-layer MRAC yardsticks + routing diagnosis (night-run.md), with each mode's TUNED knobs.
  python yard_3l.py mrac3l_unrouted mrac3l_reactive ...   (tag -> results/<tag>_tune.json 'params')
(1) reference model vs plant: RMSE % of true-rate RMS and lag, zigzag_1.0 nominal noise-free, gamma = 0.
    Same method as sanity_mrac3l.check_b, which used firmware-default knobs; the ref model copies the tuned
    ang/rate gains, so it must be measured with them.  A tag without a 3L class runs on MRAC3L_Unrouted.
(2) routing diagnosis on every 5th tune row (all 4 trajectories): time-mean gate weight per band (and its time std), max ||W||,
    RMS u_ad / RMS u_nom (roll+pitch).  Output: stdout + results/yard_3l.json.
"""
import json, sys
import numpy as np
import plant, scen
import ctrl_mrac3l as M

CLS = {'mrac3l_unrouted': M.MRAC3L_Unrouted, 'mrac3l_reactive': M.MRAC3L_Reactive,
       'mrac3l_predictive': M.MRAC3L_Predictive, 'mrac3l_both': M.MRAC3L_Both}
DT = 0.005


def refmodel(cls, params):
    class Log(cls):
        def controller_update(self, o, u_nom, wd):
            self.p['gamma'] = 0.0
            u = super().controller_update(o, u_nom, wd)
            self.log.append(self.rate_m[:, 0].copy())
            return u
    ref, sp = scen.build([('zigzag_1.0', 'nominal', 0)])
    sp['noise_scale'] = np.array([0.0])
    sp['gyro_bias'] = np.zeros((1, 3)); sp['acc_bias'] = np.zeros((1, 3))
    c = Log(1, params); c.log = []
    L = plant.run(c, ref, sp, seed=0)
    rm = np.array(c.log)
    e = L['e'][0]
    w = np.zeros((e.shape[0], 2))
    for k in range(1, e.shape[0] - 1):
        ed = (e[k + 1] - e[k - 1]) / (2 * DT)
        ph, th = e[k][0], e[k][1]
        w[k] = np.rad2deg([ed[0] - ed[2] * np.sin(th), ed[1] * np.cos(ph) + ed[2] * np.sin(ph) * np.cos(th)])
    w[0] = w[1]; w[-1] = w[-2]
    k0 = int(scen.T_HOLD / DT); n = min(len(rm), len(w))
    a, b = rm[k0:n], w[k0:n]
    pct = 100 * np.sqrt(np.mean((a - b) ** 2)) / (np.sqrt(np.mean(b ** 2)) + 1e-6)
    lag = np.mean([(np.argmax(np.correlate(a[:, i], b[:, i], 'full')) - (len(b) - 1)) * DT * 1000 for i in range(2)])
    return float(pct), float(lag)


def diag(cls, params, rl):
    class D(cls):
        def controller_update(self, o, u_nom, wd):
            u = super().controller_update(o, u_nom, wd)
            self.gs.append(self.g_smooth.mean(axis=(0, 1)))
            self.wn.append(np.sqrt((self.W ** 2).sum(-1)).max())
            self.ua.append(self.u_ad_smooth.copy()); self.un.append(u_nom[:, :2].T.copy())
            return u
    ref, sp = scen.build(rl)
    c = D(len(rl), params); c.gs, c.wn, c.ua, c.un = [], [], [], []
    plant.run(c, ref, sp, seed=500)
    g = np.array(c.gs); ua = np.array(c.ua); un = np.array(c.un)
    return dict(g_mean=np.round(np.nanmean(g, 0), 3).tolist(), g_std=np.round(np.nanstd(g, 0), 3).tolist(),
                w_max=float(np.nanmax(c.wn)),
                uad_over_unom=float(np.sqrt(np.nanmean(ua ** 2)) / np.sqrt(np.nanmean(un ** 2))))


def main():
    rl = scen.rows('tune')[::5]      # stride 5 (not 8): every 8th row was a 'steps' row (fix 2026-09-28)
    out = {}
    for t in sys.argv[1:]:
        p = json.load(open('results/%s_tune.json' % t))['params']
        cls = CLS.get(t, M.MRAC3L_Unrouted)
        pct, lag = refmodel(cls, p)
        d = diag(cls, p, rl)
        out[t] = dict(refmodel_rmse_pct=round(pct, 1), refmodel_lag_ms=round(lag, 1), n_rows=len(rl), **d)
        print(t, json.dumps(out[t]))
    json.dump(out, open('results/yard_3l.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
