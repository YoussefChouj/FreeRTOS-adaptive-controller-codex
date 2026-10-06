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
