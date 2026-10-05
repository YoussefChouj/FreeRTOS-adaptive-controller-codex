"""Load and drag terms from a flight: residuals against the nominal rigid-body model, a library of physics
candidates, and a ranking by FROLS error reduction ratio and STLSQ bootstrap inclusion.

docs/research/nonlinear-sysid-features.md sections 3 and 7. Inputs are plain arrays on one time grid, so bench runs
and real logs share the path: p (N,3) world position m, e (N,3) ZYX Euler rad, mot (N,4) commanded motor PWM.

Alignment: the targets are a second difference of p and a first difference of mid-step body rates, so each sees the
force over steps k and k+1 through a triangular kernel; motor_thrust applies that kernel to the sub-step thrust. A
plain two-step mean is half a sub-step off it, and the attitude loop turns that offset into a spurious rate-damping
term (-0.44 on roll in a 1 m/s zigzag, truth -0.11). Velocity, attitude and rates are the boundary-k values. Target and features then pass the same zero-phase low-pass, which leaves a linear fit
unchanged and removes the differentiation noise.
"""
import os
import sys
import numpy as np
from scipy.signal import butter, filtfilt

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_DIR = os.path.abspath(os.path.join(HERE, '..'))
if BENCH_DIR not in sys.path:
    sys.path.insert(0, BENCH_DIR)

import plant  # noqa: E402
from sim.bench.sysid.frols import frols  # noqa: E402
from sim.bench.sysid.sindy import bootstrap_ensemble  # noqa: E402

GRAVITY = np.array([0.0, 0.0, plant.G])


def motor_thrust(mot, dt=plant.DT_C, v_ratio=1.0, sub=plant.SUB):
    """Thrust (N,4) in N at each sample k from the commanded PWM, as the second difference over steps k and k+1
    sees it: the plant's transport delay (DELAY_TICKS control ticks, rounded to steps of dt), the first-order lag
    TAU_M integrated at dt/sub, the nominal map A1 x + A2 x^2, weighted j/sub^2 over step k's sub-steps and
    (sub-j)/sub^2 over step k+1's (j = 0..sub-1; the plant's semi-implicit Euler), times v_ratio^2 (battery V /
    V_NOM; leave 1 when the log has no vbat, the thrust coefficient then carries it)."""
    mot = np.asarray(mot, float)
    delay = int(round(plant.DELAY_TICKS * plant.DT_C / dt))
    hist = np.concatenate([np.repeat(mot[:1], delay, 0), mot], 0)
    m = mot[0].copy()
    ts = np.zeros((len(mot) + 1, sub, 4))
    h = dt / sub
    for k in range(len(mot)):
        for j in range(sub):
            m += h / plant.TAU_M * (hist[k] - m)
            x = m - plant.PWM_MIN
            ts[k, j] = plant.A1 * x + plant.A2 * x * x
    ts[-1] = ts[-2]
    j = np.arange(sub)[:, None]
    out = (ts[:-1] * j).sum(1) / sub ** 2 + (ts[1:] * (sub - j)).sum(1) / sub ** 2
    return out * v_ratio ** 2


def _pair(x):
    """(x[k] + x[k+1]) / 2, last sample repeated: mid-step rates back to the boundary."""
    return 0.5 * (x + np.concatenate([x[1:], x[-1:]], 0))


def _body_rates(e, ed):
    """Body rates from ZYX Euler angles and their rates (inverse of plant.euler_rates)."""
    sf, cf, st, ct = np.sin(e[:, 0]), np.cos(e[:, 0]), np.sin(e[:, 1]), np.cos(e[:, 1])
    return np.stack([ed[:, 0] - ed[:, 2] * st,
                     ed[:, 1] * cf + ed[:, 2] * ct * sf,
                     -ed[:, 1] * sf + ed[:, 2] * ct * cf], 1)


