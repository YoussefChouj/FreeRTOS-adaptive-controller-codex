"""
test_equiv.py -- Equivalence test: Python adaptive controllers vs C89 reference.

Runs one bench row (zigzag_0.5, nominal, seed 7) through bench.run_rows for
each class (tierA.L1, tierA.MRAC_S6) with (a) default knobs and (b) tuned knobs.
Records per-tick inputs/outputs via monkeypatch, then replays through the C code
loaded via ctypes.  Reports max |u_py - u_c| and max relative error per axis.

Pass criterion: relative error <= 1e-3 of peak |u| (float32 vs float64).
"""
import sys, os, json, ctypes, struct
import numpy as np

# Paths
HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_DIR = os.path.dirname(HERE)
sys.path.insert(0, BENCH_DIR)

import bench
import tierA
from plant import B_RP, J0, B_YAW, DT_C

# ============================================================
# Load C shared library
# ============================================================
SO_PATH = os.path.join(HERE, 'c_ref.so')
if not os.path.exists(SO_PATH):
    raise RuntimeError('c_ref.so not found; compile first')
lib = ctypes.CDLL(SO_PATH)

# ---- L1 ctypes structures ----
class AugL1Params(ctypes.Structure):
    _fields_ = [('b', ctypes.c_float),
                ('exp_Am_Ts', ctypes.c_float),
                ('Phi_Ts', ctypes.c_float),
                ('alpha', ctypes.c_float),
                ('clip', ctypes.c_float)]

class AugL1(ctypes.Structure):
    _fields_ = [('w_hat', ctypes.c_float),
                ('u_ad', ctypes.c_float),
                ('first', ctypes.c_int)]

lib.c_aug_l1_init.argtypes = [ctypes.POINTER(AugL1), ctypes.POINTER(AugL1Params)]
lib.c_aug_l1_init.restype = None
lib.c_aug_l1_update.argtypes = [ctypes.POINTER(AugL1), ctypes.POINTER(AugL1Params),
                                ctypes.c_float, ctypes.c_float]
lib.c_aug_l1_update.restype = ctypes.c_float

# ---- MRAC S6 ctypes structures ----
class AugMracS6Params(ctypes.Structure):
    _fields_ = [('gamma', ctypes.c_float)]

class AugMracS6(ctypes.Structure):
    _fields_ = [('Th', ctypes.c_float * 6),
                ('u_ad', ctypes.c_float),
                ('acc_f', ctypes.c_float),
                ('p_prev', ctypes.c_float)]

lib.c_aug_mrac_s6_init.argtypes = [ctypes.POINTER(AugMracS6), ctypes.POINTER(AugMracS6Params)]
lib.c_aug_mrac_s6_init.restype = None
lib.c_aug_mrac_s6_update.argtypes = [ctypes.POINTER(AugMracS6), ctypes.POINTER(AugMracS6Params),
                                     ctypes.c_float, ctypes.c_float,
                                     ctypes.c_float, ctypes.c_float, ctypes.c_float,
                                     ctypes.c_float, ctypes.c_float]
lib.c_aug_mrac_s6_update.restype = ctypes.c_float

# Verify struct sizes match
assert lib.c_sizeof_AugL1() == ctypes.sizeof(AugL1), \
    'AugL1 size mismatch: C=%d, Python=%d' % (lib.c_sizeof_AugL1(), ctypes.sizeof(AugL1))
assert lib.c_sizeof_AugL1Params() == ctypes.sizeof(AugL1Params), \
    'AugL1Params size mismatch'
assert lib.c_sizeof_AugMracS6() == ctypes.sizeof(AugMracS6), \
    'AugMracS6 size mismatch: C=%d, Python=%d' % (lib.c_sizeof_AugMracS6(), ctypes.sizeof(AugMracS6))
assert lib.c_sizeof_AugMracS6Params() == ctypes.sizeof(AugMracS6Params), \
    'AugMracS6Params size mismatch'

# ============================================================
# Bench row specification
# ============================================================
ROW = [('zigzag_0.5', 'nominal', 7)]

def load_tune(name):
    """Load tuned params from results/<name>_tune.json"""
    path = os.path.join(BENCH_DIR, 'results', name + '_tune.json')
    with open(path) as f:
        d = json.load(f)
    return d['params']


