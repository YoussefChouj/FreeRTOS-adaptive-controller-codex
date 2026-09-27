# bench_v1 controller API (frozen; read before writing a controller)

Frozen files (never edit): `plant.py scen.py fwpid.py bench.py bench_v1.json calib_*`, and
everything in `sim/adaptive_compare/` (import only). New code: `sim/bench/ctrl_<name>.py`,
sanity cases `sim/bench/sanity_<name>.py`. No firmware dirs, no builds, no hardware.

## Class
```python
from plant import Controller          # or: from fwpid import FwPID (reuse its stages)
class MyCtrl(Controller):
    name = 'my'
    PARAMS = {'k1': (default, lo, hi, 'log'|'lin'), ...}   # what the common CMA-ES tunes (<=14 knobs)
    def __init__(self, B, params=None): super().__init__(B, params)   # self.p[k]: float OR (B,) array
    def step(self, obs): return dict(U=(B,3) array, thr=(B,) array)
```
- Every param may arrive as a (B,) array (population-batched tuning). All math must broadcast.
- `step` is called at 200 Hz (DT_C = 0.005 s), k = tick. The firmware runs XY/Z_pos at 100 Hz (k even).
- Defaults = your best hand design. Bounds must contain the defaults. The supervisor runs the tuning.

## obs (estimated, firmware units, batched over B rows)
| key | shape | meaning |
|---|---|---|
| k, t | int, float | tick, time [s] |
| rpy | (B,3) | Mahony estimate roll, pitch, yaw [deg], ZYX, z up |
| gyro | (B,3) | 50 Hz Butterworth-filtered gyro [dps], with bias and noise |
| acc | (B,3) | 30 Hz Butterworth-filtered body specific force [m/s^2] (hover = [0,0,9.81]) |
| pos, vel | (B,3) | KF estimates [m, m/s], world frame (ToF z 25 Hz, OF xy velocity 50 Hz, delayed) |
| vbat | (B,) | battery voltage [V] |
| mot | (B,4) | last motor PWM commands (after the mixer and clamp) |
| ref | dict | onboard generator now: p (B,3) m, v, a, yaw (B,) deg |
| preview(n) | fn | onboard generator n ticks ahead: dict p, v, a, yaw |

No truth states, no disturbance values, no row parameters (`sp`). Nominal model constants
from `plant` are allowed: MASS, J0, B_RP (roll dps2/U; pitch = B_RP*J0[0]/J0[1]), B_YAW (rad/s^2/U),
HOVER_PWM, A1/A2 thrust curve (T per motor [N] = A1 x + A2 x^2, x = PWM-2000, times (V/15.4)^2), ARM.

## Output → plant
U = (roll, pitch, yaw) in firmware rate-loop U units; thr = absolute PWM.
Mixer: M1=T-Up-Ur-Uy, M2=T+Up+Ur-Uy, M3=T-Up+Ur+Uy, M4=T+Up-Ur+Uy; clamp 2000..4000; 15 ms delay; motor lag 50 ms.
+U_roll → +p, +U_pitch → +q (+x accel), +U_yaw → +r. Firmware-rate limits apply to your own design.

## Firmware PID as stages (fwpid.FwPID)
xy_loop(o) → roll/pitch Des [deg] (100 Hz) · z_pos(o) → vz_des · z_rate(o, vz_des) → thr ·
att_loop(o, des) → rate Des wd [dps] · rate_loop(o, wd) → U · controller_update(o, u_nom, wd) → U
(= firmware `Controller_Update(axis, u_nom)` hook; an adaptive augmentation overrides only this).

## Quick checks (the tuning and the test split belong to the supervisor)
```python
import bench, fwpid, ctrl_my
rl = [('zigzag_0.5','nominal',0), ('circle_1.0','wind',0), ('steps','payload',1), ('lem_1.0','cog',0)]
for c in (fwpid.FwPID, ctrl_my.MyCtrl): print([round(d['rmse'],3) for d in bench.run_rows(c, {}, rl, 7)])
```
One 4-row run takes about 16 s. Never run `bench.py tune` or `--split test`.
Portability: float32-friendly, no matrix inverse larger than 3x3, no dynamic allocation in the step
(pre-allocate in __init__), C89-translatable (`Controller_Update` fit).
