#include "StabilizerTask.h"
#ifndef ALT_CF_HOST_TEST
#include "imu_update.h"
#include "ekf_of.h"         /* 8-state OF position+velocity-bias+accel-bias KF (Mode 2 bias estimation) */
#include "ekf.h"            /* g_ekf_gate: EKF body velocity for the x/y velocity loops */
#include "math.h"
#include "pid.h"
#include "ADC.h"
#include "mrac.h"
#include "controller.h"
#include "gyro_filter.h"
#include "sysid.h"
#include "excite.h"
#include "rc_input.h"
#include "flight_fsm.h"
#include "calib.h"          /* ADR-0011 Phase 3 (acc-trim) + Phase 4 (gyro-hot) FSMs */
#include "RemoterTask.h"   /* OFHOLD_CH (ch6 OF-hold enable switch), sbus_channel[] */
#include "thrust_estimators.h"
#include "rpm.h"
#include "flight_telemetry.h"   /* g_tlm telemetry groups, filled by Tlm_Snapshot */
#include "prearm.h"             /* g_prearm_* masks (hlth group) */
#include "fw_health.h"          /* g_rtos_budget (hlth group) */
#include "land_gate.h"           /* always-on low-V / motor-pin auto-land, g_land_gate_enable */
/* WFB BEGIN glue */
#include "wfb_glue.h"       /* Workflow B: GS takeoff/trajectory/land sequencer + safety net */
#include "wfb_traj.h"
#include "wfb_prim.h"
#include "xy_ctrl.h"        /* ADRC / LQR / MPC xy outer loops (g_xy_ctrl_kind) */
#include "land_xy_gate.h"   /* camera pad gate for the land (g_land_xy_gate_cm) */
#include "exp_row.h"
#include "drift_nn_task.h"   /* g_drift_nn residual, g_drift_nn_live (shadow unless set) */
extern volatile float g_land_xy_gate_cm;   /* defined with the xy_ctrl VARs below */
extern volatile uint8_t g_xy_ctrl_kind;
extern volatile uint8_t g_imu_mahony_yaw_decouple;
/* WFB END glue */
extern volatile uint8_t traj_active;   /* AutoflyTask.c: id of the running Keil preset, 0 idle */

/**
 * @module     StabilizerTask.c
 * @subsystem  control
 * @owner      Stabilizer_Task (USER/main.c:418): stabilizer_Task() runs once per 5 ms tick (200 Hz).
 * @purpose    One control tick: sensors -> loop feedback, setpoints, PID/MRAC cascade, mixer, motor gating.
 * @inputs     imu_data, Gyro_*_Real, Lin_Acc_*_body, Gravity_Body_* (IMU), ano_of (optical flow + ToF),
 *             RCInput_* (RemoterTask), TWC targets (send_data.c, wfb glue), FlightFSM state, flight_phase.
 * @outputs    Ctrler.*.{Des,FB,U} (API/pid.c), Throttle_out, u_gyrox/y/z, mymotor -> Set_PWM_Motors(),
 *             OF bias + OF KF state (s_of_bias_*, s_ekf_of), g_cal_health, the landing detector.
 * @depends    pid.h, controller.h, mrac.h, ekf_of.h, ekf.h, calib.h, flight_fsm.h, rc_input.h, wfb_glue.h
 * @caution    mixer sign conventions and motor channel ordering are safety critical (Compute_Motor).
 *
 * Tick order, stabilizer_Task():
 *   warm-up origin pin, arm-state mirror, cold-boot sync
 *   Update_Data()    trig cache, OF bias + OF KF, OF position, calibrators, OF velocity, ToF height, attitude FB
 *   Wfb_Step()       Workflow B glue (API/wfb_glue.c)
 *   Compute_Motor()  landing contact, Z loops, ARM-edge rebase, position/velocity loops (every 2nd tick,
 *                    100 Hz), angle + rate loops, SysID, MRAC, controller layer, mixer, thrust estimators
 *   Update_Motor()   bench zero, motor-test/debug overrides, landing detector, ground idle, disarm
 *   Tlm_Snapshot()   read-only copy of this tick's key state into g_tlm (API/flight_telemetry.h)
 * Units: position cm (locx/locy loops, TWC.target_x/y), height m (Z loops), angles deg, rates deg/s,
 * motor commands in TIM CCR counts (2000 = stop, 4000 = full, BSP/pwm.h).
 */
unsigned char cnt_h,cnt_loc;
float Throttle_out,u_gyrox,u_gyroy,u_gyroz;
/* Rate-loop output c, sysid torque add d and mixer input u = c + d per axis, [0] pitch [1] roll [2] yaw (SysID_Axis_e),
 * mixer units, before the pitch sign flip. Written by Mix_Compute, streamed in g_tlm.sysid_cdu. */
static float s_sysid_c[3], s_sysid_d[3], s_sysid_u[3];
short Throttle_th = 2200;

/* ---- Constants --------------------------------------------------------------------------------- */
#define STAB_DT_S            0.005f   /* control tick, s (200 Hz) */
#define CM_PER_M             100.0f
#define M_PER_CM             0.01f
#define THR_IDLE_MAX         0.2f     /* RC throttle below this (0..1) = "motors idle" for every ground gate */
#define THR_TAKEOVER_DELTA   0.15f    /* GS flight: throttle moved this far from its take-off spot = pilot takeover (chosen, not measured) */
static int  Thr_StickActive(void);          /* below Des_Height: GS flight ignores the throttle until takeover */
static void GsIdle_ReleaseOnDisarm(void);    /* below Des_Height: GS idle authority back on disarm */
#define TAKEOFF_ALT_M        0.2f     /* GROUND_IDLE -> FLYING once Z FB is above this (with THR or TWC) */
#define POS_LOOP_DIV         2U       /* height and position loops run every 2nd tick (100 Hz) */
#define DES_FLOOR_M          0.01f    /* Z setpoint at or below this = the landing ramp reached the floor */
#define HOVER_THR_BENCH      3100     /* Throttle_th, CCR, on the 4DOF fixture (bench_mode_active) */
#define HOVER_THR_FREE       3050     /* Throttle_th, CCR, free flight; see the history at Compute_Motor */

/* LAND is rate mode (2026-10-06): Des_VHeight commands the climb-rate loop directly,
 * -LAND_VZ_MPS down to LAND_FAST_ALT, then -LAND_VZ_FAST_MPS to contact (PX4 MPC_LAND_SPEED
 * style). It replaces the position ramp + Z_posPID (Kp 0.7 -> sink ~0.7*z, slowest in ground
 * effect) and reverses the 2026-10-02 0.15 m/s slow stage. Source: operator manual landing M8
 * (docs/workflow-b/manual-landing-reference.md): 0.37 m/s from 1.41 to 1.0 m, 0.61-0.87 m/s
 * below 0.5 m, 0.3 -> 0.13 m in 0.28 s vs 0.86 s auto (F6). Des_Height still lowers
 * Z_posPID.Des at the same rate so the Des-at-floor touchdown gates keep working.
 * Touchdown detected by Z_ratePID.FB -> 0 on frame contact. */
/* LAND_VZ_MPS / LAND_FAST_ALT / LAND_VZ_FAST_MPS (two stages since 2026-10-21) live in land_profile.h. */
#include "land_profile.h"
#include "pid_fallback.h"
#define LAND_TICK_S        0.005f   /* Des_Height runs every 200 Hz tick */
/* Safety net: 3000 ticks at 200 Hz = 15 s max landing time before forced disarm
 * (was 10 s; kept for margin: rate-mode LAND from 2.5 m is about 5.7 s, arithmetic). */
#define LAND_MAX_TICKS  3000U   /* 41c9dea2 (2026-10-07) cut it to 500 (2.5 s) unexplained; restored 2026-10-21 */
/* Touchdown ground evidence (2026-10-02b): FB within LAND_REST_MARGIN of the pre-takeoff
 * rest height, or the sink bias saturated at LAND_SINK_BIAS_MAX (see Update_Motor). */
#define LAND_REST_MARGIN     0.03f
#define LAND_SINK_BIAS_MAX   0.40f
/* FIX 2026-10-03 (flight_test_drift_fix_1 F5): sitting at rest height cuts on its own.
 * F5 touched down at 0.05 m (rest 0.05) for 0.44 s, but the climb-rate reading swung
 * +-0.3 m/s on the ground, the 0.08 m/s stable-rate test never passed, and the rate
 * loop threw throttle at each impact: 5 bounces in 7 s. Now Des at the floor and FB
 * within LAND_REST_MARGIN of rest for LAND_REST_CUT_TICKS also lands (F2-F4 sat there
 * 0.26-0.40 s right before their normal cut, so they are unchanged). */
#define LAND_REST_CUT_TICKS  5U   /* 25 ms at 200 Hz, then the spool-down below (was 40: 0.2 s) */
/* FIX 2026-10-06 (logs/livewatch/touchdown_20261006.csv): touchdown spool-down. The cut was one
 * step from ~90% of hover to Motor_PWM_ZERO after 0.19 s on the skids with the attitude loop live:
 * the skids slid (drift), then the gear took the full weight at once and the drone hopped +5 cm
 * and rocked -4.5/-5.2 deg. Now every touchdown gate starts a spool-down instead: vz command 0 and
 * each motor's command above ZERO scaled by k = 1 -> 0 over LAND_SPOOL_TICKS, then LANDED + disarm
 * (PX4 maybe_landed -> landed). Once started it always finishes. PROPOSED values, not flown. */
#define LAND_SPOOL_TICKS    100U   /* 0.5 s (F8; was 60 = 0.3 s, F7 still bounced a little) */
/* FIX 2026-10-03 (FLIGHT_TEST_DRIFT_FIX_1 F1, F3): floor for s_land_rest_z. Right after
 * power-up the ground reads 0.00 m, but after a landing it reads 0.05 m, so the rest gate
 * (0.03 m) never opened on the first flight per battery: props spun 1.5 s on the floor at
 * ~94% hover throttle while the xy estimate ran 15 cm and the hold tilted up to 15 deg (skid).
 * Gate is now 0.08 m minimum, still under the 0.11-0.14 m ground-effect float. */
#define LAND_REST_MIN        0.05f
/* WP-21 B: ground-contact stage (ArduPilot land_complete_maybe / soften_for_landing_xy idea).
 * In LANDING with the Z ramp at the floor, FB below LAND_CONTACT_ALT and |vz| below
 * LAND_CONTACT_VZ for LAND_CONTACT_TICKS, hold (not zero) the xy position and velocity
 * integrators. With feet touching, friction stops the motion the I terms are pushing for,
 * so they would wind up a tilt that skids or tips the drone at the cut. P and D stay
 * active: position hold is NOT turned off. It can also latch while floating on the
 * ground-effect cushion (0.11-0.14 m, vz ~0); holding I there is harmless. Does not fix
 * the OF position-estimate error itself.
 * F8 2026-10-06: in contact (or once the spool-down started) the xy lean command is now zero
 * and the xy integrators are cleared (PX4 style, see the end of Pos_Compute). F7 never latched
 * this stage: the rest gate started the spool 0.05 s after contact, before 60 ticks. */
#define LAND_CONTACT_ALT     0.15f
#define LAND_CONTACT_VZ      0.10f
#define LAND_CONTACT_TICKS   60U   /* 0.3 s at 200 Hz */
/* Motor bench-test dead-man: stabilizer runs at 200 Hz, so 100 ticks = 500 ms.
 * If the dashboard stops sending CMD 0x16 heartbeats, motors are zeroed. */
#define MOTOR_TEST_DEADMAN_TICKS  100U
float Cos_Yaw_01= 0;
float Sin_Yaw_01= 0;
/* OF position-hold applied state (see case_Update_pitrol_Des); telemetered as status.of_hold. */
uint8_t g_of_hold_active = 0;
/* Keil debug manual motor override: set dbg_motor_manual=1 in the Watch window, then edit
 * dbg_motor_ccr[0..3] (M1..M4, 2000=stop, 2150=idle, max 4000). DISARMED-only, no dead-man:
 * set dbg_motor_manual=0 to stop. Props off. Never halt the CPU while a motor spins. */
volatile uint8_t  dbg_motor_manual = 0U;
volatile uint16_t dbg_motor_ccr[4] = {2000U, 2000U, 2000U, 2000U};
/* Yaw mixer sign: -1 = 09-10 signs (M3/M4 get +u_gyroz), +1 = flipped. Flight4 (+1) spun 3.5x faster. */
volatile float g_yaw_mix_dir = -1.0f;
/* 0: the OF EKF ignores raw flow of1 (single of2 channel, as before 41c9dea). 1 = 10-07 flown behaviour.
 * 10-07 load flights: swing-band velocity FB tracked body rate at 85-103 cm/rad, the of1 slope, ~3x the
 * physical g/w^2 (docs/flights/2026-10-07-load-swing-analysis.md, PROPOSED cause of the growing MRAC swing). */
volatile uint8_t g_ekf_of1_on = 1U;
/* Keil watch-window presets, one write each, applied on the ground (DISARMED or EMERGENCY, not flying/landing):
 * kp_id = N (kp_go = 1 re-applies), vp_id = N (MRAC variant row). kp_active/vp_active show the applied id
 * (vp 0xEE = a field was refused). A kp request made in flight waits for the ground; a vp request (vp_id, or
 * vp_user + vp_user_go = 1) also applies in flight while ch8 is off. Reboot = kp 7 (2026-10-10 matrix default: of1 on
 * + z headroom; boot globals are row 0, so kp_active 0 makes the first ground tick apply row 7), vp 0.
 * Tables: s_kp before Keil_PresetPoll, s_vp before Keil_VariantPoll. */
volatile uint8_t kp_id = 7U, kp_go = 0U, kp_active = 0U;
/* Limit mode (s_lim table, Limit_ModePoll): lim_mode = 0 slow, 1 conservative, 2 max; lim_active shows the
 * applied row (0xFF until the first ground tick). Ground only, on change, so later GS writes to gs_max_* stick. */
volatile uint8_t lim_mode = 0U, lim_active = 0xFFU;
/* Lab stage (s_stage table, Lab_StagePoll): one Keil write sets path tilt/laps/yaw rate and lim_mode for the
 * 2026-10-10 matrix. lab_stage_active shows the applied row (0xFF until the first ground tick, 0xEE bad id). */
volatile uint8_t lab_stage = 1U, lab_stage_active = 0xFFU;
float g_vxy_des_max = 120.0f;       /* cm/s  */
float g_yawrate_des_max = 90.0f;    /* deg/s */
float g_traj_v_max_cms = 80.0f;     /* cm/s, AutoflyTask refuses faster trajectories */
volatile uint8_t vp_id = 0U, vp_active = 0U;
volatile uint8_t exp_id = 0xFFU, exp_active = 0xFFU;

float Sin_roll_01= 0;
float Cos_roll_01= 0;
float Sin_pitch_01= 0;
float Cos_pitch_01= 0;
//
TargetSet_WorldReal_Coordinate TWC;

/* Accumulated near-ground sink bias (m/s). Ramps up while drone is stuck against
 * ground effect; adapts to battery voltage without needing voltage measurement.
 * Reset on every landing exit so it starts fresh each attempt. */
static float s_land_sink_bias = 0.0f;

/* OF velocity-bias calibration (docs/tracking_baseline_and_drift.md, mitigation #1).
 * of2_dx_fix/of2_dy_fix carry a small constant offset (measured ~12-14 / ~2-3 raw
 * units on the bench) that integrates into unbounded locx/locyPID.FB drift.
 *
 * Three estimation modes (g_of_bias_mode, CMD 0x1E idx=0):
 *
 * Mode 0 (FIXED): bias captured at boot from the first locked OF sample and
 *   locked forever. No pull-back during motion (the root cause of the user's
 *   2026-09-14 complaint). Recaptures on every ARM edge.
 *   Tradeoff: bias is wrong if OF zero-point drifts during flight.
 *
 * Mode 1 (EMA): continuous EMA tracking with optional freeze (CMD 0x1E idx=1).
 *   tau=20s keeps typical bench-scale motion (seconds) from being absorbed while
 *   still correcting minutes-scale thermal drift.
 *   The EMA absorbs any *sustained* real translational flow into the bias
 *   estimate — this is the pull-back artifact the user observed: during
 *   physical translation the OF sees the real velocity, and over 3-5 tau the
 *   EMA converges to the translation, erasing the displacement from position.
 *   Use g_of_bias_ema_freeze=1 to lock the current estimate before maneuvers.
 *
 * Mode 2 (EKF): dedicated 8-state OF position+bias+accel bias KF (API/ekf_of.c).
 *   Jointly estimates [pos_x, vel_x, bof_x, pos_y, vel_y, bof_y, ba_x, ba_y].
 *   The measurement model (z = vel - bias) lets the KF separate real motion
 *   from bias in the Kalman sense — no pull-back by construction.
 *   Its position state feeds Ctrler.locx/yPID.FB directly, bypassing the
 *   raw OF integration. Runs in parallel with the 9-state IMU EKF (send_data.c).
 *   See API/ekf_of.h for the model and noise parameters.
 *
 * Optical-flow velocity-bias estimation mode selector.
 * 0 = FIXED: bias set once at boot (first locked OF sample), never changes.
 *           ARM edge also re-snaps to current sample.
 * 1 = EMA:   continuous EMA tracking (OF_BIAS_EMA_ALPHA), with optional
 *            freeze via g_of_bias_ema_freeze (CMD 0x1E idx=1).
 * 2 = EKF:   dedicated 8-state OF position+bias KF (API/ekf_of.c).
 *            Runs in parallel with the existing 9-state IMU EKF (send_data.c).
 *            Mode 2 FEEDS the control loop (Ctrler.locx/yPID.FB) from the
 *            KF's debiased position state — bypasses the raw OF integration.
 *
 * Switch modes via CMD 0x1E idx=0 (dashboard UI wires this to 3 buttons).
 * EMA freeze is CMD 0x1E idx=1 (0=run, 1=frozen). Freeze persists across ARM.
 * Mode and freeze state are live-pokeable via g_of_bias_mode / g_of_bias_ema_freeze. */
#ifndef OF_BIAS_MODE_DEFAULT
#define OF_BIAS_MODE_DEFAULT  2   /* EKF (WP-21 default; was 0 FIXED). Bias re-snapped at every ARM edge in all modes */
#endif
volatile uint8_t g_of_bias_mode = OF_BIAS_MODE_DEFAULT; /* 0=FIXED 1=EMA 2=EKF */
volatile uint8_t g_of_bias_ema_freeze = 0U;             /* 1=freeze EMA update */

/* EMA time constant — operator-configurable via CMD 0x1E idx=2.
 * Valid range: [1.0, 300.0] s. Default 20 s. The per-tick alpha is
 * recomputed each cycle as dt/tau so tau changes take effect immediately.
 * Clamped in the command handler; plain global so DWARF-subscribable. */
#define OF_BIAS_EMA_TAU_DEFAULT  20.0f
#define OF_BIAS_EMA_TAU_MIN       1.0f
#define OF_BIAS_EMA_TAU_MAX     300.0f
float g_of_bias_ema_tau_s = OF_BIAS_EMA_TAU_DEFAULT; /* EMA time constant (s) */

/* EKF (Mode 2) health and fallback flags.
 * g_ekf_of_health: 1=healthy, 0=diverged (innovation magnitude > threshold).
 * g_ekf_of_fallback: set to 1 once after any health-triggered mode fallback;
 *   sticky until the next ARM so the operator can see it on the dashboard.
 * Both are plain globals so they are DWARF-subscribable. */
/* EKF_OF_INNOV_THRESH and EKF_OF_HEALTH_PERSIST are now in ekf_of.h (WP-14). */
#define EKF_OF_ACC_SIGN_X (+1.0f)
#define EKF_OF_ACC_SIGN_Y (+1.0f)
#define EKF_OF_MG_TO_MPS2 (9.80665e-3f)
/* WP-14 tilt input = SIGN * 1000*Gravity_Body_X/Y.  Both signs +1: ekf_of_replay fits k = d(OF vel)/(g*tilt*dt)
 * per axis on the 5 pinned logs and gets k_x 0.20..0.38, k_y 0.18..0.36 (all > 0) with these signs;
 * SIGN_Y = -1 gave k_y -0.18..-0.36 on every log. */
#define EKF_OF_UPDATE_ON_NEW_FRAME 1U /* 1 = feed each OF frame once (detected by ano_of.of_update_cnt changing), 0 = legacy every tick */
/* WP-21 A: full-tilt OF correction, default ON since 2026-10-03 (flown in flight_test_roaming_and_landing_2..4).  OF deltas are body-frame (sensor plane);
 * when tilted, climb/sink leaks into them (first order: v_level ~= v_body + SIGN*GB*v_up).
 * 1 = rotate each body delta to the level frame before the yaw rotation:
 *   n = (-SIGN_X*GBX, -SIGN_Y*GBY, n3) = up axis in OF axes, n3 = sqrt(1 - n1^2 - n2^2)
 *   dz = (dh_up - n1*dx - n2*dy) / n3                      (body z from the height change)
 *   level = minimal tilt rotation of (dx, dy, dz)          (yaw kept, so the yaw step is unchanged)
 * dh_up = ano_of.of2_h_f2_v (m/s, + up, tilt-corrected height rate).  Skipped above 60 deg
 * tilt (n3 < 0.5).  SIGN UNCONFIRMED: auto_landing_1 regression was inconclusive.  Before
 * flying with it, handheld test (CMD 0x1E idx=3): tilt ~10 deg on one axis, move straight
 * up/down ~50 cm; locx/locyPID.FB must stay flatter with the flag on than off. */
volatile uint8_t g_of_full_tilt = 1U;
/* OF distance scale (2026-10-03, x_y_calibration_hitting_wall_1): wall-to-wall passes in a
 * 400 cm room with a ~55 cm drone (true travel 345 cm) read 314.3 cm on body y and 316.4 cm on
 * body x at ~0.9 m, so OF under-reads by the same ~9% on both axes (345 / 315.35).  Applied
 * only where OF leaves for the loops (position deltas, velocity FB), so the KF tuning, bias
 * logic and rest thresholds stay in raw OF units.  Measured at one height.  1.0 = off. */
volatile float g_of_scale = 1.094f;
volatile uint8_t g_ekf_of_health   = 1U; /* 1=healthy 0=diverged  */
volatile uint8_t g_ekf_of_fallback = 0U; /* 1=fell back to FIXED  */
/* WP-14: shadow mode — KF runs every tick regardless of g_of_bias_mode.
 * Default 1 (shadow on). A bad innovation in shadow only clears health,
 * never changes g_of_bias_mode. Position feed stays on mode 2 only. */
volatile uint8_t g_ekf_of_shadow   = 1U;
/* External position fix (camera), shadow mode (API/ekf_of.h, CMD 0x2C): innovation telemetry only. */
volatile uint8_t        g_ext_pos_on       = 0U;
volatile float          g_ext_pos_delay_ms = 0.0f;
volatile ext_pos_post_t g_ext_pos_post;
ext_pos_stat_t          g_ext_pos;
static ext_pos_ring_t   s_ext_pos_ring;
static uint32_t         s_ext_pos_seen;
/* WP-14: active velocity feedback. When 1 AND mode 2 AND healthy,
 * locxsPID/locysPID.FB take KF velocity instead of raw OF. Default 1 (WP-21; was 0). */
volatile uint8_t g_ekf_of_vel_fb   = 1U;
/* WP-14: innovation persistence counter for the health gate.
 * Trips only after EKF_OF_HEALTH_PERSIST consecutive bad ticks. */
static uint16_t s_ekf_of_innov_bad_cnt = 0U;
/* WP-14: 1 once the health gate has tripped; keeps g_ekf_of_health at 0 across the
 * clean re-init that follows, until the next ARM edge or until the KF stops ticking.
 * WHY: without the latch the re-init zeroes the innovation and health flips back
 * to 1 within 5 ms, so a shadow flight would never show the trip. */
static uint8_t s_ekf_of_tripped = 0U;
/* FIX 2026-10-03: a trip in flight drops mode 2 to FIXED for the rest of that
 * flight; this latch puts mode 2 back on the next ARM 0->1 edge. Before, the
 * ARM edge cleared health/fallback but left g_of_bias_mode at 0, so a trip
 * during a handheld carry (test_flight_ekf_1, 167.6 s) silently flew the next
 * flight on raw OF with health shown as 1. */
static uint8_t s_ekf_of_mode_restore = 0U;
/* Handheld test (CMD 0x1E idx=3): integrate OF position on the ground so the
 * drone can be carried by hand (modes 0/2). Freezes the ground bias EMA, zeroes
 * the origin on enable, and self-clears once the drone is flying. */
volatile uint8_t g_of_handheld_test = 0U;
/* Pre-arm OF warning (FIX 2026-10-02): 1 while the on-ground averaged OF
 * velocity at rest exceeds OF_REST_WARN_CMS on either axis (2026-10-01 ~0,
 * 2026-10-02 +3.3/+5.1). The ARM snap removes it, but a large rest reading
 * means the module or floor is off: check it before flying. */
#define OF_REST_WARN_CMS            3.0f
volatile uint8_t g_of_rest_warn = 0U;

