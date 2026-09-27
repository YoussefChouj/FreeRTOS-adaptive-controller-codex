# p2-b2-sysid-a1 digest (supervisor-written; the worker hit QUOTA after its commit 5404d27)

STATUS: DONE. Merged into the working tree. `pytest sim/bench/sysid -q` gives 9 passed. B1 result files unchanged.

## Code (checked by grep)
- `sindy.stlsq(standardize=True)`: columns scaled to unit std, thresholds applied to the standardised coefficients, coefficients returned de-standardised.
- `sindy.cv_threshold(groups=, one_se=True)`: group k-fold, where each group is one whole run (row), with no sample shuffling. The threshold is chosen by the 1-SE rule.
- `sindy.bootstrap_ensemble(groups=)`: resamples whole rows with replacement.
- `hscale.py --amend a1`: turns all three on. Outputs carry the suffix `_a1`.

## Result: sim, A1 sensitivity analysis (PREREG2 A1: the registered sim verdict stays KILLED)
`hscale_sim_a1.md` reports SUPPORTED. Runtime 574.1 s.

| axis | mean Jaccard A1 (B1) | id_val band-sum/global A1 (B1) | global NRMSE A1 (B1) | band-sum NRMSE A1 (B1) |
|---|---|---|---|---|
| roll | 0.524 (0.768) | 0.446 (0.475) | 0.1831 (0.1804) | 0.0817 (0.0856) |
| pitch | 0.335 (0.752) | 0.437 (0.507) | 0.1947 (0.1924) | 0.0851 (0.0975) |
| yaw | 0.421 (0.410) | 0.152 (1.312) | 0.6077 (0.3427) | 0.0924 (0.4495) |

- Criterion (a), Jaccard < 0.5: pitch and yaw pass (2/3). Criterion (b), ratio ≤ 0.9: all 3 axes pass.
- No band is flagged non-sparse: the largest selected/K is 0.333 (pitch L). Under B1, roll L selected 33/33.
- Features that recur across bands: control terms (u, u_lag) everywhere.
  - H band adds attitude-gravity terms (sin_roll, sin_pitch) and body velocity (v_bx, v_by, v_bz).
  - Yaw M band adds thrust, thrust² and ge_term. Yaw H band adds ge_term.

## Caveats (read before citing)
1. **The verdict depends on the protocol.** The same data give KILLED under B1 and SUPPORTED under A1. The yaw flip comes from both sides: the global model got worse (0.343 → 0.608, sparser under 1-SE) and the band-sum model got better (0.450 → 0.092).
2. **Capacity is asymmetric.** The band-sum uses 3 sparse models; the global model uses 1. The 1-SE rule penalises the single global model more.
3. **The bands are non-causal.** `bands.split_bands` is a zero-phase FFT mask, so the band-filtered inputs contain future samples. This is valid for identifying structure. It is NOT evidence that a causal L2 controller would get the same gain; C4 must use causal filters.
4. **Real logs are still to run.** This is the primary A1 test: `python -m sim.bench.sysid.hscale --real 'logs/flight_tests/*' --amend a1` (supervisor, local).
