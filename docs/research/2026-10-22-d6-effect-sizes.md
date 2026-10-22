# D6: thesis effect sizes with uncertainty, 10-21 flights (2026-10-22)

**Bottom line.** The 10-21 memory A/B (MRAC on against off) shows no effect anyone could detect. With 3 complete pairs, every
95 % interval spans zero, and the smallest possible sign-flip p is 0.25. The same configuration flown at the start
and at the end of the day differs by 7.5 cm in hold rms. That is 20 times the A/B mean difference, so slow drift over the
day (battery, temperature, floor) dominates any comparison of flights that were not flown back to back.
Detecting a 1 cm difference in trajectory rms at 80 % power needs about 10 pairs: paired t power is 0.79 at 9 pairs
and 0.84 at 10, assuming the sd of differences stays at the 0.95 cm measured from only 3 pairs. The 10-22 deck adds
memory pairs.

Analogy: weighing two apples on a scale that drifts during the day. Weigh them one after the other, many times;
never weigh one in the morning and the other at night.

## Method

Tool: `tools/d6_effect_sizes.py`. Its input is the labday scorecard, `docs/lab/days/2026-10-21-report.md`.

**Pairs.** The pairs are memory-mrac-on-N and memory-mrac-off-N, flown back to back. on-3 and on-5 have no off partner, so they are
left out.

**Statistics.** For each metric the tool reports:
- the mean paired difference (on - off);
- the 95 % t interval;
- dz, the mean divided by the sd of the differences;
- the exact two-sided sign-flip p.

**Pairing by name.** Flights are paired by name. The report's config column reads `mrac_injection 0` on every
experiment row, including the plus-mrac flights. Whether MRAC was actually on in those flights is
UNVERIFIED from the report alone.

## Memory A/B, 3 pairs (1, 2, 4)

| metric | per pair (cm) | mean | 95 % CI | dz | p |
|---|---|---|---|---|---|
| hold rms (EKF) | +0.5, +1.1, -0.5 | +0.37 | -1.64 to +2.37 | +0.45 | 0.75 |
| hold rms (cam) | +0.4, +1.7, -0.4 | +0.57 | -2.07 to +3.20 | +0.53 | 0.75 |
| hold drift (cam) | -11.7, +7.6, -4.9 | -3.00 | -27.3 to +21.3 | -0.31 | 0.75 |
| traj rms (EKF) | +1.2, +2.1, +0.2 | +1.17 | -1.19 to +3.53 | +1.23 | 0.25 |
| traj rms (cam) | +0.4, +0.7, -1.0 | +0.03 | -2.22 to +2.29 | +0.04 | 1.00 |

In the EKF trajectory rms, all three pairs have MRAC on slightly worse. Camera truth does not confirm it (+0.03).

## Controller flights: one flight per arm, so no uncertainty can be computed

The figures are plus-mrac minus alone, in cm.

| controller | hold rms EKF | hold rms cam | traj rms EKF | traj rms cam | note |
|---|---|---|---|---|---|
| PID | +0.0 | +0.0 | -4.5 | +4.7 | EKF and cam disagree in sign |
| LQR | -11.4 | +18.6 | +0.8 | +5.2 | cam-EKF rms 32.4 cm on plus-mrac, so its EKF numbers are unreliable; the xy predictor ran 12 ticks (inference; the 10-22 scripts set 21) |
| MPC, ADRC | - | - | - | - | the alone flights ended before a hold (hold 0 s) |

With one flight per arm, a difference smaller than the start-to-end day drift (7.5 cm hold rms) cannot be told apart
from noise. These rows are anecdotes, not effect sizes.

## For the thesis

- Report paired differences with intervals, never single-flight deltas.
- Fly A/B arms back to back, alternating the order (ABBA), so that drift over the day cancels inside each block.
- Pool across days only as blocked pairs, never as pooled arms, because day drift is larger than the effects.
- Score against camera truth whenever cam-EKF rms is high.
- Fix the config column so that each arm is recorded and not assumed.
