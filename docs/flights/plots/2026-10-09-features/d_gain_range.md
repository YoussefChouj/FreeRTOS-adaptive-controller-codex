# D part 2: does a larger, self-tuning adaptation gain help? (2026-10-09, measured)

Replay of all 59 logs, `python -m ground_station.analysis.mrac_log_replay --set d --shadow --json <f>` (`variants_d`).
The D gain P follows Pdot = p_forget*P - P^2*m^2 within [0.05, p_max], and it multiplies the L2 gain, so the
effective gain is gamma_c*P. P rests near p_forget/m^2, which means p_forget, not p_max, decides how high it goes.

| variant | pitch cancel med (min) | roll cancel med (min) | phase p/r deg | rms u_ad/needed p/r |
|---|---|---|---|---|
| OFF | -1.01 (-6.63) | -0.99 (-6.16) | -102 / -81 | 1.13 / 1.21 |
| L2only g8 (row 17) | -0.07 (-2.80) | -0.09 (-4.01) | -83 / -74 | 0.56 / 0.68 |
| L2only g20 | -0.06 (-3.55) | -0.05 (-5.67) | -69 / -58 | 0.62 / 0.76 |
| g20 + D p4/p10/p20, f0.5 (all three identical) | -0.06 (-3.50) | -0.06 (-5.52) | -69 / -59 | 0.62 / 0.75 |
| g20 + D p20 f2 | -0.01 (-7.37) | +0.01 (-8.51) | -55 / -48 | 0.67 / 0.80 |
| g20 + D p20 f5 | **+0.04** (-8.75) | **+0.02** (-8.30) | -50 / -44 | 0.68 / 0.81 |

Compared log by log with row 17, `g20 + D p20 f5`:

- **Pitch:** better on 44/59 logs. Logs below -1 go from 5 to 8.
- **Roll:** better on 50/59 logs. Logs below -1 go from 9 to 7.
- **Losses:** the worst ones are all f17 hover logs with the load removed in flight (`f17_hover_active{1,6,8,12,15}_remov...`). Example: pitch -1.57 -> -8.75 on active6.

## Verdict

- **The gain does matter, through p_forget.** With p_forget 0.5, P stays near its floor, so p_max changes nothing. That is why D looked dead in part 1. With p_forget 5, P climbs and the effective gain rises above the var-table bound of 20. The swing-band phase lag drops from -69 to -50 deg. This is the first positive median cancel tonight, but it is small (+0.04 / +0.02).
- **The risk is a sudden load change.** A high gain chases the step when the payload drops, and these are the worst logs. A flight with a fixed rope load is the favourable case. A payload drop is the unfavourable one.
- **Candidate for the pack:** a new vp row "L2only g20 + D p_max 20, p_forget 5", flown after row 17. It is PROPOSED: replay only, not simulated or flown.