/* OF velocity-bias EMA state. s_of_bias_seeded gates the seed-on-first-sample
 * logic (see comment above). g_of_bias_ema_freeze gates the continuous update. */
/* OF offset trim (cm/s of raw of2_d*_fix): the flow the module reads while the drone
 * does not move. Every flight starts from this total bias (Reset_World_Origin at ARM);
 * the EKF bof stays frozen at 0 on top of it. Measured 2026-10-06 roam (operator
 * flew a 91 s loop back to the origin, logs/livewatch/roam_bias_20261006b.csv):
 * estimate ended -98 / -27 cm (locx / locy) from where the operator saw it land ->
 * offset = error / (91.36 s * g_of_scale 1.094). One flight; re-roam to confirm. */
#define OF_BIAS_TRIM_X_CMS   0.27f    /* of2_dx_fix -> earth_x -> locy */
#define OF_BIAS_TRIM_Y_CMS  -0.98f    /* of2_dy_fix -> earth_y -> locx */
float s_of_bias_x = OF_BIAS_TRIM_X_CMS, s_of_bias_y = OF_BIAS_TRIM_Y_CMS;
static u8 s_of_bias_seeded = 0;

/* Dedicated 8-state OF position+bias KF — used in Mode 2 (EKF).
 * Initialised once at boot, ticked every control cycle in Update_Data.
 * Its position state (x[0], x[3]) feeds Ctrler.locx/yPID.FB in EKF mode. */
EkfOf_t s_ekf_of;
static uint8_t s_ekf_of_inited = 0U;
/* Mode 2: last KF body-frame position (m) and a resync flag. earth_x/y are
 * accumulated from yaw-rotated KF position increments, not by rotating the
 * whole body-frame position with the current yaw (FIX 2026-09-26). */
static float s_ekf_prev_px = 0.0f;
static float s_ekf_prev_py = 0.0f;
static uint8_t s_ekf_pos_synced = 0U;

/* Manual bias snap (CMD 0x17). g_of_bias_capture_req is set from the ground
 * station (Send_Task) when the pilot has placed the drone level and still; it
 * jumps s_of_bias_x/y straight to the current of2_dx_fix/dy_fix sample instead
 * of waiting out the EMA time constant. The background EMA (below) then keeps
 * tracking from there — s_of_bias_x/y is only ever written from this task. */
volatile uint8_t g_of_bias_capture_req = 0;
/* OF lock gate (rec 3). of_quality is 0..255 (higher = better); a real lock sits at
 * ~250-255 on the bench. Below this we treat the sensor as unlocked: freeze both the
 * bias calibration and the earth_x/y integration so we never integrate garbage flow. */
#define OF_MIN_QUALITY              50U
/* Handheld test: OF range floor. At 1-4 cm (resting on the ground) the module
 * still reports quality 255 but its velocity is not usable. */
#define OF_HANDHELD_MIN_ALT_CM      10U


/* ADR-0011 Phase 3 (CAL_AIRBORNE_HOVER_TRIM) + Phase 4 (CAL_HOT_HOVER).
 * Initialised once at boot by CalTrim_Init / CalHot_Init, ticked each control
 * cycle from Update_Data. CalHot_TickState_t reused below for the FSM transitions. */
/* ADR-0011 Phase 3 + 4 calibrator instances — non-static so send_data.c can read them
 * for the always-on telemetry surface (CMD 0x05 v14 frame, fields 7-12). */
CalTrim_t s_cal_trim;
CalHot_t  s_cal_hot;
uint16_t g_cal_health = 0U;   /* bitmask: 0x01 BOOT_OK | 0x02 COLD_OK | 0x04 COLD_DEGRADED
                               *         0x08 AIRBORNE_OK | 0x10 AIRBORNE_DEGRADED
                               *         0x20 HOT_HOVER_OK | 0x40 HOT_REJECTED
                               *         0x80 MANUAL_ORIGIN_RESET | 0x100 BOOT_TIMEOUT
                               *         0x200 ESTIMATOR_READY */
#define CAL_HEALTH_BOOT_OK            0x01U
#define CAL_HEALTH_COLD_OK            0x02U
#define CAL_HEALTH_COLD_DEGRADED      0x04U
#define CAL_HEALTH_AIRBORNE_OK        0x08U
#define CAL_HEALTH_AIRBORNE_DEGRADED  0x10U
#define CAL_HEALTH_HOT_HOVER_OK       0x20U
#define CAL_HEALTH_HOT_REJECTED       0x40U
#define CAL_HEALTH_BOOT_TIMEOUT       0x100U
#define CAL_HEALTH_ESTIMATOR_READY    0x200U
#define IMU_SETTLE_E2                 0.0009f  /* g_imu_settle_metric at or above this = COLD_DEGRADED */
#define CAL_TRIM_WINDOW_TICKS         2000U    /* CalTrim_Init window: 10 s at 200 Hz */

/* ==== Optical-flow helpers ====================================================================== */
#define OF_TILT_N3SQ_MIN     0.25f    /* n3^2 below this = more than 60 deg tilt: leave the delta raw */

/* Rotate one tick's body OF delta (*dx, *dy) to the level frame in place; dh_up is the same
 * tick's height change in the same units (cm/tick here).  See g_of_full_tilt above. */
static void of_full_tilt_delta(float *dx, float *dy, float dh_up)
{
	float n1 = -EKF_OF_ACC_SIGN_X * Gravity_Body_X;
	float n2 = -EKF_OF_ACC_SIGN_Y * Gravity_Body_Y;
	float n3sq = 1.0f - n1 * n1 - n2 * n2;
	float n3, k, bx, by, bz;
	if (n3sq < OF_TILT_N3SQ_MIN) return;      /* > 60 deg tilt: leave the delta raw */
	n3 = sqrtf(n3sq);
	k  = 1.0f / (1.0f + n3);
	bx = *dx;
	by = *dy;
	bz = (dh_up - n1 * bx - n2 * by) / n3;
	*dx = (1.0f - n1 * n1 * k) * bx - n1 * n2 * k * by - n1 * bz;
	*dy = -n1 * n2 * k * bx + (1.0f - n2 * n2 * k) * by - n2 * bz;
}

/* FIX 2026-09-26: Mode 2 KF measures (OF - s_of_bias) = vel - bias. Any change
 * of s_of_bias by d counts shifts that measurement by -d*0.01 m/s, so shift the
 * KF bias state by the same amount; otherwise the step is read as velocity and
 * integrates into position. Every s_of_bias writer calls this with its delta. */
static void Of_RebaseKfBias(float dbx, float dby)
{
	/* WP-14: run in shadow too (was guarded on mode==2). Sign fix: measurement
	 * model z = v + bof, so a +d shift of the raw measurement = -d shift in
	 * the KF bias state (was +=, should be -=). */
	if (s_ekf_of_inited) {
		s_ekf_of.x[2] -= dbx * M_PER_CM;
		s_ekf_of.x[5] -= dby * M_PER_CM;
	}
}

/* Zero the world-frame optical-flow origin: the drone's current location becomes
 * the new (0,0) and position setpoints are synced so the next control tick sees
 * no jump. Shared by CMD 0x10 (manual reset) and the estimator-warmup auto-pin
 * (A4). Mirrors the former inline CMD 0x10 body in send_data.c.
 *
 * FIX 2026-09-13: also zero s_of_bias_x/y. Without this, CMD 0x10 zeroed
 * the accumulated position but the OF velocity bias kept integrating, so
 * position re-drifted within ~30 s. The operator's "Reset World Origin"
 * button had been broken in exactly this way for the entire v3 era; the
 * fix makes the reset sticky. The bias estimator (OF_BIAS_*) will start
 * accumulating again from the new (zero) state on the next tick. */
void Reset_World_Origin(void)
{
	Of_RebaseKfBias(OF_BIAS_TRIM_X_CMS - s_of_bias_x, OF_BIAS_TRIM_Y_CMS - s_of_bias_y);
	ano_of.earth_x       = 0.0f;
	ano_of.earth_y       = 0.0f;
	ext_pos_ring_reset(&s_ext_pos_ring);   /* the buffered path is in the old origin */
	g_ext_pos.c_x = 0.0f;                   /* mode 2 then re-anchors (slewed) to the camera frame */
	g_ext_pos.c_y = 0.0f;
	g_ext_pos.a_x = 0.0f;
	g_ext_pos.a_y = 0.0f;
	ano_of.earth_x_ture  = 0.0f;
	ano_of.earth_y_ture  = 0.0f;
	ano_of.DISTANCE_X    = 0.0f;
	ano_of.DISTANCE_Y    = 0.0f;
	Ctrler.locxPID.FB    = 0.0f;
	Ctrler.locyPID.FB    = 0.0f;
	Ctrler.locxPID.Des   = 0.0f;
	Ctrler.locyPID.Des   = 0.0f;
	Ctrler.locxsPID.Des  = 0.0f;
	Ctrler.locysPID.Des  = 0.0f;
	s_of_bias_x          = OF_BIAS_TRIM_X_CMS;   /* trim, not 0 (2026-10-06 roam) */
	s_of_bias_y          = OF_BIAS_TRIM_Y_CMS;
	/* WP-14: reset KF position in all modes (shadow included).
	 * vel and bias states stay — only accumulated position resets to zero. */
	if (s_ekf_of_inited) {
		EkfOf_ResetPos(&s_ekf_of);
	}
	s_ekf_pos_synced = 0U;
}

/* WFB BEGIN glue */
/* Workflow B (docs/workflow-b/interfaces.md): once per 200 Hz tick, snapshot the vehicle into
 * API/wfb_glue.c and apply what it asks for through the existing firmware paths. This function
 * only converts units and routes requests; every decision lives in the glue (host-tested). */
static void Wfb_Step(void)
{
	static uint8_t s_was_armed = 0U;   /* disarm edge; the armed_pub mirror above is not one (s_sync writes ARM_Status too) */
	wfb_glue_in_t  in;
	wfb_glue_out_t out;

	in.now_ms      = (uint32_t)xTaskGetTickCount() * (uint32_t)portTICK_PERIOD_MS;
	in.x_m         = Ctrler.locxPID.FB * 0.01f;   /* TWC / loc loops are cm, the glue is m */
	in.y_m         = Ctrler.locyPID.FB * 0.01f;
	in.z_m         = Ctrler.Z_posPID.FB;           /* already m */
	in.roll_deg    = imu_data.rol;
	in.pitch_deg   = imu_data.pit;
	in.vbat_v      = real_voltage;
	in.yaw_deg     = Ctrler.yawPID.FB;             /* the TWC.set_yaw frame */
	in.armed       = (FlightFSM_GetState() == FLIGHT_STATE_ARMED) ? 1U : 0U;
	in.motors_idle = g_motor_idle_enabled ? 1U : 0U;
	in.sbus_live   = sbus_lost ? 0U : 1U;
	in.airborne    = (flight_phase == FLIGHT_PHASE_FLYING || flight_phase == FLIGHT_PHASE_LANDING) ? 1U : 0U;
	in.rc_override = (RCInput_IsActive(RC_AXIS_ROLL) || RCInput_IsActive(RC_AXIS_PITCH)) ? 1U : 0U;
	in.preset_active = traj_active ? 1U : 0U;   /* AutoflyTask.c Keil preset */
	{   /* camera pad gate (VAR 46): the land's SETTLE waits for a fresh fused fix within r of the pad */
		LandXyGateIn gin;
		gin.gate_cm    = g_land_xy_gate_cm;
		gin.err_cm     = sqrtf(Ctrler.locxPID.FB * Ctrler.locxPID.FB + Ctrler.locyPID.FB * Ctrler.locyPID.FB);
		gin.fused      = (uint8_t)(g_ext_pos_on >= 2U);
		gin.stale      = g_ext_pos.stale;
		gin.fix_age_ms = in.now_ms - g_ext_pos.last_ok_ms;
		in.pad_gate    = LandXyGate_Check(&gin);
	}

	/* send_data.c and RemoterTask.c call into the glue from other tasks */
	taskENTER_CRITICAL();
	if (s_was_armed && !in.armed) {
		wfb_glue_disarmed();
	}
	wfb_glue_tick(&in, &out);
	taskEXIT_CRITICAL();
	s_was_armed = in.armed;

	/* PROTECTED BEGIN wfb_apply */
	if (out.motor_stop_req) {
		FlightFSM_Event(FLIGHT_EVENT_DANGEROUS_STOP);
	} else if (out.land_req) {
		if (flight_phase == FLIGHT_PHASE_FLYING) {   /* same two lines as the RC ch5 land */
			flight_phase = FLIGHT_PHASE_LANDING;
			TWC.execute  = 0U;
		}
	} else if (out.takeoff_req) {
		sbus_flyup_trigger = 1U;                      /* the ch7 fly-up path, consumed in Update_Data() */
	} else if (out.setpoint_valid) {
		TWC.target_x = out.x_sp_m * 100.0f;           /* m -> cm */
		TWC.target_y = out.y_sp_m * 100.0f;
		TWC.target_z = out.z_sp_m;
		TWC.set_yaw  = out.yaw_sp_deg;
		TWC.execute  = 1U;
	}
	/* PROTECTED END wfb_apply */
}
/* WFB END glue */

/* Always-on auto-land (API/land_gate.c): pack sag while FLYING lands like RC ch5. A motor pinned at the cap is only
 * counted (g_land_gate_sat_n, g_land_gate_sat_max_s), never lands. Runs after Mix_Compute so it sees this tick's
 * motor commands. Off: g_land_gate_enable = 0 in the watch window. */
static void LandGate_Tick(void)
{
	static land_gate_state_t  s_gate;
	static land_gate_limits_t s_lim;
	static uint8_t            s_init = 0U;
	land_gate_in_t in;
	short m;

	if (!s_init) {
		LandGate_Reset(&s_gate);
		LandGate_DefaultLimits(&s_lim);
		s_init = 1U;
	}
	m = mymotor.motor1;
	if (mymotor.motor2 > m) { m = mymotor.motor2; }
	if (mymotor.motor3 > m) { m = mymotor.motor3; }
	if (mymotor.motor4 > m) { m = mymotor.motor4; }
	in.vbat_v    = real_voltage;
	in.motor_max = (float)m;
	in.dt_s      = STAB_DT_S;
	in.flying    = (flight_phase == FLIGHT_PHASE_FLYING) ? 1U : 0U;
	if (LandGate_Step(&s_gate, &s_lim, g_land_gate_enable, &in) != LAND_GATE_NONE &&
	    flight_phase == FLIGHT_PHASE_FLYING) {   /* same two lines as the RC ch5 land */
		flight_phase = FLIGHT_PHASE_LANDING;
		TWC.execute  = 0U;
	}
}

/* ==== Telemetry groups (API/flight_telemetry.h) =================================================== */
#ifdef __CC_ARM
    #define TLM_CCM __attribute__((section("MRAC_CCM"), zero_init))   /* CPU-only CCM, zeroed by __main */
#else
    #define TLM_CCM
#endif
FlightTelemetry_t g_tlm TLM_CCM;

/* Copy the key flight state of this tick into g_tlm (the groups of API/flight_telemetry.h). Runs last in
 * stabilizer_Task; reads the sources only, so the control path is unchanged. */
static void Tlm_Snapshot(void)
{
	float zf[MRAC_ZFEAT_N], zw[MRAC_ZFEAT_N];

	g_tlm.att.roll_deg   =  Ctrler.rollPID.FB;
	g_tlm.att.pitch_deg  = -Ctrler.pitchPID.FB;
	g_tlm.att.yaw_deg    = -Ctrler.yawPID.FB;
	g_tlm.att.rate_x_dps =  Ctrler.gyroxPID.FB;
	g_tlm.att.rate_y_dps =  Ctrler.gyroyPID.FB;
	g_tlm.att.rate_z_dps =  Ctrler.gyrozPID.FB;

	g_tlm.sp.roll_deg    = Ctrler.rollPID.Des;
	g_tlm.sp.pitch_deg   = Ctrler.pitchPID.Des;
	g_tlm.sp.yaw_deg     = Ctrler.yawPID.Des;
	g_tlm.sp.rate_x_dps  = Ctrler.gyroxPID.Des;
	g_tlm.sp.rate_y_dps  = Ctrler.gyroyPID.Des;
	g_tlm.sp.rate_z_dps  = Ctrler.gyrozPID.Des;
	g_tlm.sp.x_cm        = Ctrler.locxPID.Des;
	g_tlm.sp.y_cm        = Ctrler.locyPID.Des;
	g_tlm.sp.vx_cms      = Ctrler.locxsPID.Des;
	g_tlm.sp.vy_cms      = Ctrler.locysPID.Des;
	g_tlm.sp.z_m         = Ctrler.Z_posPID.Des;
	g_tlm.sp.vz_mps      = Ctrler.Z_ratePID.Des;

	g_tlm.pos.x_cm       = Ctrler.locxPID.FB;
	g_tlm.pos.y_cm       = Ctrler.locyPID.FB;
	g_tlm.pos.vx_cms     = Ctrler.locxsPID.FB;
	g_tlm.pos.vy_cms     = Ctrler.locysPID.FB;
	g_tlm.pos.z_m        = Ctrler.Z_posPID.FB;
	g_tlm.pos.vz_mps     = Ctrler.Z_ratePID.FB;

	g_tlm.mot.throttle   = Throttle_out;
	g_tlm.mot.u_roll     = u_gyrox;
	g_tlm.mot.u_pitch    = u_gyroy;
	g_tlm.mot.u_yaw      = u_gyroz;
	g_tlm.mot.m1         = (float)mymotor.motor1;
	g_tlm.mot.m2         = (float)mymotor.motor2;
	g_tlm.mot.m3         = (float)mymotor.motor3;
	g_tlm.mot.m4         = (float)mymotor.motor4;

	g_tlm.mrac.e[0]      = mrac_state.pitch.e;
	g_tlm.mrac.e[1]      = mrac_state.roll.e;
	g_tlm.mrac.e[2]      = mrac_state.yaw.e;
	g_tlm.mrac.e[3]      = mrac_state.z_rate.e;
	g_tlm.mrac.e[4]      = mrac_state.x_vel.e;
	g_tlm.mrac.e[5]      = mrac_state.y_vel.e;
	g_tlm.mrac.u_nom[0]  = mrac_state.pitch.u_nom;
	g_tlm.mrac.u_nom[1]  = mrac_state.roll.u_nom;
	g_tlm.mrac.u_nom[2]  = mrac_state.yaw.u_nom;
	g_tlm.mrac.u_nom[3]  = mrac_state.z_rate.u_nom;
	g_tlm.mrac.u_nom[4]  = mrac_state.x_vel.u_nom;
	g_tlm.mrac.u_nom[5]  = mrac_state.y_vel.u_nom;
	g_tlm.mrac.u_ad[0]   = mrac_state.pitch.u_ad;
	g_tlm.mrac.u_ad[1]   = mrac_state.roll.u_ad;
	g_tlm.mrac.u_ad[2]   = mrac_state.yaw.u_ad;
	g_tlm.mrac.u_ad[3]   = mrac_state.z_rate.u_ad;
	g_tlm.mrac.u_ad[4]   = mrac_state.x_vel.u_ad;
	g_tlm.mrac.u_ad[5]   = mrac_state.y_vel.u_ad;
	g_tlm.mrac.fade      = mrac_simplex.fade;

	g_tlm.est.kf_x_m       = s_ekf_of.x[0];
	g_tlm.est.kf_y_m       = s_ekf_of.x[3];
	g_tlm.est.kf_vx_mps    = s_ekf_of.x[1];
	g_tlm.est.kf_vy_mps    = s_ekf_of.x[4];
	g_tlm.est.kf_bof_x_mps = s_ekf_of.x[2];
	g_tlm.est.kf_bof_y_mps = s_ekf_of.x[5];
	g_tlm.est.of_bias_x    = s_of_bias_x;
	g_tlm.est.of_bias_y    = s_of_bias_y;

	g_tlm.pwr.vbat_v         = real_voltage;
	g_tlm.pwr.thrust_imu_n   = g_thrust_est.imu_total;
	g_tlm.pwr.thrust_be_n[0] = g_thrust_est.blade_element[0];
	g_tlm.pwr.thrust_be_n[1] = g_thrust_est.blade_element[1];
	g_tlm.pwr.thrust_be_n[2] = g_thrust_est.blade_element[2];
	g_tlm.pwr.thrust_be_n[3] = g_thrust_est.blade_element[3];
	g_tlm.pwr.mass_hat_kg    = g_thrust_est.mass_hat;

	g_tlm.hlth.prearm_fail_mask  = (float)g_prearm_fail_mask;
	g_tlm.hlth.prearm_block_mask = (float)g_prearm_block_mask;
	g_tlm.hlth.reset_cause       = (float)g_rtos_budget.reset_cause;
	g_tlm.hlth.stack_min_words   = (float)g_rtos_budget.stack_min_words;
	g_tlm.hlth.stack_min_task    = (float)g_rtos_budget.stack_min_task;
	g_tlm.hlth.stab_cpu_pct      = g_rtos_budget.stab_cpu_pct;
	g_tlm.hlth.loop_max_us       = g_rtos_budget.loop_max_us;
	g_tlm.hlth.iwdg_on           = (float)g_rtos_budget.iwdg_on;

	MRAC_ZFeatOut(zf, zw);   /* slot k = ext k: bias, ground, drag, tilt (MRAC_ZFEAT_* are mask bits) */
	g_tlm.mrac_z.feat[0] = zf[0];
	g_tlm.mrac_z.feat[1] = zf[1];
	g_tlm.mrac_z.feat[2] = zf[2];
	g_tlm.mrac_z.feat[3] = zf[3];
	g_tlm.mrac_z.w[0]    = zw[0];
	g_tlm.mrac_z.w[1]    = zw[1];
	g_tlm.mrac_z.w[2]    = zw[2];
	g_tlm.mrac_z.w[3]    = zw[3];
	g_tlm.mrac_z.mask    = (float)mrac_z_feat_mask;

	g_tlm.sysid_cdu.c[0]   = s_sysid_c[SYSID_AXIS_PITCH];
	g_tlm.sysid_cdu.c[1]   = s_sysid_c[SYSID_AXIS_ROLL];
	g_tlm.sysid_cdu.c[2]   = s_sysid_c[SYSID_AXIS_YAW];
	g_tlm.sysid_cdu.d[0]   = s_sysid_d[SYSID_AXIS_PITCH];
	g_tlm.sysid_cdu.d[1]   = s_sysid_d[SYSID_AXIS_ROLL];
	g_tlm.sysid_cdu.d[2]   = s_sysid_d[SYSID_AXIS_YAW];
	g_tlm.sysid_cdu.u[0]   = s_sysid_u[SYSID_AXIS_PITCH];
	g_tlm.sysid_cdu.u[1]   = s_sysid_u[SYSID_AXIS_ROLL];
	g_tlm.sysid_cdu.u[2]   = s_sysid_u[SYSID_AXIS_YAW];
	g_tlm.sysid_cdu.target = (float)sysid_target;
}

/* ==== The control tick ========================================================================== */

/* Bug 2 (audit 2026-09-21 §1 row 2): published arm state must track the
 * motor gate unconditionally. s_sync() in flight_fsm.c writes ARM_Status
 * on every FSM transition, but the real flag that gates motor output in
 * Update_Motor() is FlightFSM_GetState()==FLIGHT_STATE_ARMED -- mirror it
 * into DroneStatus.ARM_Status every control cycle so the telemetry byte
 * (Frame A + slot-0 address subscription) can never diverge from it, on
 * both the RC and SDK arm paths. Observability only. */
static void Arm_PublishState(void)
{
	uint8_t armed_pub;
	armed_pub = (FlightFSM_GetState() == FLIGHT_STATE_ARMED) ? (uint8_t)Armed : (uint8_t)DisArmed;
	if (DroneStatus.ARM_Status != armed_pub) {
		DroneStatus.ARM_Status = armed_pub;
	}
}

/* ADR-0011: set BOOT_OK + COLD_OK on the rising edge of g_estimator_ready.
 * Cold-cal degraded/timeout detection is left to the dashboard via the
 * g_imu_settle_metric telemetry value (high value = didn't settle cleanly).
 * Lazy one-shot init of the calibrators on cold-cal completion. */
