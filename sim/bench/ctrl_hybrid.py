"""Hybrid axis-split controller: MRAC_RBF12 (roll/pitch) + L1 (yaw).

The MRAC_RBF12 adaptive augmentation handles roll and pitch axes while the
L1 adaptive controller handles the yaw axis.  Both methods keep their full
internal state running on all three axes, but only the designated axes are
wired to the output:

    U_out[:, 0:2]  = MRAC_RBF12 roll/pitch  (adaptive + nominal)
    U_out[:, 2]    = L1 yaw                  (adaptive + nominal)

The L1's roll/pitch estimates keep running (harmless) but their output is
discarded.  Similarly, the MRAC reference model integrates yaw but its yaw
correction (which is zero in MRACBaseController.controller_update anyway) is
overwritten by L1.
"""
import tierA
import ctrl_l1


class Hybrid_RBF12_L1Yaw(tierA.MRAC_RBF12):
    name = 'hybrid_rbf12_l1yaw'

    # PARAMS = all MRAC_RBF12 params + L1-only params (l1_am, l1_f_hz, l1_clip).
    # All shared FwPID keys have identical tuples so no renaming is needed.
    PARAMS = dict(tierA.MRAC_RBF12.PARAMS)
    for _k, _v in tierA.L1.PARAMS.items():
        if _k not in PARAMS:
            PARAMS[_k] = _v
    del _k, _v   # clean up class namespace

    def __init__(self, B, params=None):
        # Fill defaults from PARAMS, then apply caller overrides.
        q = {k: v[0] for k, v in type(self).PARAMS.items()}
        q.update(params or {})

        # Initialise the MRAC_RBF12 half (FwPID cascade + reference model + MRAC).
        tierA.MRAC_RBF12.__init__(self, B, q)

        # Build an L1 instance used *only* for its adaptive yaw correction.
        # Pass through FwPID knobs plus the L1-specific knobs so it
        # constructs its PID stages and L1 state arrays with the same gains.
        self.l1 = ctrl_l1.L1(B, q)

    def controller_update(self, o, u_nom, wd):
        # --- MRAC_RBF12 augmentation (roll/pitch adaptive, yaw passthrough) ---
        U = tierA.MRAC_RBF12.controller_update(self, o, u_nom, wd)

        # --- L1 augmentation (all three axes, we keep only yaw) ---
        # L1.controller_update is self-contained: it reads only o['k'],
        # o['gyro'] and its own instance state (w_hat, u_ad, b_vec, etc.).
        # No FwPID cascade state (self.des, etc.) is needed.
        Ul = self.l1.controller_update(o, u_nom, wd)

        # Splice: yaw from L1, roll/pitch from MRAC_RBF12.
        U[:, 2] = Ul[:, 2]
        return U
