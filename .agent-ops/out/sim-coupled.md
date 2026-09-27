# sim-coupled task digest
STATUS: done

1) Mixer & Gains (from firmware):
- Mixer signs: TASK/StabilizerTask.c:1062-1082 (M1=Thr-pit-rol-yaw, M2=Thr+pit+rol-yaw, M3=Thr-pit+rol+yaw, M4=Thr+pit-rol+yaw, because g_yaw_mix_dir = -1.0)
- PID gains: API/pid.c:6-28. Ctrler struct init.
- Roll/Pitch angle: Kp=2.6, Ki=0.1, Kd=9.5. Rate: Kp=5.0, Ki=0.01, Kd=10.0, Umax=300
- Yaw angle: Kp=6.5, Ki=0.04, Kd=1.5. Rate: Kp=4.0, Ki=0.005, Kd=2.0, EMin=1000, UiMax=500, SumEMax=100000, Umax=650 (lines 10-18)
- Z pos: Kp=0.7, Ki=0.005, Kd=0.1. Z rate: Kp=400, Ki=0.435, Kd=1.5
- PWM bounds: 2000 (stop) to 4000 (max). Clamped in Set_PWM_Motors() (BSP/pwm.c:269-285).

2) Assumptions & Key Numbers:
- Mass = 1.5 kg, Jx=Jy=0.0023 kg*m^2, Jz=0.0015 kg*m^2 (API/mrac.c:292-564).
- Hover throttle = 2950 (free flight bench mode, TASK/StabilizerTask.c:1043).
- Motor lag: tau = 1/19.8 s + 15 ms delay.
- Computed thrust map: c_T = 0.00387 N/PWM (from 1.5kg*9.81 / (4*(2950-2000))).
- Computed roll/pitch torque map: c_PR = 7.9e-5 Nm/PWM (derived from default 1170 PR K_MIX and effectiveness 0.37).
- Computed yaw torque map: c_Y = 4.9e-5 Nm/PWM (derived from default 1872 Yaw K_MIX).

3) Verification:
- `python sim/adaptive_compare/run_coupled.py` completed successfully with rc=0.
- `ls sim/adaptive_compare/figures_coupled/*.png | wc -l` yields 7 PNG files.
- `results_coupled.json` is generated correctly.
- SUBSTITUTIONS: No synthetic stubs used for physical logic; full 6-DOF Euler equations + real firmware constants implemented.

4) Open questions / risks:
- Thrust mapping is assumed linear above idle instead of actual empirical mapping since placeholder values were found in `API/thrust_estimators.c`.
- Using G_EFF = 0.37 uniformly for Yaw map estimation.