static void Cal_EstimatorReadyEdge(void)
{
	static uint8_t s_prev_ready = 0U;
	if (g_estimator_ready && !s_prev_ready) {
		g_cal_health |= CAL_HEALTH_BOOT_OK;             /* BOOT_OK rising edge */
		g_cal_health |= CAL_HEALTH_COLD_OK;             /* COLD_OK (Phase 2 done) */
		if (g_imu_settle_metric >= IMU_SETTLE_E2) {
			g_cal_health |= CAL_HEALTH_COLD_DEGRADED;
			g_cal_health |= CAL_HEALTH_BOOT_TIMEOUT;
		}
		CalTrim_Init(&s_cal_trim, CAL_TRIM_WINDOW_TICKS);
		CalHot_Init(&s_cal_hot);
		/* FIX 2026-09-13: cold-boot Des sync. While warmup-pinned, Reset_World_Origin()
		 * keeps locx/yPID.Des=0 every tick. Once g_estimator_ready rises the position
		 * loop takes over, but case_Update_loc_Des only captures FB into Des after
		 * a stick-active→released transition. With sticks centered on the bench (or
		 * with the pilot never having touched the sticks), Des stays at 0 while FB
		 * holds whatever the OF has drifted to — opening the loop with a huge
		 * error that the integral saturates immediately. Sync once at the rising
		 * edge so the first uncontrolled tick sees zero error. Subsequent updates
		 * follow the existing case_Update_loc_Des rules (stick release or TWC).
		 * Same pattern for yaw — imu_data.yaw is the heading reference, sync
		 * yaw Des to it so the rate loop has zero command at handoff. */
		Ctrler.locxPID.Des  = Ctrler.locxPID.FB;
		Ctrler.locyPID.Des  = Ctrler.locyPID.FB;
		Ctrler.locxsPID.Des = 0.0f;
		Ctrler.locysPID.Des = 0.0f;
		/* FIX 2026-09-13: yaw cold-boot sync. Same pattern as roll/pitch above —
		 * write Des directly here so the first tick sees zero error. The
		 * state-machine flag dance (is_last_yaw_valid) was attempted but the
		 * flag is a function-static inside Des_Yaw and cannot
		 * be touched here. The direct Des assignment is sufficient. */
		Ctrler.yawPID.Des   = imu_data.yaw;
	}
	s_prev_ready = g_estimator_ready;
	if (g_estimator_ready) g_cal_health |= CAL_HEALTH_ESTIMATOR_READY;
}

void stabilizer_Task(void)
{
	/* A4: while the attitude estimator is still converging, hold the OF world
	 * origin pinned at zero every tick. A tilted mid-warmup estimate would
	 * otherwise let optical flow integrate phantom drift; pinning removes the
	 * need to reset the origin by hand. On the tick the estimator goes ready
	 * this stops, so position accumulates from a clean, trustworthy origin. */
	if (!g_estimator_ready) {
		Reset_World_Origin();
	}

	Check_Fly_Mode();

	Arm_PublishState();

	Cal_EstimatorReadyEdge();

	Update_Data();

	Wfb_Step();   /* WFB glue: after Update_Data() so the FB values are this tick's */
	GsIdle_ReleaseOnDisarm();

	Compute_Motor();

	Update_Motor();

	Tlm_Snapshot();   /* telemetry groups: read-only copy of this tick's state, last */
}

/* ==== Update_Data: sensors -> loop feedback ======================================================= */
#define MG_PER_G               1000.0f
#define OF_HRATE_TO_CM_TICK    0.5f     /* of2_h_f2_v (m/s) * 100 cm/m * 0.005 s/tick */
#define ALT_BAND_MIN_CM        5U       /* ToF band: below = dropout */
#define ALT_BAND_MAX_CM        500U     /* ToF band: above = no reading (65535) or out of range */
#define ALT_JUMP_BASE_M        0.05f    /* per-tick jump gate at zero commanded rate (10 m/s) */
#define ALT_JUMP_PER_VZ        0.15f    /* gate widening per m/s of |Z_ratePID.Des| */
#define ALT_JUMP_MAX_M         0.20f
#define ALT_RESYNC_REJECTS     20U      /* consecutive rejects (100 ms) that force a resync */
#define ALT_RATE_LPF_OLD       0.9f     /* of2_h_f2_v = 0.9 * old + 0.1 * new */
#define ALT_RATE_LPF_NEW       0.1f

/* Yaw/roll/pitch sin and cos used by the frame rotations below (the _01 globals). */
static void Att_UpdateTrig(void)
{
	Cos_Yaw_01=cos(-imu_data.yaw* DEG2RAD);
	Sin_Yaw_01=sin(-imu_data.yaw* DEG2RAD);

	Cos_roll_01=cos(imu_data.rol* DEG2RAD);
	Sin_roll_01 = sin(imu_data.rol* DEG2RAD);
	Cos_pitch_01=cos(imu_data.pit* DEG2RAD);
	Sin_pitch_01 = sin(imu_data.pit* DEG2RAD);
}

/* CMD 0x17 manual snap, then the mode 0 (FIXED) / mode 1 (EMA) bias update. */
static void Of_UpdateBias(u8 of_ok)
{
	/* CMD 0x17 manual snap: jump s_of_bias_x/y to current OF sample.
	 * Available in all modes. For Mode 2 (EKF), also re-seeds the KF's
	 * velocity/bias states from the debiased OF reading. */
	if (g_of_bias_capture_req) {
		g_of_bias_capture_req = 0;
		if (of_ok) {
			Of_RebaseKfBias((float)ano_of.of2_dx_fix - s_of_bias_x,
			                (float)ano_of.of2_dy_fix - s_of_bias_y);
			s_of_bias_x = (float)ano_of.of2_dx_fix;
			s_of_bias_y = (float)ano_of.of2_dy_fix;
		}
	}

	/* ---- Mode 0 (FIXED): seed once at boot, never adapt ---- */
	if (g_of_bias_mode == OF_BIAS_FIXED) {
		if (of_ok && !s_of_bias_seeded) {
			s_of_bias_seeded = 1;
			s_of_bias_x = (float)ano_of.of2_dx_fix;
			s_of_bias_y = (float)ano_of.of2_dy_fix;
		}
		/* bias stays fixed after seed */
	}
	/* ---- Mode 1 (EMA): continuous tracking with optional freeze ---- */
	else if (g_of_bias_mode == OF_BIAS_EMA) {
		float ema_alpha;
		if (of_ok && !s_of_bias_seeded) {
			s_of_bias_seeded = 1;
			s_of_bias_x = (float)ano_of.of2_dx_fix;
			s_of_bias_y = (float)ano_of.of2_dy_fix;
		}
		/* Per-tick alpha computed from configurable tau: alpha = dt/tau.
		 * tau is clamped [1,300] s in the command handler, safe to divide. */
		ema_alpha = STAB_DT_S / g_of_bias_ema_tau_s;
		/* EMA runs only when not frozen */
		if (of_ok && !g_of_bias_ema_freeze) {
			s_of_bias_x += ema_alpha
				* ((float)ano_of.of2_dx_fix - s_of_bias_x);
			s_of_bias_y += ema_alpha
				* ((float)ano_of.of2_dy_fix - s_of_bias_y);
		}
	}
}

/* ---- Mode 2 (EKF) or shadow: tick the 8-state KF, check health ----
 * WP-14: KF ticks whenever mode==2 or shadow. Predict, ZUPT, OF update
 * and health gate run every 5 ms tick. Shadow mode only records health;
 * mode 2 feeds the position path and falls back on persistent divergence. */
static void Of_TickKf(u8 of_ok)
{
	float ofx;
	float ofy;
	float innov_mag;
	float tilt_ax;
	float tilt_ay;
	uint8_t on_ground;
	static u8 s_last_of_update_cnt = 0;
	if (!s_ekf_of_inited) {
		EkfOf_Init(&s_ekf_of);
		s_ekf_of_inited = 1U;
		g_ekf_of_health = s_ekf_of_tripped ? 0U : 1U; /* a gate trip stays visible across its re-init */
		s_ekf_of_innov_bad_cnt = 0U;
	}
	on_ground = !g_of_handheld_test && !(DroneStatus.ARM_Status == Armed && (flight_phase == FLIGHT_PHASE_FLYING || flight_phase == FLIGHT_PHASE_LANDING));
	ofx = ((float)ano_of.of2_dx_fix - s_of_bias_x) * M_PER_CM; /* m/s */
	ofy = ((float)ano_of.of2_dy_fix - s_of_bias_y) * M_PER_CM;
	/* WP-14: tilt-only input — gravity-tilt term replaces Lin_Acc.
	 * Body accel explains ~0% of OF velocity change; the gravity-tilt
	 * alone explains 34-63% (drift investigation §ekf F2).
	 * Gravity_Body_X/Y = vecxZ/vecyZ (body-frame gravity unit vector,
	 * computed each tick in imu_update.c).  Multiply by 1000 to get mg. */
	tilt_ax = EKF_OF_ACC_SIGN_X * MG_PER_G * Gravity_Body_X * EKF_OF_TILT_GAIN * EKF_OF_MG_TO_MPS2;
	tilt_ay = EKF_OF_ACC_SIGN_Y * MG_PER_G * Gravity_Body_Y * EKF_OF_TILT_GAIN * EKF_OF_MG_TO_MPS2;
	EkfOf_Predict(&s_ekf_of, STAB_DT_S, tilt_ax, tilt_ay);
	if (on_ground) EkfOf_UpdateZeroVel(&s_ekf_of);
	/* FIX 2026-10-03 (flight_test_landing_x_drift_1): resting on the ground
	 * the module repeats one stale velocity (exactly 3,5 and 2,3 cm/s, std 0,
	 * quality 255). With ZUPT pinning v=0 these updates taught it to bof, and
	 * the hover flew that fake bias as a steady +x drift. Use OF only when
	 * not on the ground and above the range floor.
	 * 2026-10-07: each OF frame also feeds of1 (raw flow, same axes as of2, no trim) as the
	 * bias-free 2nd channel that lets bof learn the of2 gyro-fix bias (EkfOf_UpdateRaw).
	 * 2026-10-10: upper bound too: no-range height reads 0xFFFFFFFF with quality still >= 50 (10-07 step B 0.1%).
	 * No extra gyro de-rotation: replay vs video truth, K 72 cm/s per m*rad/s worsens velocity (module compensates). */
	if (of_ok && !on_ground && ano_of.of_alt_cm >= OF_HANDHELD_MIN_ALT_CM && ano_of.of_alt_cm <= ALT_BAND_MAX_CM) {
		if (EKF_OF_UPDATE_ON_NEW_FRAME) {
			if (ano_of.of_update_cnt != s_last_of_update_cnt) {
				EkfOf_Update(&s_ekf_of, ofx, ofy);
				if (g_ekf_of1_on) EkfOf_UpdateRaw(&s_ekf_of, (float)ano_of.of1_dx * M_PER_CM, (float)ano_of.of1_dy * M_PER_CM);
				s_last_of_update_cnt = ano_of.of_update_cnt;
			}
		} else {
			EkfOf_Update(&s_ekf_of, ofx, ofy);
			if (g_ekf_of1_on) EkfOf_UpdateRaw(&s_ekf_of, (float)ano_of.of1_dx * M_PER_CM, (float)ano_of.of1_dy * M_PER_CM);
		}
	}
	/* WP-14: innovation-based health gate with persistence.
	 * innov_x/y are the post-update residuals set in EkfOf_Update.
	 * Trip only after EKF_OF_HEALTH_PERSIST consecutive ticks (0.5 s)
	 * with innovation magnitude > EKF_OF_HEALTH_THRESH.
	 * In shadow mode: only set health=0, never change g_of_bias_mode. */
	innov_mag = s_ekf_of.innov_x * s_ekf_of.innov_x
	          + s_ekf_of.innov_y * s_ekf_of.innov_y;
	if (innov_mag > (EKF_OF_HEALTH_THRESH * EKF_OF_HEALTH_THRESH)) {
		if (s_ekf_of_innov_bad_cnt < 0xFFFFU) {
			s_ekf_of_innov_bad_cnt++;
		}
		if (s_ekf_of_innov_bad_cnt >= EKF_OF_HEALTH_PERSIST) {
			g_ekf_of_health = 0U;
			s_ekf_of_tripped = 1U;
			/* Fall back only when the KF drives the position loop (armed,
			 * FLYING/LANDING). On the ground or in handheld the re-init
			 * below is enough and mode 2 stays selected. */
			if (g_of_bias_mode == OF_BIAS_EKF && !on_ground && !g_of_handheld_test) {
				g_of_bias_mode  = OF_BIAS_FIXED; /* forced fallback to FIXED */
				g_ekf_of_fallback = 1U;
				s_ekf_of_mode_restore = 1U;
			}
			/* Re-init from a clean state on the next tick instead of
			 * running on the diverged one (shadow and mode 2 alike). */
			s_ekf_of_inited = 0U;
			s_ekf_of_innov_bad_cnt = 0U;
		}
	} else {
		s_ekf_of_innov_bad_cnt = 0U;
		if (!s_ekf_of_tripped) {
			g_ekf_of_health = 1U;
		}
	}
}

/* External position fix: buffer the raw earth path (earth_x/y minus the applied correction a), score each posted
 * fix against it at its capture time (g_ext_pos). Mode 1 shadow: telemetry only. Mode 2: ext_pos_step() moves the
 * target c by at most EXT_POS_SLEW_CM per fix, ext_pos_spread() ramps earth_x/y (so locx/locyPID.FB) toward it at
 * most EXT_POS_SLEW_CM / EXT_POS_SPREAD_N per tick; the EKF is not touched. Mode 3 (PROPOSED, needs the OF EKF,
 * else the mode-2 path, ext_pos_path()): c follows a, so e = z - earth_past; Ext_Pos_Ekf() feeds the fix to the EKF as a position measurement.
 * Both closed modes re-derive the loc FB at once and shift PreE by the same jump (Ext_Pos_SetFb). */
static void Ext_Pos_SetFb(void)
{
	float fx = Ctrler.locxPID.FB, fy = Ctrler.locyPID.FB;
	ano_of.earth_x_ture =  ano_of.earth_y;
	ano_of.earth_y_ture = -ano_of.earth_x;
	Ctrler.locxPID.FB = ano_of.earth_x_ture;
	Ctrler.locyPID.FB = ano_of.earth_y_ture;
	Ctrler.locxPID.PreE -= Ctrler.locxPID.FB - fx;   /* E = Des - FB jumps by -dFB: no Kd kick */
	Ctrler.locyPID.PreE -= Ctrler.locyPID.FB - fy;
}

/* Mode 3: the past innovation e (cm, earth frame) rotated into the heading frame and scaled to EKF metres is applied
 * now, z = x_now + e (ring, past innovation applied now: SIMULATED M3); the position step goes into earth_x/y and
 * s_ekf_prev_px/py, so Of_IntegratePosition does not count it twice. */
static void Ext_Pos_Ekf(void)
{
	float k = CM_PER_M * g_of_scale, r = EXT_POS_CAM_SD_CM / k;
	float px0 = s_ekf_of.x[0], py0 = s_ekf_of.x[3], dx, dy;
	float ex = g_ext_pos.e_x, ey = g_ext_pos.e_y, d = sqrtf(ex * ex + ey * ey), hx, hy;
	if (d > EXT_POS_SLEW_CM) {   /* r6_f3_mode3: REJ_RELEASE took a 960 cm fix in one step; cap it like mode 2 */
		ex *= EXT_POS_SLEW_CM / d;
		ey *= EXT_POS_SLEW_CM / d;
	}
	hx = (ex * Cos_Yaw_01 - ey * Sin_Yaw_01) / k;
	hy = (ex * Sin_Yaw_01 + ey * Cos_Yaw_01) / k;
	(void)EkfOf_UpdatePos(&s_ekf_of, px0 + hx, py0 + hy, r * r, EXT_POS_EKF_GATE);
	s_ekf_prev_px += s_ekf_of.x[0] - px0;
	s_ekf_prev_py += s_ekf_of.x[3] - py0;
	dx = (s_ekf_of.x[0] - px0) * k;
	dy = (s_ekf_of.x[3] - py0) * k;
	hx = dx * Cos_Yaw_01 + dy * Sin_Yaw_01;
	hy = dy * Cos_Yaw_01 - dx * Sin_Yaw_01;
	ano_of.earth_x += hx;
	ano_of.earth_y += hy;
	/* FIX 2026-10-13 r7_f5_mode3: the jump also goes into a, so the ring (earth - a) keeps the correction-free path and
	 * c = a re-adds every correction made so far. Before, a stayed 0 and each fix (age 213-294 ms, ~17 per s) was scored
	 * against a past earth that lacked the corrections applied since, so every innovation was applied 4-5 times:
	 * locxPID.FB 1.06 Hz limit cycle, std 17.4 cm, mean step 5.9 cm per fix (mode 2: 1.4 cm p95) -> roll/pitch rocking. */
	g_ext_pos.a_x += hx;
	g_ext_pos.a_y += hy;
}

static void Ext_Pos_Tick(void)
{
	uint32_t now_ms = (uint32_t)xTaskGetTickCount() * (uint32_t)portTICK_PERIOD_MS;
	float dx, dy;
	/* c == a when mode 3 drops to the offset path (D8 fix B), so earth_x/y does not jump at the switch */
	uint8_t path = ext_pos_path(g_ext_pos_on, (uint8_t)(g_of_bias_mode == OF_BIAS_EKF && s_ekf_of_inited));
	uint8_t ekf3 = (uint8_t)(path == EXT_POS_PATH_EKF);
	uint8_t m2 = (uint8_t)(path == EXT_POS_PATH_OFFSET);
	if (ekf3) {
		g_ext_pos.c_x = g_ext_pos.a_x;   /* the ring holds earth - a: e = z - earth_past */
		g_ext_pos.c_y = g_ext_pos.a_y;
	}
	ext_pos_ring_tick(&s_ext_pos_ring, now_ms, ano_of.earth_x - g_ext_pos.a_x, ano_of.earth_y - g_ext_pos.a_y);
	if (g_ext_pos_post.seq != s_ext_pos_seen) {
		float zx, zy;
		uint32_t t_meas;
		taskENTER_CRITICAL();
		zx = g_ext_pos_post.x_cm;
		zy = g_ext_pos_post.y_cm;
		t_meas = g_ext_pos_post.t_meas_ms;
		s_ext_pos_seen = g_ext_pos_post.seq;
		taskEXIT_CRITICAL();
		if (g_ext_pos_on) {
			uint8_t res = ext_pos_fix(&g_ext_pos, &s_ext_pos_ring, t_meas, now_ms, zx, zy);
			if (m2) {
				(void)ext_pos_step(&g_ext_pos, res, &dx, &dy);
			} else if (ekf3 && ext_pos_take(&g_ext_pos, res)) {
				Ext_Pos_Ekf();
				Ext_Pos_SetFb();
			}
		}
	}
	if (m2) {
		ext_pos_spread(&g_ext_pos, &dx, &dy);
		ano_of.earth_x += dx;
		ano_of.earth_y += dy;
		Ext_Pos_SetFb();
	}
	ext_pos_stale_tick(&g_ext_pos, now_ms);
}

/* Drift NN live gate, once per tick before the position and velocity FB use it.
 * The NN task publishes a clamped body-velocity residual (m/s, EKF x[1]/x[4] frame);
 * it is applied only while live mode is set, the task is RUN with a fresh step and a
 * full receptive field, the mode-2 EKF is healthy, the drone is FLYING and the camera
 * is not in the loop (g_ext_pos_on 0 or 1: the NN must be judged against it). Any other
 * state applies 0, i.e. the plain mode-2 EKF: the shadow default is unchanged.
 * The edge into FLYING asks the task to clear its history (live or shadow): training
 * sequences start at take-off with zero history, so ground samples must not fill the
 * receptive field; live then starts after DRIFT_NN_WARM_STEPS of flight. */
static float s_dnn_dvx, s_dnn_dvy;   /* m/s, 0 when not applied */
static uint8_t s_dnn_was_flying;

static void Dnn_LiveGate(void)
{
	uint32_t now_ms = (uint32_t)xTaskGetTickCount() * (uint32_t)portTICK_PERIOD_MS;
	uint8_t flying = (uint8_t)(flight_phase == FLIGHT_PHASE_FLYING);
	uint8_t why;
	if (flying && !s_dnn_was_flying) g_drift_nn.reset_req = 1U;
	s_dnn_was_flying = flying;
	if (g_drift_nn_live == DRIFT_NN_LIVE_OFF)                         why = DRIFT_NN_WHY_OFF;
	else if (g_drift_nn.status != DRIFT_NN_ST_RUN)                    why = DRIFT_NN_WHY_STATUS;
	else if ((uint32_t)(now_ms - g_drift_nn.step_ms) > DRIFT_NN_STALE_MS) why = DRIFT_NN_WHY_STALE;
	else if (g_drift_nn.reset_req || g_drift_nn.steps < DRIFT_NN_WARM_STEPS) why = DRIFT_NN_WHY_WARMUP;
	else if (g_of_bias_mode != OF_BIAS_EKF || !g_ekf_of_health)       why = DRIFT_NN_WHY_EKF;
	else if (!flying)                                                 why = DRIFT_NN_WHY_PHASE;
	else if (g_ext_pos_on >= 2U)                                      why = DRIFT_NN_WHY_CAMERA;
	else                                                              why = DRIFT_NN_WHY_ON;
	if (why == DRIFT_NN_WHY_ON) {
		s_dnn_dvx = g_drift_nn.dvx;
		s_dnn_dvy = g_drift_nn.dvy;
	} else {
		s_dnn_dvx = 0.0f;
		s_dnn_dvy = 0.0f;
	}
	g_drift_nn.live_on = (uint8_t)(why == DRIFT_NN_WHY_ON);
	g_drift_nn.live_why = why;
}

/* ---- Position integration: Modes 0/1 use raw OF, Mode 2 uses KF ----
 * FIX 2026-09-26: one ground-hold gate for all modes (mode 2 used to
 * integrate on the ground, so armed-idle drift became a takeoff step).
 * Handheld alt is band-limited: raw u32 glitches (2905, 0xFFFFFFFF).
 * Output: ano_of.earth_x/y (+ _ture) and Ctrler.locx/locyPID.FB, cm. */
static void Of_IntegratePosition(void)
{
	u8 pos_integrate;
	pos_integrate = (u8)(ano_of.of_quality >= OF_MIN_QUALITY &&
	    ((g_of_handheld_test && ano_of.of_alt_cm >= OF_HANDHELD_MIN_ALT_CM &&
	      ano_of.of_alt_cm <= ALT_BAND_MAX_CM) ||
	     (DroneStatus.ARM_Status == Armed &&
	      (flight_phase == FLIGHT_PHASE_FLYING || flight_phase == FLIGHT_PHASE_LANDING))));
	if (g_of_bias_mode == OF_BIAS_EKF) {
		/* Mode 2: EKF state x[0]=pos_x, x[3]=pos_y is already debiased by
		 * construction. Apply yaw rotation and feed directly to PID FB.
		 * EkfOf_ResetPos() was called from Reset_World_Origin() below.
		 * FIX 2026-09-26: KF states are metres; earth_x/y and locx/yPID
		 * are cm (modes 0/1), so scale by 100. */
		float ekf_dx = (s_ekf_of.x[0] - s_ekf_prev_px) * CM_PER_M;
		float ekf_dy = (s_ekf_of.x[3] - s_ekf_prev_py) * CM_PER_M;
		if (!s_ekf_pos_synced) {
			ekf_dx = 0.0f;
			ekf_dy = 0.0f;
			s_ekf_pos_synced = 1U;
		}
		s_ekf_prev_px = s_ekf_of.x[0];
		s_ekf_prev_py = s_ekf_of.x[3];
		if (pos_integrate) {
			ekf_dx *= g_of_scale;   /* to true cm before the tilt step mixes in height */
			ekf_dy *= g_of_scale;
			/* WP-21 A: m/s * 100 * 0.005 s = cm/tick, same units as ekf_dx/dy */
			if (g_of_full_tilt) of_full_tilt_delta(&ekf_dx, &ekf_dy, ano_of.of2_h_f2_v * OF_HRATE_TO_CM_TICK);
			/* Drift NN live: body residual after scale, as nn_io.corrected adds it to earth (0 unless gated on) */
			ekf_dx += s_dnn_dvx * CM_PER_M * STAB_DT_S;
			ekf_dy += s_dnn_dvy * CM_PER_M * STAB_DT_S;
			ano_of.earth_x += ekf_dx * Cos_Yaw_01 + ekf_dy * Sin_Yaw_01;
			ano_of.earth_y += ekf_dy * Cos_Yaw_01 - ekf_dx * Sin_Yaw_01;
		}
		ano_of.earth_x_ture =  ano_of.earth_y;
		ano_of.earth_y_ture = -ano_of.earth_x;
		Ctrler.locxPID.FB = ano_of.earth_x_ture;
		Ctrler.locyPID.FB = ano_of.earth_y_ture;
	} else {
		s_ekf_pos_synced = 0U;
		/* WP-14: reached only when mode != 2. Clear the init flag only if the KF
		 * is not ticking either (shadow off), so a later start re-inits; in
		 * shadow the KF keeps running across mode changes and keeps its state. */
		if (!g_ekf_of_shadow) {
			s_ekf_of_inited = 0U;
			s_ekf_of_tripped = 0U;
		}
		/* Modes 0 and 1: debias raw OF and integrate as before.
		 * FIX 2026-09-26: hold position while on the ground (disarmed,
		 * GROUND_IDLE or LANDED): OF zero wanders 1-3 counts at rest,
		 * integrating to cm/s drift before takeoff. */
		if (pos_integrate)
		{
			float of_dx_deb = ano_of.of2_dx_fix - s_of_bias_x;
			float of_dy_deb = ano_of.of2_dy_fix - s_of_bias_y;

			ano_of.DISTANCE_X = ano_of.DISTANCE_X+of_dx_deb*STAB_DT_S;
			ano_of.DISTANCE_Y = ano_of.DISTANCE_Y+of_dy_deb*STAB_DT_S;

			float of_dx_t = of_dx_deb*STAB_DT_S*g_of_scale;   /* cm/tick, true cm */
			float of_dy_t = of_dy_deb*STAB_DT_S*g_of_scale;
			/* WP-21 A: DISTANCE_X/Y above stay raw body-frame */
			if (g_of_full_tilt) of_full_tilt_delta(&of_dx_t, &of_dy_t, ano_of.of2_h_f2_v * OF_HRATE_TO_CM_TICK);

			ano_of.earth_x = ano_of.earth_x + (of_dx_t*Cos_Yaw_01 + of_dy_t*Sin_Yaw_01 );
			ano_of.earth_y = ano_of.earth_y + (of_dy_t*Cos_Yaw_01 - of_dx_t*Sin_Yaw_01 );
		}
		ano_of.earth_x_ture  =  ano_of.earth_y;
		ano_of.earth_y_ture  =  -ano_of.earth_x;
		Ctrler.locxPID.FB= ano_of.earth_x_ture ;
		Ctrler.locyPID.FB= ano_of.earth_y_ture ;
	}
}

