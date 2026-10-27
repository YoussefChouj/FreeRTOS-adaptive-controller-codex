"""Offline, PROPOSED vertical plant study; never connects to a vehicle.

Not a flight predictor: rigid vertical motion, linear thrust slope, no attitude,
battery, aerodynamic ground effect, mixer/headroom schedules or UART jitter.
The sampled margins are fundamental-harmonic approximations, not Floquet proofs.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy import linalg, signal

DT = .005
SAMPLE_TICKS = 10
KINDS = ("fd_lpf", "current_cf", "cf_a2", "cf_a2_a3", "cf_a2_a3_a4", "delayed_kf")
F = np.array([[1, DT, -.5 * DT**2], [0, 1, -DT], [0, 0, 1.]])
B = np.array([.5 * DT**2, DT, 0.])


@dataclass(frozen=True)
class Assumptions:
    """Every physical number here is PROPOSED, not identified from hover PWM."""
    thrust_slope: float = .02  # m/s² per CCR above hover, mass absorbed in slope
    hover_ccr: float = 3050.  # coded offset, NOT an identified force calibration
    accel_noise: float = .15  # per 200-Hz sample m/s² standard deviation
    range_noise: float = .005  # per 20-Hz sample m standard deviation
    accel_bias: float = .08  # m/s²
    thrust_bias: float = .08  # measured accel error per true m/s²
    quantize_cm: bool = True


class PID:
    """Finite-state AW_LEGACY ComputePID: per-call I and D, individual clamps."""
    def __init__(self, kp, ki, kd, umax, upmax, uimax, udmax, summax, emin):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.umax, self.upmax, self.uimax, self.udmax = umax, upmax, uimax, udmax
        self.summax, self.emin = summax, emin
        self.total = self.previous = self.output = 0.

    def step(self, desired, feedback):
        e = desired - feedback
        if not np.isfinite([desired, feedback, self.total, self.previous, e]).all():
            self.total = self.previous = self.output = 0.
            return 0.
        if ((self.output <= self.umax and e > 0) or
                (self.output >= -self.umax and e < 0)) and abs(e) < self.emin:
            self.total += e
        self.total = max(-self.summax, min(self.summax, self.total))
        p = max(-self.upmax, min(self.upmax, self.kp * e))
        i = max(-self.uimax, min(self.uimax, self.ki * self.total))
        d = max(-self.udmax, min(self.udmax, self.kd * (e - self.previous)))
        self.output = max(-self.umax, min(self.umax, p + i + d))
        self.previous = e
        return self.output


def kalman_gain(a=Assumptions()):
    """Steady-state three-state KF at 20 Hz; Q/R are PROPOSED tuning."""
    ft = np.linalg.matrix_power(F, SAMPLE_TICKS)
    q = np.zeros((3, 3))
    qt = np.outer(B, B) * a.accel_noise**2
    qt[2, 2] = .02**2 * DT
    for _ in range(SAMPLE_TICKS):
        q = F @ q @ F.T + qt
    h = np.array([[1., 0, 0]])
    p = linalg.solve_discrete_are(ft.T, h.T, q, np.array([[a.range_noise**2]]))
    return p[:, 0] / (p[0, 0] + a.range_noise**2)


class Estimator:
    def __init__(self, kind, delay_ticks=0, initial=.8, complex_count=0, rate_scaled=False):
        self.kind = kind
        self.rate_scaled = rate_scaled
        self.steps = SAMPLE_TICKS - 1
        self.delay = delay_ticks if kind in KINDS[3:] else 0
        shape = (3, complex_count) if complex_count else (3,)
        self.x = np.zeros(shape, dtype=complex if complex_count else float)
        self.x[0] = initial
        self.history = np.repeat(self.x[None], 32, axis=0)
        self.gain = kalman_gain() if kind == "delayed_kf" else np.array([.21, .45, -.1])
        self.packets = []
        self.old_range = initial
        self.fd_h = initial
        self.fd_v = 0. * initial
        self.fd_pending = None

    def apply(self, delta):
        # Correct delayed horizon, propagating through all newer buffered states.
        times = np.arange(self.delay, -1, -1) * DT
        if delta.ndim == 2:
            times = times[:, None]
        self.history[:self.delay + 1, 0] += delta[0] + times * delta[1] - .5 * times**2 * delta[2]
        self.history[:self.delay + 1, 1] += delta[1] - times * delta[2]
        self.history[:self.delay + 1, 2] += delta[2]
        self.x = self.history[0].copy()

    def step(self, acceleration, measurement=None):
        if self.kind == "fd_lpf":
            previous_h = self.fd_h
            if self.fd_pending is not None:
                self.fd_h = self.fd_pending
            self.fd_pending = measurement  # per-tick median passes new value on next tick
            self.fd_v = .9 * self.fd_v + .1 * (self.fd_h - previous_h) / DT
            return self.fd_h, self.fd_v
        self.steps += 1
        for packet in self.packets:
            packet['delta'] = F @ packet['delta']
            packet['age'] = min(31, packet['age'] + 1)
        self.x = F @ self.x + B.reshape((-1,) + (1,) * (self.x.ndim - 1)) * acceleration
        self.history[1:] = self.history[:-1]
        self.history[0] = self.x
        if measurement is not None:
            y = self.old_range if self.kind == "current_cf" else measurement
            self.old_range = measurement
            error = y - self.history[self.delay, 0]
            interval = min(20, max(1, self.steps))
            delta = self.gain.reshape((-1,) + (1,) * (self.x.ndim - 1)) * error
            if self.rate_scaled and self.kind != "delayed_kf":
                delta *= interval / SAMPLE_TICKS
            self.steps = 0
            if self.kind == "cf_a2_a3_a4":
                self.packets.append(dict(delta=np.linalg.matrix_power(F, self.delay) @ delta,
                                         age=self.delay, remaining=interval))
            else:
                self.apply(delta)
        if self.kind == "cf_a2_a3_a4":
            for packet in self.packets:
                fraction = packet['delta'] / packet['remaining']
                packet['delta'] -= fraction
                packet['remaining'] -= 1
                if not np.iscomplexobj(fraction):
                    fraction[2] = max(-1., min(1., self.x[2] + fraction[2])) - self.x[2]
                times = np.arange(packet['age'] + 1) * DT
                if fraction.ndim == 2:
                    times = times[:, None]
                self.history[:packet['age'] + 1, 0] += fraction[0] - times * fraction[1] - .5 * times**2 * fraction[2]
                self.history[:packet['age'] + 1, 1] += fraction[1] + times * fraction[2]
                self.history[:packet['age'] + 1, 2] += fraction[2]
                self.x = self.history[0].copy()
            self.packets = [packet for packet in self.packets if packet['remaining']]
        return self.x[0], self.x[1]


def run(kind, latency_ms=50, motor_ms=40, ki=.001, assumptions=Assumptions(), seconds=40., seed=71027, frame_hz=20, rate_scaled=False):
    """Two experiments: hover [5,15)s, step 0.8 -> 1.0m at 15s."""
    n = int(seconds / DT)
    rng = np.random.default_rng(seed)
    delay = round(latency_ms / (DT * 1000))
    est = Estimator(kind, delay, rate_scaled=rate_scaled)
    pos = PID(.7, ki, .1, 1, .9, .3, .3, 30, .3)
    rate = PID(400, .435, 0, 300, 300, 100, 60, 250, 10)
    data = np.zeros((n, 7))
    heights = np.full(n + 1, .8)
    h, v, acceleration, command = .8, 0., 0., 0.
    old_samples = [.8, .8]
    rejected = 0
    accepted = .8
    raw = .8
    for tick in range(n):
        t = tick * DT
        y = None
        if tick % SAMPLE_TICKS == 0:
            raw = heights[max(0, tick - delay)] + rng.normal(0, assumptions.range_noise)
            if assumptions.quantize_cm:
                raw = round(raw * 100) / 100
        frame = tick == 0 or int(tick * frame_hz / 200) != int((tick - 1) * frame_hz / 200)
        if frame:
            median = sorted([raw, *old_samples])[1]
            old_samples = [raw, old_samples[0]]
            # A2 median is outlier reference only, latest raw is fused.
            gate = min(.2, .05 + .15 * abs(pos.output))
            if kind in KINDS[2:] and abs(raw - median) >= gate:
                rejected += 1
            elif .05 <= raw <= 5 and (abs(raw - accepted) < gate or rejected >= 20):
                y, accepted, rejected = raw, raw, 0
            else:
                rejected += 1
        am = acceleration * (1 + assumptions.thrust_bias) + assumptions.accel_bias
        am += rng.normal(0, assumptions.accel_noise)
        eh, ev = est.step(am, y)
        desired = .8 if t < 15 else 1.
        rate_desired = pos.output  # Update_Des precedes position ComputePID
        if tick % 2 == 1:
            pos.step(desired, eh)
        command = rate.step(rate_desired, ev)
        command = max(2000 - assumptions.hover_ccr, min(4000 - assumptions.hover_ccr, command))
        target_accel = assumptions.thrust_slope * command
        # Exact motor ZOH, trapezoidal rigid-body integration over a 5ms tick.
        next_a = target_accel + (acceleration - target_accel) * np.exp(-DT / (motor_ms / 1000))
        new_v = v + .5 * (acceleration + next_a) * DT
        h += .5 * (v + new_v) * DT
        v, acceleration = new_v, next_a
        heights[tick + 1] = h
        data[tick] = t, h, eh, ev, command, est.x[2], acceleration
    hover = data[(data[:, 0] >= 5) & (data[:, 0] < 15), 1]
    after = data[data[:, 0] >= 15]
    outside = np.flatnonzero(abs(after[:, 1] - 1.) > .004)
    settle = None if len(outside) and outside[-1] == len(after) - 1 else (
        0. if not len(outside) else float(after[outside[-1] + 1, 0] - 15))
    f, power = signal.periodogram(signal.detrend(hover), fs=1 / DT)
    band = (f >= .1) & (f <= 3)
    peak = f[band][np.argmax(power[band])]
    return dict(overshoot_cm=float(max(0, after[:, 1].max() - 1) * 100),
                settle_s=settle, hover_rms_cm=float(np.sqrt(np.mean((hover - .8)**2)) * 100),
                hover_half_pp_cm=float(np.ptp(hover) * 50), period_s=None if np.ptp(hover) < 1e-9 else float(1 / peak),
                model_domain_exceeded=bool(np.any((data[:, 1] < .05) | (data[:, 1] > 5))))


def estimator_response(kind, latency_ms, frequencies):
    """Fundamental harmonic of the 10-tick periodic linearized estimator.

    Median/gates/quantization are inactive locally; delay and held sampling remain.
    Harmonics at sample-rate aliases are discarded: margins are an approximation.
    """
    w = 2 * np.pi * frequencies
    est = Estimator(kind, round(latency_ms / 5), initial=0., complex_count=len(w))
    hh = np.zeros(len(w), complex)
    hv = hh.copy()
    count = 0
    for tick in range(16000):
        t = tick * DT
        basis = np.exp(1j * w * t)
        y = np.exp(1j * w * (t - latency_ms / 1000)) if tick % 10 == 0 else None
        h, v = est.step(-w**2 * basis, y)
        if tick >= 12000:
            hh += h / basis
            hv += v / basis
            count += 1
    return hh / count, hv / count


def margins(hh, hv, frequencies, motor_ms, ki, slope=.02):
    """Return all crossings; minimum PM and GM are reported if multiple exist."""
    z = np.exp(2j * np.pi * frequencies * DT)
    # Outer PID is 100 Hz, held across two ticks, then one tick scheduling delay.
    z_outer = z**2
    cp = (.7 + ki / (1 - 1 / z_outer) + .1 * (1 - 1 / z_outer)) * (1 + 1 / z) / 2 / z
    cv = 400 + .435 / (1 - 1 / z)
    # ZOH discretization includes motor and two position integrations.
    a = np.array([[0, 1, 0], [0, 0, 1], [0, 0, -1000 / motor_ms]])
    b = np.array([[0], [0], [slope * 1000 / motor_ms]])
    ad, bd, cd, dd, _ = signal.cont2discrete((a, b, np.array([[1, 0, 0]]), np.zeros((1, 1))), DT)
    plant = np.array([(cd @ np.linalg.solve(zz * np.eye(3) - ad, bd))[0, 0] for zz in z])
    loop = cv * (cp * hh + hv) * plant
    magnitude = 20 * np.log10(abs(loop))
    phase = np.unwrap(np.angle(loop)) * 180 / np.pi
    # Low-frequency loop includes both integrators and has phase near -270 deg.
    # Choose phase branch continuous with high-frequency damping region.
    phase += 360 * round((-180 - phase[len(phase)//2]) / 360)
    def crossing_values(x, y, target):
        values = []
        for i in np.flatnonzero((x[:-1] - target) * (x[1:] - target) <= 0):
            fraction = (target - x[i]) / (x[i + 1] - x[i])
            values.append(float(y[i] + fraction * (y[i + 1] - y[i])))
        return values
    pms = crossing_values(magnitude, phase + 180, 0)
    upper, lower = [], []
    for index in np.flatnonzero((phase[:-1] + 180) * (phase[1:] + 180) <= 0):
        fraction = (-180 - phase[index]) / (phase[index + 1] - phase[index])
        gm = float(-magnitude[index] - fraction * (magnitude[index + 1] - magnitude[index]))
        (upper if phase[index + 1] < phase[index] else lower).append(gm)
    return dict(phase_margin_deg=min(pms) if pms else None,
                gain_margin_db=min(upper) if upper else None,
                lower_gain_boundary_db=max(lower) if lower else None)


def study(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    freq = np.geomspace(.025, 12, 180)
    rows = []
    for kind in KINDS:
        print(f"SIMULATION: {kind}", flush=True)
        for delay in (0, 25, 50, 75, 100):
            hh, hv = estimator_response(kind, delay, freq)
            for motor in (20, 40, 60):
                for ki in (.001, .005):
                    row = dict(estimator=kind, latency_ms=delay, motor_ms=motor, pos_ki=ki)
                    row.update(run(kind, delay, motor, ki))
                    row.update(margins(hh, hv, freq, motor, ki))
                    rows.append(row)
    factors = []
    for label, assumptions in (
        ('no_errors', Assumptions(accel_noise=0, range_noise=0, accel_bias=0, thrust_bias=0, quantize_cm=False)),
        ('noise_quantization', Assumptions(accel_bias=0, thrust_bias=0)),
        ('constant_bias', Assumptions(accel_noise=0, range_noise=0, thrust_bias=0, quantize_cm=False)),
        ('thrust_bias', Assumptions(accel_noise=0, range_noise=0, accel_bias=0, quantize_cm=False)),
        ('all_errors', Assumptions())):
        for kind in KINDS:
            factors.append(dict(factor=label, estimator=kind, **run(kind, assumptions=assumptions)))
    with (output / 'factors.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(factors[0]))
        writer.writeheader()
        writer.writerows(factors)
    cadence = []
    for kind in ('current_cf', 'cf_a2', 'cf_a2_a3', 'cf_a2_a3_a4'):
        for scaled in (False, True):
            cadence.append(dict(estimator=kind, frame_hz=70, sensor_hz=20, rate_scaled=scaled,
                                **run(kind, frame_hz=70, rate_scaled=scaled)))
    with (output / 'cadence.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(cadence[0]))
        writer.writeheader()
        writer.writerows(cadence)
    with (output / 'sweep.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'assumptions.json').write_text(json.dumps(dict(status='PROPOSED',
        parameters=asdict(Assumptions()), seed=71027, duration_s=40, sample_hz=20,
        tick_hz=200, notes='Mass and thrust curve unidentifiable from hover CCR alone.'), indent=2) + '\n')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='Directory for offline simulation CSV/assumptions')
    args = parser.parse_args()
    rows = study(args.output)
    print(f'SIMULATION ONLY: wrote {len(rows)} PROPOSED scenarios to {args.output}')


if __name__ == '__main__':
    main()
