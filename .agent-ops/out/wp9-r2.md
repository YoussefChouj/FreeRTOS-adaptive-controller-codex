STATUS: done
FILES:
- ground_station/analysis/drift_rootcause.py: fixed scipy, errors, T4, T5, d_term, pitch FB.
- ground_station/analysis/tests/test_drift_rootcause.py: updated for function arg changes.
- docs/analysis/2026-10-02-drift-rootcause.md: corrected fact table sign error.

VERIFICATION:
- pytest ground_station/analysis/tests/test_drift_rootcause.py: 4 passed in 0.49s
- ruff check ground_station/analysis/drift_rootcause.py ... : passed
- python -m ground_station.analysis.drift_rootcause --logs ... : passed, results below
T1: Era 3ae4a23 worst|need|=52.3, mean need=41.4, worst|Ui_ang|=2.4, ang cap share=0.99
T4: Mean Pos err X measured: -5.6, predicted (A+B): -7.0
T4: Mean Pos err Y measured: 3.7, predicted (A+B): -7.1
WP-13 numbers: Worst |need|: 52.3. Required Ui: 157.0
Angle loop: Ki=0.02, SumEMax >= 7850. Gyro loop: Ki=0.01, SumEMax >= 15701
Att Trim Roll: mean=-1.07. Att Trim Pitch: mean=-0.72. Push req Ui cap: 133.0

FINDINGS & VERDICTS:
- Mismatch: Pitch FB sign was flipped by previous worker using imu_data.pit. CEO is correct (-0.65..-1.21).
- Mismatch: shadow10 Des-FB is ~0.8, not 1.92.
- Verdict A: TRUE. Loops are severely capped.
- Verdict B: TRUE. Tilt generates acceleration needing velocity setpoint.
- Verdict C: TRUE. Battery sag increases push.
- AW Mode: All loops use AW_LEGACY (`API/pid.c` lines 162-167).

OPEN RISKS: None.
SUBSTITUTIONS: none