/* ADR-0011 Phase 3 + Phase 4 calibrators, one step each per tick. */
static void Cal_Step(void)
{
	/* ADR-0011 Phase 3 (CAL_AIRBORNE_HOVER_TRIM) — closed-form accel-bias LS trim.
	 * Runs only while flying. The body-frame accel measurement in static hover is
	 *     a_meas_body = 1000 * Gravity_Body + b_a   (mg, Gravity_Body is unit vector)
	 * The estimator reconstructs a_meas = Lin_Acc + 1000*Gravity_Body (gravity-removed
	 * reading + gravity vector = raw body accel) and applies the LS step
	 *     b_a <- b_a + mu * (g_ref_world - a_meas_world)
	 * where g_ref_world = (0, 0, +1000) mg and a_meas_world is rotated to world frame.
	 * Approximated here as body-frame directly (small-angle assumption in stable hover;
	 * the rotation error is <5 % at 10 deg tilt and the slow mu keeps convergence well-
	 * behaved under that error). */
	if (flight_phase == FLIGHT_PHASE_FLYING) {
		float a_meas_x = Lin_Acc_X_body + MG_PER_G * Gravity_Body_X;
		float a_meas_y = Lin_Acc_Y_body + MG_PER_G * Gravity_Body_Y;
		float a_meas_z = Lin_Acc_Z_body + MG_PER_G * Gravity_Body_Z;
		CalTrim_Step(&s_cal_trim,
		             0.0f, 0.0f, MG_PER_G,
		             a_meas_x, a_meas_y, a_meas_z,
		             1U);
		if (s_cal_trim.state == CAL_TRIM_STATE_SETTLED) {
			g_cal_health |= CAL_HEALTH_AIRBORNE_OK;
		} else if (s_cal_trim.state == CAL_TRIM_STATE_DEGRADED) {
			g_cal_health |= CAL_HEALTH_AIRBORNE_DEGRADED;
		}
	}

	/* ADR-0011 Phase 4 (CAL_HOT_HOVER) — gyro hot-bias FSM. Always ticked so the
	 * quiescence gate can detect a still-window anywhere in the flight. */
	{
		uint8_t rc_q = !Thr_StickActive() &&
		               !RCInput_IsActive(RC_AXIS_PITCH) &&
		               !RCInput_IsActive(RC_AXIS_ROLL) &&
		               !RCInput_IsActive(RC_AXIS_YAW);
		CalHot_Step(&s_cal_hot,
		            Gyro_X_Real, Gyro_Y_Real, Gyro_Z_Real,
		            Lin_Acc_X_body, Lin_Acc_Y_body,
		            (uint8_t)(flight_phase == FLIGHT_PHASE_FLYING),
		            rc_q);
		if (s_cal_hot.rejected) {
			g_cal_health |= CAL_HEALTH_HOT_REJECTED;  /* sticky until next quiescent cycle */
		} else if (s_cal_hot.state == CAL_HOT_STATE_WAIT_STILL && !s_cal_hot.cleared) {
			/* One-shot commit fired: HOT_HOVER_OK. */
			g_cal_health |= CAL_HEALTH_HOT_HOVER_OK;
			s_cal_hot.cleared = 1U;
		}
	}
}

/* Horizontal velocity FB (cm/s, world frame) for locxsPID/locysPID. */
static void Of_UpdateVelocityFB(void)
{
	/* EKF velocity only when selected (on ground) and still healthy; else debiased OF.
	 * FIX 2026-10-02: was raw of2_dx/dy, which no bias mode ever corrected. Same
	 * motion as of2_*_fix (gain 1.0-1.1, corr 0.92-0.97, no lag, hover logs
	 * 2026-10-01/02) but it read +8..+14 cm/s at rest on 2026-10-02, and the
	 * loop flew that phantom away as a ~12 cm -x drift. Now it uses the same
	 * debiased velocity the position loop integrates (modes 0/1/2). */
	float fb_dx = (float)ano_of.of2_dx_fix - s_of_bias_x;
	float fb_dy = (float)ano_of.of2_dy_fix - s_of_bias_y;
	u8 nn_vel = 0U;
	if (g_ekf_gate.ctrl_enable && g_ekf_gate.healthy) {
		fb_dx = g_ekf_gate.vx_cms;
		fb_dy = g_ekf_gate.vy_cms;
	}
	/* WP-14: OF EKF velocity feedback. When enabled AND mode 2 AND
	 * healthy, KF body velocity (m/s, x[1]/x[4]) replaces raw OF.
	 * Same frame and units as fb_dx/fb_dy after *100 → cm/s. */
	if (g_ekf_of_vel_fb && g_of_bias_mode == OF_BIAS_EKF && g_ekf_of_health) {
		fb_dx = s_ekf_of.x[1] * CM_PER_M;   /* vel_x body, cm/s */
		fb_dy = s_ekf_of.x[4] * CM_PER_M;   /* vel_y body, cm/s */
		nn_vel = (u8)(g_drift_nn_live == DRIFT_NN_LIVE_POSVEL);
	}
	fb_dx *= g_of_scale;   /* true cm/s, same scale as locx/locyPID.FB */
	fb_dy *= g_of_scale;
	if (nn_vel) {           /* drift NN live 2: same residual as the position track (0 unless gated on) */
		fb_dx += s_dnn_dvx * CM_PER_M;
		fb_dy += s_dnn_dvy * CM_PER_M;
	}
	Ctrler.locxsPID.FB= (fb_dy) *Cos_Yaw_01 +(-fb_dx)*Sin_Yaw_01;
	Ctrler.locysPID.FB=  (-fb_dx) * Cos_Yaw_01 - (fb_dy)*Sin_Yaw_01;
}

#endif /* !ALT_CF_HOST_TEST */

#ifdef ALT_CF_HOST_TEST
#include <math.h>
#ifndef STAB_DT_S
#define STAB_DT_S              0.005f
#endif
#ifndef M_PER_CM
#define M_PER_CM               0.01f
#endif
#ifndef ALT_BAND_MIN_CM
#define ALT_BAND_MIN_CM        5U
#endif
#ifndef ALT_BAND_MAX_CM
#define ALT_BAND_MAX_CM        500U
#endif
#ifndef ALT_JUMP_BASE_M
#define ALT_JUMP_BASE_M        0.05f
#endif
#ifndef ALT_JUMP_PER_VZ
#define ALT_JUMP_PER_VZ        0.15f
#endif
#ifndef ALT_JUMP_MAX_M
#define ALT_JUMP_MAX_M         0.20f
#endif
#ifndef ALT_RESYNC_REJECTS
#define ALT_RESYNC_REJECTS     20U
#endif
#ifndef ALT_RATE_LPF_OLD
#define ALT_RATE_LPF_OLD       0.9f
#endif
#ifndef ALT_RATE_LPF_NEW
#define ALT_RATE_LPF_NEW       0.1f
#endif
#ifndef ALT_IS_ARMED
#define ALT_IS_ARMED()         0U
#endif
#else
#define ALT_IS_ARMED()         ((DroneStatus.ARM_Status == Armed) ? 1U : 0U)
#endif

/* P0 Finding 2 (docs/p0-verdicts.md): per-tick ToF validity flag. 1 = this tick's
 * sample passed the band+jump gates in Update_Data. While 0 (out of range, e.g.
 * above the 5 m band ceiling or a 0xFFFF no-reading) the Z loops hold their last
 * valid FB and their integrators are frozen in Compute_Motor. */
static uint8_t  s_alt_valid_tick   = 0U;
static uint16_t s_alt_reject_cnt   = 0U;
static uint16_t s_alt_hist[3]      = {0U, 0U, 0U};
static uint8_t  s_alt_hist_n       = 0U;
static uint8_t  s_alt_cnt_prev     = 0U;
static uint8_t  s_alt_cf_inited    = 0U;
static uint8_t  s_alt_vz_mode_prev = ALT_VZ_MODE_DEFAULT;
static uint8_t  s_alt_armed_prev   = 0U;

/* Altitude complementary filter, one row per gain set; runtime state starts at 0.
   @k1      -        [0, 1]     height correction per ToF sample
   @k2      1/s      [0, 50]    climb-rate correction per ToF sample, per m of error
   @k3      1/s^2    [0, 5]     accel-bias correction per ToF sample (0 = off)
   @AccMax  m/s^2    [0, 30]    clamp on world-up linear accel */
#define ALT_CF_ROW(k1, k2, k3, AccMax) \
    { k1, k2, k3, AccMax }

static const AltCfGains s_alt_cf_gains[] = {
/*             k1     k2     k3     AccMax    set */
    ALT_CF_ROW(0.21f, 0.45f, 0.1f,  20.0f)  /* nominal  PROPOSED, S1 closed-loop sim */
};

/* Change history (newest first)
 * nominal
 *   2026-10-26 k1=0.21, k2=0.45, k3=0.1, AccMax=20.0: PROPOSED from S1 closed-loop sim (wn=3 rad/s, zeta=0.7 at Ts=0.05 s; poles |z|=0.894, 0.894, 0.988; dU p99 19.9x lower than FD)
 */

volatile uint8_t g_alt_vz_mode = ALT_VZ_MODE_DEFAULT;
AltCfState_t     alt_cf;

void Alt_CfReset(float h_init, uint8_t zero_bias)
{
	alt_cf.h = h_init;
	alt_cf.vz = 0.0f;
	alt_cf.e = 0.0f;
	alt_cf.a_up = 0.0f;
	if (zero_bias) {
		alt_cf.b_az = 0.0f;
		alt_cf.n_corr = 0U;
	}
	s_alt_cf_inited = (h_init > 0.0f) ? 1U : 0U;
	s_alt_vz_mode_prev = g_alt_vz_mode;
}

/* Altitude sanity gate (of_alt_cm is cm, u32; clamped to u16 below). Three layers (ADR-0011 Z-gate),
 * mirroring PX4/DJI altitude filtering:
 *   1. median-of-3 on the raw sample — a lone spike (in-band or the 0xFFFF
 *      no-reading) is never the median of three, so it is dropped before the
 *      band/jump gates ever see it.
 *   2. band gate — 5..500 cm (the 500 upper bound rejects 65535 no-reading;
 *      the 5 cm floor rejects sub-band 1 cm dropouts).
 *   3. rate-aware per-tick jump gate — baseline 0.05 m/tick (=10 m/s, still
 *      ~10x this drone's ~1 m/s climb), widened by the commanded vertical rate
 *      so legit fast ascents are not clipped: gate = 0.05 + 0.15*|Z_ratePID.Des|,
 *      capped at 0.20 m/tick. The 20-reject (100 ms) force-resync escape is kept
 *      so a genuine sustained level change is not locked out.
 * Then the Z FB: Z_posPID.FB = height (m), Z_ratePID.FB = low-passed climb rate (m/s), both held
 * on a rejected tick (s_alt_valid_tick = 0, P0 Finding 2). */
void Alt_UpdateFB(void)
{
	uint8_t cnt_changed;
	uint8_t sample_accepted = 0U;
	uint8_t resynced = 0U;
	uint8_t armed;
	float a_up;
	const AltCfGains *g = &s_alt_cf_gains[0];

	cnt_changed = (uint8_t)(ano_of.alt_update_cnt != s_alt_cnt_prev);
	s_alt_cnt_prev = ano_of.alt_update_cnt;

	{
		uint16_t a0, a1, a2, alt_med;

		/* Layer 1: median-of-3. */
		s_alt_hist[2] = s_alt_hist[1];
		s_alt_hist[1] = s_alt_hist[0];
		/* of_alt_cm is u32: clamp, a bare cast aliases 65536+x to x cm */
		s_alt_hist[0] = (ano_of.of_alt_cm > 0xFFFFU) ? 0xFFFFU : (uint16_t)ano_of.of_alt_cm;
		if (s_alt_hist_n < 3U) s_alt_hist_n++;
		if (s_alt_hist_n >= 3U)
		{
			a0 = s_alt_hist[0]; a1 = s_alt_hist[1]; a2 = s_alt_hist[2];
			alt_med = (a0 > a1) ? ((a1 > a2) ? a1 : ((a0 > a2) ? a2 : a0))
			                    : ((a0 > a2) ? a0 : ((a1 > a2) ? a2 : a1));
		}
		else
		{
			alt_med = s_alt_hist[0];  /* warmup: not enough history yet */
		}

		/* Layer 2: band gate. */
		if( alt_med >= ALT_BAND_MIN_CM && alt_med <= ALT_BAND_MAX_CM )
		{
			float h_new = alt_med*M_PER_CM*Cos_roll_01*Cos_pitch_01;
			/* Layer 3: rate-aware jump gate. */
			float gate = ALT_JUMP_BASE_M + ALT_JUMP_PER_VZ * fabsf(Ctrler.Z_ratePID.Des);
			uint8_t jump_ok;
			if (gate > ALT_JUMP_MAX_M) gate = ALT_JUMP_MAX_M;
			jump_ok = (uint8_t)(fabsf(h_new - ano_of.of2_raw_h) < gate);
			if( jump_ok || s_alt_reject_cnt >= ALT_RESYNC_REJECTS )
			{
				ano_of.of2_raw_h = h_new;
				s_alt_reject_cnt = 0U;
				s_alt_valid_tick = 1U;   /* P0 Finding 2: sample accepted this tick */
				if (jump_ok) {
					if (cnt_changed) sample_accepted = 1U;
				} else {
					resynced = 1U;
				}
			}
			else
			{
				s_alt_reject_cnt++;
				s_alt_valid_tick = 0U;   /* P0 Finding 2: jump-rejected - hold */
			}
		}
		else
		{
			s_alt_valid_tick = 0U;   /* P0 Finding 2: out of band (>5 m / 0xFFFF) - hold */
		}
	}

	ano_of.of2_h =ano_of.of2_raw_h;
	if (s_alt_valid_tick) { /* P0 Finding 2: valid sample - else hold last FB */
	                        /* and never publish the fake-zero d(frozen h)/dt.  */
		ano_of.of2_h_v = (ano_of.of2_h - ano_of.of2_last_h )/(STAB_DT_S);  /* one tick, 5 ms */
		ano_of.of2_last_h = ano_of.of2_h;
		ano_of.of2_h_f2_v  = ano_of.of2_h_f2_v  *ALT_RATE_LPF_OLD +ano_of.of2_h_v *ALT_RATE_LPF_NEW;

		Ctrler.Z_posPID.FB =  ano_of.of2_h;
		Ctrler.Z_ratePID.FB = ano_of.of2_h_f2_v ;
	}

	armed = ALT_IS_ARMED();
	if (g_alt_vz_mode > ALT_VZ_MODE_CF) {
		g_alt_vz_mode = s_alt_vz_mode_prev;
	}
	if (armed && g_alt_vz_mode != s_alt_vz_mode_prev) {
		g_alt_vz_mode = s_alt_vz_mode_prev;
	} else if (!armed && g_alt_vz_mode != s_alt_vz_mode_prev) {
		if (s_alt_vz_mode_prev == ALT_VZ_MODE_FD && g_alt_vz_mode == ALT_VZ_MODE_CF) {
			Alt_CfReset(ano_of.of2_h, 0U);
		}
		s_alt_vz_mode_prev = g_alt_vz_mode;
	}
	if (armed != s_alt_armed_prev) {
		Alt_CfReset(ano_of.of2_h, 0U);
		/* 2026-10-26 smoke hover: every arm waits for a real range reading. */
		if (armed) s_alt_cf_inited = 0U;
		s_alt_armed_prev = armed;
	}

	a_up = ALT_CF_ACC_SIGN * (Lin_Acc_X_body * Gravity_Body_X
	       + Lin_Acc_Y_body * Gravity_Body_Y
	       + Lin_Acc_Z_body * Gravity_Body_Z) * MG_TO_MPS2 - alt_cf.b_az;
	if (a_up > g->AccMax) a_up = g->AccMax;
	else if (a_up < -g->AccMax) a_up = -g->AccMax;
	alt_cf.a_up = a_up;

	if (!armed) {
		/* Disarmed = on the ground: pin h to the rangefinder, no velocity, no bias update.
		 * Free integration on the pad (no valid sample) drifted h to 110 m on 2026-10-26, and the
		 * camera tracker, which projects pixels at this height, rejected every fix. */
		alt_cf.h = ano_of.of2_h;
		alt_cf.vz = 0.0f;
		alt_cf.e = 0.0f;
	} else if (!s_alt_cf_inited && sample_accepted) {
		alt_cf.h = ano_of.of2_h;
		alt_cf.vz = 0.0f;
		alt_cf.e = 0.0f;
		s_alt_cf_inited = 1U;
		alt_cf.n_corr++;
	} else if (!s_alt_cf_inited) {
		/* 2026-10-26 smoke hover: hold armed-pad height until range initialization. */
		alt_cf.h = ano_of.of2_h;
		alt_cf.vz = 0.0f;
		alt_cf.e = 0.0f;
		if (resynced) s_alt_cf_inited = 1U;
	} else {
		alt_cf.h  += alt_cf.vz * STAB_DT_S + 0.5f * alt_cf.a_up * STAB_DT_S * STAB_DT_S;
		alt_cf.vz += alt_cf.a_up * STAB_DT_S;
		if (sample_accepted) {
			alt_cf.e     = ano_of.of2_h - alt_cf.h;
			alt_cf.h    += g->k1 * alt_cf.e;
			alt_cf.vz   += g->k2 * alt_cf.e;
			alt_cf.b_az -= g->k3 * alt_cf.e;
			if (alt_cf.b_az > ALT_CF_BAZ_MAX) alt_cf.b_az = ALT_CF_BAZ_MAX;
			else if (alt_cf.b_az < -ALT_CF_BAZ_MAX) alt_cf.b_az = -ALT_CF_BAZ_MAX;
			alt_cf.n_corr++;
		} else if (resynced) {
			alt_cf.e = ano_of.of2_h - alt_cf.h;
			alt_cf.h = ano_of.of2_h;
			s_alt_cf_inited = 1U;
		}
	}

	if (g_alt_vz_mode == ALT_VZ_MODE_CF) {
		Ctrler.Z_posPID.FB  = alt_cf.h;
		Ctrler.Z_ratePID.FB = alt_cf.vz;
	}
}

#ifdef ALT_CF_HOST_TEST
void Alt_TestResetAll(void)
{
	int i;
	ano_of.of_alt_cm = 0U;
	ano_of.alt_update_cnt = 0U;
	ano_of.of2_raw_h = 0.0f;
	ano_of.of2_h = 0.0f;
	ano_of.of2_h_v = 0.0f;
	ano_of.of2_h_f2_v = 0.0f;
	ano_of.of2_last_h = 0.0f;
	s_alt_valid_tick = 0U;
	s_alt_reject_cnt = 0U;
	for (i = 0; i < 3; i++) s_alt_hist[i] = 0U;
	s_alt_hist_n = 0U;
	s_alt_cnt_prev = 0U;
	s_alt_armed_prev = 0U;
	g_alt_vz_mode = ALT_VZ_MODE_DEFAULT;
	s_alt_vz_mode_prev = ALT_VZ_MODE_DEFAULT;
	s_alt_cf_inited = 0U;
	Alt_CfReset(0.0f, 1U);
	s_alt_cf_inited = 0U;
}
#endif
#ifndef ALT_CF_HOST_TEST

/* Angle FB from imu_data (one critical section, so the three angles are one sample) and rate FB. */
static void Att_UpdateFB(void)
{
	{
		float imu_pit, imu_rol, imu_yaw;
		taskENTER_CRITICAL();
		imu_pit = imu_data.pit;
		imu_rol = imu_data.rol;
		imu_yaw = imu_data.yaw;
		taskEXIT_CRITICAL();

		Ctrler.pitchPID.FB = -imu_pit;
		Ctrler.rollPID.FB  =  imu_rol;
		Ctrler.yawPID.FB   = -imu_yaw;
	}

	// Phase-1 gyro low-pass (default pass-through; enable via CMD 0x15). Filters the rate FB that
	// the rate PID, MRAC, and the system-ID frame all consume — see API/gyro_filter.c / ADR-0004.
	Ctrler.gyroyPID.FB = GyroFilter_Apply(GYRO_FILT_PITCH, -Gyro_Y_Real*RAD2DEG);
	Ctrler.gyroxPID.FB = GyroFilter_Apply(GYRO_FILT_ROLL,   Gyro_X_Real*RAD2DEG);
	Ctrler.gyrozPID.FB = GyroFilter_Apply(GYRO_FILT_YAW,   -Gyro_Z_Real*RAD2DEG);
}

/* Sensors -> loop feedback, in this order: trig cache, OF bias + OF KF, OF position, calibrators,
 * OF velocity, ToF height, attitude. */
void Update_Data(void)
{
	Att_UpdateTrig();

	{
		u8 of_ok = (ano_of.of_quality >= OF_MIN_QUALITY);
		Of_UpdateBias(of_ok);
		if (g_of_bias_mode == OF_BIAS_EKF || g_ekf_of_shadow) {
			Of_TickKf(of_ok);
		}
	}
	Dnn_LiveGate();
	Of_IntegratePosition();
	Ext_Pos_Tick();

	Cal_Step();

	Of_UpdateVelocityFB();

	Alt_UpdateFB();

	Att_UpdateFB();
}

/* ==== Update_Motor: motor gating by arm state and flight phase ===================================== */
#define LAND_RATE_SLOW_ALT_M   0.20f    /* below this height the touchdown rate threshold widens */
#define LAND_RATE_THR_LOW      0.08f    /* m/s, |Z rate| that counts as still below LAND_RATE_SLOW_ALT_M */
#define LAND_RATE_THR_HIGH     0.02f    /* m/s, |Z rate| that counts as still above it */
#define LAND_STABLE_TICKS      10       /* still ticks the rate test needs (s_stable_ticks is an int) */
#define LAND_CUT_ALT_M         0.15f    /* the rate test only cuts below this height */
#define GROUND_REST_MAX_M      0.15f    /* rest height is sampled only below this (hand-lift guard) */

/* Landing detector state. Reset on every way out of LANDING: the cut itself (Land_Step),
 * EMERGENCY and DISARMED (Land_ResetDetector). */
static uint8_t  s_land_init    = 0U;
static int      s_stable_ticks = 0;
static uint16_t s_land_rest_ticks = 0U;
static uint16_t s_land_timeout = 0U;
static uint16_t s_land_spool   = 0U;              /* spool-down ticks done, 0 = not started */
static float    s_land_spool_thr = 0.0f;          /* collective held during the spool (Mix_Compute) */
static float    s_land_rest_z  = LAND_REST_MIN;   /* ground z sampled in GROUND_IDLE */

/* Bench-mode height zero: captures the fixture resting height when bench mode
 * activates (0→1 transition). The Z position PID then sees (of2_h - offset),
 * so the drone treats its current height as zero — prop wash displaces it by
 * ±Δ and the controller does not fight to return to a virtual ground.
 * Only captures on 0→1 transition; the offset holds until bench mode deactivates. */
static void Bench_ZeroHeight(void)
{
	static float  s_bench_z_offset = 0.0f;
	static uint8_t s_bench_was_active = 0U;

	if (bench_mode_active && !s_bench_was_active) {
		s_bench_z_offset = Ctrler.Z_posPID.FB;   /* ~0.4 m on the 4DOF fixture */
	}
	s_bench_was_active = bench_mode_active;
	if (bench_mode_active) {
		Ctrler.Z_posPID.FB  -= s_bench_z_offset;  /* zero out the fixture resting height */
		Ctrler.Z_ratePID.FB  = 0.0f;              /* freeze Z rate so controller stays calm */
	}
}

/* Motor bench-test override (CMD 0x16): drive ONE chosen motor to a commanded CCR
 * for the thrust-stand experiment. Strictly DISARMED-only, with a dead-man — if the
 * dashboard stops sending heartbeats (watchdog exceeds the window) or anything leaves
 * DISARMED, motors are zeroed and test mode exits. RC/arming stays the final authority.
 * Set_PWM_Motors() applies the [2000,4000] clamp. See docs/bench_characterization.md.
 * Returns 1 when it owned the motors this tick. */
