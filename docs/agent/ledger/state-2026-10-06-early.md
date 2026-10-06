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

## 10-06 lab workflow C f01 (moved from .claude_state.md 12:55)
11:55 pack P4000-1; 8081 UP (pid 12476). Run logs/workflow-c/20261006-1155/flight01.yaml, launch copy
logs/campaigns/launch/wfc-20261006-1155-01_20261006-1153.yaml. Preflight ok (2nd call primes core): 16.02 V SoC 78%,
pos 0, disarmed.
12:00 FLIGHT 1 FLYAWAY, operator RC-killed at ~5.5 s ("drifting, did not happen in last flight experiments").
Rec logs/sessions/20261006-115832-wfc-20261006-1155-01-001_f01 (scratchpad f01.py/f01b.py pivot). Facts:
(a) GS abort L1 "nonfinite:age_s" at t .8 s = a _FRESHNESS_SYMS symbol (campaign_live.py:45) never arrived ->
LAND sent t .94, ACKed, IGNORED: prim stayed 1, z climbed to .58 m. (b) spool-up on ground (z 0) of2_dx/dy_fix
jumped +17..20/+15 counts. (c) airborne t 1.9: EKF vx -> -42 cm/s, vy +30, x -115 cm y +60 by 5.4 s; position loop
correct sign (locxs.Des +94) but drift velocity CONSTANT, roll ~-1 pit ~+.8 deg: vel-loop demand never reaches
attitude (roll/pitch Des not on log plan). Suspects: 10-05 firmware refactors since last flown build (WP-41 ekf.c
EKF_NOISE_ROW, WP-43 AutoflyTask "V_max/dead code removed", stm32f4xx_it, RemoterTask...); or takeoff state gates
horizontal loop. NEXT: tell operator (drone disarmed? keep it on ground); diff firmware last-flown-build..HEAD for
velocity->attitude chain + prim TAKEOFF/LAND handling; fix GS freshness symbol. No more flights until found.
12:35 ROOT CAUSE (confirmed 13:05): RC ch6 (OF hold) LOW = angle mode. Des_Att (StabilizerTask.c:1820, same gate in 1220a7d)
is the ONLY writer of pitch/roll Des; ch6 LOW drops locxs/ys.U -> flies level, drifts at takeoff velocity. f01:
flight_phase FLYING from 1.9 s (vel loop integrating), vx err 134 cm/s x Kp 3 = clamp, yet rol/pit +-1.6 deg. 10-03
flights were stick-flown with ch6 HIGH. Bias zero-at-arm fix (1850b03) intact at :1432. status.of_hold not on log
plan -> unconfirmed; ask operator ch6 position. FIXED GS side: preflight row of_hold (red if 0), workflow-b checklist
"RC ready with ch6 HIGH". OPEN: GS freshness abort (campaign_live _FRESHNESS_SYMS), LAND ignored in CLIMB (wfb_*
protected, propose), yaw kick -36 deg at spool-up, add status.of_hold to log plan.



