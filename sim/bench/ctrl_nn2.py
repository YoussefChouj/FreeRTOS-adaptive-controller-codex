"""Item 7 of the adaptive-arch study (doc sec L): richer, x/y-coupled features on the x/y layers of MRAC5_XYZ.

MRAC5_XYZ (ctrl_mrac6.py) adapts x and y separately on the fixed basis [1, v_i, v_i|v_i|].  Two richer bases, both
coupled across x and y (an arm load or drag that depends on the other axis's speed can be learned).  Reference model,
error, normalisation and box projection |th| <= tan(TILT_MAX) are those of ctrl_mrac6._Layer; both add e-modification
(kappa: the weights leak toward zero / their start in proportion to |e|, which bounds them without persistent
excitation).  The z layer is MRAC5_XYZ's; there is no yaw layer.
  RBF2_XYZ  [1, v_i, v_i|v_i|] + 9 Gaussians on a 3x3 grid over (v_x, v_y)/VXY_MAX, width rbf_w.  Linear in the
            weights, so the usual MRAC argument still applies.
  NN2_XYZ   one hidden layer: H tanh units on x = [1, v_x, v_y, vd_x, vd_y] (all / VXY_MAX), two outputs (x, y).
            Outer weights W by the same normalised law, inner weights V by the back-propagated term
            x (sigma' * W e)^T, the two-layer NN tuning law of Lewis, Yesildirek and Liu (IEEE TNN 1996, cited from
            memory).  V starts at a fixed random draw V0 (seed 0) and leaks back toward it.
Frozen bench files are not edited.
"""
import numpy as np
from fwpid import VXY_MAX, TILT_MAX
import ctrl_mrac6 as m6

TH_XY = np.tan(np.deg2rad(TILT_MAX))


def _rows(x, B, nd):
    """A parameter (scalar, or one value per row while tuning) as a (B, 1, ...) column with nd trailing axes."""
    return np.broadcast_to(np.asarray(x, float), (B,)).reshape((B,) + (1,) * nd)
GRID = np.array([(a, b) for a in (-0.6, 0.0, 0.6) for b in (-0.6, 0.0, 0.6)])


class RBF2_XYZ(m6.MRAC5_XYZ):
    name = 'rbf2_xyz'
    N_RBF = len(GRID)
    PARAMS = dict(m6.MRAC5_XYZ.PARAMS)
    PARAMS['rbf_w'] = (0.5, 0.2, 1.5, 'log')
    PARAMS['kappa'] = (0.01, 1e-4, 1.0, 'log')

    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.th = np.zeros((B, 2, 3 + self.N_RBF))
        self.xref = None

    def ref_error(self, vd, v):
        """Per-axis reference model (pole from the wrapped velocity loop, as MRAC5_XYZ) and its tracking error."""
        a = _rows(self.lxy[0].a, self.B, 1)
        self.xref = v.copy() if self.xref is None else self.xref + a * (vd - self.xref)
        return v - self.xref

    def feats(self, v):
        d2 = ((v[:, None, :] - GRID[None, :self.N_RBF]) ** 2).sum(2)
        g = np.exp(-d2 / (2.0 * _rows(self.p['rbf_w'], self.B, 1) ** 2))
        one = np.ones((self.B, 1))
        return [np.concatenate([one, v[:, i:i + 1], v[:, i:i + 1] * np.abs(v[:, i:i + 1]), g], 1) for i in range(2)]

    def adapt(self, vd, v):
        """Add-on in g units for x and y; vd, v are (B, 2) and already divided by VXY_MAX."""
        p = self.p
        e = self.ref_error(vd, v)
        kap = _rows(p['kappa'], self.B, 1)
        out = np.zeros((self.B, 2))
        for i, phi in enumerate(self.feats(v)):
            g = p['gamma_o'] * m6.DT_XY / (1.0 + np.sum(phi * phi, 1))
            th = self.th[:, i]
            th += g[:, None] * (e[:, i:i + 1] * phi - kap * np.abs(e[:, i:i + 1]) * th)
            np.clip(th, -TH_XY, TH_XY, out=th)
            out[:, i] = np.sum(th * phi, 1)
        return out

    def xy_loop(self, o):
        p = self.p; r = o['ref']
        e = (r['p'][:, :2] - o['pos'][:, :2]) * 100.0
        a, vd = np.zeros((self.B, 2)), np.zeros((self.B, 2))
        for i in range(2):
            vd[:, i] = np.clip(self.locx[i].step(e[:, i]), -VXY_MAX, VXY_MAX) + p['ff_v'] * r['v'][:, i] * 100.0
            a[:, i] = self.locxs[i].step(vd[:, i] - o['vel'][:, i] * 100.0) + p['ff_a'] * r['a'][:, i] * 100.0
        a -= m6.G_CM * self.adapt(vd / VXY_MAX, o['vel'][:, :2] * 100.0 / VXY_MAX)
        ps = np.deg2rad(o['rpy'][:, 2]); c, s = np.cos(ps), np.sin(ps)
        af, al = c * a[:, 0] + s * a[:, 1], -s * a[:, 0] + c * a[:, 1]
        return np.clip(np.rad2deg(np.stack([np.arctan(-al / 981.0), np.arctan(af / 981.0)], 1)), -TILT_MAX, TILT_MAX)


class NN2_XYZ(RBF2_XYZ):
    name = 'nn2_xyz'
    H, V_MAX = 8, 3.0
    PARAMS = dict(m6.MRAC5_XYZ.PARAMS)
    PARAMS['gamma_v'] = (0.1, 1e-3, 10.0, 'log')
    PARAMS['kappa'] = (0.01, 1e-4, 1.0, 'log')

    def __init__(self, B, params=None):
        m6.MRAC5_XYZ.__init__(self, B, params)
        self.V0 = np.random.default_rng(0).normal(0.0, 1.0, (5, self.H))
        self.V = np.repeat(self.V0[None], B, 0)
        self.W = np.zeros((B, self.H + 1, 2))
        self.xref = None

    def adapt(self, vd, v):
        p = self.p
        e = self.ref_error(vd, v)
        x = np.concatenate([np.ones((self.B, 1)), v, vd], 1)
        s = np.tanh(np.einsum('bi,bih->bh', x, self.V))
        sig = np.concatenate([np.ones((self.B, 1)), s], 1)
        n = 1.0 + np.sum(sig * sig, 1) + np.sum(x * x, 1)
        ne = np.sqrt(np.sum(e * e, 1))[:, None, None] * _rows(p['kappa'], self.B, 2)
        gw = (p['gamma_o'] * m6.DT_XY / n)[:, None, None]
        gv = (p['gamma_v'] * m6.DT_XY / n)[:, None, None]
        back = np.einsum('bhk,bk->bh', self.W[:, 1:], e) * (1.0 - s * s)
        self.W += gw * (sig[:, :, None] * e[:, None, :] - ne * self.W)
        self.V += gv * (x[:, :, None] * back[:, None, :] - ne * (self.V - self.V0))
        np.clip(self.W, -TH_XY, TH_XY, out=self.W)
        np.clip(self.V, self.V0 - self.V_MAX, self.V0 + self.V_MAX, out=self.V)
        return np.einsum('bh,bhk->bk', sig, self.W)
