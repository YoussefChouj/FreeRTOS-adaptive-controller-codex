"""Physical candidate feature library and residual angular acceleration target builder.

Implements candidate physical regressors and nominal rigid-body residual target
for both simulation logs and real-flight log arrays.
"""
import json
import numpy as np

# Nominal physical constants from plant.py
MASS = 1.2961
J0_BASE = np.array([0.00839, 0.0093, 0.01485])
ARM = 0.2 / np.sqrt(2.0)
PWM_MIN, PWM_MAX = 2000.0, 4000.0
V_NOM, HOVER_PWM, T_MAX = 15.4, 3090.0, 8.37
X_H, X_M = HOVER_PWM - PWM_MIN, PWM_MAX - PWM_MIN
U_IMB_NOM = 430.0
X2_H = X_H ** 2 + U_IMB_NOM ** 2
A2 = (T_MAX - MASS * 9.81 / 4 * X_M / X_H) / (X_M ** 2 - X_M * X2_H / X_H)
A1 = (MASS * 9.81 / 4 - A2 * X2_H) / X_H
DTDX_H = A1 + 2 * A2 * X_H
B_YAW = np.deg2rad(7.55)                       # rad/s^2 per U
KAPPA = B_YAW * J0_BASE[2] / (4 * DTDX_H)
B_RP = 8.0                                     # dps^2/U roll
J_RP_SCALE = np.rad2deg(4 * ARM * DTDX_H / J0_BASE[0]) / B_RP
J0 = J0_BASE * np.array([J_RP_SCALE, J_RP_SCALE, 1.0])  # effective inertia
TAU_M = 1.0 / 19.8                             # motor lag [s]
R_PROP = 0.0635                                # prop radius [m]

# Nominal control effectiveness vector in rad/s^2 per U
G_NOM = np.array([
    np.deg2rad(B_RP),
    np.deg2rad(B_RP * J0[0] / J0[1]),
    B_YAW
])


def euler_to_body_rates(e, dt=0.005):
    """Convert Euler angles (N, 3) in radians (ZYX convention) to body rates w (N, 3) in rad/s."""
    ed = np.gradient(e, dt, axis=0)
    phi, theta = e[:, 0], e[:, 1]
    c_phi, s_phi = np.cos(phi), np.sin(phi)
    c_th, s_th = np.cos(theta), np.sin(theta)

    # Invert Euler rate kinematics:
    # w_x = dphi - dpsi * sin(theta)
    # w_y = dtheta * cos(phi) + dpsi * cos(theta) * sin(phi)
    # w_z = -dtheta * sin(phi) + dpsi * cos(theta) * cos(phi)
    dphi, dtheta, dpsi = ed[:, 0], ed[:, 1], ed[:, 2]
    wx = dphi - dpsi * s_th
    wy = dtheta * c_phi + dpsi * c_th * s_phi
    wz = -dtheta * s_phi + dpsi * c_th * c_phi
    return np.stack([wx, wy, wz], axis=1)


def compute_target(true_e, cmd_u, dt=0.005, true_w=None, j0=J0, g_nom=G_NOM):
    """Compute per-axis residual angular acceleration.

    Target = true angular acceleration - nominal rigid-body model (J0, commanded torque, gyroscopic).

    Parameters:
        true_e: (N, 3) true attitude [roll, pitch, yaw] in radians
        cmd_u: (N, 3) commanded rate-loop U [roll, pitch, yaw] in firmware U units
        dt: time step [s]
        true_w: optional (N, 3) true body angular rates in rad/s
        j0: nominal inertia (3,)
        g_nom: nominal control effectiveness (3,) in rad/s^2 per U

    Returns:
        residual_wd: (N, 3) residual angular acceleration [rad/s^2]
        true_w: (N, 3) body rates [rad/s]
        true_wd: (N, 3) total angular acceleration [rad/s^2]
    """
    if true_w is None:
        true_w = euler_to_body_rates(true_e, dt=dt)

    true_wd = np.gradient(true_w, dt, axis=0)

    # 1. Commanded control acceleration
    wd_cmd = cmd_u * g_nom[None, :]

    # 2. Gyroscopic cross-coupling: J0^-1 * ( - w x (J0 w) )
    wx, wy, wz = true_w[:, 0], true_w[:, 1], true_w[:, 2]
    w_gyro_x = -((j0[2] - j0[1]) / j0[0]) * wy * wz
    w_gyro_y = -((j0[0] - j0[2]) / j0[1]) * wz * wx
    w_gyro_z = -((j0[1] - j0[0]) / j0[2]) * wx * wy
    wd_gyro = np.stack([w_gyro_x, w_gyro_y, w_gyro_z], axis=1)

    # Nominal model
    wd_nom = wd_cmd + wd_gyro

    # Residual target
    residual_wd = true_wd - wd_nom
    return residual_wd, true_w, true_wd


def first_order_lag(u, dt=0.005, tau=TAU_M):
    """Filter signal u through first-order motor lag with time constant tau."""
    u = np.asarray(u, dtype=float)
    alpha = np.exp(-dt / tau)
    out = np.zeros_like(u)
    if len(u) == 0:
        return out
    out[0] = u[0]
    for k in range(1, len(u)):
        out[k] = alpha * out[k - 1] + (1.0 - alpha) * u[k]
    return out


