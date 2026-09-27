"""Why the 3L predictive gate stays uniform (yard_3l: g_std = 0 for MRAC3L_Predictive).
  python diag_3l_pred.py      (zigzag_1.0 + circle_1.0, nominal, tuned predictive knobs, seed 500)
Prints the max |preview acceleration|, max |rate_m_ahead| (deg/s), max band energy E_P, and the max time-std
of the predictive gate g_P per band.  If E_P never exceeds the 1e-12 floor, log(E_P) is equal in every band
and the softmax is exactly uniform: the predictive layer is inert.
"""
import json
import numpy as np
import plant, scen
import ctrl_mrac3l as M

P = json.load(open('results/mrac3l_predictive_tune.json'))['params']


class D(M.MRAC3L_Predictive):
    def controller_update(self, o, u_nom, wd):
        u = super().controller_update(o, u_nom, wd)
        r = M._preview(o, self.p['horizon'])
        self.gl.append(self.g_smooth[:, 0].copy())
        self.log.append((np.abs(r['a'][:, :2]).max(), np.abs(self.rate_m_ahead).max(), self.E_P.max(),
                         self.E_P.min()))
        return u


for rl in ([('zigzag_1.0', 'nominal', 500)], [('circle_1.0', 'nominal', 500)]):
    ref, sp = scen.build(rl)
    c = D(1, P); c.log, c.gl = [], []
    plant.run(c, ref, sp, seed=500)
    L = np.array(c.log)
    print(rl[0][0], 'max|a_preview| %.3g m/s2, max|rate_m_ahead| %.3g deg/s, E_P max %.3g min %.3g'
          % (L[:, 0].max(), L[:, 1].max(), L[:, 2].max(), L[:, 3].min()))
    G = np.array(c.gl)                      # (t, axis, band)
    print('   g_smooth time-mean per band (roll)', np.round(G[:, 0].mean(0), 4), 'time-std', np.round(G[:, 0].std(0), 4))