## Archived from .claude_state.md at 14:30 (LAB workflow C 12:33-14:00)
13:25 F3 FLEW, GS FALSE ABORT. Idle fix worked (idle 10 s, TAKEOFF, xy <10 cm, z climbing) but L1 position_error at
11.2 s: z ref 0.5 vs drone 0.05 during TAKEOFF. Fixed 6ca93b1 (TAKEOFF: xy only). afe7510 takeoff_gate group, c54d8fb skill
Q3, 094a582 debrief next.yaml keeps flown log plan. 8081 restarted pid 8464. Debrief logs/workflow-c/20261006-1155/
01_wfc-20261006-1155-03-001 (next.yaml patched 100 Hz+takeoff_gate; campaign name still -02: rename -04). 100 Hz max gap .029 s.
OPERATOR (13:30, away 40 min, will FLASH on return): IDLE must NOT need throttle stick down. Armed by RC in flymode 1 at
campaign start = enough; agent idles + lifts off alone. Fix firmware (send_data.c:1945 RC_IDLE_THR_THRESHOLD gate + whatever
clears g_motor_idle_enabled on stick) + runner reason text + skill checklist text; hunt similar stick-dependent gates.
  14:00 NEW: Des_VHeight (StabilizerTask ~1754) turns |THR|>0.05 into a z-rate cmd, so after flyup releases authority a
  non-centred stick (throttle sits at bottom after RC arm) commands descent; f03 flew only because operator centred it.
  FIX PLAN: send_data IDLE in FlyMode_SDK takes authority; StabilizerTask latches "GS flight owns z" ignoring THR until
  the stick moves > delta from its flyup snapshot (= pilot takeover). Build only, operator flashes.
  FOUND: only stick gate = send_data.c:1941 (GS CMD 0x14 idx1 IDLE needs RCInput_Get(THR) < -0.85). Nothing else clears
  idle. But StabilizerTask.c:1231 GroundIdle_Step: idle held only while !TWC.execute && RCInput_Get(THR) < THR_IDLE_MAX,
  else Set_PWM_Motors -> so a raised stick must NOT reach it. PLAN: in send_data.c IDLE enable, take GS authority
  (RCInput_SetAuthority(1) = virtual THR -1.0, physical ignored; stick motion = takeover) when physical stick not down.
  CHECK FIRST: rc_input.c heartbeat-loss with authority (does it revert to physical THR on the ground?), wfb gate
  sbus_live/takeover (wfb_glue.c:109,300), host test tests/firmware_host/test_idle_decouple.c. rc_input.c/wfb_* protected
  (propose only); send_data.c + StabilizerTask.c editable. Then runner reason text (campaign_runner "throttle stick fully
  down"), workflow-b/c skill checklist, failure-modes.md; build (no flash: operator flashes on return).
12:55 F3 GO. e04fd31 runner: IDLE confirm (status.motor_idle within 1.5 s) + gate re-check before TAKEOFF; wfb rejects
named by wfb_err_t. Uncommitted: log group takeoff_gate (g_motor_idle_enabled, sbus_lost) in livewatch/campaign_capture.py;
workflow-c SKILL asks Q3 log plan every flight (operator asked). 8081 pid 18524. F3: flight03.yaml (f03, hover .5/20/idle 10),
launch copy logs/campaigns/launch/wfc-20261006-1155-03_20261006-1252.yaml, 100 Hz 4 groups 45600/70042 B/s (operator pick),
15.70 V 67%. NEXT: campaign_state, debrief f03, commit takeoff_gate+skill, flight_debrief --launch, propose_next keeps args.
13:40 F2 NO TAKEOFF. campaign wfc-20261006-1155-02-001 (outputs logs/campaigns/wfc-20261006-1155-02_20261006-123751,
rec logs/sessions/20261006-123729-wfc-20261006-1155-02-001_f02): IDLE (14,1) "applied" .5 s, 10 s idle, TAKEOFF (26,0)
rejected reason 1 at 10.66 s. GS labels it BAD_VERSION (transactions.RejectReason) but wfb reason 1 = WFB_ERR_STATE
(wfb_types.h). prim_state 0 (IDLE) so gate is wfb_glue.c:109 (have_tick/armed/motors_idle/sbus_live/airborne/takeover/
safety.action). Telemetry: ARM 1, phase 0, gs_flight_active 0, safety_trip 0. Suspect motors_idle=0 (send_data.c:1945 IDLE
silently no-ops unless throttle < RC_IDLE_THR_THRESHOLD) or cleared during 10 s. NEXT: find failing gate (f01 vs f02), fix
GS wfb reason decode, flight_debrief --launch + propose_next keeps idle_s ({**args,**rung}), debrief f01 then f02.
12:33 e068a77 takeoff idle_s (hover.yaml default 10 s; operator: spool-up too short). 8081 restarted pid 20340
(abort alias + idle live). F2: logs/workflow-c/20261006-1155/flight02.yaml, launch copy
logs/campaigns/launch/wfc-20261006-1155-02_20261006-1233.yaml, P4000-1 15.75 V SoC 68%. Preflight red: ARMED
-> operator disarms, refresh, checklist (ch6 HIGH), arm, go. Operator: runs too slow -> streamline (one-command next flight).
13:05 CONFIRMED: operator flew again with ch6 (sbus_channel[5]) HIGH -> no drift. f01 root cause = ch6 LOW (angle
mode). GS abort at .8 s FIXED: live_sample asked imu_data.rol/pit + Ctrler.gyro*PID.FB, service files them only as
status.*_deg / pid.gyro*.FB (schema_registry builtin_dashboard) -> age inf; now reads the alias (campaign_live _ALIASES,
test_live_sample_reads_dashboard_spellings). velocity_loops log group += g_of_hold_active, pitch/rollPID.Des (9 vars,
22000/70042 B/s). OPEN: LAND ignored in CLIMB (wfb protected, propose), yaw kick -36 deg at spool-up.
f01 flyaway + root cause (ch6 LOW) archived to state-2026-10-06-early.md.


## Archived from .claude_state.md 2026-10-06 17:00
16:00 F6 (hover 1.0, 8081 pid 19272, campaign out logs/campaigns/wfc-20261006-1155-06_20261006-155243, session
logs/sessions/20261006-155152-*_f06, debrief 06_*) clean. OPERATOR: ~30 cm -x drift, "mostly at landing" (F4 12 cm y
also at landing). Data: descent 35.5-40 s 1.0->0.1 m, OF dx within +-6 cm/s mean ~0, xFB 1->7.8->3.9 cm: OF never saw
it. of_alt_cm tracks z (101 hold), of_quality constant 255 (useless). Hold 20 s fine (x RMS 3 cm). Hypothesis: OF blind
to lateral motion during vertical motion (descent ~0.2 m/s; texture/pad asymmetry -> expansion flow bias?).
BUG: debrief "Did the last change help?" credits gyroyPID Kd 10->8.5 that was NEVER applied (proposal treated as flown).
Scripts: scratchpad d0468d51.../phase.py (per-prim OF integral), live.py (hold OF liveness).
14:25 F5 done, debrief 05_*: z std 0.042->0.036 (Kp 340 better). Debrief "ACT telemetry gap 703 s" is FALSE: session csv
starts with 4 stale rows (received_ns from F4 13:57:41) AND every row is written twice (zero-dt 3671/7341 in f04+f05 ->
median dt 0 -> "gaps" counts all). TODO fix: recorder double-write (core.py _note_recorder paths / storage.note) + debrief
dedupe/drop stale lead. OPERATOR: F5 drifted ~1 m +x (RC manual only 20-30 cm). Data: locxPID.FB stays within +-4 cm,
locxsPID.FB ~0 -> the ESTIMATE never saw the drift (controller held a wrong estimate). Suspect OF bias: g_of_bias_mode=2 EKF
(StabilizerTask.c:139-260) bias state can absorb slow real drift; ground OF reads frozen 3/4 raw before TO. Bias states
not in log plan. NEXT: log s_of_bias_x/y + KF bias, tape-measure drift, consider mode 0 vs 2 A/B (CMD 0x1E idx0, ground only).
14:35 Operator: "nothing to measure", manual RC hover in flymode 1 = no drift. Manual and GS share Des_Loc/Des_VLoc
(StabilizerTask.c:1813-1856) + same KF -> difference is GS-only: LEADING HYPOTHESIS the 10 s ground IDLE (props spinning,
OF at 5 cm reads frozen 3/4 raw) lets mode-2 KF learn a wrong OF bias before TO; manual pilots take off within seconds.
Also z 0.5 m (low for OF laser?) vs manual height. KF bias fields exist: telemetry_groups.py:61 TlmGroup "est"
(kf_bof_x/y_mps, of_bias_x/y) - check if campaign log plan can select it. Hold-mean raw ofx ~0.4 vs ground 3.0.
NEXT: tell operator hypothesis; propose f06 = same hover + "est" group logged (+ maybe idle_s 3 to A/B the idle).
14:10 OPERATOR FLASHED (axf 13:50); 8081 restarted -> pid 13332. F4 (wfc-...-04) FIRST STICK-FREE FLIGHT: idle 10 s, TO,
hold 20 s, land, no abort. Hold: z std 0.042, x/y RMS 0.024/0.022, pitch rate line 8.2 deg/s @26 Hz, motor2 +0.065.
Operator: landed ~12 cm off in y (hold was 2 cm -> drift is in descent/touchdown; CHECK in f04/f05 data). Debrief 04_*.
Gain plan-1 Z_ratePID Kp 400->340 applied (approved via POST with operator chat words, memory chat-approval-for-gain-writes;
resets on power cycle). F5 (wfc-...-05, launch copy logs/campaigns/launch/wfc-20261006-1155-05_20261006-1402.yaml) GO 14:09.
14:30 STICK-FREE GS FLIGHT (operator 13:30: flymode 1 + RC arm = only input; operator FLASHES on return, I do not).
0e5beae firmware: send_data IDLE in FlyMode_SDK takes stick authority (no THR<-0.85 gate); StabilizerTask Thr_StickActive
latch: GS flight ignores THR in Des_Height/Des_VHeight/CalHot until |THR-flyup spot|>0.15 (chosen; 42ffacc: roll/pitch
takeover alone keeps the alt hold); authority released on disarm edge. b2cd094 runner refuses TAKEOFF if rc_authority 1->0
during the idle wait (pilot takeover; wfb_apply is protected) + failure-modes 27c. armcc compile rc 0 both files (uVision GUI open -> no full Keil link; the
operator's flash builds it). CHECK PASS. c6242ff runner reason + test + workflow-b SKILL checklist + failure-modes 27b.
F4 ready: logs/workflow-c/20261006-1155/01_wfc-20261006-1155-03-001/next.yaml (wfc-...-04, f04, hover .5/20/idle 10,
100 Hz 3 groups). 8081 pid 18404 (restarted with b2cd094). NEXT on return: operator flashes (rebuild_and_flash --yes), then launch f04.
OPEN: no host test for Thr_StickActive; LAND in CLIMB (runner resends at HOVER; proposal docs/agent/proposals/2026-10-06-protected-gs-flight.md); yaw kick at spool-up; debrief
flight_debrief --launch; propose_next keeps args; debrief f01/f02.
F3 13:25: idle 10 s, TAKEOFF ok, GS false L1 position_error (z ref ramp) fixed 6ca93b1. Debrief in 01_wfc-...-03-001.


## Moved from .claude_state.md 17:5x (14:50 OF, 16:40 manual landing, 16:05 F7 launch)
14:50 KF-bias/idle hypothesis REFUTED: raw of2_dx_fix (pre-bias) summed over f05 hold = +0.07 m (TO -0.05, f04 same) ->
the OF sensor itself never saw the ~1 m; estimate followed it. OF alive in air (std 2 cm/s). Suspect OF scale at z 0.5 m
(module height scales flow) vs manual hover height. dc5d381: optical_flow group + of_quality, of_alt_cm, of_update_cnt
(81 vars still fit @100 Hz). NEXT: restart 8081, ask f06 = hover z 1.0 (A/B height) with new OF fields; compare of_alt_cm.
16:40 MANUAL LANDING CAPTURE done (8 landings, operator: last = best) -> docs/workflow-b/manual-landing-reference.md.
M8 0.3->0.13 m in 0.28 s vs F6 auto 0.86 s (3x); auto slows near ground (pos ramp + Z_posPID Kp 0.7 -> sink ~0.7*z,
10-02 slow stage 0.15 m/s <0.40 m). Operator chose rate-mode land -> 23a0c9c: LAND_VZ_MPS 0.40 to LAND_FAST_ALT 0.50,
LAND_VZ_FAST_MPS 0.70 below (Des_VHeight LANDING; Des_Height lowers Des at same rate for floor gates). armcc rc 0,
CHECK PASS, NOT FLOWN. NEXT: operator flashes (rebuild_and_flash --yes; resets Kp 340/Kd 8.5 to pid.c 400/10), then hover
A/B + tape-measured landing drift. fake_drone descend_rate 0.3 not updated (sim). 8081 pid 1480.
16:05 F7 launched (launch copy logs/campaigns/launch/wfc-20261006-1155-07_20261006-1556.yaml) NOT flown. gyroyPID Kd
10->8.5 applied (plan-1, live until power cycle) WITHOUT a fresh arm check (my miss: read status.arm right before every write).

## Archived from .claude_state.md 18:58 (touchdown F7-F9)
17:xx TOUCHDOWN CAPTURE DONE (1 landing, logs/livewatch/touchdown_20261006.csv). 23a0c9c IS flashed (-0.40/-0.70 seen).
Contact 77.08 s z 0.07, rest gate cut 0.19 s later (40 ticks) at motor avg ~2974 (hover ~3082, ZERO 2000) = ~90% hover
on ground, attitude loop live (motors 2400-3650) -> skid slide = drift; then 1-tick step cut -> +5 cm hop at 77.40,
tip -4.5/-5.2 deg. vzDes stayed -0.70 on ground; Z loop frozen 77.11-77.26 (s_alt_valid_tick? unverified).
FIX f81da19 (armcc rc 0, CHECK PASS, NOT FLOWN): every land gate -> 0.3 s spool-down (mymotor above ZERO x k 1->0,
vzDes 0), then LANDED+disarm; rest debounce 40->10 ticks. Operator flashed it via Keil (axf 17:22; SWD verify failed
"no cores", skipped). 17:28 F7 FLOWN (hover 1.0, pid.c gains, Kd 8.5 NOT live): spool 37.34->37.61 avg 3139->2096 linear,
disarm 37.64; tilt peak 2.0/2.1 deg (was 4.5/5.2); 1.0->0.5 1.34 s, 0.5->0.3 0.35 s, 0.3->0.13 0.23 s (M8 0.69/0.23/0.28).
z/vzFB frozen on ground (vzFB -0.33 const) -> hop not visible in z; need operator eyes. Debrief 07_*: act pitch_rate_osc
10 deg/s @26 Hz -> Kd 10->8.5 again. After disarm mymotor shows mix 3159 (telemetry only). 8081 pid 19012.
Recorder fix = cbdd473. 17:40 operator: "small bounce, still drifts, needs faster; stop ad-hoc analysis, use reusable
scripts; give landing params + PX4 reference". Param guide + PX4/ArduPilot DONE 9af19c0 (docs/workflow-b/landing-parameters.md).
landing_report DONE 0eeb27b (+ Landing section in debrief.md, workflow-c skill note). F7 via tool: 1.34/0.35/0.30 s
(scratch said 0.23; z steps ~15 Hz). FINDING: bare alias X is rounded to 0.01, slot0.X full precision -> reader audit: prefer slot0.
F8 8d9ef22 (LAND_VZ 0.60, FAST 0.50, SPOOL 100, contact->xy lean 0 + SumE 0) flown by operator (axf 17:54), NOT
recorded; "small bounce at the end". F9 c5380e4 (armcc rc 0, CHECK PASS, NOT FLASHED): spool latches collective
(min(Throttle_out, Throttle_th)) in Land_Step, Mix_Compute uses it while s_land_spool!=0 (fix for F7 3085->3139 push).
18:2x operator: F9 flown, "landing is perfect" (operator eyes, not recorded). LANDING CLOSED.
