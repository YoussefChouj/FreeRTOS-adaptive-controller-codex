"""The flown firmware MRAC in the bench loop: a bench PID controller plus API/mrac*.c run by the host replay driver
(ground_station/analysis/mrac_log_replay_host.c, stream mode), one driver process per row.

Per 5 ms tick the driver gets the firmware inputs (armed, FLYING, gyroy/gyrox/gyroz/Z_rate Des FB U, pitch/roll deg,
the previous tick's mixer deficit) and returns u_ad * mrac_to_mixer * fade * inj_alpha per axis, which is added to
the PID output the way API/controller.c Controller_Update adds it (before StabilizerTask negates gyroy):
mix pitch -> U[:, 1], roll -> U[:, 0], yaw -> U[:, 2], z -> throttle. Bench pitch/roll/yaw/z units are the
firmware's (deg/s, m/s, PWM), so no scaling.  cfg = extra driver args, e.g. ['cfg:0:omega_u:20'] (axis 0 pitch,
1 roll, 2 yaw, 3 z; 'gamma' scales every rate). mask = g_ctrl_axis_mask (bit per axis, clear = pure PID there,
the law still learns in shadow).

    ctrl = with_mrac(fwpid.FwPID, inj=1, cfg=[...])(B, params)
"""
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ground_station.analysis import mrac_log_replay as rp  # noqa: E402

EXE_DIR = Path('C:/tmp/mrac_x')
N_IN = 20


def with_mrac(base, inj=1, cfg=(), simplex=2, mask=0x0F):
    class FwMRAC(base):
        name = base.__name__.lower() + '+fw_mrac'

        def __init__(self, B, params=None):
            super().__init__(B, params)
            EXE_DIR.mkdir(parents=True, exist_ok=True)
            exe = rp.build(EXE_DIR, False)
            args = [str(exe), '-', f'inj:{inj}', f'simplex:{simplex}', *cfg]
            self.procs = [subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL) for _ in range(B)]
            self.n_out = rp.N_OUT + 4
            self.udef = np.zeros((B, 4))
            self.trace = []          # per tick (B, n_out) driver outputs

        def mrac(self, o, wd, U, thr):
            B = self.B; z = np.zeros(B)
            ins = np.stack([np.ones(B), np.ones(B),
                            wd[:, 1], o['gyro'][:, 1], U[:, 1],
                            wd[:, 0], o['gyro'][:, 0], U[:, 0],
                            wd[:, 2], o['gyro'][:, 2], U[:, 2],
                            self.vz_des + z, o['vel'][:, 2], thr - self.p['thr_base'],
                            o['rpy'][:, 1], o['rpy'][:, 0], *self.udef.T], 1).astype(np.float32)
            for i, pr in enumerate(self.procs):
                pr.stdin.write(ins[i].tobytes())
                pr.stdin.flush()
            out = np.stack([np.frombuffer(pr.stdout.read(4 * self.n_out), np.float32) for pr in self.procs])
            self.trace.append(out)
            return out[:, -4:].astype(float) * [(mask >> i) & 1 for i in range(4)]

        def step(self, o):
            if o['k'] % 2 == 0:
                self.des = self.xy_loop(o)
                self.vz_des = self.z_pos(o)
            thr = self.z_rate(o, self.vz_des)
            wd = self.att_loop(o, self.des)
            U = self.controller_update(o, self.rate_loop(o, wd), wd)
            mix = self.mrac(o, wd, U, thr)
            U = U + mix[:, [1, 0, 2]]
            thr = thr + mix[:, 3]
            # bench mixer rows = firmware rows with U[:, 1] = u_gyroy; only |u_def| enters the law (controller.c)
            self.udef = rp.mixer_deficit(thr, U[:, 0], -U[:, 1], U[:, 2], 1.0)
            return dict(U=U, thr=thr)

        def close(self):
            for pr in self.procs:
                pr.stdin.close(); pr.wait()

    return FwMRAC


def with_desat(base, hi=4000.0):
    """Mixer desaturation candidate (firmware Mix_Compute clips each motor alone): when the top motor would pass hi,
    lower the collective by the overshoot so the pitch/roll/yaw differential survives. Bench mixer rows as plant.py."""
    class Desat(base):
        name = getattr(base, 'name', base.__name__.lower()) + '+desat'

        def step(self, o):
            out = super().step(o)
            U, thr = out['U'], out['thr']
            gx, gy, gz = U[:, 0], U[:, 1], U[:, 2]
            top = np.stack([-gy - gx - gz, gy + gx - gz, -gy + gx + gz, gy - gx + gz], 1).max(1)
            return dict(out, thr=thr - np.maximum(thr + top - hi, 0.0))

    return Desat