# ============================================================
# L1 equivalence test
# ============================================================
def test_l1(knobs, label):
    """Record L1 controller_update inputs/outputs, replay through C, compare."""
    print('\n=== L1 [%s] ===' % label)

    # b_vec as computed in ctrl_l1.py __init__
    b_vec = np.array([B_RP, B_RP * J0[0] / J0[1], np.rad2deg(B_YAW)])

    # Storage for recorded inputs/outputs
    records = []  # list of (k, gyro (3,), u_nom (3,), u_out (3,))

    # Create controller class with monkeypatching
    orig_cls = tierA.L1

    class L1Recorder(orig_cls):
        def controller_update(self, o, u_nom, wd):
            # Call original
            u_out = orig_cls.controller_update(self, o, u_nom, wd)
            # Record (B=1 so squeeze)
            records.append((
                o['k'],
                o['gyro'][0].copy(),   # (3,) dps
                u_nom[0].copy(),       # (3,) before augmentation
                u_out[0].copy(),       # (3,) after augmentation
            ))
            return u_out

    L1Recorder.name = orig_cls.name
    L1Recorder.PARAMS = orig_cls.PARAMS

    # Run bench
    results = bench.run_rows(L1Recorder, knobs, ROW, 7)
    print('  bench rmse: %.6f' % results[0]['rmse'])
    print('  recorded %d ticks' % len(records))

    if len(records) == 0:
        print('  ERROR: no ticks recorded')
        return False

    # Extract L1-specific params from the controller we built
    # Reconstruct them from the knobs
    l1_am = knobs.get('l1_am', 10.0)
    l1_f_hz = knobs.get('l1_f_hz', 2.0)
    l1_clip = knobs.get('l1_clip', 50.0)

    Am = -l1_am
    exp_Am_Ts = np.exp(Am * DT_C)
    Phi_Ts = (exp_Am_Ts - 1.0) / Am
    alpha = np.exp(-2 * np.pi * l1_f_hz * DT_C)

    passed = True

    for axis in range(3):
        # Create C params
        p = AugL1Params(
            b=float(b_vec[axis]),
            exp_Am_Ts=float(exp_Am_Ts),
            Phi_Ts=float(Phi_Ts),
            alpha=float(alpha),
            clip=float(l1_clip)
        )
        s = AugL1()
        lib.c_aug_l1_init(ctypes.byref(s), ctypes.byref(p))

        max_abs_err = 0.0
        peak_u = 0.0
        n = len(records)
        u_py_arr = np.zeros(n)
        u_c_arr = np.zeros(n)

        for i, (k, gyro, u_nom, u_out) in enumerate(records):
            gyro_axis = float(gyro[axis])
            u_nom_axis = float(u_nom[axis])
            u_out_py = float(u_out[axis])

            u_out_c = lib.c_aug_l1_update(ctypes.byref(s), ctypes.byref(p),
                                          ctypes.c_float(gyro_axis),
                                          ctypes.c_float(u_nom_axis))

            u_py_arr[i] = u_out_py
            u_c_arr[i] = float(u_out_c)

            err = abs(u_out_py - float(u_out_c))
            if err > max_abs_err:
                max_abs_err = err
            au = abs(u_out_py)
            if au > peak_u:
                peak_u = au

        rel_err = max_abs_err / peak_u if peak_u > 0 else 0.0
        axis_names = ['roll', 'pitch', 'yaw']
        status = 'PASS' if rel_err <= 1e-3 else 'FAIL'
        if status == 'FAIL':
            passed = False
        print('  axis %s: max_abs=%.6e  peak_u=%.4f  rel_err=%.6e  %s' %
              (axis_names[axis], max_abs_err, peak_u, rel_err, status))

    return passed


