# State archive: 2026-10-05 to 2026-10-06 06:0x (moved out of .claude_state.md)

## 2026-10-06 06:0x LAB CHECK DONE (lab sheet step 0 note): HEAD 37265dd Keil build-only OK 0E/4W (old), Code 118700;
139 campaign + 14 analysis tests pass; 8081 down; all lab-sheet modules import. NEXT: NN-denoising feasibility doc
(docs/analysis/imu-nn-denoising.md, offline on logs first; downloads need user yes). h0g step 2 paused.

## 2026-10-06 06:0x h0g port step 1 DONE on branch h0g-port a2ccf99 (worktree ../FreeRTOS-h0g, NOT merged/flashed,
not Keil-built): API/h0g.c/.h, g_h0g_on 0, hooks Pos_Compute x/y + Z_Compute z + H0G_Init at arm edge, test_h0g 25/25,
CHECK PASS. Main: doc sec R + HANDOFF item 6. NEXT: step 2 = PIDG attitude layer (ref model choice), PROPOSED;
then h0g telemetry slot / g_h0g_on param switch (branch), then "sky is the limit" list.
Step 2 facts (sim/bench/ctrl_g.py, ctrl_h0g.py): per roll/pitch th=[bias,P,I,D], phi=[1,Up,Ui,Ud]/UN_N (300,
sim_axes.py); s=(p-p_m)+LAM(4)(phi-phi_m) rad vs a ref model of the TUNED angle+rate PIDs on b=B_RP 8; th+=DT_C(0.005)
(gamma_g phi s/(1+phi'phi)-(SIGMA .01+mu dU)th), clip lo[-.3,-1,-1,-1] hi[.3,.5,.5,.5]; U-=UN_N th'phi; dU=|U_cmd_prev-
mixer torque|. gamma_g=1/(10 tau_r S_r), tau_r=(1+8 Kd .005)/(8 Kp), S_r=rad(ANG Umax). mu: grep ctrl_mrac MRAC_SatAware.

## 2026-10-06 00:1x H step 2 DONE (doc sec P): h0g_nom (ctrl_h0g.py = h0_sep_nom + ctrl_g attitude layer at derived
gamma_g 0.601) ladder div 19.6 % (h0_sep_nom 24.2, mrac5_xyz_rob 22.0, pid_nom 33.1), nominal p90 0.083 m, arm_load
break L3 -> L4; actuator L1 slightly worse. P.1: gamma_g x0.25..x4 -> div 19.2-20.9 % (flat). Sec Q (demo_loads pid_nom vs h0g_nom): arm
limit ~350 g -> ~450 g (450 g: 7-10/10 div vs 0/10; 500 g: 10/10 vs 2-4/10). NEXT: "sky is the limit" list (C waits
download yes); h0g firmware port PROPOSED branch after demo.


## 2026-10-05 20:1x-20:5x critique -> stress ladder (doc K, c1bbe9a), K.5 nominal re-tune (tune_nominal.py), K.6 motor lag
not identifiable from logs (RPM ignored until operator fixes sensor 10-06). combo L1 = arm-tip cable + actuator lag ->
10-06: mount arm load rigidly. div: pid_nom 33.1, mrac5_nom 30.7, sataware_nom 52.9 (under-tuned).

## 2026-10-05 18:1x demo build for 10-06 (return-to-origin; asymmetric 250-500 g load) DONE
Commits d01a933..58e0403 (of_gyro_cal, re-seat gate DRIFT_BUDGET_M 0.5 PROPOSED needs operator 8081 restart, asym twins,
ofcal, demo_loads + doc J: arm load <=350 g ok, 500 all div; lab run sheet; twin_compare; ctrl_h0 + doc H).

## 2026-10-05 continuous improvement (CEO inline, user: "dont stop working until i prompt you to stop")
Scope: telemetry stream, workflows A/B/C, control/driver compute (no compromise), agent compatibility, standards.
Rules: one item per commit; firmware behaviour changes go on branches only, NOT merged or flashed before the 10-06 demo.
NEVER run unfiltered `git status`/`git diff --stat` : add `-- . ':!OBJ' ':!USER'`.

Branches (each has its doc, HANDOFF Next 2-4):
| Branch | Worktree | Measured | Doc |
|---|---|---|---|
| ram-savings 6e7d192 | ../FreeRTOS-ram-savings | SRAM 124,984 -> 22,048 B | docs/firmware-ram-budget.md |
| o2-build 05fe642 | ../FreeRTOS-o2-build | Code 118,300 -> 92,284 B | docs/firmware-compiler-optimisation.md |
| static-etag (see log) | ../FreeRTOS-static-etag | ETag/304 static files, 78 service tests pass | commit msg |
| float-math cbf80f8 | ../FreeRTOS-float-math | Code -2,548 B, RO -284 B | docs/firmware-float-math.md |
Branch speed NOT measured (bench: hlth.stab_cpu_pct, loop_max_us, mrac_cyc).

NEXT: pick the next "sky is the limit" item (telemetry stream / workflows A,B,C / agent compat); gate is done.
Backlog: A/B/C analysis dedup (post-demo); `_Static_assert` layout checks (branch); PI=3.14159f (finding).


## Archived from .claude_state.md 2026-10-06 12:35

## 11:15 operator: rock results? gyro bias fixed for return-home? NN worth it? -> ANSWERED: gyro not the problem (residual <.03 deg/s;
of2_dx_fix is the module's own tilt-comp velocity, 0.01 m/s/count, send_data.c:531). Flow zero-point is: re-snapped
from ONE sample at every ARM edge (StabilizerTask.c:140-174), default bias mode 2 EKF; preflight never sends 0x17.
Still-on-stand OF log (no flow in noise/warm-up logs) measures offset + 1-sample noise -> decides if a 2 s averaged
snap is needed (PROPOSED). NN: not now (needs mocap truth, gyro not the bottleneck).
11:33 STILL OF capture RUNNING (bg, logs/livewatch/of_still_20261006-113321.csv). Operator: "did you account for the
ground freeze?" -> NO. 72 s so far: of2_dx/dy(_fix) ALL exactly 0, std 0, quality 255, s_of_bias 0 = module freezes
output on ground (StabilizerTask.c:719 FIX 10-03: froze at 3,5/2,3 then). Still test can't see the zero point.
Hypothesis: ARM snap copies a frozen stale value (0 today, 3.5/5.7 other days) -> fake constant drift in flight.
STILL OF DONE 11:41 (478 s, 199.2 Hz): flow exactly 0 except one burst t~390-400 s (-1..+2 counts, ~100 rows,
likely a touch); quality 255 throughout, of2_h 0, s_of_bias 0. Gyro means x -.016 y +.067 z +.001 deg/s (LPF).
Frozen value today = 0, so an arm snap today is harmless -> a hover today tests drift with bias 0. Fix idea (PROPOSED,
StabilizerTask.c arm snap, operator decides, post-demo?): skip snap when std==0 frozen, learn bias in first hover s.
11:45 operator: GO hover test (workflow C). Runner (agent_arms False): operator RC-arms, runner sends IDLE then
TAKEOFF = operator's arm->idle->fly order. Keil debug: do NOT open during flight (grabs probe, may halt/reset MCU).
WARMUP DONE 11:05 logs/livewatch/warmup_20261006-105524.csv (614 s, 199.4 Hz, Real_Temp 18->30 C, lights on t~100-116 s:
no step). New ground_station/analysis/warmup_bias.py (+test): total bias (offset+ORI mean) slope x +.0017 y +.0170 z +.0017
+-.006 deg/s/C -> y ~+0.2 deg/s over 12 C (~3 sigma), x/z zero. 10 s windows are noise (+-0.15): use bins/regression.
z tracker (bmi088_driver.c:435, tau 10 s, PROTECTED file) wanders Gyro_Z_Offset std .034 p2p .19 deg/s > z thermal drift;
finding only (post-demo, operator decides). NEXT: ask operator: still-on-stand OF test vs 8081 + workflow C flights.

## 2026-10-06 LAB (operator in lab, AUW 967.5+365=1332.5 g): FLASHED HEAD (Code 118700, rebuild_and_flash rc 0,
axf matches). 8081 DOWN on purpose (livewatch binds UDP 14550). 200 Hz WiFi = poke g_telemetry_mode 0x2000092c=2
AND mrac_flags.of_frame_on 0x20015384=1 (Send_Task 5 ms floor only under of/id_frame_on, main.c:297); mode 2 alone
stays 100 Hz. Measured 199-200/s. Script scratchpad hz200.ps1 (restores 1/0). 100 Hz noise log done
(noise_floor_20261006-101937.csv: raw gyro std 4.7-5.2 deg/s white, LPF 1.2-1.4). Findings: imu_full lists struct
Gyro_X_Lpf; 2 old test_stream ceiling fails. 200 Hz noise (a7b5e27): residual bias <0.03 deg/s all axes, raw white 0.47 deg/s/rtHz (~30x BMI088
datasheet), FC BMI088 only (not ano_of gyro). Boot offsets x +0.16 y +0.12 z -0.11; Real_Temp 30 C (int, 1 C) at
blocks 1224. Operator asked: does capture time after reset matter -> proposed warm-up log from reset w/ Real_Temp.
ROCK DONE logs/livewatch/of_gyro_cal_20261006-103549.csv (200 Hz 65 s, rol +-30 pit -23..41, quality 245-255):
ano_of.gyr_data_x/y/z ALL ZERO (module gyro not parsed/sent); FC gyro explains raw flow R2 .98/.96, k 18.8/-16.9,
flow lags FC gyro +65 ms (EKF delay? check); of2_dx_fix/dy_fix mean +5.0/+6.6 (still parts +3.5/+5.7), dy_fix
gyro left R2 .61. One SWD read glitch (mode 161, re-read 1). NEXT PROPOSED: 30 s still-on-stand OF test to split
hand drift vs fix-channel bias; warm-up-from-reset log; then 8081 + workflow C.

## 2026-10-06 06:2x NN-denoising study DONE: docs/analysis/imu-nn-denoising.md (Brossard 2002.10718, TinyGC-Net
2403.02618, Cioffi 2210.15287; NN needs a reference, yaw unobservable; offline-first plan on Gyro_X_Real/Acc_X_Real
logs; nothing built). NEXT after lab: h0g step 2 (paused), C waits download yes.