static uint8_t Motor_TestOverride(FlightState_t state)
{
	if (motor_test_active)
	{
		if (state != FLIGHT_STATE_DISARMED ||
		    ++motor_test_watchdog > MOTOR_TEST_DEADMAN_TICKS)
		{
			motor_test_active = 0U;
			Set_Zero_Motors();
			return 1U;
		}
		mymotor.motor1 = (motor_test_id == 1U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
		mymotor.motor2 = (motor_test_id == 2U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
		mymotor.motor3 = (motor_test_id == 3U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
		mymotor.motor4 = (motor_test_id == 4U) ? (short)motor_test_ccr : Motor_PWM_ZERO;
		Set_PWM_Motors();
		return 1U;
	}
	return 0U;
}

/* Keil presets: of1 = g_ekf_of1_on, axis mask = g_ctrl_axis_mask (bit per ctrl_axis_e, clear = pure PID),
 * p/r gamma = mrac_g_gamma scale on every pitch and roll group (learning rate), zhd = Z_ratePID headroom
 * (1 = UMax/UiMax/SumEMax from s_zhd, 0 = the API/pid.c boot values). Ctrler persists to reboot.
 * Legend (tools/row_meta.py, bounds PROPOSED):
 *   @of1    -  [0, 1]   g_ekf_of1_on
 *   @mask   -  [0, 63]  g_ctrl_axis_mask, one bit per ctrl_axis_e (6 axes)
 *   @g_pr   -  [0, 1]   mrac_g_gamma scale on every pitch and roll group
 *   @zhd    -  [0, 1]   1 = Z_ratePID headroom from s_zhd, 0 = boot values */
typedef struct { uint8_t of1; uint8_t axis_mask; float g_pr; uint8_t zhd; } KeilPreset_t;
#define KP_ROW(of1, mask, g_pr, zhd)  { (of1), (mask), (g_pr), (zhd) }
static const KeilPreset_t s_kp[] = {
	/*        of1  axis mask  p/r gamma  zhd */
	KP_ROW(   1U,     0x0FU,     1.0f,   0U ),  /* 0 10-07 flown: two-channel EKF, MRAC on every axis       */
	KP_ROW(   0U,     0x0FU,     1.0f,   0U ),  /* 1 of1 off (load swing fix candidate)                     */
	KP_ROW(   0U,     0x0FU,     0.1f,   0U ),  /* 2 of1 off + MRAC pitch/roll learning x0.1                 */
	KP_ROW(   0U,     0x08U,     1.0f,   0U ),  /* 3 of1 off + MRAC on z only (sink fix, no p/r adaptation)  */
	KP_ROW(   1U,     0x08U,     1.0f,   0U ),  /* 4 MRAC on z only, EKF as flown                            */
	KP_ROW(   0U,     0x0FU,     1.0f,   1U ),  /* 5 preset 1 + z headroom (PID no longer sinks, 0.5 kg)    */
	KP_ROW(   0U,     0x08U,     1.0f,   1U ),  /* 6 preset 3 + z headroom                                   */
	KP_ROW(   1U,     0x3FU,     1.0f,   1U ),  /* 7 preset 5, of1 on (replay vs truth 22/21 vs 32/45 cm), x/y MRAC on 10-10 */
};
/* zhd 1: Z_ratePID UMax, UiMax, SumEMax (Ki 0.435 * 700 = 305 > UiMax). Bench 2026-10-08 (PROPOSED): flown
 * 300/100/250 holds the 0.5 kg sling at z 0.47 of 1.0 (Up+Ui clip at 300); 500/300/700 holds 1.00. */
static const float s_zhd[3] = { 500.0f, 300.0f, 700.0f };
static float s_zboot[3];
static uint8_t s_zboot_ok = 0U;
static uint8_t s_vp_last = 0U;      /* last vp_id handled; 0xFF forces a re-apply */

/* Same rule as CMD 0x1D (send_data.c): not armed, and not flying or landing. EMERGENCY on the ground passes. */
static uint8_t Keil_OnGround(FlightState_t state)
{
	return (uint8_t)((state != FLIGHT_STATE_ARMED) && (flight_phase != FLIGHT_PHASE_FLYING) &&
	                 (flight_phase != FLIGHT_PHASE_LANDING));
}

/* Keil_OnGround, or RC-armed on the pad before the climb: GROUND_IDLE, no WFB primitive or TWC policy running
 * and the throttle under the idle gate, so the motors sit at Set_IDLE_Motors and no controller drives them.
 * Gates the exp row and the kp/vp/lim rows it writes. Lab 10-19: campaign_fly waits for the RC arm before it
 * sets exp_id, so with Keil_OnGround alone exp_active trailed one flight (255 -> 4 -> 5 -> 6). */
static uint8_t Keil_PreTakeoff(FlightState_t state)
{
	if (Keil_OnGround(state)) return 1U;
	return (uint8_t)((state == FLIGHT_STATE_ARMED) && (flight_phase == FLIGHT_PHASE_GROUND_IDLE) &&
	                 (g_wfb_status.prim_state == (float)WFB_PRIM_IDLE) && !TWC.execute &&
	                 (RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX));
}

static void Keil_PresetPoll(FlightState_t state)
{
	uint8_t k, id = kp_id;
	if (!s_zboot_ok) {             /* first tick: the API/pid.c values, so zhd 0 restores them */
		s_zboot[0] = Ctrler.Z_ratePID.UMax; s_zboot[1] = Ctrler.Z_ratePID.UiMax; s_zboot[2] = Ctrler.Z_ratePID.SumEMax;
		s_zboot_ok = 1U;
	}
	if (!kp_go && id == kp_active) return;
	if (!Keil_PreTakeoff(state)) return;
	kp_go = 0U;
	if (id >= (uint8_t)(sizeof(s_kp) / sizeof(s_kp[0]))) { kp_id = kp_active; return; }
	g_ekf_of1_on = s_kp[id].of1;
	g_ctrl_axis_mask = s_kp[id].axis_mask;
	for (k = 0U; k < MRAC_N_GROUPS; k++) {
		mrac_g_gamma[MRAC_AXIS_PITCH][k] = s_kp[id].g_pr;
		mrac_g_gamma[MRAC_AXIS_ROLL][k] = s_kp[id].g_pr;
	}
	{
		const float *z = s_kp[id].zhd ? s_zhd : s_zboot;
		Ctrler.Z_ratePID.UMax = z[0]; Ctrler.Z_ratePID.UiMax = z[1]; Ctrler.Z_ratePID.SumEMax = z[2];
	}
	kp_active = id;
	s_vp_last = 0xFFU;             /* the kp row wrote p/r gamma: re-apply the variant row on top */
}

/* Limit modes (docs/lab/today-2026-10-10.md). tilt = gs_max_pitch/roll_deg (lean cap in accel_to_lean_angles),
 * vxy = position-loop velocity setpoint cap, trajv = fastest trajectory AutoflyTask accepts, yawr = heading-hold
 * rate cap. Fixed caps above every row: locxsPID UMax 600 cm/s^2 = 31.4 deg lean, yawPID UMax 160 deg/s, z 1 m/s.
 * Mode 2 sits just under those. Hover throttle 0.43-0.47 (10-07 logs), so 30 deg needs about 0.55 collective.
 * Legend (tools/row_meta.py, bounds PROPOSED, max = the fixed caps above):
 *   @tilt   deg    [0, 31.4]  gs_max_pitch_deg and gs_max_roll_deg
 *   @vxy    cm/s   [0, 300]   g_vxy_des_max
 *   @trajv  cm/s   [0, 300]   g_traj_v_max_cms
 *   @yawr   deg/s  [0, 160]   g_yawrate_des_max */
typedef struct { float tilt_deg; float vxy_cms; float trajv_cms; float yawr_dps; } LimitMode_t;
#define LIM_ROW(tilt, vxy, trajv, yawr)  { (tilt), (vxy), (trajv), (yawr) }
static const LimitMode_t s_lim[] = {
	/*        tilt   vxy     trajv   yawr */
	LIM_ROW( 15.0f, 120.0f,  80.0f,  90.0f ),  /* 0 slow: the 10-07 limits, yaw cap 60 -> 90 = traj yaw_rate_dps */
	LIM_ROW( 20.0f, 150.0f, 110.0f, 120.0f ),  /* 1 conservative: f8_laps 3 fits (104 cm/s)                     */
	LIM_ROW( 30.0f, 200.0f, 150.0f, 160.0f ),  /* 2 max: just under the fixed PID caps above                    */
};

static void Limit_ModePoll(FlightState_t state)
{
	uint8_t id = lim_mode;
	if (id == lim_active) return;
	if (!Keil_PreTakeoff(state)) return;
	if (id >= (uint8_t)(sizeof(s_lim) / sizeof(s_lim[0]))) { lim_mode = (lim_active < 3U) ? lim_active : 0U; return; }
	gs_max_pitch_deg = s_lim[id].tilt_deg;
	gs_max_roll_deg = s_lim[id].tilt_deg;
	g_vxy_des_max = s_lim[id].vxy_cms;
	g_traj_v_max_cms = s_lim[id].trajv_cms;
	g_yawrate_des_max = s_lim[id].yawr_dps;
	lim_active = id;
}

/* Lab stages (docs/lab/run-sheet-2026-10-10.md). Each row sets traj_id 4 (figure-8), traj_p.f8_tilt_deg,
 * f8_laps, yaw_rate_dps and lim_mode, so a flight needs only lab_stage, vp_id (ch8 for MRAC) and traj_go.
 * Loads are hung by hand; stages 4 and 5 only name them. Ground only, on change, before Limit_ModePoll.
 * Legend (tools/row_meta.py, bounds PROPOSED):
 *   @tilt   deg    [0, 31.4]  traj_p.f8_tilt_deg
 *   @laps   -      [1, 10]    traj_p.f8_laps
 *   @yaw    deg/s  [0, 160]   traj_p.yaw_rate_dps
 *   @lim    -      [0, 2]     lim_mode, a row of s_lim */
typedef struct { float tilt_deg; float laps; float yaw_dps; uint8_t lim; } LabStage_t;
#define STAGE_ROW(tilt, laps, yaw, lim)  { (tilt), (laps), (yaw), (lim) }
static const LabStage_t s_stage[] = {
	/*          tilt   laps  yaw     lim */
	STAGE_ROW(  0.0f, 1.0f,  90.0f, 0U ),  /* 0 flat, 1 lap: nose check, x/y shakedown                 */
	STAGE_ROW( 30.0f, 2.0f,  90.0f, 0U ),  /* 1 no load, mode 0 (boot default)                         */
	STAGE_ROW( 30.0f, 3.0f, 120.0f, 1U ),  /* 2 no load, mode 1 (104 cm/s computed)                    */
	STAGE_ROW( 30.0f, 4.0f, 160.0f, 2U ),  /* 3 no load, mode 2 (138 cm/s computed)                    */
	STAGE_ROW( 30.0f, 2.0f,  90.0f, 0U ),  /* 4 0.2 kg under the front-left motor                      */
	STAGE_ROW( 30.0f, 2.0f,  90.0f, 0U ),  /* 5 0.2 kg front-left + 0.4 kg centre                      */
};

static void Lab_StagePoll(FlightState_t state)
{
	uint8_t id = lab_stage;
	if (id == lab_stage_active) return;
	if (!Keil_OnGround(state)) return;
	if (id >= (uint8_t)(sizeof(s_stage) / sizeof(s_stage[0]))) { lab_stage_active = 0xEEU; return; }
	Traj_SetStage(s_stage[id].tilt_deg, s_stage[id].laps, s_stage[id].yaw_dps);
	lim_mode = s_stage[id].lim;
	lab_stage_active = id;
}

/* MRAC variant rows (docs/flights/2026-10-08-variant-sweep-plan.md). Every row writes every column, so any
 * row restores the others. Axes: p/r = pitch+roll, y = yaw, pry = all three; z is never touched. Values
 * copy the WP-33 SIL presets (ground_station/analysis/controllers/mrac_v1/v2/pr/st.yaml); the SIL injected
 * p/r only and was stable at gamma 0.25-0.5, every variant tipped at gamma 1 (PROPOSED for flight).
 * Legend (tools/row_meta.py; bounds PROPOSED, the MRAC_SET legend in API/mrac.c where the field matches):
 *   @ref_pr    -      [-1, 2]    ref_type on pitch and roll, -1 = global ref_model_type
 *   @ref_y     -      [-1, 2]    ref_type on yaw
 *   @dn        -      [0, 1]     drive_norm on pitch, roll and yaw
 *   @lam_edot  s      [0, 0.1]   lam_edot on pitch and roll
 *   @bw_y      rad/s  [0.5, 100] ref_model_bw on yaw
 *   @mu_sat    -      [0, 10]    mu_sat on pitch, roll and yaw
 *   @kappa     -      [0, 2]     kappa_pr on pitch and roll
 *   @crm       1/s    [0, 50]    crm_ell on pitch and roll
 *   @st_eps    rad/s  [0, 2]     st_eps on pitch and roll
 *   @lam_ang   1/s    [0, 20]    lam_ang on pitch and roll
 *   @g         -      [0, 1]     mrac_g_gamma scale on pitch, roll and yaw
 *   @basis     -      [0, 10]    basis MRAC_BASIS_* on pitch and roll
 *   @ext_g     -      [0, 10]    ext block (RBF / S10X slots) learns at g * ext_g */
typedef struct {
	int8_t ref_pr, ref_y; uint8_t dn; float lam_edot, bw_y, mu_sat, kappa, crm_ell, st_eps, lam_ang, g; uint8_t basis; float ext_g;
	/* 3L-v2 p/r (f00412a): te_off 1 = tracking-error learning off on p/r (gamma 0, yaw keeps g); L2 composite
	 * gamma_c, b_axis, pe_delay, wc_pe; D self-tuning gain p_max, p_forget. VP_ROW leaves them 0 = off. */
	uint8_t te_off; float gamma_c, b_axis, pe_delay, wc_pe, p_max, p_forget;
} VariantPreset_t;
#define VP_ROW(ref_pr, ref_y, dn, lam_edot, bw_y, mu_sat, kappa, crm, st_eps, lam_ang, g, basis, ext_g) \
	{ (ref_pr), (ref_y), (dn), (lam_edot), (bw_y), (mu_sat), (kappa), (crm), (st_eps), (lam_ang), (g), (basis), (ext_g), \
	  0U, 0.0f, 0.0f, 0.0f, 20.0f, 0.0f, 0.0f }   /* wc_pe 20: the setter refuses 0 (range 1-100) */
#define VP_ROW3(lam_ang, g, te_off, gamma_c, b_axis, pe_delay, wc_pe, p_max, p_forget) \
	{ 2, 1, 1U, 0.0018f, 2.0f, 0.0f, 0.0f, 0.0f, 0.0f, (lam_ang), (g), 0U, 1.0f, \
	  (te_off), (gamma_c), (b_axis), (pe_delay), (wc_pe), (p_max), (p_forget) }
static const VariantPreset_t s_vp[] = {
	/*     ref p/r  ref y  drive_norm pry  lam_edot p/r  bw y   mu_sat pry  kappa p/r  crm_ell p/r  st_eps p/r  lam_ang p/r  gamma pry  basis p/r  ext_g pry */
	VP_ROW(   -1,     -1,       0U,          0.0f,     30.0f,   0.0f,      0.0f,       0.0f,      0.0f,       0.0f,      1.0f,   0U,  1.0f ),  /* 0 flown S6   */
	VP_ROW(   -1,     -1,       0U,          0.0f,     30.0f,   0.0f,      0.0f,       0.0f,      0.0f,       0.0f,      0.25f,   0U,  1.0f ),  /* 1 S6 x0.25   */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       0.0f,      0.25f,   0U,  1.0f ),  /* 2 V1         */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,  0.85f,      0.0f,       0.0f,      0.0f,       0.0f,      0.25f,   0U,  1.0f ),  /* 3 V2 mu_sat  */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.5f,      10.0f,      0.0f,       0.0f,      0.25f,   0U,  1.0f ),  /* 4 PR         */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      2.0f,       0.0f,      0.25f,   0U,  1.0f ),  /* 5 ST         */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,   0U,  1.0f ),  /* 6 3L lam_ang */
	/* FW-B sim feature sets on the vp 6 law (basis = MRAC_BASIS_*, sim_core.py features()). Need the MULTI build
	 * (Keil Define MRAC_VARIANT=2); a default build refuses basis > 0 and shows vp_active 0xEE. Gamma PROPOSED. */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,   1U,  1.0f ),  /* 7 S10      */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,   3U,  1.0f ),  /* 8 RBF12    */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,   2U,  1.0f ),  /* 9 RBF6     */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,   4U,  1.0f ),  /* 10 RBF24   */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,   5U,  1.0f ),  /* 11 S6+RBF12*/
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.5f,      10.0f,      0.0f,       4.0f,      0.25f,   6U,  1.0f ),  /* 12 S10X+PR (vp 12, Z battery slot) */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,   7U,  4.0f ),  /* 13 RBF24T  (vp 10 retuned, gap width) */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,   8U,  4.0f ),  /* 14 RBF24D  (vp 10 retuned, Russian doll) */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,  0.85f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,   9U,  4.0f ),  /* 15 S10X+RBF16 (vp 12 physics at 1/4 gamma + vp 13 inner 16 Gaussians, mu_sat, no PR) */
	/* 3L-v2 on the vp 6 law, S6 basis, default build. b_axis 49 rad/s^2 per u measured and pe_delay 7 ticks = 35 ms (the cap; measured delay 40-50 ms)
	 * on the 2026-10-08 PID logs; replay pitch/roll cancel (mrac_log_replay --set 3l --shadow, 5 PID logs):
	 * vp 6 -1.16/-1.59, row 16 -0.11/-0.15, row 17 -0.03/-0.05. Gains PROPOSED until flown.
	 *       lam_ang  gamma  te_off  gamma_c  b_axis  pe_delay  wc_pe  p_max  p_forget */
	VP_ROW3(  4.0f,   0.25f,   0U,    20.0f,  49.0f,    7.0f,   20.0f,  0.0f,  0.0f ),  /* 16 3Lv2: vp 6 + L2 composite */
	VP_ROW3(  4.0f,   0.25f,   1U,     8.0f,  49.0f,    7.0f,   20.0f,  0.0f,  0.0f ),  /* 17 L2 only (tracking learning off p/r) */
	VP_ROW3(  4.0f,   0.25f,   0U,    20.0f,  49.0f,    7.0f,   20.0f,  4.0f,  0.5f ),  /* 18 3Lv2 + D self-tuning gain */
	/* 19: replay of every log (mrac_log_replay --set d, 59 logs, d_gain_range.md): p_forget sets the D gain (P rests near
	 * p_forget/m^2), p_max barely matters; L2only g20 + D p_max 20 p_forget 5 gives median p/r cancel +0.04/+0.02 vs
	 * row 17 -0.07/-0.09, but is worst on load-drop logs (f17 *_remov, -8.75). Fly after row 17, hover only. PROPOSED. */
	VP_ROW3(  4.0f,   0.25f,   1U,    20.0f,  49.0f,    7.0f,   20.0f, 20.0f,  5.0f ),  /* 19 L2 only g20 + D self-tuning gain */
	/* 20-24: single maskable feature basis RBF40T_BPQ (basis 10, 36 RBF + 4 physics cells, 40 ext slots total).
	 * Each row sets a different ext_g (learning rate) and ships a starting mask in mrac_vp12.mask[axis] on
	 * the Keil-side through Keil_VariantPoll. The user can dial any subset at runtime by writing the mask
	 * in the Keil watch window -- no flash cycle between variants. See docs/design/vp-maskable-basis.md. */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,  10U, 4.0f ),  /* 20 RBF40T_BPQ, 36 RBF cells, no physics (mask = all 36 + 0 phys) */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,  10U, 4.0f ),  /* 21 RBF40T_BPQ, 36 RBF + 4 physics (mask = all 40) */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,  10U, 4.0f ),  /* 22 RBF40T_BPQ, 24 RBF (mask inner 12 off), no physics (vp 13 equivalent) */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,  10U, 4.0f ),  /* 23 RBF40T_BPQ, 24 RBF + 4 physics (the multi-axis reusable pattern) */
	VP_ROW(    2,      1,       1U,       0.0018f,      2.0f,   0.0f,      0.0f,       0.0f,      0.0f,       4.0f,      0.25f,  10U, 6.0f ),  /* 24 RBF40T_BPQ, 24 RBF + 4 physics, ext_g 6 (faster learning for the arm) */
};
#define VP_PR   0x03U
#define VP_Y    0x04U
#define VP_PRY  0x07U

static uint8_t Vp_Set(uint8_t axes, uint8_t field, float v)
{
	uint8_t a, ok = 1U;
	for (a = 0U; a < 3U; a++)
		if (axes & (1U << a)) ok &= MRAC_VariantParamSet(a, field, v);
	return ok;
}

/* In-flight preset: edit vp_user field by field in the Keil watch window, then vp_user_go = 1 (or vp_id = 100).
 * Like vp_id it applies on the ground, and also in flight while ch8 is off (MRAC shadow: u_ad is not on the
 * motors, the weights restart from zero and learn until ch8 goes on). With ch8 on the write waits.
 * vp_active = 100 shows the user row is live; vp_id = N goes back to a table row. Starts as row 16. */
#define VP_USER_ID 100U
VariantPreset_t vp_user = VP_ROW3(4.0f, 0.25f, 0U, 20.0f, 49.0f, 7.0f, 20.0f, 0.0f, 0.0f);   /* row 16 */
volatile uint8_t vp_user_go = 0U;

static void Keil_VariantPoll(FlightState_t state)
{
	uint8_t ok = 1U, id = vp_id;
	const VariantPreset_t *r;
	if (!Keil_PreTakeoff(state) && mrac_flags.output_injection_on) return;
	if (vp_user_go) { vp_user_go = 0U; vp_id = id = VP_USER_ID; s_vp_last = 0xFFU; }
	if (id == s_vp_last) return;
	s_vp_last = id;
	if (id == VP_USER_ID) r = &vp_user;
	else if (id < (uint8_t)(sizeof(s_vp) / sizeof(s_vp[0]))) r = &s_vp[id];
	else { vp_active = 0xEEU; return; }
	ok &= Vp_Set(VP_PR,  MRAC_VF_REF_TYPE,     (float)r->ref_pr);
	ok &= Vp_Set(VP_Y,   MRAC_VF_REF_TYPE,     (float)r->ref_y);
	ok &= Vp_Set(VP_PRY, MRAC_VF_DRIVE_NORM,   (float)r->dn);
	ok &= Vp_Set(VP_PR,  MRAC_VF_LAM_EDOT,     r->lam_edot);
	ok &= Vp_Set(VP_Y,   MRAC_VF_REF_MODEL_BW, r->bw_y);
	ok &= Vp_Set(VP_PRY, MRAC_VF_MU_SAT,       r->mu_sat);
	ok &= Vp_Set(VP_PR,  MRAC_VF_KAPPA_PR,     r->kappa);
	ok &= Vp_Set(VP_PR,  MRAC_VF_CRM_ELL,      r->crm_ell);
	ok &= Vp_Set(VP_PR,  MRAC_VF_ST_EPS,       r->st_eps);
	ok &= Vp_Set(VP_PR,  MRAC_VF_ST_PHI_MAX,   10.0f);
	ok &= Vp_Set(VP_PR,  MRAC_VF_ST_BAR,       0.0f);
	ok &= Vp_Set(VP_PR,  MRAC_VF_LAM_ANG,      r->lam_ang);
	ok &= Vp_Set(VP_PRY, MRAC_VF_RBF_ON,       0.0f);
	ok &= Vp_Set(VP_PRY, MRAC_VF_LF_GAIN,      0.0f);
	ok &= Vp_Set(VP_PR,  MRAC_VF_BASIS,        (float)r->basis);
	/* vp 12: Z runs the battery slot only with S10X, S6 otherwise (the setter restores the Z rows) */
	ok &= MRAC_VariantParamSet((uint8_t)MRAC_AXIS_Z, MRAC_VF_BASIS,
	                           (r->basis == MRAC_BASIS_S10X) ? (float)MRAC_BASIS_S10X : 0.0f);
	ok &= Vp_Set(VP_PRY, MRAC_VF_GAMMA_SCALE,  r->g);
	if (r->te_off) ok &= Vp_Set(VP_PR, MRAC_VF_GAMMA_SCALE, 0.0f);
	ok &= Vp_Set(VP_PR,  MRAC_VF_GAMMA_C,      r->gamma_c);
	ok &= Vp_Set(VP_PR,  MRAC_VF_B_AXIS,       r->b_axis);
	ok &= Vp_Set(VP_PR,  MRAC_VF_PE_DELAY,     r->pe_delay);
	ok &= Vp_Set(VP_PR,  MRAC_VF_WC_PE,        r->wc_pe);
	ok &= Vp_Set(VP_PR,  MRAC_VF_P_MAX,        r->p_max);
	ok &= Vp_Set(VP_PR,  MRAC_VF_P_FORGET,     r->p_forget);
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
	{	/* ext block (RBF / S10X slots) learns at g * ext_g, u_nom and xm stay at g (exp15b: |Theta_ext| 0.008 of 0.05 lim);
		 * te_off keeps p/r tracking-error learning off here too */
		uint8_t a;
		for (a = 0U; a < 3U; a++) mrac_g_gamma[a][MRAC_GRP_RBF] = (r->te_off && a < 2U) ? 0.0f : r->g * r->ext_g;
	}
#endif
	MRAC_ResetWeights();
	vp_active = ok ? id : 0xEEU;
}

