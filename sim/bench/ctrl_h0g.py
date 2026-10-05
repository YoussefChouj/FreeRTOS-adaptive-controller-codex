"""Item H step 2 (docs/analysis/adaptive-arch-study-2026-10-05.md sec P): three no-knob layers. H0_Sep's derived-rate
x, y, z layers plus ctrl_g's PID-term roll / pitch layer, whose rate is derived the same way instead of tuned:
  gamma_g = 1 / (SEP tau_r S_r)
  tau_r = (1 + B_RP Kd DT_IN) / (B_RP Kp)   roll / pitch rate-loop time constant (plant b, tuned rate PID; lag ignored)
  S_r   = ANG_PR Umax in rad/s              the rate-command limit, so s / S_r is dimensionless as in the outer layers
Every parameter is a FwPID one, so the PID's tuned set is passed unchanged (stress.py tag h0g_nom).
"""
import numpy as np
from plant import B_RP
from ctrl_mrac6 import DT_IN
from ctrl_g import PIDG_XYZ
from ctrl_h0 import H0_Sep


class H0G(PIDG_XYZ):
    name = 'h0g'
    SEP = H0_Sep.SEP

    def __init__(self, B, params=None):
        super().__init__(B, params)
        g = self.rate[0].g   # tuned roll rate gains; pitch shares them
        self.tau_r = (1.0 + B_RP * g['Kd'] * DT_IN) / (B_RP * g['Kp'])
        self.p['gamma_g'] = 1.0 / (self.SEP * self.tau_r * np.deg2rad(self.ang[0].g['Umax']))
        h = H0_Sep(B, self.p)
        self.g_xy, self.g_z = h.g_xy, h.g_z

    def xy_loop(self, o):   # MRAC6_Dec reads gamma_o at each call: the x, y layers get H0_Sep's rate
        self.p['gamma_o'] = self.g_xy
        return super().xy_loop(o)

    def z_rate(self, o, vz_des):
        self.p['gamma_o'] = self.g_z
        return super().z_rate(o, vz_des)
