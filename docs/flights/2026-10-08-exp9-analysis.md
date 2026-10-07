# 2026-10-08 exp9: 570 g water load, crash at 77 s

Log `logs/exp9_vp6-500g.slot*.csv` (the name says 500 g; the load was 570 ml of water in the bottle, operator).
Review page `docs/flights/plots/2026-10-08-exp9.review.html`. 0 drops on all 4 slots. All numbers below are
measured from the log; the causes and the recommendations are PROPOSED.

## What flew

| item | value |
|---|---|
| presets | `kp_active` 6 (of1 off, z headroom, **axis mask 0x08 = MRAC on z only**), `vp_active` 6 |
| airborne | 52.1-77.8 s (25.8 s); ch8 on at 65.7 s |
| pitch/roll MRAC | never injected (mask bit clear): pitch `u_ad` stayed 0.0 to -0.1. vp 6 was not tested. |

## Timeline

| t (s) | z / z_des (m) | motor2 at its 4000 cap | what happened |
|---|---|---|---|
| 57.7-65.7 PID | 0.61-0.65 / 0.89-0.79 | 18 %, then 41 % of samples | PID sinks 0.15-0.28 m under the load |
| 65.7-73.7 MRAC z | 0.70-0.79 / 0.79 | 40 %, 34 % | z MRAC closes the height gap; pitch sd 2.0 then 1.4 deg |
| 68-76 | | motor2 mean 3890-4000; motor3 2900-3200 | 800-1000 count spread: one corner carries the load |
| 76.25 | | | x/y setpoint step (stick): x -4.9 -> 8.2, y 0 -> 15.6 |
| 77.0-77.5 | | 4000 (pinned) | pitch rate command -21 -> -91 -> -166 deg/s, actual +39 -> +77 deg/s (opposite sign) |
| 77.5-78.0 | | | pitch bin mean -36 -> -60 deg (min -85), disarm, crash |

Pack: 14.0 V minimum in flight, 15.5 V after disarm.

## Reading (PROPOSED)

1. **Thrust ceiling, not the adaptive law.** Motor2 was already at its cap a fifth to two fifths of the time
   under plain PID. When the stick step asked for pitch, the rate loop asked for the opposite rate but motor2 had
   nothing left, so pitch ran away in about 1 s. Contributors: 570 g instead of 500 g, the load hanging toward the
   motor2 corner, water sloshing, and pack sag.
2. **z MRAC helped.** It held 0.79 m where PID sat 0.15-0.28 m low.
3. **No conclusion about vp 6 under load**: preset 6 keeps pitch/roll on PID.

## Before the next load flight (PROPOSED)

| # | change |
|---|---|
| 1 | Load at most 500 g (the 10-07 mass), rope centred under the CG; check motor2 and its prop on the bench |
| 2 | Fresh pack |
| 3 | No x/y stick moves while any motor sits at 4000 (watch motor2 in the Keil window or the live plot) |
| 4 | To test vp 6 on pitch/roll: `kp_id = 5` (mask 0x0F), not 6 |
