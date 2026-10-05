"""Real flight logs into loads.residuals: the wide per-slot CSVs ground_station/service/streams.py writes
(<stem>.slot0.csv ... slotN.csv, header t_src_ms, t_host_s, seq, symbols), merged on the device clock.

Device time matters: a session's telemetry.csv has only the host receive time and frames arrive batched (median dt
0), so a second difference on it is meaningless. Each slot here is a fixed-rate stream (20 or 40 ms); every column
is interpolated onto the fastest slot's grid over the span all slots cover, and native_dt records each column's own
period (a 40 ms position stream differentiated on a 20 ms grid is smooth, not 50 Hz data).

Units and frames (firmware Ctrler.* FB, the same the bench plant logs): attitude deg, body rates deg/s, Z_posPID.FB
m (up), locx/locyPID.FB cm, mymotor CCR counts (plant.PWM_MIN..PWM_MAX), real_voltage V. Plant frame: position
rotated back with fw_x = -plant_y, fw_y = plant_x (sim/sil/plant.py), pitch and pitch rate negated (PITCH_SIGN). Motor order and signs match loads.body_torque (API/controller.c
MIX_ROW). Usage:
    python -m sim.bench.sysid.reallog logs/vofa/f17_hover_shadow2_batery_type_2
"""
import argparse
import glob
import re

import numpy as np

from sim.bench.sysid import loads
import plant  # noqa: E402  (loads put the bench dir on sys.path)

POS = ('Ctrler.locxPID.FB', 'Ctrler.locyPID.FB', 'Ctrler.Z_posPID.FB')   # firmware frame: cm, cm, m
ATT = (('Ctrler.rollPID.FB', 'Ctrler.pitchPID.FB', 'Ctrler.yawPID.FB'), ('imu_data.rol', 'imu_data.pit', 'imu_data.yaw'))
GYRO = ('Ctrler.gyroxPID.FB', 'Ctrler.gyroyPID.FB', 'Ctrler.gyrozPID.FB')
MOT = tuple(f'mymotor.motor{i}' for i in range(1, 5))
VBAT = 'real_voltage'
# Logged pitch and gyroy are minus the plant's theta and q (StabilizerTask.c: u_gyroy = -gyroyPID.U, "motor mixer
# needs gyroy reversed"). Measured on f17_hover_shadow2 (36.7 s segment, 1 Hz low-pass, centred): fw_y acceleration
# against sin(pitchPID.FB) T / m gain -0.59 (R2 0.46), fw_x against sin(roll) +0.70 (R2 0.53); wdot against the
# plant pitch torque came out negative before the flip.
PITCH_SIGN = -1.0


def load_wide(stem):
    """(t s, {column: values}, {column: native period s}) on one uniform grid from <stem>.slot*.csv."""
    files = sorted(glob.glob(stem + '.slot*.csv'), key=lambda f: int(re.search(r'slot(\d+)', f).group(1)))
    if not files:
        raise FileNotFoundError(stem + '.slot*.csv')
    slots = []
    for f in files:
        with open(f) as fh:
            head = fh.readline().strip().split(',')
        if head[0] != 't_src_ms':
            raise ValueError(f'{f}: no device time column (t_src_ms)')
        d = np.genfromtxt(f, delimiter=',', skip_header=1)
        d = d[np.isfinite(d[:, 0])]
        slots.append((head, d[np.argsort(d[:, 0], kind='stable')]))
    periods = [np.median(np.diff(d[:, 0])) for _, d in slots]
    dt = min(periods) / 1e3
    t0 = max(d[0, 0] for _, d in slots) / 1e3
    t1 = min(d[-1, 0] for _, d in slots) / 1e3
    t = np.arange(t0, t1, dt)
    cols, native = {}, {}
    for (head, d), per in sorted(zip(slots, periods), key=lambda s: -s[1]):   # fastest slot wins a shared column
        for i, name in enumerate(head[3:], 3):
            ok = np.isfinite(d[:, i])
            if ok.sum() > 1:
                cols[name] = np.interp(t, d[ok, 0] / 1e3, d[ok, i])
                native[name] = per / 1e3
    return t, cols, native


def flight(stem, z_min=0.3, min_s=5.0, trim_s=1.0):
    """Arrays for loads.residuals over each airborne stretch (Z_posPID.FB > z_min for at least min_s, trimmed by
    trim_s at both ends): list of dicts t, p (N,3) m (x, y NaN when not logged), e (N,3) rad, w (N,3) rad/s, mot (N,4),
    vbat (N,) or None, plus dt and native (column periods)."""
    t, c, native = load_wide(stem)
    dt = t[1] - t[0]
    missing = [n for n in (POS[2],) + GYRO + MOT if n not in c]
    if missing:
        raise KeyError(f'{stem}: missing {missing}')
    att = next((a for a in ATT if all(n in c for n in a)), None)
    if att is None:
        raise KeyError(f'{stem}: no attitude columns')
    fx, fy = (0.01 * c[n] if n in c else np.full(len(t), np.nan) for n in POS[:2])
    p = np.stack([fy, -fx, c[POS[2]]], 1)                       # plant frame
    sign = np.array([1.0, PITCH_SIGN, 1.0])
    e = np.deg2rad(np.stack([c[n] for n in att], 1)) * sign
    w = np.deg2rad(np.stack([c[n] for n in GYRO], 1)) * sign
    mot = np.stack([c[n] for n in MOT], 1)
    vbat = c.get(VBAT)
    air = p[:, 2] > z_min
    edges = np.flatnonzero(np.diff(np.concatenate([[0], air.astype(int), [0]])))
    trim = int(round(trim_s / dt))
    out = []
    for a, b in zip(edges[::2], edges[1::2]):
        a, b = a + trim, b - trim
        if (b - a) * dt < min_s:
            continue
        s = slice(a, b)
        out.append({'t': t[s], 'p': p[s], 'e': e[s], 'w': w[s], 'mot': mot[s],
                    'vbat': None if vbat is None else vbat[s], 'dt': dt, 'native': native})
    return out


