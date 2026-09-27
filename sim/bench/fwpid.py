"""Firmware PID cascade (StabilizerTask structure) as a bench controller.

Stages are methods so other controllers can override one stage and keep the rest:
  xy_loop (100 Hz) -> roll/pitch Des [deg];  z_loop (Z_pos 100 Hz, Z_rate 200 Hz) -> thr PWM;
  att_loop (200 Hz) -> rate Des [dps];  rate_loop (200 Hz) -> U;  controller_update hook
  (= firmware Controller_Update(axis, u_nom)) returns u_nom + correction (zero here).
PARAMS are multiplicative scales on firmware gains (1.0 = firmware) plus reference
feed-forward gains (0 = firmware) and the throttle base (2950 = firmware).
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'adaptive_compare'))
from sim_coupled import PID, ANG_PR, RATE_PR, ANG_Y, RATE_Y, POS_Z, RATE_Z  # noqa: E402
from plant import Controller  # noqa: E402

LOCX = dict(Kp=0.8, Ki=0.01, Kd=4.0, Umax=300, Upmax=300, Uimax=300, Udmax=300, SumEmax=200, EMin=30)
LOCXS = dict(Kp=3.0, Ki=0.0, Kd=6.0, Umax=600, Upmax=600, Uimax=600, Udmax=600, SumEmax=200, EMin=10)
VXY_MAX, TILT_MAX, YAWRATE_MAX, THR_BASE, DZ_MAX = 120.0, 15.0, 60.0, 2950.0, 0.005


def scaled(g, **s):
    g = dict(g)
    for k, v in s.items():
        g[k] = g[k] * v
    return g


class FwPID(Controller):
    name = 'pid'
    PARAMS = {'pos_kp': (1.0, 0.25, 4.0, 'log'), 'pos_kd': (1.0, 0.25, 4.0, 'log'),
              'vel_kp': (1.0, 0.25, 4.0, 'log'), 'vel_kd': (1.0, 0.25, 4.0, 'log'),
              'ang_kp': (1.0, 0.5, 2.0, 'log'), 'rate_kp': (1.0, 0.5, 2.0, 'log'),
              'rate_kd': (1.0, 0.5, 2.0, 'log'), 'zp_kp': (1.0, 0.25, 4.0, 'log'),
              'zp_ki': (1.0, 0.25, 8.0, 'log'), 'zr_kp': (1.0, 0.25, 4.0, 'log'),
              'z_sumemax': (1.0, 1.0, 30.0, 'log'), 'thr_base': (THR_BASE, 2900.0, 3200.0, 'lin'),
              'ff_v': (0.0, 0.0, 1.0, 'lin'), 'ff_a': (0.0, 0.0, 1.0, 'lin')}

    def __init__(self, B, params=None):
        super().__init__(B, params)
        p = self.p
        self.locx = [PID(scaled(LOCX, Kp=p['pos_kp'], Kd=p['pos_kd']), B) for _ in range(2)]
        self.locxs = [PID(scaled(LOCXS, Kp=p['vel_kp'], Kd=p['vel_kd']), B) for _ in range(2)]
        self.ang = [PID(scaled(ANG_PR, Kp=p['ang_kp']), B) for _ in range(2)]
        self.rate = [PID(scaled(RATE_PR, Kp=p['rate_kp'], Kd=p['rate_kd']), B) for _ in range(2)]
        self.angy, self.ratey = PID(ANG_Y, B), PID(RATE_Y, B)
        self.zpos = PID(scaled(POS_Z, Kp=p['zp_kp'], Ki=p['zp_ki'], SumEmax=p['z_sumemax']), B)
        self.zrate = PID(scaled(RATE_Z, Kp=p['zr_kp'], SumEmax=p['z_sumemax']), B)
        self.des = np.zeros((B, 2)); self.vz_des = np.zeros(B); self.tz = None

    def xy_loop(self, o):
        p = self.p; r = o['ref']
        e = (r['p'][:, :2] - o['pos'][:, :2]) * 100.0
        a = np.zeros((self.B, 2))
        for i in range(2):
            vd = np.clip(self.locx[i].step(e[:, i]), -VXY_MAX, VXY_MAX) + p['ff_v'] * r['v'][:, i] * 100.0
            a[:, i] = self.locxs[i].step(vd - o['vel'][:, i] * 100.0) + p['ff_a'] * r['a'][:, i] * 100.0
        ps = np.deg2rad(o['rpy'][:, 2]); c, s = np.cos(ps), np.sin(ps)
        af, al = c * a[:, 0] + s * a[:, 1], -s * a[:, 0] + c * a[:, 1]
        return np.clip(np.rad2deg(np.stack([np.arctan(-al / 981.0), np.arctan(af / 981.0)], 1)), -TILT_MAX, TILT_MAX)

    def z_pos(self, o):
        zr = o['ref']['p'][:, 2]
        self.tz = zr.copy() if self.tz is None else self.tz + np.clip(zr - self.tz, -DZ_MAX, DZ_MAX)
        return self.zpos.step(self.tz - o['pos'][:, 2]) + self.p['ff_v'] * o['ref']['v'][:, 2]

    def z_rate(self, o, vz_des):
        return self.zrate.step(vz_des - o['vel'][:, 2]) + self.p['thr_base']

    def att_loop(self, o, des):
        wd = np.stack([self.ang[i].step(des[:, i] - o['rpy'][:, i]) for i in range(2)], 1)
        ey = (o['ref']['yaw'] - o['rpy'][:, 2] + 180.0) % 360.0 - 180.0
        wz = np.clip(self.angy.step(ey), -YAWRATE_MAX, YAWRATE_MAX)
        return np.concatenate([wd, wz[:, None]], 1)

    def rate_loop(self, o, wd):
        e = wd - o['gyro']
        return np.stack([self.rate[0].step(e[:, 0]), self.rate[1].step(e[:, 1]), self.ratey.step(e[:, 2])], 1)

    def controller_update(self, o, u_nom, wd):
        return u_nom

    def step(self, o):
        if o['k'] % 2 == 0:
            self.des = self.xy_loop(o)
            self.vz_des = self.z_pos(o)
        thr = self.z_rate(o, self.vz_des)
        wd = self.att_loop(o, self.des)
        U = self.controller_update(o, self.rate_loop(o, wd), wd)
        return dict(U=U, thr=thr)