# ============================================================
# MRAC S6 equivalence test
# ============================================================
def test_mrac_s6(knobs, label):
    """Record MRAC_S6 controller_update inputs/outputs, replay through C, compare."""
    print('\n=== MRAC_S6 [%s] ===' % label)

    # Storage for recorded inputs/outputs
    # MRAC controller_update adds u_ad to u_nom for roll and pitch only
    # We need per-axis: pm_meas, phm_meas, U_pid, pmk, phmk, q_cross, r_cross
    records_roll = []
    records_pitch = []
    u_ad_roll_py = []
    u_ad_pitch_py = []

    orig_cls = tierA.MRAC_S6

    class MRACS6Recorder(orig_cls):
        def controller_update(self, o, u_nom, wd):
            # Record the inputs that the MRAC step sees
            # From ctrl_mrac.py controller_update:
            cmd_r = self.des[:, 0]
            cmd_p = self.des[:, 1]
            cmd_y = o['ref']['yaw']
            cmd_z = o['ref']['p'][:, 2]

            ref_state = self.ref.step(cmd_r, cmd_p, cmd_y, cmd_z)

            phm_r = np.deg2rad(o['rpy'][:, 0])
            pm_r = np.deg2rad(o['gyro'][:, 0])
            qm = np.deg2rad(o['gyro'][:, 1])
            rm = np.deg2rad(o['gyro'][:, 2])

            phm_p = np.deg2rad(o['rpy'][:, 1])
            pm_p = qm  # pitch rate

            p_r_ref = np.deg2rad(ref_state['p'])
            phi_r_ref = np.deg2rad(ref_state['phi'])

            p_p_ref = np.deg2rad(ref_state['q'])
            phi_p_ref = np.deg2rad(ref_state['theta'])

            if isinstance(self.p['gamma'], np.ndarray):
                gamma = self.p['gamma']
            else:
                gamma = np.full(self.B, self.p['gamma'])

            # Record inputs for roll (B=1 => index 0)
            records_roll.append((
                float(pm_r[0]),
                float(phm_r[0]),
                float(u_nom[0, 0]),
                float(p_r_ref[0]),
                float(phi_r_ref[0]),
                float(qm[0]),
                float(rm[0]),
                float(gamma[0]),
            ))

            # Record inputs for pitch
            records_pitch.append((
                float(pm_p[0]),
                float(phm_p[0]),
                float(u_nom[0, 1]),
                float(p_p_ref[0]),
                float(phi_p_ref[0]),
                float(pm_r[0]),  # q_cross for pitch axis is roll rate
                float(rm[0]),
                float(gamma[0]),
            ))

            # Now call the ORIGINAL MRAC step (but we must NOT call self.ref.step again!)
            # We already called ref.step above, which advanced the reference model.
            # The original controller_update would call ref.step again.
            # So we need to replay the mrac.step calls manually.
            u_ad_r_val = self.mrac_r.step(pm_r, phm_r, u_nom[:, 0],
                                          p_r_ref, phi_r_ref, qm, rm, gamma)
            u_ad_p_val = self.mrac_p.step(pm_p, phm_p, u_nom[:, 1],
                                          p_p_ref, phi_p_ref, pm_r, rm, gamma)

            u_ad_roll_py.append(float(u_ad_r_val[0]))
            u_ad_pitch_py.append(float(u_ad_p_val[0]))

            U = u_nom.copy()
            U[:, 0] += u_ad_r_val
            U[:, 1] += u_ad_p_val
            return U

    MRACS6Recorder.name = orig_cls.name
    MRACS6Recorder.PARAMS = orig_cls.PARAMS

    results = bench.run_rows(MRACS6Recorder, knobs, ROW, 7)
    print('  bench rmse: %.6f' % results[0]['rmse'])
    print('  recorded %d ticks' % len(records_roll))

    if len(records_roll) == 0:
        print('  ERROR: no ticks recorded')
        return False

    gamma_val = knobs.get('gamma', 0.31622776601683794)

    passed = True
    for axis_name, records_axis, u_ad_py_list in [
            ('roll', records_roll, u_ad_roll_py),
            ('pitch', records_pitch, u_ad_pitch_py)]:

        p = AugMracS6Params(gamma=float(gamma_val))
        s = AugMracS6()
        lib.c_aug_mrac_s6_init(ctypes.byref(s), ctypes.byref(p))

        max_abs_err = 0.0
        peak_u_ad = 0.0
        n = len(records_axis)

        for i in range(n):
            pm_meas, phm_meas, U_pid, pmk, phmk, q_cross, r_cross, gamma_tick = records_axis[i]
            u_ad_py = u_ad_py_list[i]

            u_ad_c = lib.c_aug_mrac_s6_update(
                ctypes.byref(s), ctypes.byref(p),
                ctypes.c_float(pm_meas),
                ctypes.c_float(phm_meas),
                ctypes.c_float(U_pid),
                ctypes.c_float(pmk),
                ctypes.c_float(phmk),
                ctypes.c_float(q_cross),
                ctypes.c_float(r_cross))

            err = abs(u_ad_py - float(u_ad_c))
            if err > max_abs_err:
                max_abs_err = err
            au = abs(u_ad_py)
            if au > peak_u_ad:
                peak_u_ad = au

        # Relative error based on peak |u_ad|, not peak |u_nom+u_ad|
        # But the spec says "relative error <= 1e-3 of the peak |u|"
        # where u is the adaptive output.  For MRAC, U[:,axis] = u_nom + u_ad.
        # Let's use peak |u_ad| for the denominator since that's the output
        # of the C function; but also compute against total for clarity.
        rel_err = max_abs_err / peak_u_ad if peak_u_ad > 0 else 0.0
        status = 'PASS' if rel_err <= 1e-3 else 'FAIL'
        if status == 'FAIL':
            passed = False
        print('  axis %s: max_abs=%.6e  peak_|u_ad|=%.4f  rel_err=%.6e  %s' %
              (axis_name, max_abs_err, peak_u_ad, rel_err, status))

    return passed


# ============================================================
# Main
# ============================================================
if __name__ == '__main__':
    all_pass = True

    # L1 default knobs
    ok = test_l1({}, 'default')
    all_pass = all_pass and ok

    # L1 tuned knobs
    l1_tuned = load_tune('l1')
    ok = test_l1(l1_tuned, 'tuned')
    all_pass = all_pass and ok

    # MRAC_S6 default knobs
    ok = test_mrac_s6({}, 'default')
    all_pass = all_pass and ok

    # MRAC_S6 tuned knobs
    mrac_tuned = load_tune('mrac_s6')
    ok = test_mrac_s6(mrac_tuned, 'tuned')
    all_pass = all_pass and ok

    print('\n' + ('ALL PASS' if all_pass else 'SOME FAILURES'))
    sys.exit(0 if all_pass else 1)
