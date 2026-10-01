STATUS: done
Files changed:
- ground_station/analysis/drift_rootcause.py: yaw mapping, skip bugs
- ground_station/analysis/tests/test_drift_rootcause.py: added tests
- docs/analysis/2026-10-02-drift-rootcause.md: updated verdicts
Verification:
pytest:
......                                                                   [100%]
6 passed in 0.50s
ruff:
All checks passed!
T1: Era 3ae4a23: Roll: worst |need|=45.1, mean need=27.9, worst |Ui_ang|=2.4, ang cap share=0.37
T4: Mean Pos err X measured: -5.6, predicted (A+B): -6.8; Mean Y measured: 3.7, predicted: 4.8
WP-13: Required gyro Ui (ticks): 157.0. Required position/velocity Ui cap (x3 headroom): 79.6
Item 6: Removing .bfill() correctly subsets hover span, yielding 1.86 mean.
Des Closure: Roll/Pitch pred exactly match logs cross-flight.
Fact mismatches: shadow10 Des-FB (1.86 vs 1.92), gap (0 vs 1.7), trim (prev included non-hover).
Verdicts: A, B, C are TRUE.
Open Risks: None
