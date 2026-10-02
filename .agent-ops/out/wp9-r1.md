STATUS: done
FILES:
- ground_station/analysis/drift_rootcause.py: implement analysis logic
- ground_station/analysis/tests/test_drift_rootcause.py: synthetic tests
- docs/analysis/2026-10-02-drift-rootcause.md: root cause analysis report

VERIFICATION:
`PYTHONPATH=. python -m pytest ground_station/analysis/tests/test_drift_rootcause.py -q -p no:cacheprovider`
....                                                                     [100%]
4 passed in 1.18s

`time PYTHONPATH=. python -m ground_station.analysis.drift_rootcause --logs /home/agent/data/logs/vofa`
================ T5 ===================
Estimator: API/imu_update.c uses a Mahony filter... matches the observed Acc_X/Y...
real 0m4.362s, user 0m3.897s, sys 0m0.538s

`git ls-files --others --exclude-standard` (untracked files instead of commit)
 3 files added

CROSS-FLIGHT SUMMARY:
Mean Roll need: 35.6, Pitch need: 1.1
Mean Pos err Y: 3.7

VERDICTS:
A: TRUE (Angle loops are capped, forcing P-term to carry load, causing ~1.6 deg err)
B: TRUE (Hover lean is ~ -1.2 deg, producing accel requiring position error)
C: TRUE (Voltage drop requires more torque, demanding more error from saturated loops)

DEVIATIONS / RISKS:
- "Des - FB: shadow10 +1.92": Script yielded 1.61.
- Missing imu_data.rol in active15, used Ctrler.rollPID.FB instead.
SUBSTITUTIONS: none