/* Experiment rows (docs/agent/briefs/WP-46.md). One write to exp_id picks controller, adaptation,
 * limits and reference model on the ground.
 * Legend (tools/row_meta.py, bounds PROPOSED):
 *   @xy     -      [0, 255]   g_xy_ctrl_kind (0-3, 0xFF keep)
 *   @kp     -      [0, 255]   kp_id (0-7, 0xFF keep)
 *   @vp     -      [0, 255]   vp_id (0-24, 0xFF keep)
 *   @lim    -      [0, 255]   lim_mode (0-2, 0xFF keep)
 *   @mdec   -      [0, 255]   g_imu_mahony_yaw_decouple (0/1, 0xFF keep)
 *   @xyrm   -      [-1, 127]  s_xyrm row (-1 auto/follow xy, 0-3, 0xFF keep)
 *   @layer  -      [0, 255]   reserved layer (0 off) */
#define EXP_ROW(name, xy, kp, vp, lim, mdec, xyrm, layer)  { (xy), (kp), (vp), (lim), (mdec), (xyrm), (layer) }
static const ExpRow_t s_exp[] = {
	/*         name       xy    kp    vp    lim   mdec  xyrm  layer */
	EXP_ROW(   pid,       0U,   7U,   0U,   0U,   1U,   -1,   0U ),  /* 0 PROPOSED */
	EXP_ROW(   adrc,      1U,   7U,   0U,   0U,   1U,   -1,   0U ),  /* 1 PROPOSED */
	EXP_ROW(   lqr,       2U,   7U,   0U,   0U,   1U,   -1,   0U ),  /* 2 PROPOSED */
	EXP_ROW(   mpc,       3U,   7U,   0U,   0U,   1U,   -1,   0U ),  /* 3 PROPOSED */
	EXP_ROW(   pid_3l,    0U,   7U,  16U,   0U,   1U,   -1,   0U ),  /* 4 PROPOSED */
	EXP_ROW(   adrc_3l,   1U,   7U,  16U,   0U,   1U,   -1,   0U ),  /* 5 PROPOSED */
	EXP_ROW(   lqr_3l,    2U,   7U,  16U,   0U,   1U,   -1,   0U ),  /* 6 PROPOSED */
	EXP_ROW(   mpc_3l,    3U,   7U,  16U,   0U,   1U,   -1,   0U ),  /* 7 PROPOSED */
};

/* xy reference model per controller kind (step D fit to figure-8 velocity profile).
 * Legend (tools/row_meta.py, bounds PROPOSED):
 *   @ref_type  -      [0, 2]     ref_type (1.0 first order, 2.0 second order)
 *   @bw        rad/s  [0.5, 50]  ref_model_bw
 *   @crm_ell   1/s    [0, 50]    crm_ell (0 off)
 *   @g         -      [0, 2]     gamma scale */
#define XYRM_ROW(ref_type, bw, crm_ell, g)  { (ref_type), (bw), (crm_ell), (g) }
static const XyRm_t s_xyrm[4] = {
	/*          ref_type    bw     crm_ell    g   */
	XYRM_ROW(   1.0f,     2.0f,     0.0f,   1.0f ),  /* 0 PID: measured 2.0 */
	XYRM_ROW(   1.0f,     2.2f,     0.0f,   1.0f ),  /* 1 ADRC: PROPOSED (SIL fit) */
	XYRM_ROW(   1.0f,     4.8f,     0.0f,   1.0f ),  /* 2 LQR: PROPOSED (SIL fit) */
	XYRM_ROW(   1.0f,     4.4f,     0.0f,   1.0f ),  /* 3 MPC: PROPOSED (SIL fit) */
};

static uint8_t s_exp_last = 0xFFU;

static void Exp_Poll(FlightState_t state)
{
	int8_t xyrm = -1;
	uint8_t res, on_ground;
	ExpKnobs_t knobs;

	knobs.xy_kind = &g_xy_ctrl_kind;
	knobs.kp_id = &kp_id;
	knobs.vp_id = &vp_id;
	knobs.lim_mode = &lim_mode;
	knobs.mdec = &g_imu_mahony_yaw_decouple;

	on_ground = Keil_PreTakeoff(state);
	res = Exp_Step(exp_id, &s_exp_last, exp_active, on_ground,
	               s_exp, (uint8_t)(sizeof(s_exp) / sizeof(s_exp[0])),
	               &knobs, &xyrm);

	if (xyrm >= 0 && xyrm < (int8_t)(sizeof(s_xyrm) / sizeof(s_xyrm[0]))) {
		uint8_t ok = 1U;
		const XyRm_t *r = &s_xyrm[xyrm];
		ok &= MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_REF_TYPE,     r->ref_type);
		ok &= MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_REF_MODEL_BW, r->bw);
		ok &= MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_CRM_ELL,      r->crm_ell);
		ok &= MRAC_VariantParamSet(MRAC_AXIS_X, MRAC_VF_GAMMA_SCALE,  r->g);
		ok &= MRAC_VariantParamSet(MRAC_AXIS_Y, MRAC_VF_REF_TYPE,     r->ref_type);
		ok &= MRAC_VariantParamSet(MRAC_AXIS_Y, MRAC_VF_REF_MODEL_BW, r->bw);
		ok &= MRAC_VariantParamSet(MRAC_AXIS_Y, MRAC_VF_CRM_ELL,      r->crm_ell);
		ok &= MRAC_VariantParamSet(MRAC_AXIS_Y, MRAC_VF_GAMMA_SCALE,  r->g);
		if (!ok) {
			res = 0xEEU;
		}
	}
	exp_active = res;
}

/* Keil debug manual motor override (dbg_motor_manual / dbg_motor_ccr, see their definition).
 * DISARMED-only. Returns 1 when it owned the motors this tick. */
static uint8_t Motor_DebugOverride(FlightState_t state)
{
	if (dbg_motor_manual)
	{
		if (state != FLIGHT_STATE_DISARMED)
		{
			dbg_motor_manual = 0U;
			Set_Zero_Motors();
			return 1U;
		}
		mymotor.motor1 = (short)dbg_motor_ccr[0];
		mymotor.motor2 = (short)dbg_motor_ccr[1];
		mymotor.motor3 = (short)dbg_motor_ccr[2];
		mymotor.motor4 = (short)dbg_motor_ccr[3];
		Set_PWM_Motors();
		return 1U;
	}
	return 0U;
}

/* Spool-down scale: the part of a motor command above ZERO times k (Set_PWM_Motors clamps). */
static short Land_SpoolScale(short m, float k)
{
	return (short)((float)Motor_PWM_ZERO + k * (float)(m - Motor_PWM_ZERO));
}

/* ARMED + LANDING: drive the motors, then the touchdown detector; on touchdown the spool-down
 * (LAND_SPOOL_TICKS), then LANDED + disarm. */
static void Land_Step(void)
{
	if (!s_land_init)
	{
		s_stable_ticks = 0;
		s_land_rest_ticks = 0U;
		s_land_timeout = 0U;
		s_land_spool   = 0U;
		s_land_init    = 1U;
	}

	/* Spool-down: Mix_Compute rewrote mymotor this tick, so scaling in place does not compound.
	 * Des_VHeight holds the vz command at 0 meanwhile, and the attitude terms shrink with thrust.
	 * (The old note here, "no motor spike when LANDED fires", was wrong: the 10-06 capture shows
	 * ~90% of hover on the skids right up to the cut.) */
	if (s_land_spool != 0U)
	{
		float k = 1.0f - (float)s_land_spool / (float)LAND_SPOOL_TICKS;
		mymotor.motor1 = Land_SpoolScale(mymotor.motor1, k);
		mymotor.motor2 = Land_SpoolScale(mymotor.motor2, k);
		mymotor.motor3 = Land_SpoolScale(mymotor.motor3, k);
		mymotor.motor4 = Land_SpoolScale(mymotor.motor4, k);
		if (s_land_spool < LAND_SPOOL_TICKS)
		{
			s_land_spool++;
			Set_PWM_Motors();
			return;
		}
		s_stable_ticks    = 0;
		s_land_rest_ticks = 0U;
		s_land_timeout    = 0U;
		s_land_spool      = 0U;
		s_land_init       = 0U;
		s_land_sink_bias  = 0.0f;
		flight_phase      = FLIGHT_PHASE_LANDED;
		FlightFSM_Event(FLIGHT_EVENT_DISARM_REQUEST);
		Set_Zero_Motors();
		return;
	}

	/* Rate-mode descent (LAND_VZ_MPS / LAND_VZ_FAST_MPS in Des_VHeight); Throttle_out drives the
	 * motors to the ground. */
	Set_PWM_Motors();

	/* Touchdown detection: rate stable for 0.25 s AND near ground.
	 * Below 0.20 m a 0.15 m/s sink bias is active, so widen threshold to
	 * 0.08 m/s — the drone decelerates through that on contact.
	 * Above 0.20 m keep tight (0.02 m/s) to avoid false triggers mid-descent.
	 * FIX 2026-10-02: also require the Z setpoint ramp to have reached the floor.
	 * hover_7 and unique_position1 disarmed at 0.13-0.14 m while floating on the
	 * ground-effect cushion (vz ~0), then fell 4-8 cm with motors off. Gated, the
	 * existing sink bias pushes through the cushion and motors cut on the ground.
	 * FIX 2026-10-02b: Des <= 0.01 did not stop it (auto_landing_1 F2-F5 still cut at
	 * 0.11-0.14 m: Des leads FB, so Des hits the floor while still airborne).
	 * Now also require ground evidence (PX4 land detector style), either:
	 *   (a) FB within 0.03 m of the rest height read before takeoff
	 *       (measured rest 0.00-0.07 m, so mid-air cuts at 0.11-0.14 m are blocked), or
	 *   (b) sink bias saturated: 0.40 m/s commanded descent not achieved for ~2 s.
	 * (b) covers a rest reading that shifts after touchdown (F3: 0.06 pre, 0.10 post).
	 * Safety net: force disarm after LAND_MAX_TICKS (15 s) regardless.
	 * FIX 2026-10-06: every gate starts the spool-down; the cut happens at its end. */
	{
		float rate_thr = (Ctrler.Z_posPID.FB < LAND_RATE_SLOW_ALT_M) ? LAND_RATE_THR_LOW : LAND_RATE_THR_HIGH;
		if (fabsf(Ctrler.Z_ratePID.FB) < rate_thr)
			s_stable_ticks++;
		else
			s_stable_ticks = 0;
	}

	if (Ctrler.Z_posPID.Des <= DES_FLOOR_M &&
	    Ctrler.Z_posPID.FB <= s_land_rest_z + LAND_REST_MARGIN)
		s_land_rest_ticks++;
	else
		s_land_rest_ticks = 0U;

	s_land_timeout++;

	if (s_land_rest_ticks >= LAND_REST_CUT_TICKS ||
	    (s_stable_ticks >= LAND_STABLE_TICKS && Ctrler.Z_posPID.FB < LAND_CUT_ALT_M &&
	     Ctrler.Z_posPID.Des <= DES_FLOOR_M &&
	     (Ctrler.Z_posPID.FB <= s_land_rest_z + LAND_REST_MARGIN ||
	      s_land_sink_bias >= LAND_SINK_BIAS_MAX)) ||
	    s_land_timeout >= LAND_MAX_TICKS)
	{
		s_land_spool = 1U;   /* this tick ran unscaled; the ramp starts next tick */
		/* F9 2026-10-06, PX4 style (on ground contact the thrust command goes to zero instead of
		 * coming from the climb loop): hold this tick's collective, capped at hover, for the whole
		 * spool; the ramp above fades it to ZERO. F7 motor avg rose 3085 -> 3139 at spool start:
		 * vzDes 0 against the vzFB frozen on the ground at -0.33 m/s made the Z loop add thrust. */
		s_land_spool_thr = (Throttle_out < (float)Throttle_th) ? Throttle_out : (float)Throttle_th;
	}
}

/* ARMED + GROUND_IDLE: zero until the idle gesture, then rest-height sampling, takeoff detection
 * and idle/PWM output. */
static void GroundIdle_Step(void)
{
	if (!g_motor_idle_enabled)
	{
		/* ARMED but idle not yet enabled: motors at zero, integrators
		 * cleared so they do not wind up while waiting for the idle
		 * gesture.  Takeoff detection is impossible in this state. */
		Clear_Structure();
		Set_Zero_Motors();
	}
	else
	{
		/* Rest height for the touchdown gate: sample while motors idle on the ground.
		 * FB < 0.15 keeps a hand-lifted reading out, so the gate is never looser
		 * than the FB < 0.15 term in Land_Step. */
		if (!TWC.execute && RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX &&
		    Ctrler.Z_posPID.FB < GROUND_REST_MAX_M)
			s_land_rest_z = (Ctrler.Z_posPID.FB > LAND_REST_MIN) ?
			                Ctrler.Z_posPID.FB : LAND_REST_MIN;

		/* Auto-detect takeoff: transition to FLYING once the OF sensor reads > 0.2 m above
		 * the zeroed height. Bench mode (CMD 0x07) does NOT block this — the height
		 * offset is captured on bench activation so of2_h is already relative to the fixture
		 * resting height. The controller therefore does not fight prop wash at any altitude.
		 * Throttle_cap_active (CMD 0x08) is also independent and does not affect this. */
		/* P0 Finding 3: height alone must not leave GROUND_IDLE — a slow hand-lift of an
		 * armed aircraft ramps of2_h past every alt gate, so gate the transition itself
		 * on THR >= 20% or an executing TWC policy, matching the IDLE interlock below. */
		if (Ctrler.Z_posPID.FB > TAKEOFF_ALT_M &&
		    (TWC.execute || RCInput_Get(RC_AXIS_THR) >= THR_IDLE_MAX))
			flight_phase = FLIGHT_PHASE_FLYING;

		/* IDLE motors: hold until pilot or policy pushes THR above 20%. */
		if (!TWC.execute && RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX)
			Set_IDLE_Motors();
		else if (SDK_DelayWakeFlag == 1)
			Set_IDLE_Motors();
		else
			Set_PWM_Motors();
	}
}

/* EMERGENCY and DISARMED: forget any landing in progress. */
static void Land_ResetDetector(void)
{
	s_land_init      = 0U;
	s_stable_ticks   = 0;
	s_land_timeout   = 0U;
	s_land_spool     = 0U;
	s_land_sink_bias = 0.0f;
}

void Update_Motor(void)
{
	FlightState_t state;

	Bench_ZeroHeight();

	state = FlightFSM_GetState();

	if (Motor_TestOverride(state)) return;

	Exp_Poll(state);
	Keil_PresetPoll(state);
	Keil_VariantPoll(state);
	Lab_StagePoll(state);
	Limit_ModePoll(state);
	if (Motor_DebugOverride(state)) return;

	if (state == FLIGHT_STATE_ARMED)
	{
		if (flight_phase == FLIGHT_PHASE_LANDING)
		{
			Land_Step();
		}
		else if (flight_phase == FLIGHT_PHASE_GROUND_IDLE)
		{
			GroundIdle_Step();
		}
		else if (flight_phase == FLIGHT_PHASE_FLYING)
		{
			/* Normal FLYING state: drive motors. Bench mode height zero is already applied
			 * at the top of this function so the controller stays calm throughout. */
			Set_PWM_Motors();
		}
		/* FLIGHT_PHASE_LANDED: disarm was issued this tick; do nothing — motors
		 * were already zeroed by the LANDING block that triggered this transition. */
	}
	else if (state == FLIGHT_STATE_EMERGENCY)
	{
		Land_ResetDetector();
		/* FIX 2026-09-13 (autonomous-debug Tier 1.2): zero PID integrators every tick
		 * in EMERGENCY. Without this, gyro-bias feedthrough and steady-state OF drift
		 * wind roll/pitch/yaw SumE to the ±SumEMax clamp during long bench sits in
		 * DangerousStop (sbus_lost or RC mode switch LOW). The transition path
		 * EMERGENCY→DISARMED does call Clear_Structure on the first DISARMED tick so
		 * arming after recovery is safe — but in-EMERGENCY windup is wasted CPU and
		 * makes cold-boot symptoms harder to diagnose (SumE != 0 in EMERGENCY masks
		 * the absence of a real fix in some metrics). Mirrors the DISARMED branch. */
		Clear_Structure();
		Set_Zero_Motors();
	}
	else   /* DISARMED */
	{
		Land_ResetDetector();
		TWC.execute = 0U;
		sbus_flyup_trigger = 0U;
		SDK_StateMachine_Init();
		Clear_Structure();
		Set_Zero_Motors();
	}
}

/* ==== Compute_Motor: setpoints, the PID/MRAC cascade, the mixer ==================================== */
/* WP-9 hover means in the controller frame (pitchPID frame = -imu_pit), operator zeroes them if the
 * rotated-takeoff flight shows the lean is the room. */
volatile float g_att_trim_roll_deg = -1.24f;
volatile float g_att_trim_pitch_deg = -0.90f;
volatile uint8_t g_traj_ff_on = 1U;
static TrajFF_t s_traj_ff;
/* Preset trajectories (AutoflyTask traj_id) feed their reference rates forward: xy velocity, climb rate and
 * yaw rate. 0 = the old pure-position reference (the P loops then trail by v / Kp). */
volatile uint8_t g_traj_ff_preset = 1U;
extern float g_traj_vff_x, g_traj_vff_y, g_traj_vff_z, g_traj_vff_yaw;
/* xy outer controller (API/xy_ctrl.c, VAR_WRITE id 44): 0 PID cascade, 1 ADRC, 2 LQR, 3 MPC. Latched while
 * not airborne. g_traj_ff_shift_s (id 45): position-loop reference lag behind the FF, s (0 = off). */
volatile uint8_t g_xy_ctrl_kind = 0U;
volatile float g_traj_ff_shift_s = 0.0f;
/* Camera-gated landing (API/land_xy_gate.c, VAR_WRITE id 46): with fusion 2/3, the ground-station land does not
 * start its descent until the camera-fused xy is within this radius of the pad (origin) on a fresh fix, cm
 * (0 = off). Read in Wfb_Step; the descent itself is unchanged. */
volatile float g_land_xy_gate_cm = 0.0f;
/* Smith-predictor delay of the LQR/MPC xy loops, 10 ms ticks (VAR_WRITE id 47, latched on the ground with the kind).
 * 2026-10-15: the gains assume 33; a shorter real delay (camera stale, flow only) limit-cycled LQR/MPC at ~2 Hz in
 * ground_station/analysis/xy_delay_sim.py. Default 12 is PROPOSED from that sim, not measured. */
volatile uint8_t g_xyc_delay_ticks = 12U;
static uint8_t s_xyc_kind;
static XyCtrlState s_xyc[2];
#define TRAJ_FF_VZ_MAX_MPS     1.0f     /* = Z_posPID UMax, the climb-rate the position loop may ask for */
/* Trajectory feed-forward (TrajFF_Step) on the xy velocity loops. Vmax for the FF clamp: the v_max_mps
 * of wfb_traj_default_limits (API/wfb_traj.c) x 100 = 100.0f cm/s. */
#define TRAJ_FF_DT_S           0.01f    /* the xy loops run every 2nd tick */
#define TRAJ_FF_TAU_S          0.2f
#define TRAJ_FF_VMAX_CMS       100.0f
#define OF_PRE_EMA_ALPHA       0.005f   /* 1 s EMA of the resting OF velocity at 200 Hz */
#define PWM_PCT_BASE           2000.0f  /* CCR at 0 % (gs_throttle_min/max_pct are 0..1) */
#define PWM_PCT_SPAN           2000.0f  /* CCR per 100 % */
#define GRAVITY_STD_MPS2       9.80665f

/* WP-21 B ground contact: in LANDING with the Z ramp at the floor, low and slow for
 * LAND_CONTACT_TICKS. Returns 1 while in contact (the xy integrators are then held). */
static uint8_t Land_ContactStep(void)
{
	static uint8_t s_land_contact_cnt = 0U;   /* WP-21 B, see LAND_CONTACT_* */
	if (flight_phase == FLIGHT_PHASE_LANDING && Ctrler.Z_posPID.Des <= DES_FLOOR_M &&
	    Ctrler.Z_posPID.FB < LAND_CONTACT_ALT && fabsf(Ctrler.Z_ratePID.FB) < LAND_CONTACT_VZ) {
		if (s_land_contact_cnt < LAND_CONTACT_TICKS) s_land_contact_cnt++;
	} else {
		s_land_contact_cnt = 0U;
	}
	return (uint8_t)(s_land_contact_cnt >= LAND_CONTACT_TICKS);
}

/* Height (every 2nd tick) and climb-rate loops. P0 Finding 2: while the ToF sample is invalid
 * (s_alt_valid_tick == 0) each integrator is restored after ComputePID, so P/D run on the held FB. */
static void Z_Compute(void)
{
	Update_Des(case_Update_height_Des);
	cnt_h++;
	if(cnt_h>=POS_LOOP_DIV)
	{
		{   /* P0 Finding 2: ToF out of range - freeze the Z position integrator
		     (its FB is the frozen height, so letting SumE run would wind the
		     cascade that feeds Z_ratePID.Des). */
			float zp_sumE = Ctrler.Z_posPID.SumE;
			float zp_Ui   = Ctrler.Z_posPID.Ui;
			float ez = SysID_GetPosSetpoint(SYSID_AXIS_Z, Excite_Get(EXC_Z, EXC_OUTER));   /* pattern library + sysid target 4: add then undo */
			Ctrler.Z_posPID.Des += ez;
			ComputePID(&Ctrler.Z_posPID);
			Ctrler.Z_posPID.Des -= ez;
			if (!s_alt_valid_tick) { Ctrler.Z_posPID.SumE = zp_sumE; Ctrler.Z_posPID.Ui = zp_Ui; }
		}
		cnt_h=0;
	}
	Update_Des(case_Update_v_h_Des);
	{   /* P0 Finding 2: ToF out of range - freeze the Z-rate integrator while
	     still running P/D on the held FB, so the manual height-rate loop cannot
	     wind throttle up without bound (verdict: unbounded climb at >5 m AGL). */
		float zr_sumE = Ctrler.Z_ratePID.SumE;
		float zr_Ui   = Ctrler.Z_ratePID.Ui;
		float evz = Excite_Get(EXC_Z, EXC_INNER);
		Ctrler.Z_ratePID.Des = SysID_GetVelSetpoint(SYSID_AXIS_Z, Ctrler.Z_ratePID.Des) + evz;
		ComputePID(&Ctrler.Z_ratePID);
		Ctrler.Z_ratePID.Des -= evz;
		if (!s_alt_valid_tick) { Ctrler.Z_ratePID.SumE = zr_sumE; Ctrler.Z_ratePID.Ui = zr_Ui; }
	}
}

/* FIX 2026-09-13: ARM rising-edge world-origin rebase.
 * The cold-boot sync (Cal_EstimatorReadyEdge) syncs Des=FB at the
 * rising edge of g_estimator_ready — but that fires at boot, before
 * the OF has integrated any drift. After a long disarmed sit on the
 * bench, FB can drift to ~+200/-188 cm while the drone is physically
 * still. Arming then opens the position loop with a -195/+188 error,
 * which immediately saturates the SumE and flies the drone into a
 * wall. Fix it the same way the dashboard "Reset World Origin" button
 * does (CMD 0x10): on the ARM 0→1 edge, rebase FB/Des/OF bias to zero
 * so the position loop opens with zero error and the drone physically
 * holds until the pilot commands throttle. The pilot can still hit
 * the dashboard button mid-flight to re-anchor the path.
 * Runs every 2nd tick, armed or not. Also: the resting-OF average (g_of_rest_warn) and the
 * handheld-test enable edge. */