def residuals(p, e, mot, dt=plant.DT_C, v_ratio=1.0, mass=plant.MASS, j0=plant.J0, cutoff_hz=10.0):
    """Residual targets and candidate libraries per axis: {axis: (y (N,), Theta (N,K), names)}.

    x, y, z: a - (T b3 / mass - g). Truth in the bench plant: thrust coef = (mass / m) gain - 1, v coef =
    -DRAG_LIN / m, 1 = DRAG_LIN wind / m. roll, pitch, yaw: wdot - tau_nom / j0. Truth: tau_nom coef = j0 / J - 1,
    thrust (Ts / j0) coef = -cog_y (roll) and +cog_x (pitch) times j0 / J, w coef = -DRAG_ROT / J, gyro coef =
    (J_b - J_c) / J_a, 1 = constant torque (motor mismatch, yaw imbalance). The rest are candidates with a physical
    reading that the plant does not model (zero truth): v|v| quadratic drag, vh2 the Faessler k_h v_h^2 thrust term,
    cross-axis velocity."""
    p, e = np.asarray(p, float), np.unwrap(np.asarray(e, float), axis=0)
    T = motor_thrust(mot, dt, v_ratio)
    Ts = T.sum(1)
    tau = np.stack([plant.ARM * (T[:, 1] + T[:, 2] - T[:, 0] - T[:, 3]),
                    plant.ARM * (T[:, 1] + T[:, 3] - T[:, 0] - T[:, 2]),
                    plant.KAPPA * (T[:, 2] + T[:, 3] - T[:, 0] - T[:, 1])], 1)
    v = np.gradient(p, dt, axis=0)
    a = np.zeros_like(p)
    a[1:-1] = (p[2:] - 2 * p[1:-1] + p[:-2]) / dt ** 2
    b1, b2, b3 = plant.rot(e)
    e_mid = np.concatenate([e[:1], 0.5 * (e[1:] + e[:-1])], 0)
    ed_mid = np.concatenate([np.zeros((1, 3)), np.diff(e, axis=0) / dt], 0)
    w_mid = _body_rates(e_mid, ed_mid)
    wd = np.zeros_like(w_mid)
    wd[:-1] = np.diff(w_mid, axis=0) / dt
    w = _pair(w_mid)
    thrust = Ts[:, None] * b3 / mass
    vh = (v * (b1 + b2)).sum(1)
    r_acc = a - (thrust - GRAVITY)
    r_rot = wd - tau / j0
    ones = np.ones(len(p))
    ba, aa = butter(2, cutoff_hz / (0.5 / dt))
    out = {}
    for i, ax in enumerate('xyz'):
        cols = {'thrust': thrust[:, i], 'v': v[:, i], 'v|v|': v[:, i] * np.abs(v[:, i]), 'vh2': vh ** 2 * b3[:, i],
                '1': ones, 'v_' + 'xyz'[(i + 1) % 3]: v[:, (i + 1) % 3], 'v_' + 'xyz'[(i + 2) % 3]: v[:, (i + 2) % 3]}
        out[ax] = (r_acc[:, i], cols)
    for i, ax in enumerate(('roll', 'pitch', 'yaw')):
        b, c = (i + 1) % 3, (i + 2) % 3
        cols = {'tau_nom': tau[:, i] / j0[i], 'thrust': Ts / j0[i], 'w': w[:, i], 'gyro': w[:, b] * w[:, c],
                '1': ones, 'v_x': v[:, 0], 'v_y': v[:, 1]}
        out[ax] = (r_rot[:, i], cols)
    return {ax: (filtfilt(ba, aa, y), np.stack([filtfilt(ba, aa, c) for c in cols.values()], 1), list(cols))
            for ax, (y, cols) in out.items()}


def rank_terms(y, Theta, names, dt=plant.DT_C, rel_threshold=0.05, n_boot=30, block_s=1.0, seed=0):
    """Rows (name, ERR, FROLS coef, bootstrap inclusion, median coef), FROLS order first, unchosen terms after.

    FROLS stops when the next term adds under 1e-3 of y'y. STLSQ runs on standardised columns with the threshold
    rel_threshold * std(y), i.e. a term is dropped when its contribution is under that share of the residual; the
    bootstrap resamples whole blocks of block_s seconds (the samples are correlated)."""
    res = frols(Theta, y, min_err=1e-3, esr_tol=1e-4)
    groups = np.arange(len(y)) // max(1, int(round(block_s / dt)))
    incl, med, _ = bootstrap_ensemble(Theta, y, rel_threshold * np.std(y), n_boot=n_boot, seed=seed, groups=groups,
                                      standardize=True)
    err = dict(zip(res['order'], res['err']))
    order = list(res['order']) + [k for k in range(len(names)) if k not in err]
    return [(names[k], float(err.get(k, 0.0)), float(res['coef'][k]), float(incl[k]), float(med[k])) for k in order]