def trim(seg, delay_s=0.015):
    """The static balance of a segment, from mean motor thrust (the mean body acceleration and rotation rate of a
    flight that ends where it started are near zero): hover thrust ratio = mean(Ts b3z) / (MASS g) (effective mass
    over plant.MASS: payload, or 1 / thrust-map gain), the CoM offset that balances the mean roll and pitch motor
    torque (loads.residuals truth: cog_y = mean tau_roll / Ts, cog_x = -mean tau_pitch / Ts; motor mismatch and
    frame asymmetry read as offset too, so compare a load flight with a baseline) and the yaw torque the mean yaw
    motor torque cancels (plant tz_imb)."""
    dt = seg['dt']
    v_ratio = 1.0 if seg['vbat'] is None else seg['vbat'] / plant.V_NOM
    T = loads.motor_thrust(seg['mot'], dt, v_ratio, max(1, int(round(dt / plant.DT))), delay_s)
    Ts = T.sum(1)
    tau = loads.body_torque(T).mean(0)
    b3z = np.cos(seg['e'][:, 0]) * np.cos(seg['e'][:, 1])
    return {'thrust_ratio': float(np.mean(Ts * b3z) / (plant.MASS * plant.G)),
            'cog_x': float(-tau[1] / Ts.mean()), 'cog_y': float(tau[0] / Ts.mean()), 'yaw_imb': float(-tau[2])}


def analyse(seg, delay_s=0.015, cutoff_hz=5.0, axes=None, edge_s=1.0, **rank_kw):
    """{axis: rank_terms rows} for one flight() segment, ranked on the centred target and candidates: the static
    balance (trim(), reported separately) would otherwise take '1' and tau_nom with ERR near 1 (f17_hover_shadow2:
    tau_nom -1.0 on roll, pitch and yaw), so '1' is dropped. delay_s: actuator transport delay (the plant's 3
    control ticks by default); vbat, when logged, scales thrust per sample. Translational x, y only when position
    is logged. edge_s trims the filter edges after residuals."""
    dt = seg['dt']
    v_ratio = 1.0 if seg['vbat'] is None else seg['vbat'] / plant.V_NOM
    p = seg['p'].copy()
    have_xy = bool(np.all(np.isfinite(p[:, :2])))
    if not have_xy:
        p[:, :2] = 0.0
    R = loads.residuals(p, seg['e'], seg['mot'], dt=dt, v_ratio=v_ratio, cutoff_hz=cutoff_hz, w_meas=seg['w'],
                        sub=max(1, int(round(dt / plant.DT))), delay_s=delay_s)
    axes = axes or (['x', 'y'] if have_xy else []) + ['z', 'roll', 'pitch', 'yaw']
    k = max(2, int(round(edge_s / dt)))
    out = {}
    for ax in axes:
        y, Th, names = R[ax]
        keep = [i for i, n in enumerate(names) if n != '1']
        y, Th = y[k:-k], Th[k:-k][:, keep]
        out[ax] = loads.rank_terms(y - y.mean(), Th - Th.mean(0), [names[i] for i in keep], dt=dt, **rank_kw)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('stem', help='path without .slotN.csv, e.g. logs/vofa/flight15')
    ap.add_argument('--z-min', type=float, default=0.3)
    ap.add_argument('--delay-ms', type=float, default=15.0)
    ap.add_argument('--cutoff-hz', type=float, default=5.0)
    a = ap.parse_args()
    for i, seg in enumerate(flight(a.stem, z_min=a.z_min)):
        print(f'segment {i}: {seg["t"][-1] - seg["t"][0]:.1f} s, dt {seg["dt"] * 1e3:.0f} ms, '
              f'z {np.nanmin(seg["p"][:, 2]):.2f}-{np.nanmax(seg["p"][:, 2]):.2f} m')
        tr = trim(seg, a.delay_ms / 1e3)
        print(f'  trim: thrust ratio {tr["thrust_ratio"]:.3f}, CoM offset x {tr["cog_x"] * 1e3:+.1f} mm '
              f'y {tr["cog_y"] * 1e3:+.1f} mm, yaw torque {tr["yaw_imb"] * 1e3:+.2f} mN m')
        for ax, rows in analyse(seg, delay_s=a.delay_ms / 1e3, cutoff_hz=a.cutoff_hz).items():
            top = [f'{n} {err:.2f}/{coef:+.3g}/{incl:.1f}' for n, err, coef, incl, _ in rows if incl >= 0.5 or err >= 0.05]
            print(f'  {ax:5s} ' + ('  '.join(top) or '-'))


if __name__ == '__main__':
    main()