static void Pos_ArmEdge(void)
{
	static uint8_t s_prev_armed = 0U;
	/* FIX 2026-09-26: OF is integer counts, so a single-sample bias
	 * snap leaves up to 1 count residual that integrates to ~1.4 cm/s
	 * drift. While disarmed, average OF with a 1 s EMA and snap to that. */
	static float s_of_pre_x = 0.0f, s_of_pre_y = 0.0f;
	static uint8_t s_of_pre_ok = 0U;
	static uint8_t s_prev_handheld = 0U;
	uint8_t armed_now = (DroneStatus.ARM_Status == Armed) ? 1U : 0U;
	/* FIX 2026-09-26: keep averaging while armed on the ground too and
	 * track the Mode 0 bias with it, so the bias locks at takeoff. */
	uint8_t on_ground = (!armed_now || (flight_phase != FLIGHT_PHASE_FLYING &&
	                     flight_phase != FLIGHT_PHASE_LANDING)) ? 1U : 0U;
	if (!on_ground) g_of_handheld_test = 0U; /* never carried into flight */
	/* FIX 2026-09-27: GROUND_IDLE lasts until of2_h > 0.2 m, so the drone
	 * is already airborne and moving while still "on_ground". Averaging then
	 * locked the lift-off velocity into the Mode 0 bias, and the position
	 * loop held that velocity as "zero" for the whole flight (slow one-way
	 * drift). Average only while motors are off or at idle (THR < 20%). */
	/* FIX 2026-10-02 (hover_5): props spinning at idle on the ground made the
	 * OF read -1 cm/s y for 2.4 s with the drone still, and this average
	 * locked -0.66 as the bias, so the hold flew that fake velocity all
	 * flight. Average only while the motors are stopped. */
	if (armed_now && (g_motor_idle_enabled || TWC.execute ||
	                  RCInput_Get(RC_AXIS_THR) >= THR_IDLE_MAX))
		on_ground = 0U;
	if (on_ground && !g_of_handheld_test && ano_of.of_quality >= OF_MIN_QUALITY) {
		if (!s_of_pre_ok) {
			s_of_pre_x = (float)ano_of.of2_dx_fix;
			s_of_pre_y = (float)ano_of.of2_dy_fix;
			s_of_pre_ok = 1U;
		} else {
			s_of_pre_x += OF_PRE_EMA_ALPHA * ((float)ano_of.of2_dx_fix - s_of_pre_x);
			s_of_pre_y += OF_PRE_EMA_ALPHA * ((float)ano_of.of2_dy_fix - s_of_pre_y);
		}
		g_of_rest_warn = (fabsf(s_of_pre_x) > OF_REST_WARN_CMS
		               || fabsf(s_of_pre_y) > OF_REST_WARN_CMS) ? 1U : 0U;
	}
	if (armed_now && !s_prev_armed) {
		Reset_World_Origin();
		/* Also clear any position-PID integral the previous session
		 * left behind: with Des=FB=0, the SumE the only thing that
		 * can drive a step jump on the first armed tick. */
		Ctrler.locxPID.SumE  = 0.0f;
		Ctrler.locyPID.SumE  = 0.0f;
		Ctrler.locxsPID.SumE = 0.0f;
		Ctrler.locysPID.SumE = 0.0f;
		/* FIX 2026-09-13 (Tier 2): bias auto-snap on ARM 0→1.
		 * Reset_World_Origin() zeroes s_of_bias_x/y to satisfy
		 * the "position re-drift within 30 s" fix. Re-snap from the
		 * current OF sample so the EMA starts from a clean value.
		 * In Mode 2 (EKF): re-initialize the KF velocity/bias from
		 * the current OF reading so the position estimate is warm
		 * at ARM rather than cold-starting from (0,0,0,0).
		 * Gated on OF quality. */
		/* FIX 2026-10-03: no ground snap. The resting OF value is a stale
		 * repeat, not a bias (good flights read 0 at rest and ~0-1 cm/s in
		 * hover; the drift flights read 3,5 and 2,3 at rest). Start every
		 * flight from the trim: s_of_bias = OF_BIAS_TRIM_* (Reset_World_Origin) and
		 * KF bof = 0 with a tight variance, so the hold flies true zero flow. */
		s_of_bias_seeded = 1U;
		if (s_ekf_of_inited) {
			EkfOf_ResetBias(&s_ekf_of, EKF_OF_BOF_ARM_VAR);
			s_ekf_of.x[1] = 0.0f;
			s_ekf_of.x[4] = 0.0f;
		}
		/* Clear EKF fallback sticky flag on ARM 0→1 edge so the
		 * dashboard sees a clean state for each new flight. */
		g_ekf_of_fallback = 0U;
		s_ekf_of_innov_bad_cnt = 0U;
		s_ekf_of_tripped = 0U;
		g_ekf_of_health = 1U;
		if (s_ekf_of_mode_restore) {
			s_ekf_of_mode_restore = 0U;
			g_of_bias_mode = OF_BIAS_EKF;   /* mode-2 tick re-inits the KF if shadow was off */
		}
	}
	/* Handheld test enabled: zero the origin and the bias (same rule as ARM;
	 * the resting OF value is stale, not a bias). Hold still when enabling. */
	if (g_of_handheld_test && !s_prev_handheld) {
		Reset_World_Origin();
		if (s_ekf_of_inited) {
			EkfOf_ResetBias(&s_ekf_of, EKF_OF_BOF_ARM_VAR);
			s_ekf_of.x[1] = 0.0f;
			s_ekf_of.x[4] = 0.0f;
		}
	}
	s_prev_handheld = g_of_handheld_test;
	s_prev_armed = armed_now;
}

/* RC fallback (2026-10-15, lab-friction C): configurable aux switch channel (g_rc_pid_fallback_ch 1..16,
 * 0 = disabled) > FALLBACK_ON_MIN for FALLBACK_DEBOUNCE_TICKS ticks in a row, or direct command
 * (g_ctrl_pid_fallback != 0) while armed, latches fallback until disarm.
 * On transition edge: s_xyc_kind is forced to XY_CTRL_PID and xy_ctrl_reset() is called for bumpless
 * hand-off (the PID cascade is already warm). Attitude correction fades out to CTRL_PID (controller.c).
 * Motors stay on; operator lands by hand. Status exposed via g_ctrl_pid_fallback_active & g_rc_fallback. */
volatile uint8_t g_rc_pid_fallback_ch = 0U;
volatile uint8_t g_ctrl_pid_fallback = 0U;
volatile uint8_t g_ctrl_pid_fallback_active = 0U;
volatile uint8_t g_rc_fallback = 0U;

static PidFallbackState s_pid_fallback = {0U, 0U};

static void xy_ctrl_reset(void)
{
	XyCtrl_Reset(&s_xyc[0]);
	XyCtrl_Reset(&s_xyc[1]);
}

static void Rc_FallbackStep(void)
{
	uint8_t armed = (uint8_t)(DroneStatus.ARM_Status == Armed);
	uint8_t edge = PidFallback_Step(&s_pid_fallback,
	                                armed,
	                                g_ctrl_pid_fallback,
	                                g_rc_pid_fallback_ch,
	                                sbus_lost,
	                                (const uint16_t *)sbus_channel);
	if (edge) {
		s_xyc_kind = XY_CTRL_PID;
		xy_ctrl_reset();
	}
	g_ctrl_pid_fallback_active = s_pid_fallback.latched;
	g_rc_fallback = s_pid_fallback.latched;
	g_ctrl_fallback = s_pid_fallback.latched;
	if (!armed) {
		g_ctrl_pid_fallback = 0U;
	}
}

/* xy position loops, then the velocity loops with the trajectory feed-forward. Armed only.
 * land_contact: touchdown, no xy lean and the integrators cleared. land_xy_hold: landing below LAND_XY_HOLD_ALT,
 * the loops stay closed with their integrators held and the lean clamped (API/land_profile.h, 2026-10-19). */
static void Pos_Compute(uint8_t airborne, uint8_t land_contact, uint8_t land_xy_hold)
{
	uint8_t hold = (uint8_t)(land_contact | land_xy_hold);
	uint8_t active = g_traj_ff_on && airborne && TWC.execute && g_wfb_status.traj_state == (float)WFB_TRAJ_EXECUTING && g_wfb_status.prim_state == (float)WFB_PRIM_TRAJ;
	float vx, vy, ax, ay, vff_x, vff_y;
	uint8_t xyc_on[2];
	/* the reference velocity (trajectory FF + preset FF), computed first so the position loops can use it */
	TrajFF_Step(&s_traj_ff, active, TWC.target_x, TWC.target_y, TRAJ_FF_DT_S, TRAJ_FF_TAU_S, TRAJ_FF_VMAX_CMS, &vx, &vy, &ax, &ay);
	vff_x = vx + ((g_traj_ff_preset && airborne) ? g_traj_vff_x : 0.0f);
	vff_y = vy + ((g_traj_ff_preset && airborne) ? g_traj_vff_y : 0.0f);

	Update_Des(case_Update_loc_Des);
	{
		/* FF lead (2026-10-14, xy_tune): the position loops compare with the reference g_traj_ff_shift_s ago,
		 * so the velocity FF leads them by the plant lag instead of both pushing (0 = the loops before). */
		float sx = g_traj_ff_shift_s * vff_x, sy = g_traj_ff_shift_s * vff_y;
		sx -= SysID_GetPosSetpoint(SYSID_AXIS_X, Excite_Get(EXC_X, EXC_OUTER));   /* pattern library + sysid target 4: add then undo */
		sy -= SysID_GetPosSetpoint(SYSID_AXIS_Y, Excite_Get(EXC_Y, EXC_OUTER));
		Ctrler.locxPID.Des -= sx;
		Ctrler.locyPID.Des -= sy;
		ComputePID_GatedHold(&Ctrler.locxPID, airborne, hold);
		ComputePID_GatedHold(&Ctrler.locyPID, airborne, hold);
		Ctrler.locxPID.Des += sx;
		Ctrler.locyPID.Des += sy;
	}

	Update_Des(case_Update_v_loc_Des);
	SDK_Set_V_Loc();   /* SDK velocity override (AutoflyTask) */
	/* xy_ctrl flies an axis only while its velocity setpoint is the position loop's own output (not the RC stick,
	 * an SDK velocity, or the VXY clamp); otherwise the PID flies and xy_ctrl tracks. Kind latched on the ground;
	 * the RC fallback forces the PID in flight (the PID cascade runs every tick, so it is warm). */
	if (!airborne || land_contact || g_ctrl_pid_fallback_active) {
		s_xyc_kind = (g_xy_ctrl_kind <= XY_CTRL_MPC && !g_ctrl_pid_fallback_active) ? g_xy_ctrl_kind : XY_CTRL_PID;
		if (!airborne && g_xyc_delay_ticks != XyCtrl_Delay())
			XyCtrl_SetDelay(g_xyc_delay_ticks);
		if (!airborne) {
			MRAC_XyRefSelect(g_xy_ctrl_kind);   /* item 4: the MRAC x/y reference row of the selected nominal */
			MRAC_XyLayersLatch();               /* item 5: the x/y MRAC layers (CMD 0x1D field 31) fly from takeoff */
			MRAC_GroupLayersLatch();            /* all-loop item 1: att / yaw / z layers (fields 33-35) */
		}
		if (!g_ctrl_pid_fallback_active) {
			xy_ctrl_reset();
		}
	}
	xyc_on[0] = s_xyc_kind && airborne && !hold && Ctrler.locxsPID.Des == Ctrler.locxPID.U;
	xyc_on[1] = s_xyc_kind && airborne && !hold && Ctrler.locysPID.Des == Ctrler.locyPID.U;

	{
		/* item 5: outer x/y MRAC position layer, cm/s (m and m/s inside). PID path only: it tracks while xy_ctrl
		 * flies, in hold, or when the velocity demand is not the position PID's. Added only when OUTER is live. */
		float pos_u[2];
		pos_u[0] = 100.0f * MRAC_PosStep(0U, (uint8_t)(!xyc_on[0] && airborne && !hold && Ctrler.locxsPID.Des == Ctrler.locxPID.U),
		                                 0.01f * Ctrler.locxPID.Des, 0.01f * Ctrler.locxPID.FB,
		                                 0.01f * Ctrler.locxsPID.FB, 0.01f * Ctrler.locxsPID.Des);
		pos_u[1] = 100.0f * MRAC_PosStep(1U, (uint8_t)(!xyc_on[1] && airborne && !hold && Ctrler.locysPID.Des == Ctrler.locyPID.U),
		                                 0.01f * Ctrler.locyPID.Des, 0.01f * Ctrler.locyPID.FB,
		                                 0.01f * Ctrler.locysPID.FB, 0.01f * Ctrler.locysPID.Des);
		Ctrler.locxsPID.Des = SysID_GetVelSetpoint(SYSID_AXIS_X, Ctrler.locxsPID.Des + vx + Excite_Get(EXC_X, EXC_INNER));   /* inner pattern + sysid target 3 */
		Ctrler.locysPID.Des = SysID_GetVelSetpoint(SYSID_AXIS_Y, Ctrler.locysPID.Des + vy + Excite_Get(EXC_Y, EXC_INNER));
		if (((unsigned)mrac_xy_layers_live & MRAC_XY_LAYER_OUTER) != 0U) {
			Ctrler.locxsPID.Des += pos_u[0];
			Ctrler.locysPID.Des += pos_u[1];
		}
		if (g_traj_ff_preset && airborne) {
			Ctrler.locxsPID.Des = fmaxf(-g_vxy_des_max, fminf(g_vxy_des_max, Ctrler.locxsPID.Des + g_traj_vff_x));
			Ctrler.locysPID.Des = fmaxf(-g_vxy_des_max, fminf(g_vxy_des_max, Ctrler.locysPID.Des + g_traj_vff_y));
		}
		ComputePID_GatedHold(&Ctrler.locxsPID, airborne, hold);
		ComputePID_GatedHold(&Ctrler.locysPID, airborne, hold);
		if (xyc_on[0]) {
			float u = XyCtrl_Step(&s_xyc[0], s_xyc_kind, SysID_GetPosSetpoint(SYSID_AXIS_X, Ctrler.locxPID.Des + Excite_Get(EXC_X, EXC_OUTER)), Ctrler.locxPID.FB, Ctrler.locxsPID.FB, vff_x);
			if (isfinite(u)) Ctrler.locxsPID.U = u; else XyCtrl_Reset(&s_xyc[0]);
		} else if (s_xyc_kind) XyCtrl_Track(&s_xyc[0], s_xyc_kind, Ctrler.locxPID.FB);
		if (xyc_on[1]) {
			float u = XyCtrl_Step(&s_xyc[1], s_xyc_kind, SysID_GetPosSetpoint(SYSID_AXIS_Y, Ctrler.locyPID.Des + Excite_Get(EXC_Y, EXC_OUTER)), Ctrler.locyPID.FB, Ctrler.locysPID.FB, vff_y);
			if (isfinite(u)) Ctrler.locysPID.U = u; else XyCtrl_Reset(&s_xyc[1]);
		} else if (s_xyc_kind) XyCtrl_Track(&s_xyc[1], s_xyc_kind, Ctrler.locyPID.FB);
		Ctrler.locxsPID.U += ax;
		Ctrler.locysPID.U += ay;
		/* 6-axis MRAC (2026-10-10): keep the nominal accel (PID or xy_ctrl) for the x/y layers' u_nom, then add u_ad. Off unless
		 * axis_enable_x/y learns and g_ctrl_axis_mask bits 4/5 inject (both on at boot via kp row 7). */
		mrac_xy_unom[0] = Ctrler.locxsPID.U;
		mrac_xy_unom[1] = Ctrler.locysPID.U;
		Ctrler.locxsPID.U = Controller_Update(CTRL_AXIS_X, Ctrler.locxsPID.U);
		Ctrler.locysPID.U = Controller_Update(CTRL_AXIS_Y, Ctrler.locysPID.U);
		if (land_xy_hold) {   /* low-authority hold to touchdown: bounds a false-velocity chase near the floor */
			Ctrler.locxsPID.U = fmaxf(-LAND_XY_HOLD_UMAX, fminf(LAND_XY_HOLD_UMAX, Ctrler.locxsPID.U));
			Ctrler.locysPID.U = fmaxf(-LAND_XY_HOLD_UMAX, fminf(LAND_XY_HOLD_UMAX, Ctrler.locysPID.U));
		}
	}

	/* F8 2026-10-06, PX4 style (MulticopterPositionControl: on ground contact the xy setpoint is
	 * cleared and the integrators reset): with the gear on the floor, stop correcting xy. Zero
	 * lean command, so the angle loop holds level instead of tilting the drone to chase an
	 * estimate that drifts on the ground (F7: P and D kept tilting the skids), and clear every
	 * xy integrator so nothing is wound up for the next takeoff. */
	if (land_contact)
	{
		Ctrler.locxPID.SumE  = 0.0f;
		Ctrler.locyPID.SumE  = 0.0f;
		Ctrler.locxsPID.SumE = 0.0f;
		Ctrler.locysPID.SumE = 0.0f;
		Ctrler.locxsPID.U    = 0.0f;
		Ctrler.locysPID.U    = 0.0f;
		mrac_xy_unom[0]      = 0.0f;
		mrac_xy_unom[1]      = 0.0f;
	}
	if (s_xyc_kind) {   /* the command the plant gets enters the predictor pipe */
		XyCtrl_Commit(&s_xyc[0], Ctrler.locxsPID.U);
		XyCtrl_Commit(&s_xyc[1], Ctrler.locysPID.U);
	}
}

/* Angle loops (pitch/roll gated on airborne, yaw), rate setpoints + SysID dither, rate loops. */
static void Att_Compute(uint8_t airborne)
{
	float ep = Excite_Get(EXC_PITCH, EXC_OUTER), er = Excite_Get(EXC_ROLL, EXC_OUTER), ey = Excite_Get(EXC_YAW, EXC_OUTER);

	Update_Des(case_Update_pitrol_Des);   /* pitch/roll angle setpoints */
	Ctrler.pitchPID.Des = SysID_GetAngleSetpoint(SYSID_AXIS_PITCH, Ctrler.pitchPID.Des) + ep;   /* pattern library + sysid target 2 */
	Ctrler.rollPID.Des  = SysID_GetAngleSetpoint(SYSID_AXIS_ROLL,  Ctrler.rollPID.Des)  + er;
	ComputePID_Gated(&Ctrler.pitchPID, airborne);
	ComputePID_Gated(&Ctrler.rollPID, airborne);
	Ctrler.pitchPID.Des -= ep;
	Ctrler.rollPID.Des  -= er;

	Update_Des(case_Update_yaw_Des);      /* yaw angle setpoint */
	Ctrler.yawPID.Des += ey;              /* persistent heading Des: undo after the loop */
	ComputeYawPID(&Ctrler.yawPID);
	Ctrler.yawPID.Des -= ey;

	Update_Des(case_Update_gyro_Des);     /* rate setpoints */

	SDK_Set_Gyroz();

	// SysID excitation (ADR-0004): tick the signal generator + safety FSM, then SUPERIMPOSE the
	// excitation onto the active axis's RATE setpoint with += (closed-loop SysID). The outer
	// angle/position cascade stays live, so position hold keeps the drone on station (it wiggles in
	// place instead of translating open-loop and walking out of the green zone). Pitch/roll/yaw here;
	// Z is not injected (SysID_Start rejects Z, ADR-0004 #1). sysid_target 1 skips this add (SysID_IsAxisActive
	// returns 0) and adds the excitation to the rate-loop output in Mix_Compute instead. No-op while the FSM is IDLE.
	// NOTE: the rate setpoint now = outer-loop output + dither, so the logged `r`/`u` are closed-loop
	// signals. Plant ID is done offline with the direct method (Phi_xu/Phi_uu) on u_nom+u_ad -> x.
	SysID_Update();
	if (SysID_IsAxisActive(SYSID_AXIS_PITCH)) Ctrler.gyroyPID.Des += SysID_GetRateSetpoint(SYSID_AXIS_PITCH);
	if (SysID_IsAxisActive(SYSID_AXIS_ROLL))  Ctrler.gyroxPID.Des += SysID_GetRateSetpoint(SYSID_AXIS_ROLL);
	if (SysID_IsAxisActive(SYSID_AXIS_YAW))   Ctrler.gyrozPID.Des += SysID_GetRateSetpoint(SYSID_AXIS_YAW);
	Ctrler.gyroyPID.Des += Excite_Get(EXC_PITCH, EXC_INNER);   /* pattern library (inner), 0 unless running */
	Ctrler.gyroxPID.Des += Excite_Get(EXC_ROLL, EXC_INNER);
	Ctrler.gyrozPID.Des += Excite_Get(EXC_YAW, EXC_INNER);

	/* FIX 2026-09-27: flight8 wound gyrozPID up to U=650 on the ground at idle
	 * (heading-hold Des with the airframe unable to turn). Hold the yaw integrals
	 * at 0 until lift-off throttle; same idle test as the OF bias averaging. */
	if (flight_phase != FLIGHT_PHASE_FLYING && flight_phase != FLIGHT_PHASE_LANDING &&
	    !TWC.execute && RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX)
	{
		Ctrler.gyrozPID.SumE = 0.0f;
		Ctrler.yawPID.SumE   = 0.0f;
	}

	ComputePID_Gated(&Ctrler.gyroxPID, airborne);
	ComputePID_Gated(&Ctrler.gyroyPID, airborne);
	ComputePID(&Ctrler.gyrozPID);
}

/* Controller layer, throttle window, quad-X mixer into mymotor (CCR; Set_PWM_Motors clamps later). */
static void Mix_Compute(void)
{
	 //Throttle_th=2800+(16.70f-real_voltage)*105.5f;  //4s+d435i+t265+orin
	 // Fixture (extendable metal) adds load. 3200 covers ~+250 g fixture at 4S full;
	 // free-flight hover 2950->3150: flight_test_pid_2/_4 (matched props, 2026-09-28) hovered at
	 // motor avg 3135-3145 with Z_rate P carrying the missing ~190 -> 0.4-0.5 m altitude sag.
	 // PWM ceiling is 4000 - anything above risks ESC saturation.
	 Throttle_th = bench_mode_active ? (short)HOVER_THR_BENCH : (short)HOVER_THR_FREE;

	// Controller layer (API/controller.c): u = u_nom + correction of the selected controller.
	// CTRL_MRAC (default) == PID + MRAC u_ad gated by output_injection_on and simplex fade; CTRL_PID == pure PID.
	Throttle_out = Controller_Update(CTRL_AXIS_Z, Ctrler.Z_ratePID.U) + Throttle_th;
	if (s_land_spool != 0U)
		Throttle_out = s_land_spool_thr;   /* touchdown spool: collective held, see Land_Step */
	// Sysid torque target (sysid_target 1): u = c + d, d = 0 unless a run is active on that axis and the drone is
	// FLYING (sysid.c SysID_GetTorque; an abort/RECOVERY or a land gives 0). d == 0 leaves u = c bit-exact.
	{
		uint8_t flying = (flight_phase == FLIGHT_PHASE_FLYING) ? 1U : 0U;
		uint8_t k;
		s_sysid_c[SYSID_AXIS_ROLL]  = Controller_Update(CTRL_AXIS_ROLL, Ctrler.gyroxPID.U);   /* call order as before */
		s_sysid_c[SYSID_AXIS_PITCH] = Controller_Update(CTRL_AXIS_PITCH, Ctrler.gyroyPID.U);
		s_sysid_c[SYSID_AXIS_YAW]   = Controller_Update(CTRL_AXIS_YAW, Ctrler.gyrozPID.U);
		for (k = 0U; k < 3U; k++) {
			s_sysid_d[k] = SysID_GetTorque((SysID_Axis_e)k, flying);
			s_sysid_u[k] = (s_sysid_d[k] != 0.0f) ? (s_sysid_c[k] + s_sysid_d[k]) : s_sysid_c[k];
		}
	}
	u_gyrox      = s_sysid_u[SYSID_AXIS_ROLL];
	u_gyroy      = -s_sysid_u[SYSID_AXIS_PITCH]; // Motor mixer needs gyroy reversed
	u_gyroz      = s_sysid_u[SYSID_AXIS_YAW];
	{
		float pwm_lo = PWM_PCT_BASE + gs_throttle_min_pct * PWM_PCT_SPAN;
		float pwm_hi = PWM_PCT_BASE + gs_throttle_max_pct * PWM_PCT_SPAN;
		if (pwm_hi < pwm_lo) {
			float t = pwm_hi;
			pwm_hi = pwm_lo;
			pwm_lo = t;
		}
		Throttle_out = Constrain_Float(Throttle_out, pwm_lo, pwm_hi);
	}

	// CONSTRAINT: the motor signs live in one place, the MIX_ROW table in API/controller.c (also used by the
	// MRAC V2 saturation deficit). Keep it in sync with the physical motor map and pwm.h channel mapping.
	// WHY: Sign or channel drift here can invert closed-loop attitude response.
	// g_yaw_mix_dir (+-1) is read once per motor, as before.
	mymotor.motor1 = Mix_Motor(0U, Throttle_out, u_gyroy, u_gyrox, g_yaw_mix_dir*u_gyroz);   /* M1 CW  */
	mymotor.motor2 = Mix_Motor(1U, Throttle_out, u_gyroy, u_gyrox, g_yaw_mix_dir*u_gyroz);   /* M2 CW  */
	mymotor.motor3 = Mix_Motor(2U, Throttle_out, u_gyroy, u_gyrox, g_yaw_mix_dir*u_gyroz);   /* M3 CCW */
	mymotor.motor4 = Mix_Motor(3U, Throttle_out, u_gyroy, u_gyrox, g_yaw_mix_dir*u_gyroz);   /* M4 CCW */
}

/* Shadow thrust estimators (200 Hz, after motor mixer, before Set_PWM_Motors).
 * Three models: empirical (PWM→thrust bench LUT), blade-element (RPM→thrust),
 * IMU-derived (accel projection). Telemetry-only, no control feedback. */