def build_candidate_features(data, dt=0.005, tau_m=TAU_M):
    """Construct candidate physical feature library from estimated states and commands.

    Parameters:
        data: dict containing:
            - 'gyro': (N, 3) gyro [dps] or [rad/s]
            - 'rpy': (N, 3) roll, pitch, yaw [deg]
            - 'pos': (N, 3) position [m] (or (N,) altitude)
            - 'vel': (N, 3) velocity [m/s] in world frame
            - 'vbat': (N,) battery voltage [V]
            - 'U': (N, 3) commanded control U [roll, pitch, yaw]
            - 'thr': optional (N,) commanded throttle PWM
        dt: time step [s]
        tau_m: motor lag time constant [s]

    Returns:
        Theta: (N, K) feature matrix
        names: list of K feature names
    """
    N = len(data['rpy'])

    # Rates in rad/s
    gyro = np.asarray(data['gyro'], dtype=float)
    if np.max(np.abs(gyro)) > 25.0:  # Likely in degrees per second
        w = np.deg2rad(gyro)
    else:
        w = gyro.copy()
    wx, wy, wz = w[:, 0], w[:, 1], w[:, 2]

    # Angles in radians
    rpy_deg = np.asarray(data['rpy'], dtype=float)
    rpy_rad = np.deg2rad(rpy_deg)
    phi, theta, psi = rpy_rad[:, 0], rpy_rad[:, 1], rpy_rad[:, 2]

    # Commands U
    U = np.asarray(data['U'], dtype=float)
    ux, uy, uz = U[:, 0], U[:, 1], U[:, 2]

    # First-order motor lagged commands
    u_lag = first_order_lag(U, dt=dt, tau=tau_m)
    u_lag_x, u_lag_y, u_lag_z = u_lag[:, 0], u_lag[:, 1], u_lag[:, 2]

    # Position and height
    if 'pos' in data and data['pos'].ndim == 2:
        pos = np.asarray(data['pos'], dtype=float)
        height = np.maximum(pos[:, 2], 0.02)
    elif 'alt' in data:
        height = np.maximum(np.asarray(data['alt'], dtype=float), 0.02)
    else:
        height = np.full(N, 1.0)

    # Body velocity
    if 'vel' in data and data['vel'].ndim == 2:
        vel_w = np.asarray(data['vel'], dtype=float)
        # Rotation matrix rows b1, b2, b3 (body to world)
        sf, cf = np.sin(phi), np.cos(phi)
        st, ct = np.sin(theta), np.cos(theta)
        sp, cp = np.sin(psi), np.cos(psi)
        b1 = np.stack([cp * ct, sp * ct, -st], axis=1)
        b2 = np.stack([cp * st * sf - sp * cf, sp * st * sf + cp * cf, ct * sf], axis=1)
        b3 = np.stack([cp * st * cf + sp * sf, sp * st * cf - cp * sf, ct * cf], axis=1)
        # v_body = R^T v_world
        v_bx = np.sum(b1 * vel_w, axis=1)
        v_by = np.sum(b2 * vel_w, axis=1)
        v_bz = np.sum(b3 * vel_w, axis=1)
    else:
        v_bx = np.zeros(N)
        v_by = np.zeros(N)
        v_bz = np.zeros(N)

    # Collective thrust proxy
    if 'thr' in data:
        thr = np.asarray(data['thr'], dtype=float)
        thrust_proxy = (thr - PWM_MIN) / 1000.0
    elif 'mot' in data:
        mot = np.asarray(data['mot'], dtype=float)
        thrust_proxy = (np.mean(mot, axis=1) - PWM_MIN) / 1000.0
    else:
        thrust_proxy = np.ones(N)

    # Battery voltage proxy
    if 'vbat' in data:
        vbat = np.asarray(data['vbat'], dtype=float)
        v_proxy = (vbat / V_NOM) ** 2
    else:
        v_proxy = np.ones(N)

    # Ground effect term: 1.0 / (1.0 + (z / R_prop)^2)
    ge_term = 1.0 / (1.0 + (height / R_PROP) ** 2)

    # Build candidate column dict
    cols = {
        'bias': np.ones(N),
        # Body rates
        'w_x': wx,
        'w_y': wy,
        'w_z': wz,
        # Gyroscopic cross terms
        'w_x*w_y': wx * wy,
        'w_y*w_z': wy * wz,
        'w_z*w_x': wz * wx,
        'w_x^2': wx ** 2,
        'w_y^2': wy ** 2,
        'w_z^2': wz ** 2,
        # Rotational drag
        '|w_x|w_x': np.abs(wx) * wx,
        '|w_y|w_y': np.abs(wy) * wy,
        '|w_z|w_z': np.abs(wz) * wz,
        # Commanded torque / control input
        'u_x': ux,
        'u_y': uy,
        'u_z': uz,
        # Motor-lagged commands
        'u_lag_x': u_lag_x,
        'u_lag_y': u_lag_y,
        'u_lag_z': u_lag_z,
        # Collective thrust
        'thrust': thrust_proxy,
        'thrust^2': thrust_proxy ** 2,
        # Body velocity and blade flapping / rotor drag
        'v_bx': v_bx,
        'v_by': v_by,
        'v_bz': v_bz,
        '|v_bx|v_bx': np.abs(v_bx) * v_bx,
        '|v_by|v_by': np.abs(v_by) * v_by,
        '|v_bz|v_bz': np.abs(v_bz) * v_bz,
        # Height ground-effect
        'ge_term': ge_term,
        # Attitude trigonometric terms
        'sin_roll': np.sin(phi),
        'cos_roll': np.cos(phi),
        'sin_pitch': np.sin(theta),
        'cos_pitch': np.cos(theta),
        # Battery voltage proxy
        'v_sq': v_proxy
    }

    names = list(cols.keys())
    Theta = np.column_stack([cols[k] for k in names])
    return Theta, names


def load_library(path):
    """Load reusable SINDy feature library JSON.

    Format:
        keyed axis -> band -> traj_class -> [{name, coef, p_incl, universal}]
    """
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)
