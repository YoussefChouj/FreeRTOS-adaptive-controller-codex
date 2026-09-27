STATUS: done
Files changed:
- sim_axes.py: Fixed yaw error wrapping and prevented diverged logic from falsely triggering on valid >180 wrap.
- sim_coupled.py: Increased U_tot_y clipping limit to [-650, 650] matching rate-PID tuning.
- run_coupled.py: Used nominal-PID as reference for rms metrics, wrapped C5 yaw plot, reversed C2 bias direction to -0.1067 Nm.
- run_axes.py: Regenerated axes JSON outputs.

Coupled RMS + sat% (PID vs best layer):
C1 Nominal: PID (R:0.0 P:0.0 Y:0.0 Z:0.00 Sat:0.0%) | RBF24 (R:0.0 P:0.0 Y:0.6 Z:0.00 Sat:0.0%)
C2 Yaw Imbalance (Flight 8): PID (R:0.0 P:0.1 Y:24.0 Z:0.00 Sat:0.1%) | RBF24 (R:0.0 P:0.1 Y:23.8 Z:0.00 Sat:0.2%)
C3 Motor Fault: PID (R:2.9 P:3.5 Y:1.1 Z:0.23 Sat:0.0%) | RBF24 (R:2.5 P:1.4 Y:1.0 Z:0.23 Sat:0.0%)
C4 Gust+Drag+CG: PID (R:3.4 P:2.5 Y:2.0 Z:0.00 Sat:0.3%) | RBF24 (R:4.1 P:2.5 Y:2.4 Z:0.00 Sat:1.7%)
C5 Combined: PID (R:37.9 P:13.2 Y:71.7 Z:0.45 Sat:55.3%) | RBF24 (R:17.2 P:8.0 Y:76.9 Z:0.47 Sat:55.7%)

Axes MC Medians (Yaw):
PID: 54.4, S6: 27.0, S10: 58.1, RBF6: 53.7, RBF12: 37.0, RBF24: 41.1

Verification:
- Commands run: `python sim/adaptive_compare/run_coupled.py` (exit 0), `python sim/adaptive_compare/run_axes.py` (exit 0).
- PID steady yaw U in C2 is 539.6. C2 M1..M4 means: 2416.5, 2413.9, 3493.1, 3495.6 (M3/M4 pushed towards clamp).
- PID holds heading (C2 Y rms 24.0 deg), sat_pct > 0 (0.1%), climb/roll authority is noticeably reduced due to high baseline M3/M4.
- Yaw MC medians now reflect true error distributions rather than artificial ~40 deg false-wrap / divergence-masking artifacts.
SUBSTITUTIONS: none