static void ThrustEst_Step(void)
{
	float motor_pwm[4];
	uint16_t motor_rpm[4];

	motor_pwm[0] = (float)mymotor.motor1;
	motor_pwm[1] = (float)mymotor.motor2;
	motor_pwm[2] = (float)mymotor.motor3;
	motor_pwm[3] = (float)mymotor.motor4;

	motor_rpm[0] = RPM_Get(0);
	motor_rpm[1] = RPM_Get(1);
	motor_rpm[2] = RPM_Get(2);
	motor_rpm[3] = RPM_Get(3);

	ThrustEst_Update(motor_pwm, motor_rpm, Lin_Acc_Z_body * GRAVITY_STD_MPS2 / MG_PER_G, imu_data.pit, imu_data.rol);
}

void Compute_Motor(void)
{
	uint8_t airborne = (flight_phase == FLIGHT_PHASE_FLYING || flight_phase == FLIGHT_PHASE_LANDING);
	uint8_t land_contact = (uint8_t)(Land_ContactStep() | (s_land_spool != 0U));   /* spool = touchdown too */
	uint8_t land_xy_hold = LandProfile_XyHold(flight_phase == FLIGHT_PHASE_LANDING, Ctrler.Z_posPID.FB);   /* 10-19 */
	land_contact |= LandProfile_XyFreeze(flight_phase == FLIGHT_PHASE_LANDING, Ctrler.Z_posPID.FB);   /* 10-15 D2, 0.04 m since 10-19 */

	Rc_FallbackStep();
	Excite_Update(airborne);   /* pattern library: guards + this tick's outputs (no-op while IDLE) */
	Z_Compute();

	cnt_loc++;
	if(cnt_loc>=POS_LOOP_DIV)
	{
		cnt_loc=0;
		Pos_ArmEdge();
		/* Tier 1.0 (2026-09-13): only run position/velocity PIDs while armed.
		   While disarmed Clear_Structure zeros SumE every tick, but Compute_Motor
		   runs all PIDs first, so position SumE re-clamped to SumEMax=200 from OF
		   drift within one tick. Gating keeps locx/y/xs/ys.SumE at 0 in disarmed.
		   cnt_loc=0 sits outside the arm gate so the divider keeps running even
		   while disarmed (otherwise cnt_loc grows unbounded and the gate fires
		   every 256th tick on a uint8 wrap). */
		if (DroneStatus.ARM_Status != 0U) {
			Pos_Compute(airborne, land_contact, land_xy_hold);
		}
		else
		{
			TrajFF_Reset(&s_traj_ff);
		}
	}

	Att_Compute(airborne);

	// Execute MRAC after all PID controllers have computed their nominal outputs (u_nom)
	// MRAC uses the current PID rates, references, and nominal outputs to learn and compute u_ad.
	Controller_CheckSwitch(DroneStatus.ARM_Status == Armed);
	mrac_in_armed = (DroneStatus.ARM_Status == Armed) ? 1U : 0U;
	mrac_in_vbat = real_voltage;                 /* vp 12 battery sag, V */
	mrac_in_acc[0] = Acc_X_Real * 0.00980665f;   /* vp 12 swing tracker: body accel mg -> m/s^2 */
	mrac_in_acc[1] = Acc_Y_Real * 0.00980665f;
	mrac_in_phase = (uint8_t)flight_phase;
	/* experiment telemetry pattern word (mrac.h MRAC_Xt_t.pat): layer, speed, running, axis mask */
	MRAC_XtSetPattern((uint16_t)((excite_layer & 1U) | ((excite_speed & 1U) << 1)
		| ((excite_state == EXC_RUNNING) ? 4U : 0U) | ((excite_mask & 0x3FU) << 3)), excite_phase, excite_period);
	MRAC_Control(&Ctrler);

	Mix_Compute();
	LandGate_Tick();

	ThrustEst_Step();
}

/* ==== Update_Des: setpoints, one case per loop level ============================================== */
#define TWC_ARRIVE_M           0.15f    /* TWC_arrived: 3-D distance to the TWC target below this */
#define FLYUP_TARGET_Z_M       0.5f     /* SBUS ch7 fly-up height */
#define Z_DES_SLEW_M           0.005f   /* max Z setpoint step per call toward TWC.target_z */
#define LAND_SINK_BIAS_STEP    0.001f   /* m/s added per tick while stuck in ground effect */
#define LAND_SINK_SLOW_VZ      0.10f    /* m/s, |Z rate| below this counts as stuck */
#define VXY_DES_MAX            g_vxy_des_max    /* cm/s, clamp on the position-loop velocity setpoints (limit mode) */
#define YAWRATE_DES_MAX        g_yawrate_des_max /* deg/s, clamp on the heading-hold rate setpoint (limit mode) */
#define OFHOLD_ON_MIN          1000     /* ch6 (OFHOLD_CH) above this = OF position hold on */

/* Body-frame lean commands (cm/s^2) of the last pitch/roll setpoint update; telemetry only. */
float des_pitch = 0;
float	des_roll = 0;

/* FIX 2026-10-06 (workflow C, operator): a GS (wfb) flight must not depend on where the throttle stick sits. The
 * throttle does not self-centre and stays at the bottom after the RC arm; read as an active stick it is a full-rate
 * descent command at lift-off (f03 climbed only because the operator centred it by hand). During a GS flight the
 * throttle stick is ignored until the pilot moves it more than THR_TAKEOVER_DELTA from where it sat when the GS
 * flight took off. A roll/pitch takeover alone keeps the altitude hold: a drift correction with the throttle still
 * at the bottom must not become a full-rate descent. The takeover holds until the next take-off. Outside GS flights
 * the stick works as before. Idempotent within a tick. */
static uint8_t s_thr_pilot = 1U;   /* 1: the throttle stick drives z */
static uint8_t s_thr_ref_ok;
static float   s_thr_ref;          /* physical throttle when the take-off released the stick authority */

static void Thr_GsTakeoff(void)
{
	if (g_wfb_status.gs_flight_active > 0.5f) {
		s_thr_pilot  = 0U;
		s_thr_ref_ok = 0U;
	}
}

static int Thr_StickActive(void)
{
	if (g_wfb_status.gs_flight_active < 0.5f)
		s_thr_pilot = 1U;
	else if (!s_thr_pilot && !RCInput_GetAuthority())
	{
		float thr = RCInput_Get(RC_AXIS_THR);
		if (!s_thr_ref_ok) { s_thr_ref = thr; s_thr_ref_ok = 1U; }
		if (fabsf(thr - s_thr_ref) > THR_TAKEOVER_DELTA)
			s_thr_pilot = 1U;
	}
	return s_thr_pilot && RCInput_IsActive(RC_AXIS_THR);
}

/* FIX 2026-10-06: the GS idle (send_data.c CMD 0x0E idx 1 in FlyMode_SDK) takes the stick authority; give it back
 * on disarm so a manual flight after a campaign reads the physical sticks. */
static void GsIdle_ReleaseOnDisarm(void)
{
	static uint8_t s_was_armed;
	uint8_t armed = (FlightFSM_GetState() == FLIGHT_STATE_ARMED) ? 1U : 0U;
	if (s_was_armed && !armed && RCInput_GetAuthority()) {
		RCInput_SetAuthority(0U);
		GS_KeySDKflag = 0U;
	}
	s_was_armed = armed;
}

/* Z position setpoint: ch8/ch7 triggers, LANDING ramp, LANDED/GROUND_IDLE pins, stick capture,
 * TWC slew. */
static void Des_Height(void)
{
	/* Init to 1 (not 0) so the existing capture-on-stick-released state machine
	 * fires on the very first iteration at boot when sticks are centered.
	 * Before this (2026-09-13), is_last_*_valid was 0 at boot, so the condition
	 * `is_last_*_valid && (!RCInput_IsActive(...))` was false on every tick
	 * until the stick was actually moved, leaving Des stuck at whatever the
	 * BSS RAM happened to contain — typically a previous flight's captured FB.
	 * That stale Des was the root cause of the OF-hold cold-boot saturation
	 * cascade / wall-crash. Keeping the 1 means: first tick captures Des=FB,
	 * and the rest of the state machine behaves exactly as before.
	 * (Same rule for the yaw, pitch and roll flags in Des_Loc and Des_Yaw.) */
	static unsigned char is_last_thr_valid = 1U;

	/* SBUS ch8 rising-edge preset-path trigger — handler to be added.
	 * sbus_path_trigger is set by RemoterTask; clear it here for now. */
	if (sbus_path_trigger)
	{
		/* TODO: launch preset path sequence */
		sbus_path_trigger = 0U;
	}

	/* SBUS ch7 fly-up: release authority so physical RC takeover detection
	 * works normally during flight. TWC.execute=1 gates the IDLE block. */
	if (sbus_flyup_trigger)
	{
		sbus_flyup_trigger = 0U;
		RCInput_SetAuthority(0U);   /* release IDLE throttle lock */
		Thr_GsTakeoff();            /* GS flight: z ignores the throttle stick until a takeover */
		TWC.target_x = TWC.world_x;
		TWC.target_y = TWC.world_y;
		TWC.target_z = FLYUP_TARGET_Z_M;
		TWC.execute  = 1U;
	}

	/* LANDING: lower Z setpoint at the rate-mode LAND speed (Des_VHeight owns the rate
	 * command; Des only feeds the Des-at-floor touchdown gates).
	 * Snap Des = min(Des, FB) so a setpoint above current altitude cannot
	 * pull the drone upward at landing entry (case A fix). */
	if (flight_phase == FLIGHT_PHASE_LANDING)
	{
		TWC.execute = 0U;
		if (Ctrler.Z_posPID.Des > Ctrler.Z_posPID.FB)
			Ctrler.Z_posPID.Des = Ctrler.Z_posPID.FB;
		Ctrler.Z_posPID.Des -= LandProfile_SinkMps(Ctrler.Z_posPID.FB) * LAND_TICK_S;
		if (Ctrler.Z_posPID.Des < 0.0f) Ctrler.Z_posPID.Des = 0.0f;
		return;
	}
	/* LANDED: hold setpoint at zero while disarm completes this tick. */
	if (flight_phase == FLIGHT_PHASE_LANDED)
	{
		TWC.execute = 0U;
		Ctrler.Z_posPID.Des = 0.0f;
		return;
	}

	/* GROUND_IDLE hold: pin Z setpoint until pilot pushes THR above 20%.
	 * Also pin when idle not yet enabled (motors at zero, no takeoff possible). */
	if (flight_phase == FLIGHT_PHASE_GROUND_IDLE &&
	    (!g_motor_idle_enabled || (!TWC.execute && RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX)))
	{
		Ctrler.Z_posPID.Des = Ctrler.Z_posPID.FB;
		return;
	}

	/* FLY mode (normal) */
	if (is_last_thr_valid && (!Thr_StickActive()))
		Ctrler.Z_posPID.Des = Ctrler.Z_posPID.FB;

	is_last_thr_valid = Thr_StickActive();

	if (TWC.execute == 1)
	{
		/* Rate-limit the Z setpoint so it cannot jump to a far target: Z_DES_SLEW_M per call.
		 * This runs every tick (Z_Compute), so 0.005 m x 200 Hz = 1.0 m/s; the original
		 * comment said 0.5 m/s at ~100 Hz. */
		float z_err = TWC.target_z - Ctrler.Z_posPID.Des;
		if      (z_err >  Z_DES_SLEW_M) Ctrler.Z_posPID.Des += Z_DES_SLEW_M;
		else if (z_err < -Z_DES_SLEW_M) Ctrler.Z_posPID.Des -= Z_DES_SLEW_M;
		else                            Ctrler.Z_posPID.Des  = TWC.target_z;
	}
}

/* Climb-rate setpoint: Z position loop output, the LANDING sink bias, or the throttle stick. */
static void Des_VHeight(void)
{
	/* Landing: THR stick must not override the cascade. LANDING is rate mode: a fixed
	 * sink command, faster below LAND_FAST_ALT so the drone crosses ground effect quickly
	 * (operator M8). LANDED keeps Z_posPID.U. */
	if (flight_phase == FLIGHT_PHASE_LANDING || flight_phase == FLIGHT_PHASE_LANDED)
	{
		if (flight_phase == FLIGHT_PHASE_LANDING)
			Ctrler.Z_ratePID.Des = -LandProfile_SinkMps(Ctrler.Z_posPID.FB);
		else
			Ctrler.Z_ratePID.Des = Ctrler.Z_posPID.U;
		/* Progressive sink bias: once setpoint has reached the floor, ramp up
		 * commanded sink rate while the drone is slow (stuck in ground effect).
		 * 0.001 m/s per tick at 200 Hz → reaches 0.15 m/s in 0.75 s, max 0.40 m/s.
		 * Adapts to any battery voltage — no altitude threshold needed. */
		if (Ctrler.Z_posPID.Des <= DES_FLOOR_M)
		{
			if (fabsf(Ctrler.Z_ratePID.FB) < LAND_SINK_SLOW_VZ)
				s_land_sink_bias += LAND_SINK_BIAS_STEP;
			if (s_land_sink_bias > LAND_SINK_BIAS_MAX) s_land_sink_bias = LAND_SINK_BIAS_MAX;
			Ctrler.Z_ratePID.Des -= s_land_sink_bias;
		}
		/* Touchdown spool-down (LAND_SPOOL_TICKS): stop pushing down into the ground. The
		 * 10-06 capture held -0.70 m/s on the skids until the cut. */
		if (s_land_spool != 0U)
			Ctrler.Z_ratePID.Des = 0.0f;
	}
	else if (flight_phase == FLIGHT_PHASE_GROUND_IDLE &&
	         (!g_motor_idle_enabled || (!TWC.execute && RCInput_Get(RC_AXIS_THR) < THR_IDLE_MAX)))
		Ctrler.Z_ratePID.Des = 0.0f;
	else if(Thr_StickActive())
		Ctrler.Z_ratePID.Des = RCInput_Get(RC_AXIS_THR) * gs_max_vertical_speed_mps ;
	else if (g_traj_ff_preset)
		Ctrler.Z_ratePID.Des = fmaxf(-TRAJ_FF_VZ_MAX_MPS, fminf(TRAJ_FF_VZ_MAX_MPS, Ctrler.Z_posPID.U + g_traj_vff_z));
	else
		Ctrler.Z_ratePID.Des = Ctrler.Z_posPID.U;
}

/* xy position setpoints (cm): capture FB on stick release, TWC target while executing. */
static void Des_Loc(void)
{
	static unsigned char is_last_pitch_valid = 1U, is_last_roll_valid = 1U;   /* see Des_Height */

	if( is_last_roll_valid && (!RCInput_IsActive(RC_AXIS_ROLL)) )
	{
		Ctrler.locxPID.Des = Ctrler.locxPID.FB;
	}
	if( is_last_pitch_valid && (!RCInput_IsActive(RC_AXIS_PITCH)) )
	{
		Ctrler.locyPID.Des = Ctrler.locyPID.FB;
	}

	is_last_pitch_valid = RCInput_IsActive(RC_AXIS_PITCH);
	is_last_roll_valid = RCInput_IsActive(RC_AXIS_ROLL);
	/* Manual pitch/roll input cancels TWC XY target so the drone
	 * does not snap back to the fly-up launch point after ch7. */
	if (RCInput_IsActive(RC_AXIS_ROLL) || RCInput_IsActive(RC_AXIS_PITCH))
		TWC.execute = 0U;
	if(TWC.execute == 1){Ctrler.locxPID.Des = TWC.target_x;Ctrler.locyPID.Des = TWC.target_y;}   /* go to the TWC target */
}

/* xy velocity setpoints (cm/s): stick, else the position loop output clamped to VXY_DES_MAX. */
static void Des_VLoc(void)
{
	if(RCInput_IsActive(RC_AXIS_PITCH))
		   Ctrler.locysPID.Des = -RCInput_Get(RC_AXIS_PITCH) * (gs_max_horizontal_speed_mps * CM_PER_M);
	else if (Ctrler.locyPID.U>VXY_DES_MAX)
			Ctrler.locysPID.Des = VXY_DES_MAX;
	else if (Ctrler.locyPID.U< -VXY_DES_MAX)
			Ctrler.locysPID.Des = -VXY_DES_MAX;
	else
		Ctrler.locysPID.Des = 	Ctrler.locyPID.U;   /* cascade: position loop output */

	if(RCInput_IsActive(RC_AXIS_ROLL))
		Ctrler.locxsPID.Des = -RCInput_Get(RC_AXIS_ROLL) * (gs_max_horizontal_speed_mps * CM_PER_M);
	else if(Ctrler.locxPID.U>VXY_DES_MAX)
		Ctrler.locxsPID.Des = VXY_DES_MAX;
	else if(Ctrler.locxPID.U< -VXY_DES_MAX)
		Ctrler.locxsPID.Des = -VXY_DES_MAX;
	else
		Ctrler.locxsPID.Des = 	Ctrler.locxPID.U;   /* cascade: position loop output */
}

/* Pitch/roll angle setpoints (deg): OF hold (velocity loop outputs rotated to body) or angle mode
 * (sticks), then accel_to_lean_angles and the attitude trim. */
static void Des_Att(void)
{
	uint8_t flying = (flight_phase == FLIGHT_PHASE_FLYING);
	/* OF position-hold enable switch on ch6 (OFHOLD_CH = sbus_channel[5]).
	 * HIGH (>1000, ~1694) = OF hold ON; LOW (~306) or signal-lost = ANGLE MODE.
	 * Angle mode bypasses ALL optical-flow loops (position AND velocity): the
	 * sticks command a body-frame lean angle directly and centered sticks = level
	 * (held by the IMU angle loop), so a bad OF velocity/position estimate can no
	 * longer drive tilt - this is the loop that ran the drone away on takeoff.
	 * Default at boot / on failsafe is angle mode: the drone never lifts off into
	 * OF hold unless the pilot deliberately flips ch6 high while already stable. */
	u8 of_hold_on = (sbus_lost == 0U) && (OFHOLD_CH > OFHOLD_ON_MIN);
	g_of_hold_active = of_hold_on;   /* publish for Frame 0x01 status.of_hold */
	if (of_hold_on)
	{
		/* BUGFIX 2026-07-20: sign flip on velocity PID outputs.
		 * locxsPID.U > 0 = moving forward (positive X). Desired: decelerate
		 * by pitching nose DOWN (negative des_pitch). Current code produced the
		 * opposite sign -> positive feedback -> runaway on arm.
		 * Fix: negate both velocity PID outputs before the world->body lean
		 * rotation so that positive velocity error produces correcting lean. */
		des_pitch = -(Ctrler.locysPID.U)*Cos_Yaw_01 - (Ctrler.locxsPID.U)*Sin_Yaw_01;
		des_roll  = -(Ctrler.locxsPID.U)*Cos_Yaw_01 + (Ctrler.locysPID.U)*Sin_Yaw_01;
	}
	else
	{
		/* Stick -> accel that maps full deflection to exactly the configured lean
		 * limit (gs_max_pitch/roll_deg), which accel_to_lean_angles then re-clamps.
		 * Sign matches the OF manual path (minus stick). No yaw rotation: body frame. */
		des_pitch = -RCInput_Get(RC_AXIS_PITCH) * tanf(gs_max_pitch_deg*DEG2RAD) * (GRAVITY_MSS*CM_PER_M);
		des_roll  = -RCInput_Get(RC_AXIS_ROLL)  * tanf(gs_max_roll_deg *DEG2RAD) * (GRAVITY_MSS*CM_PER_M);
	}

	accel_to_lean_angles( des_pitch,-des_roll,
	  &Ctrler.pitchPID.Des,&Ctrler.rollPID.Des);
	Ctrler.pitchPID.Des = AttTrim_Apply(Ctrler.pitchPID.Des, g_att_trim_pitch_deg, gs_max_pitch_deg, flying);
	Ctrler.rollPID.Des  = AttTrim_Apply(Ctrler.rollPID.Des,  g_att_trim_roll_deg,  gs_max_roll_deg,  flying);
}

/* Heading setpoint (deg): capture FB on yaw-stick release, TWC.set_yaw while executing. */
static void Des_Yaw(void)
{
	static unsigned char is_last_yaw_valid = 1U;   /* see Des_Height */

	if(is_last_yaw_valid && (!RCInput_IsActive(RC_AXIS_YAW)) )
	Ctrler.yawPID.Des = Ctrler.yawPID.FB;
	is_last_yaw_valid = RCInput_IsActive(RC_AXIS_YAW);

	if(TWC.execute == 1){Ctrler.yawPID.Des = TWC.set_yaw; }   /* go to the TWC heading */
}

/* Rate setpoints (deg/s): angle loop outputs; yaw from the stick or the clamped heading loop. */
static void Des_Gyro(void)
{
	Ctrler.gyroyPID.Des = Ctrler.pitchPID.U ;
	Ctrler.gyroxPID.Des = Ctrler.rollPID.U ;
	if(RCInput_IsActive(RC_AXIS_YAW))
		Ctrler.gyrozPID.Des = RCInput_Get(RC_AXIS_YAW) * Stick_to_MAX_GyroZ ;
	else
	{
		float r = Ctrler.yawPID.U + (g_traj_ff_preset ? g_traj_vff_yaw : 0.0f);   /* + preset yaw-rate FF */
		if(r>YAWRATE_DES_MAX)
			Ctrler.gyrozPID.Des  = YAWRATE_DES_MAX;
		else if(r< -YAWRATE_DES_MAX)
			Ctrler.gyrozPID.Des  = -YAWRATE_DES_MAX;
		else
			Ctrler.gyrozPID.Des = r;
	}
}

/* Update one level's setpoints (which_level = case_Update_*_Des, StabilizerTask.h). Every call first
 * refreshes TWC.world_* / real_yaw and TWC_arrived. */
void Update_Des(unsigned char which_level)
{
	TWC.world_x = Ctrler.locxPID.FB;
	TWC.world_y = Ctrler.locyPID.FB;
	TWC.world_z = Ctrler.Z_posPID.FB;
	TWC.real_yaw = Ctrler.yawPID.FB;

	if (TWC.execute == 1) {
		float dx = (Ctrler.locxPID.FB - TWC.target_x) * M_PER_CM; /* cm → m */
		float dy = (Ctrler.locyPID.FB - TWC.target_y) * M_PER_CM; /* cm → m */
		float dz = Ctrler.Z_posPID.FB - TWC.target_z;           /* already m */
		float dist = sqrtf(dx * dx + dy * dy + dz * dz);
		TWC_arrived = (dist < TWC_ARRIVE_M) ? 1U : 0U;
	} else {
		TWC_arrived = 0U;
	}

	switch(which_level)
	{
		case case_Update_height_Des: Des_Height();  break;
		case case_Update_v_h_Des:    Des_VHeight(); break;
		case case_Update_loc_Des:    Des_Loc();     break;
		case case_Update_v_loc_Des:  Des_VLoc();    break;
		case case_Update_pitrol_Des: Des_Att();     break;
		case case_Update_yaw_Des:    Des_Yaw();     break;
		case case_Update_gyro_Des:   Des_Gyro();    break;
		default:                                    break;
	}
}

/* ==== Helpers ===================================================================================== */
/* Clamp amt to [low, high]. */
float Constrain_Float(float amt, float low, float high)
{
  return ((amt)<(low)?(low):((amt)>(high)?(high):(amt)));
}

/* Rational approximation of atan(v), rad. */
float fast_atan(float v)
{
    float v2 = v*v;
    return (v*(1.6867629106f+v2*0.4378497304f)/(1.6867633134f+v2));
}

/* Horizontal accel commands (cm/s^2, body frame) -> pitch/roll angle setpoints (deg), clamped to
 * gs_max_pitch/roll_deg. */
void accel_to_lean_angles(float acc_tar_forward,float acc_tar_right,float *tar_pitch,float *tar_roll)
{
  float lim_p = gs_max_pitch_deg;
  float lim_r = gs_max_roll_deg;

	float my_Cos_Roll;
	float my_Cos_Pitch;
	my_Cos_Roll = cos(imu_data.rol*DEG2RAD);
	my_Cos_Pitch = cos(imu_data.pit*DEG2RAD);

  *tar_pitch=Constrain_Float(
									fast_atan(    acc_tar_forward    *my_Cos_Roll   /(GRAVITY_MSS*100)    )*RAD2DEG,
														-lim_p,lim_p);//pitch
  *tar_roll = Constrain_Float(
									fast_atan(acc_tar_right * my_Cos_Pitch /(GRAVITY_MSS*100))*RAD2DEG,
														-lim_r,lim_r);//roll
}

/* ==== Battery voltage (called at 1 Hz from SystemMonitor_Task, USER/main.c:252) ==================== */
#define VBAT_CAL_GAIN          7.0663f  /* 2-pt cal 2026-06-24: (2.21289 V, 16.53) (1.98081 V, 14.89) */
#define VBAT_CAL_OFFSET        0.8930f
#define VBAT_LOW_BEEP_V        15.0f    /* beep below this pack voltage */

float voltage;         /* ADC pin voltage, V (2.67-2.99 on a full pack) */
float real_voltage;    /* pack voltage, V */
uint16_t adc_value ;
void Get_Voltage(void)
{
	adc_value = ADC_Read();
	voltage = Voltage_Calculation(adc_value);
	real_voltage = VBAT_CAL_GAIN*voltage + VBAT_CAL_OFFSET;
	if(real_voltage<VBAT_LOW_BEEP_V)
	{
		SetBeep(1);   /* low-battery beep */

	}
}
#endif /* !ALT_CF_HOST_TEST */
