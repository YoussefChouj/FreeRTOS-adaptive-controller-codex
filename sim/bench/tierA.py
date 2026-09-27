"""Tier A controllers as evaluated (ledger P2): every FwPID-based controller keeps the full FwPID knob set.

The worker classes dropped FwPID knobs to fit a 14-knob cap (ff_v/ff_a fixed at 0), which would handicap
them against pid_tuned2 for reasons unrelated to their method.  Here each keeps all FwPID knobs except the
ones its structure makes dead (INDI replaces the rate PID: rate_kp, rate_kd), plus its own knobs.
Worker files are not edited; these subclasses only change PARAMS and pass the full knob set down.
  bench.py eval tierA:MRAC_S6 ...   |   tune2.py tierA:INDI --start results/pid_tuned_tune.json --tag indi
"""
from fwpid import FwPID
import ctrl_indi, ctrl_l1, ctrl_mrac, ctrl_se3


def fair(base, name, drop=()):
    P = {k: v for k, v in FwPID.PARAMS.items() if k not in drop}
    P.update({k: v for k, v in base.PARAMS.items() if k not in FwPID.PARAMS})

    def __init__(self, B, params=None):
        q = {k: v[0] for k, v in type(self).PARAMS.items()}
        q.update(params or {})
        base.__init__(self, B, q)
        self.p.update({k: q[k] for k in ('ff_v', 'ff_a', 'z_sumemax') if k in q})

    return type(name, (base,), {'PARAMS': P, '__init__': __init__, '__module__': __name__})


INDI = fair(ctrl_indi.INDI, 'INDI', drop=('rate_kp', 'rate_kd'))
L1 = fair(ctrl_l1.L1, 'L1')
MRAC_S6 = fair(ctrl_mrac.MRAC_S6, 'MRAC_S6')
MRAC_S10 = fair(ctrl_mrac.MRAC_S10, 'MRAC_S10')
MRAC_RBF6 = fair(ctrl_mrac.MRAC_RBF6, 'MRAC_RBF6')
MRAC_RBF12 = fair(ctrl_mrac.MRAC_RBF12, 'MRAC_RBF12')
MRAC_RBF24 = fair(ctrl_mrac.MRAC_RBF24, 'MRAC_RBF24')
SE3ESO = ctrl_se3.SE3ESO
