# 2026-10-08 exp11: 500 g, fresh P5300 pack, kp 5 + vp 6 (first real vp 6 load flight)

Log `logs/exp11_vp6-500g.slot*.csv`, 0 drops. Plots (160 dpi PNG, zoomable): `docs/flights/plots/2026-10-08-exp11/`.
Numbers are measured from the log; readings and next steps are PROPOSED.

| item | value |
|---|---|
| presets | `kp_active` 5 (mask 0x0F, of1 off, z headroom), `vp_active` 6 |
| airborne | 22-69 s (47.5 s), injection on 65 % (35.5-58.6 s, 61.4-69.4 s) |
| motors | no sample at the 3995 limit (exp9: motor2 18-69 %); motor2 mean 3362 vs 3203-3231 others, max 3975 |

## PID vs MRAC (4 s bins around the switches)

| bin | inj | z / z_des (m) | pitch sd (deg) | roll sd (deg) |
|---|---|---|---|---|
| 27.5-31.5 | PID | 0.988 / 1.009 | 0.49 | 0.65 |
| 31.5-35.5 | PID | 0.856 / 0.919 | 0.40 | 0.37 |
| 35.5-47.5 | MRAC | 0.888-0.969 / 0.919 | 0.76-0.85 | 0.95-1.82 |
| 47.5-58.6 | MRAC, y step | 0.916-0.926 / 0.919 | 2.1-2.6 (peak 6.7) | 0.58-0.71 |
| 58.6-61.4 | PID | 0.760 / 0.919 | 0.60 | 0.49 |
| 61.4-65.4 | MRAC | 0.787 / 0.912 | 0.98 | 0.46 |

Stick-free hover: pitch sd 1.49 (PID) vs 1.58 (MRAC), roll 1.00 vs 1.39. u_ad vs body rate r = -0.03 / -0.05
(neutral, not the 10-07 anti-damping). Stick steps: x 0 -> 0.57 m tracked; y step to -0.83 m overshot to -0.93 m.

## Reading (PROPOSED)

1. **PID "weak" = height sink.** PID sat 0.06-0.16 m under z_des; MRAC held z within 0.05 m. After the 58.6 s
   PID stretch MRAC needed more than 4 s to climb back (0.787 at 61-65 s).
2. **Attitude cost.** MRAC wobbles slightly more in still hover (roll sd 1.39 vs 1.00) and swings up to 6.7 deg
   on a 0.8 m y step. Neutral u_ad correlation: no runaway.
3. **The exp9 ceiling is gone** with 500 g and a fresh pack; motor2 is still the heavy corner (+130-160 counts).
