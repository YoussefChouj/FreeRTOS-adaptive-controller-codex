/**
 * @module     AutoflyTask.c
 * @subsystem  guidance
 * @owner      AutoflyTask() runs every 5 ms from USER/main.c. Stabilizer_Task calls SDK_StateMachine_Init at
 *             start-up and SDK_Set_V_Loc / SDK_Set_Gyroz each cycle; the path Start and Run functions are also
 *             called by the GS path commands.
 * @purpose    Onboard reference generators (circle, sinusoid, figure-8) with a shared waypoint-density quantizer,
 *             path arbitration (PROTECTED AutoflyTask_PathArbitrate, kept byte for byte), and the legacy SDK
 *             script state machine (entered by holding raw ch4 and ch5 at AFLY_KEY_SBUS_HIGH for AFLY_KEY_HOLD_MS).
 * @inputs     sbus_channel[], sbus_lost, Ctrler loc/yaw/Z PID feedback, the *_path parameter blocks, temp_V_* overrides.
 * @outputs    Ctrler loc/yaw/Z PID Des setpoints, locxs/locys/gyroz rate setpoints, KeySDKflag, SDK_* flags.
 */
#include "AutoflyTask.h"
#include "math.h"
#include "flight_fsm.h"
#include "rc_input.h"
#include "FreeRTOS.h"
#include "task.h"
#include "wfb_safety.h"
#include "wfb_traj.h"
#include "wfb_prim.h"
#include "wfb_glue.h"      /* g_wfb_status: a WFB hover hold yields to a preset */
#include "traj_shape.h"
#include "xy_ctrl.h"
#include "sysid.h"         /* sysid_target (VAR_WRITE 55), SysID_GetState for its BUSY guard */

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

#define AFLY_TICK_MS        5U        /* AutoflyTask period [ms]                                  */
#define AFLY_DT_S           0.005f    /* the same period in seconds, used by the path generators  */
#define AFLY_KEY_SBUS_HIGH  1800      /* raw SBUS value both ch4 and ch5 must hold to arm the SDK */
#define AFLY_KEY_HOLD_MS    2000      /* hold time before KeySDKflag is set [ms]                  */

/* SDK script commands. A script is (command, duration) pairs in SDK_StateMachine[]; the duration counts down by
   5 per tick. Only DelayWake, TakeOff, PosHold, Pos1 and Land have a case in SDK_StateMachine_Loop; the others
   only select the velocity / yaw-rate override in SDK_Set_V_Loc and SDK_Set_Gyroz. */
#define SDK_Cmd_TakeOff        0  /* climb to SDK_Height                      */
#define SDK_Cmd_Land           1  /* descend; at SDK_LAND_DONE_Z stop motors  */
#define SDK_Cmd_Search0        2  /* rotate in place while searching          */
#define SDK_Cmd_Search1        3  /* move while searching                     */
#define SDK_Cmd_PosHold        4  /* hold the current position                */
#define SDK_Cmd_Circle         5  /* fly a circle                             */
#define SDK_Cmd_FollowLine     6  /* follow a line                            */
#define SDK_Cmd_PowerLine      7  /* follow a power line (competition task)   */
#define SDK_Cmd_Surround       8  /* orbit a target (competition task)        */
#define SDK_Cmd_GetLine        9  /* acquire a line (competition task)        */
#define SDK_Cmd_GetClose       10 /* approach a target (competition task)     */
#define SDK_Cmd_DelayWake      11 /* wait, holding the current pose           */
#define SDK_Cmd_Pos1           12 /* go to point 1                            */
#define SDK_Cmd_Pos2           13 /* go to point 2                            */
#define SDK_Cmd_Pos3           14 /* go to point 3                            */
#define SDK_Cmd_Pos4           15 /* go to point 4                            */
#define SDK_Cmd_SearchLand     16 /* search for the landing pad               */
#define SDK_Cmd_SearchLand_down 17 /* descend over the landing pad            */
#define SDK_Cmd_Searchgan      18 /* search for a pole                        */
#define SDK_Cmd_Pos5           19 /* go to point 5                            */
#define SDK_Cmd_Pos6           20 /* go to point 6                            */
#define SDK_Cmd_Pos7           21 /* go to point 7                            */
#define SDK_Cmd_Pos8           22 /* go to point 8                            */
#define SDK_Cmd_Pos9           23 /* go to point 9                            */
#define SDK_Cmd_Pos10          24 /* go to point 10                           */
#define SDK_Cmd_go_to_land     25 /* fly to the landing point                 */

#define SDK_Height       0.7f                  /* take-off target height [m]          */
#define SDK_Height_H     (SDK_Height + 0.05f)  /* take-off done band, upper edge [m]  */
#define SDK_Height_L     (SDK_Height - 0.05f)  /* take-off done band, lower edge [m]  */
#define SDK_LAND_DONE_Z  0.2f                  /* height that ends SDK_Cmd_Land [m]   */

/* ------------------------------------------------------------------
 * Public state  (extern in AutoflyTask.h; read by Stabilizer_Task and Send_Task)
 * ------------------------------------------------------------------ */


float x_test = 1;
float y_test = 1;
float yaw_test = 0;
unsigned int SDK_StateMachine[200];
unsigned int CurrentSDKState,SDKStateMAX;
unsigned int KeyPressedTimeMS;
unsigned char KeySDKflag=0,SDK_StateChangeFlag=0,SDK_DelayWakeFlag=0,SDKLandFlag=0;
float temp_V_x=-1,temp_V_y=-1,temp_Gyroz=0,temp_V_h=0,temp_Yaw;
int take_off_flag=0;
int SearchLand_down_cnt = 0;

/* ------------------------------------------------------------------
 * Private helpers: path arbitration (PROTECTED) and waypoint quantizer
 * ------------------------------------------------------------------ */

static void AutoflyTask_PathArbitrate(void)
{
	/* No GS path may drive the setpoints unless the GS holds RC authority.
	 * If the pilot has taken over (physical-stick takeover in RCInput_Update
	 * dropped authority) or the GS released it, stop every preset so control
	 * hands cleanly to manual alt/position-hold. */
	if (!RCInput_GetAuthority()) {
		sinusoid_path.active = 0U;
		circle_path.active   = 0U;
		figure8_path.active  = 0U;
		TWC.execute          = 0;
		return;
	}

	if (sinusoid_path.active) {
		circle_path.active = 0U;
		figure8_path.active = 0U;
		TWC.execute = 0;
	} else if (circle_path.active) {
		sinusoid_path.active = 0U;
		figure8_path.active = 0U;
		TWC.execute = 0;
	} else if (figure8_path.active) {
		sinusoid_path.active = 0U;
		circle_path.active = 0U;
		TWC.execute = 0;
	} else if (TWC.execute != 0) {
		sinusoid_path.active = 0U;
		circle_path.active = 0U;
		figure8_path.active = 0U;
	}
}

/* ---- Shared waypoint-density quantizer (reference arc-length accumulator) ----
 * Holds the committed position setpoint fixed until the CONTINUOUS reference has
 * travelled waypoint_spacing cm of arc (loc-PID units), then commits the new point. Yaw is
 * NOT quantized (callers set yaw Des directly). waypoint_spacing<=0 => continuous. */
static float wp_accum = 0.0f;
static float wp_last_x = 0.0f, wp_last_y = 0.0f, wp_last_z = 0.0f;
static uint8_t wp_have_last = 0U;

void AutoflyTask_WaypointReset(void)
{
	wp_accum = 0.0f;
	wp_have_last = 0U;
}

static void AutoflyTask_CommitRef(float cont_x, float cont_y, float cont_z)
{
	float ds = waypoint_spacing;
	if (ds <= 0.0f) {
		Ctrler.locxPID.Des = cont_x;
		Ctrler.locyPID.Des = cont_y;
		Ctrler.Z_posPID.Des = cont_z;
		wp_have_last = 0U;
		wp_accum = 0.0f;
		return;
	}
	if (!wp_have_last) {
		Ctrler.locxPID.Des = cont_x;
		Ctrler.locyPID.Des = cont_y;
		Ctrler.Z_posPID.Des = cont_z;
		wp_last_x = cont_x;
		wp_last_y = cont_y;
		wp_last_z = cont_z;
		wp_accum = 0.0f;
		wp_have_last = 1U;
		return;
	}
	{
		float dx = cont_x - wp_last_x;
		float dy = cont_y - wp_last_y;
		float dz = (cont_z - wp_last_z) * 100.0f; /* z is metres; x/y are cm. Scale z to cm so waypoint_spacing (cm) is uniform across all axes (incl. Z-axis sinusoid). */
		wp_accum += sqrtf(dx * dx + dy * dy + dz * dz);
	}
	wp_last_x = cont_x;
	wp_last_y = cont_y;
	wp_last_z = cont_z;
	if (wp_accum >= ds) {
		Ctrler.locxPID.Des = cont_x;
		Ctrler.locyPID.Des = cont_y;
		Ctrler.Z_posPID.Des = cont_z;
		wp_accum = 0.0f;
	}
}

/* ------------------------------------------------------------------
 * Public API: path generators (start at the current pose, then step every tick)
 * ------------------------------------------------------------------ */

/* ---- Path start: begin at the current pose ----
 * A path starts where the drone is: its centre is moved so the first reference point is the current
 * position (loc/Z_pos FB), overwriting the centre set by CMD idx 0-2. Its heading is the current
 * heading plus the path's own turn (theta for the circle, none for the sinusoid and figure-8).
 * The send_data.c start commands call these inside taskENTER_CRITICAL. One path runs at a time
 * (AutoflyTask_PathArbitrate), so one start heading serves all three. */
static volatile float path_yaw0 = 0.0f;

static float AutoflyTask_WrapDeg(float deg)
{
	deg = fmodf(deg, 360.0f);
	if (deg >= 180.0f) {
		deg -= 360.0f;
	} else if (deg < -180.0f) {
		deg += 360.0f;
	}
	return deg;
}

void AutoflyTask_StartSinusoid(void)
{
	/* the offset is 0 at t = 0, so the first point is the centre */
	sinusoid_path.center_x = Ctrler.locxPID.FB;
	sinusoid_path.center_y = Ctrler.locyPID.FB;
	sinusoid_path.center_z = Ctrler.Z_posPID.FB;
	sinusoid_path.t_elapsed = 0.0f;
	path_yaw0 = Ctrler.yawPID.FB;
	AutoflyTask_WaypointReset();
	sinusoid_path.active = 1U;
}

void AutoflyTask_StartCircle(void)
{
	/* theta 0 is (center_x + radius, center_y) */
	circle_path.center_x = Ctrler.locxPID.FB - circle_path.radius;
	circle_path.center_y = Ctrler.locyPID.FB;
	circle_path.center_z = Ctrler.Z_posPID.FB;
	circle_path.theta = 0.0f;
	circle_path.t_elapsed = 0.0f;
	path_yaw0 = Ctrler.yawPID.FB;
	AutoflyTask_WaypointReset();
	circle_path.active = 1U;
}

void AutoflyTask_StartFigure8(void)
{
	/* theta 0 is (center_x + amplitude, center_y) for Bernoulli and the centre for Gerono */
	float start_dx = (figure8_path.type == 1U) ? 0.0f : figure8_path.amplitude;

	figure8_path.center_x = Ctrler.locxPID.FB - start_dx;
	figure8_path.center_y = Ctrler.locyPID.FB;
	figure8_path.center_z = Ctrler.Z_posPID.FB;
	figure8_path.theta = 0.0f;
	figure8_path.t_elapsed = 0.0f;
	path_yaw0 = Ctrler.yawPID.FB;
	AutoflyTask_WaypointReset();
	figure8_path.active = 1U;
}

static void Traj_Commit(float x, float y, float z);   /* the GS paths feed the preset FF too (Traj_Tick) */

void AutoflyTask_RunCircle(void)
{
	const float dt = AFLY_DT_S;

	if (DroneStatus.FlyMode != FlyMode_SDK) {
		circle_path.active = 0U;
		return;
	}

	if (!circle_path.active) {
		return;
	}

	circle_path.t_elapsed += dt;
	circle_path.theta += circle_path.angular_speed * dt;

	Traj_Commit(
		circle_path.center_x + circle_path.radius * cosf(circle_path.theta),
		circle_path.center_y + circle_path.radius * sinf(circle_path.theta),
		circle_path.center_z);
	/* One turn per lap from the start heading; theta keeps counting past a turn, Des stays in [-180, 180). */
	Ctrler.yawPID.Des = AutoflyTask_WrapDeg(path_yaw0 + circle_path.theta * RAD2DEG);

	if (circle_path.duration > 0.0f &&
	    circle_path.t_elapsed >= circle_path.duration) {
		circle_path.active = 0U;
		TWC.execute = 0;
	}
}

void AutoflyTask_RunSinusoid(void)
{
	const float dt = AFLY_DT_S;

	if (DroneStatus.FlyMode != FlyMode_SDK) {
		sinusoid_path.active = 0;
		return;
	}

	if (!sinusoid_path.active) {
		return;
	}

	sinusoid_path.t_elapsed += dt;

	{
		float off = sinusoid_path.amplitude *
		            sinf(2.0f * PI * sinusoid_path.frequency * sinusoid_path.t_elapsed);
		{
			float cont_x = sinusoid_path.center_x;
			float cont_y = sinusoid_path.center_y;
			float cont_z = sinusoid_path.center_z;
			switch (sinusoid_path.axis) {
				case 0: cont_x = sinusoid_path.center_x + off; break;
				case 1: cont_y = sinusoid_path.center_y + off; break;
				case 2: cont_z = sinusoid_path.center_z + off; break;
				default: break;
			}
			Traj_Commit(cont_x, cont_y, cont_z);
		}
	}

	Ctrler.yawPID.Des = path_yaw0;

	if (sinusoid_path.duration > 0.0f &&
	    sinusoid_path.t_elapsed >= sinusoid_path.duration) {
		sinusoid_path.active = 0;
		TWC.execute = 0;
	}
}

void AutoflyTask_RunFigure8(void)
{
	const float dt = AFLY_DT_S;
	float th, cx, cy;

	if (DroneStatus.FlyMode != FlyMode_SDK) {
		figure8_path.active = 0U;
		return;
	}

	if (!figure8_path.active) {
		return;
	}

	figure8_path.t_elapsed += dt;
	figure8_path.theta += figure8_path.angular_speed * dt;
	th = figure8_path.theta;

	if (figure8_path.type == 1U) {
		/* Gerono: vertical figure-8 (taller in y) */
		cx = figure8_path.center_x + 0.5f * figure8_path.amplitude * sinf(2.0f * th);
		cy = figure8_path.center_y + figure8_path.amplitude * sinf(th);
	} else {
		/* Bernoulli: lying infinity (wider in x) */
		float s = sinf(th);
		float c = cosf(th);
		float denom = 1.0f + s * s;
		cx = figure8_path.center_x + figure8_path.amplitude * c / denom;
		cy = figure8_path.center_y + figure8_path.amplitude * s * c / denom;
	}

	Traj_Commit(cx, cy, figure8_path.center_z);
	Ctrler.yawPID.Des = path_yaw0;

	if (figure8_path.duration > 0.0f &&
	    figure8_path.t_elapsed >= figure8_path.duration) {
		figure8_path.active = 0U;
		TWC.execute = 0;
	}
}

/* ------------------------------------------------------------------
 * Keil trajectory presets (traj_id / traj_go), docs/flights/2026-10-08-trajectory-presets.md
 * ------------------------------------------------------------------
 * While hovering: edit traj_p and traj_id in the watch window, then traj_go = 1. The block takes the stick
 * authority (all four virtual sticks centred), flies the preset from the hover setpoint for move_s, flies back
 * to that point over ret_s, holds it for hold_s and hands the sticks back (traj_status 2). traj_go = 1 again
 * repeats it without landing. traj_stop = 1 ends the move early and flies back. Any stick move is a pilot
 * takeover and ends it at once, without the return (traj_status 3). The parameters are latched at traj_go.
 * Units: loc x/y cm, Z_pos m, yaw deg; the traj_p fields are cm, Hz and s. Numbers are PROPOSED, untested. */

#define TRAJ_TOTAL_MAX_S   25.0f        /* move_s + ret_s + hold_s ceiling (motor 2 runs hot with the load) [s] */
#define TRAJ_V_MAX_CMS     g_traj_v_max_cms /* peak reference speed above this is refused [cm/s]; limit mode  */
extern float g_traj_v_max_cms;          /* StabilizerTask.c s_lim: 80 / 110 / 150 for lim_mode 0 / 1 / 2       */
#define TRAJ_Z_OFF_MAX_CM  40.0f        /* largest z step or zigzag amplitude [cm]                              */
#define TRAJ_Z_MIN_M       0.4f         /* lowest z reference [m]                                               */
#define TRAJ_PI            3.14159265f

typedef struct {
	uint8_t profile;    /* 0 constant speed and hard steps, 1 smooth: cosine ramps of ramp_s at both ends */
	uint8_t axis;       /* step and zigzag axis: 0 x, 1 y, 2 z                                         */
	uint8_t zz_shape;   /* zigzag: 0 triangle (constant speed, sharp turns), 1 sine                    */
	uint8_t f8_type;    /* figure-8: 0 Bernoulli (wide in x), 1 Gerono (tall in y)                     */
	float   move_s;     /* preset time [s]                                                             */
	float   ret_s;      /* fly back to the start point [s], 3-10                                       */
	float   hold_s;     /* hold the start point before the sticks go back [s], 0-5                     */
	float   ramp_s;     /* profile 1 ramp time [s], 0.5 to move_s / 4                                  */
	float   step_cm;    /* step: out for move_s / 2, then back [cm], +-100 (z +-40)                    */
	float   zz_amp_cm;  /* zigzag amplitude [cm], up to 80 (z 40)                                      */
	float   zz_hz;      /* zigzag frequency [Hz], up to 1                                              */
	float   circ_r_cm;  /* circle radius [cm], 10-80; the start point is on the circle, centre at -x   */
	float   circ_laps;  /* circle laps in move_s, up to 3                                              */
	float   f8_a_cm;    /* figure-8 size [cm], 10-80                                                   */
	float   f8_laps;    /* figure-8 laps in move_s, up to 4                                            */
	float   f8_tilt_deg;  /* figure-8 plane tilted about x: y lobes climb/sink, z = y * sin(tilt) [deg],
	                         +-60 and |z| up to 40 cm (Gerono a 70: up to 34 deg)                      */
	uint8_t yaw_follow;   /* circle and figure-8: 1 nose faces the direction of travel, 0 hold heading  */
	float   yaw_rate_dps; /* yaw follow slew limit [deg/s], 10-180; corners sharper than this lag       */
	float   yaw_nose_deg; /* where the nose points in the loc x/y frame at yaw 0 [deg, x toward y].
	                         x is the roll axis at yaw 0, so the nose is +90 or -90: check it on the
	                         first slow run (nose must lead) and flip the sign if the tail leads        */
	uint8_t start_fixed;  /* 1 fly to the fixed start point first and run the preset around it, so every
	                         run starts at the same place; 0 run it around the hover setpoint at traj_go */
	float   start_x_cm;   /* fixed start point in the loc frame [cm]                                     */
	float   start_y_cm;
	float   start_z_m;    /* fixed start height [m], 0.5-1.5                                             */
	float   goto_s;       /* time to fly there [s], 2-10; stretched when the limit mode's speed needs it */
	float   settle_s;     /* hover there before the move [s], 0-5; the nose turns to the path meanwhile  */
} TrajParams_t;

volatile uint8_t traj_id        = 4U;  /* 1 step, 2 zigzag, 3 circle, 4 figure-8 (default: the standard test path) */
volatile uint8_t traj_go        = 0U;  /* write 1 to start traj_id (ignored while one runs); the firmware clears it */
volatile uint8_t traj_stop      = 0U;  /* write 1 to end the move early and fly back                           */
volatile uint8_t traj_fence_off = 1U;  /* 0 fence on (x/y/z checks); 1 fence off (bypassed). Default 1 (2026-10-10 operator); floor check always holds */
volatile uint8_t traj_active = 0U;  /* id of the running preset, 0 idle                                     */
volatile uint8_t traj_phase  = 0U;  /* 0 idle, 1 move, 2 return, 3 hold at the start point,
                                       4 fly to the fixed start point, 5 settle there (start_fixed 1)   */
volatile uint8_t traj_status = 0U;  /* 1 running, 2 done, 3 aborted (takeover/landing), 4 stopped and back,
                                       0xE0 not flying, 0xE1 busy (GS path or GS authority), 0xE2 bad traj_id,
                                       0xEE a traj_p field out of range or too fast, 0xEF outside the soft fence */
volatile uint8_t traj_id_seen = 0U; /* traj_id as read at the last traj_go (explains a 0xE2)                */
volatile float   traj_t      = 0.0f; /* time since traj_go [s], goto and settle included                   */
volatile float   traj_home_x = 0.0f, traj_home_y = 0.0f, traj_home_z = 0.0f, traj_home_yaw = 0.0f; /* start point */
TrajParams_t traj_p = {                 /* watch-window struct, latched at traj_go (same pattern as vp_user) */
	1U, 0U, 0U, 1U,                 /* profile smooth, axis x, zigzag triangle, figure-8 Gerono    */
	20.0f, 3.0f, 2.0f, 2.0f,        /* move, return, hold, ramp [s]: 25 s in total                 */
	40.0f,                          /* step [cm]                                                   */
	30.0f, 0.2f,                    /* zigzag amplitude [cm], frequency [Hz]                       */
	40.0f, 1.0f,                    /* circle radius [cm], laps                                    */
	70.0f, 2.0f,                    /* figure-8 size [cm], laps: standard test path, x +-35 cm,
	                                   y +-70 cm, peak 69 cm/s (limit 80)                          */
	0.0f,                           /* figure-8 tilt [deg]: flat (30 gives z +-35 cm)              */
	1U, 90.0f, 90.0f,               /* yaw follow on, 90 deg/s, nose +y at yaw 0 (PROPOSED)        */
	1U, 0.0f, 0.0f, 0.85f,          /* fly to (0, 0, 0.85 m) first and start the preset there      */
	4.0f, 2.0f                      /* goto at least 4 s, settle 2 s (not in the 25 s move cap)    */
};

/* ------------------------------------------------------------------
 * VAR_WRITE allowlist: GS CMD 0x2D, the WiFi twin of a Keil watch-window write (docs/firmware-table-pattern.md)
 * ------------------------------------------------------------------
 * One frame writes one row: INDEX = the row id, value = float32 (a u8 row takes an integral value only). Nothing
 * outside this table can be written. Ranges are the ones in each variable's comment; the traj_go checks (0xEE,
 * 0xEF) still run on top. Row ids are permanent: never renumber or reuse one, so a stale build refuses an id it
 * does not know instead of writing the wrong variable. ground_station/platform/wfb_var.py parses these rows.
   @id   -  [1, 254]     permanent row id, the command's INDEX
   @min  -  [-500, 500]  lowest value accepted
   @max  -  [-500, 500]  highest value accepted */
#define VW_U8   0U
#define VW_F32  1U
typedef struct {
	uint8_t        id;
	uint8_t        type;   /* VW_U8 or VW_F32 */
	volatile void *addr;
	float          min;
	float          max;
	const char    *name;   /* the variable as written in the row, echoed in the APPLIED detail */
} VarWriteRow_t;
#define VAR_WRITE_ROW(var, type, id, min, max)  { (id), (type), (volatile void *)&(var), (min), (max), #var }

extern volatile uint8_t kp_id, kp_go, vp_user_go, g_traj_ff_preset;   /* StabilizerTask.c */
extern volatile uint8_t g_xy_ctrl_kind;                                /* StabilizerTask.c, API/xy_ctrl.h */
extern volatile float g_traj_ff_shift_s;                               /* StabilizerTask.c */
extern volatile float g_land_xy_gate_cm;                               /* StabilizerTask.c */
extern volatile uint8_t g_xyc_delay_ticks;                             /* StabilizerTask.c */
extern volatile uint8_t g_rc_pid_fallback_ch;                          /* StabilizerTask.c */
extern volatile uint8_t g_ctrl_pid_fallback;                           /* StabilizerTask.c */
extern volatile uint8_t exp_id, vp_id, lim_mode;                     /* StabilizerTask.c */
extern volatile uint8_t g_imu_mahony_yaw_decouple;                   /* API/imu_update.h */
extern volatile uint8_t mrac_z_feat_mask;                            /* API/mrac.h, MRAC_ZFEAT_* bits */
extern volatile uint8_t g_drift_nn_live;                             /* NN/drift_nn_task.h, DRIFT_NN_LIVE_* */
extern volatile float g_drift_nn_clamp_mps;                          /* NN/drift_nn_task.h */
extern volatile uint8_t g_alt_vz_mode;                               /* StabilizerTask.c, ALT_VZ_MODE_* */

/* n26-B: slow clock, arrival radius, predictor lead, and runtime xy gains (VAR 70..83) */
volatile uint8_t g_traj_slow_on     = 0U;          /* 0 off (default), 1 slow clock when behind */
volatile float   g_traj_slow_elo_cm = 15.0f;       /* cm, s = 1 below e_lo (PROPOSED) */
volatile float   g_traj_slow_ehi_cm = 45.0f;       /* cm, s = 0 at/above e_hi (PROPOSED) */
volatile float   g_traj_arrive_r_cm = 15.0f;       /* cm, waypoint arrival radius (PROPOSED) */
volatile uint8_t g_xyc_pred_lead    = 0U;          /* 0 current ref (default), 1 lead ref d ticks + aff */
volatile float   g_xyc_lqr_k0       = 29.7682778f; /* XYC_LQR_K[0] default */
volatile float   g_xyc_lqr_k1       = 10.0523956f; /* XYC_LQR_K[1] default */
volatile float   g_xyc_lqr_k2       = 0.35933145f; /* XYC_LQR_K[2] default */
volatile float   g_xyc_lqr_k3       = 2.2333094f;  /* XYC_LQR_K[3] default */
volatile float   g_xyc_adrc_wc      = 1.34060242f; /* XYC_ADRC_WC default */
volatile float   g_xyc_adrc_b0      = 0.572712305f;/* XYC_ADRC_B0 default */
volatile float   g_xyc_model_k      = 0.674429432f;/* XYC_K default */
volatile float   g_xyc_model_al     = 0.862128296f;/* XYC_AL default */
volatile float   g_xyc_model_drag   = 0.42558222f; /* XYC_DRAG default */

static const VarWriteRow_t s_var_write[] = {
/*                var                 type    id    min      max            range source */
    VAR_WRITE_ROW(traj_id,            VW_U8,   1,   1,       4      ),  /* 1 step .. 4 figure-8          */
    VAR_WRITE_ROW(traj_go,            VW_U8,   2,   0,       1      ),  /* bool                          */
    VAR_WRITE_ROW(traj_stop,          VW_U8,   3,   0,       1      ),  /* bool                          */
    VAR_WRITE_ROW(traj_fence_off,     VW_U8,   4,   0,       1      ),  /* bool                          */

    VAR_WRITE_ROW(traj_p.profile,     VW_U8,  10,   0,       2      ),  /* enum 0 hard, 1 smooth, 2 min-jerk */
    VAR_WRITE_ROW(traj_p.axis,        VW_U8,  11,   0,       2      ),  /* enum 0 x, 1 y, 2 z            */
    VAR_WRITE_ROW(traj_p.zz_shape,    VW_U8,  12,   0,       1      ),  /* enum triangle, sine           */
    VAR_WRITE_ROW(traj_p.f8_type,     VW_U8,  13,   0,       1      ),  /* enum Bernoulli, Gerono        */
    VAR_WRITE_ROW(traj_p.move_s,      VW_F32, 14,   0.0f,    25.0f  ),  /* TRAJ_TOTAL_MAX_S              */
    VAR_WRITE_ROW(traj_p.ret_s,       VW_F32, 15,   3.0f,    10.0f  ),  /* 3-10                          */
    VAR_WRITE_ROW(traj_p.hold_s,      VW_F32, 16,   0.0f,    5.0f   ),  /* 0-5                           */
    VAR_WRITE_ROW(traj_p.ramp_s,      VW_F32, 17,   0.5f,    6.25f  ),  /* 0.5 to move_s / 4, move_s 25  */
    VAR_WRITE_ROW(traj_p.step_cm,     VW_F32, 18, -100.0f,   100.0f ),  /* +-100 (z +-40 at traj_go)     */
    VAR_WRITE_ROW(traj_p.zz_amp_cm,   VW_F32, 19,   0.0f,    80.0f  ),  /* up to 80 (z 40 at traj_go)    */
    VAR_WRITE_ROW(traj_p.zz_hz,       VW_F32, 20,   0.0f,    1.0f   ),  /* up to 1                       */
    VAR_WRITE_ROW(traj_p.circ_r_cm,   VW_F32, 21,  10.0f,    80.0f  ),  /* 10-80                         */
    VAR_WRITE_ROW(traj_p.circ_laps,   VW_F32, 22,   0.0f,    3.0f   ),  /* up to 3                       */
    VAR_WRITE_ROW(traj_p.f8_a_cm,     VW_F32, 23,  10.0f,    80.0f  ),  /* 10-80                         */
    VAR_WRITE_ROW(traj_p.f8_laps,     VW_F32, 24,   0.0f,    4.0f   ),  /* up to 4                       */
    VAR_WRITE_ROW(traj_p.f8_tilt_deg, VW_F32, 25, -60.0f,    60.0f  ),  /* +-60                          */
    VAR_WRITE_ROW(traj_p.yaw_follow,  VW_U8,  26,   0,       1      ),  /* bool                          */
    VAR_WRITE_ROW(traj_p.yaw_rate_dps,VW_F32, 27,  10.0f,    180.0f ),  /* 10-180                        */
    VAR_WRITE_ROW(traj_p.yaw_nose_deg,VW_F32, 28, -180.0f,   180.0f ),  /* an angle (+90 or -90 in use)  */
    VAR_WRITE_ROW(traj_p.start_fixed, VW_U8,  29,   0,       1      ),  /* bool                          */
    VAR_WRITE_ROW(traj_p.start_x_cm,  VW_F32, 30, -500.0f,   500.0f ),  /* wfb_traj x_abs_m legend 5 m   */
    VAR_WRITE_ROW(traj_p.start_y_cm,  VW_F32, 31, -500.0f,   500.0f ),  /* wfb_traj y_abs_m legend 5 m   */
    VAR_WRITE_ROW(traj_p.start_z_m,   VW_F32, 32,   0.5f,    1.5f   ),  /* 0.5-1.5                       */
    VAR_WRITE_ROW(traj_p.goto_s,      VW_F32, 33,   2.0f,    10.0f  ),  /* 2-10                          */
    VAR_WRITE_ROW(traj_p.settle_s,    VW_F32, 34,   0.0f,    5.0f   ),  /* 0-5                           */

    VAR_WRITE_ROW(kp_id,              VW_U8,  40,   0,       7      ),  /* s_kp rows 0..7                */
    VAR_WRITE_ROW(kp_go,              VW_U8,  41,   0,       1      ),  /* bool                          */
    VAR_WRITE_ROW(vp_user_go,         VW_U8,  42,   0,       1      ),  /* bool                          */
    VAR_WRITE_ROW(g_traj_ff_preset,   VW_U8,  43,   0,       1      ),  /* bool                          */
    VAR_WRITE_ROW(g_xy_ctrl_kind,     VW_U8,  44,   0,       3      ),  /* 0 PID 1 ADRC 2 LQR 3 MPC; ground */
    VAR_WRITE_ROW(g_traj_ff_shift_s,  VW_F32, 45,   0.0f,    0.6f   ),  /* s, FF lead (xy_tune .31)      */
    VAR_WRITE_ROW(g_land_xy_gate_cm,  VW_F32, 46,   0.0f,    50.0f  ),  /* cm, land pause radius; 0 off  */
    VAR_WRITE_ROW(g_xyc_delay_ticks,  VW_U8,  47,   1,       48     ),  /* ticks, LQR/MPC predictor; ground */
    VAR_WRITE_ROW(g_rc_pid_fallback_ch, VW_U8, 48,  0,       16     ),  /* RC aux ch: 0 disabled, 1..16  */
    VAR_WRITE_ROW(g_ctrl_pid_fallback,  VW_U8, 49,  0,       1      ),  /* direct fallback trigger: bool */
    VAR_WRITE_ROW(exp_id,              VW_U8, 50,  0,       7      ),  /* s_exp rows 0..7               */
    VAR_WRITE_ROW(vp_id,               VW_U8, 51,  0,       24     ),  /* s_vp rows 0..24               */
    VAR_WRITE_ROW(lim_mode,            VW_U8, 52,  0,       2      ),  /* s_lim rows 0..2               */
    VAR_WRITE_ROW(g_imu_mahony_yaw_decouple, VW_U8, 53, 0,     1      ),  /* bool                          */
    VAR_WRITE_ROW(mrac_z_feat_mask,   VW_U8, 54,  0,       15     ),  /* Z MRAC feature bits, mrac.h   */
    VAR_WRITE_ROW(sysid_target,       VW_U8, 55,  0,       4      ),  /* 0 rate, 1 torque, 2 angle, 3 vel, 4 pos; sysid.h */
    VAR_WRITE_ROW(g_drift_nn_live,    VW_U8, 56,  0,       2      ),  /* NN 0 shadow, 1 pos, 2 pos+vel */
    VAR_WRITE_ROW(g_drift_nn_clamp_mps, VW_F32, 57, 0.0f,  0.30f  ),  /* m/s, NN residual clamp        */
    VAR_WRITE_ROW(g_alt_vz_mode,      VW_U8, 58,  0,       1      ),  /* altitude vz mode: 0=FD, 1=CF  */
    VAR_WRITE_ROW(g_traj_slow_on,     VW_U8,  70,   0,       1      ),  /* bool: slow clock when behind  */
    VAR_WRITE_ROW(g_traj_slow_elo_cm, VW_F32, 71,   1.0f,  200.0f   ),  /* cm, slow clock e_lo PROPOSED  */
    VAR_WRITE_ROW(g_traj_slow_ehi_cm, VW_F32, 72,   5.0f,  300.0f   ),  /* cm, slow clock e_hi PROPOSED  */
    VAR_WRITE_ROW(g_traj_arrive_r_cm, VW_F32, 73,   1.0f,  100.0f   ),  /* cm, wp arrival r PROPOSED     */
    VAR_WRITE_ROW(g_xyc_pred_lead,    VW_U8,  74,   0,       1      ),  /* bool: predictor ref lead+aff  */
    VAR_WRITE_ROW(g_xyc_lqr_k0,       VW_F32, 75,   0.0f,  200.0f   ),  /* LQR K[0] pos; ground only     */
    VAR_WRITE_ROW(g_xyc_lqr_k1,       VW_F32, 76,   0.0f,  100.0f   ),  /* LQR K[1] vel; ground only     */
    VAR_WRITE_ROW(g_xyc_lqr_k2,       VW_F32, 77, -10.0f,   50.0f   ),  /* LQR K[2] acc; ground only     */
    VAR_WRITE_ROW(g_xyc_lqr_k3,       VW_F32, 78,   0.0f,   50.0f   ),  /* LQR K[3] int; ground only     */
    VAR_WRITE_ROW(g_xyc_adrc_wc,      VW_F32, 79,   0.1f,   20.0f   ),  /* ADRC wc rad/s; ground only    */
    VAR_WRITE_ROW(g_xyc_adrc_b0,      VW_F32, 80,   0.05f,  10.0f   ),  /* ADRC b0; ground only          */
    VAR_WRITE_ROW(g_xyc_model_k,      VW_F32, 81,   0.05f,   5.0f   ),  /* xy plant k; ground only       */
    VAR_WRITE_ROW(g_xyc_model_al,     VW_F32, 82,   0.01f,   0.999f ),  /* xy plant al; ground only      */
    VAR_WRITE_ROW(g_xyc_model_drag,   VW_F32, 83,   0.0f,    5.0f   )   /* xy plant drag; ground only    */
};

/* GS CMD 0x2D: write row `id` = val. Returns 0 (written) or a VAR_WRITE_REJECT_* code (nothing written);
 * *name is the row's variable name when the id is known. NaN fails the range check. */
uint8_t VarWrite_Apply(uint8_t id, float val, const char **name)
{
	uint8_t i;
	for (i = 0U; i < (uint8_t)(sizeof(s_var_write) / sizeof(s_var_write[0])); i++) {
		const VarWriteRow_t *r = &s_var_write[i];
		if (r->id == id) {
			*name = r->name;
			if (!((val >= r->min) && (val <= r->max))) {
				return VAR_WRITE_REJECT_RANGE;
			}
			if (r->type == VW_U8) {
				uint8_t u = (uint8_t)val;
				if ((float)u != val) {
					return VAR_WRITE_REJECT_RANGE;   /* a u8 row takes an integral value only */
				}
				/* The target is latched at SysID_Start, and only Cmd_SysIdControl (send_data.c, the same GS
				 * command table as this Cmd_VarWrite caller) starts a run, so IDLE here stays IDLE until the
				 * write lands. */
				if (r->addr == (volatile void *)&sysid_target && SysID_GetState() != (uint8_t)SYSID_IDLE) {
					return VAR_WRITE_REJECT_BUSY;
				}
				if (r->id == 47U && (FlightFSM_GetState() == FLIGHT_STATE_ARMED || DroneStatus.ARM_Status == Armed)) {
					return VAR_WRITE_REJECT_BUSY;
				}
				*(volatile uint8_t *)r->addr = u;
				if (r->id == 47U || r->id == 74U) {
					(void)XyCtrl_WriteParam(0U, r->id, val);
				}
			} else {
				if ((r->id >= 75U && r->id <= 83U) &&
				    (FlightFSM_GetState() == FLIGHT_STATE_ARMED || DroneStatus.ARM_Status == Armed)) {
					return VAR_WRITE_REJECT_BUSY;
				}
				*(volatile float *)r->addr = val;
				if (r->id >= 75U && r->id <= 83U) {
					(void)XyCtrl_WriteParam(0U, r->id, val);
				}
			}
			return 0U;
		}
	}
	return VAR_WRITE_REJECT_UNKNOWN;
}

/* Lab stage (StabilizerTask s_stage, ground only): figure-8 path, tilt, laps and yaw rate for the stage. */
void Traj_SetStage(float tilt_deg, float laps, float yaw_rate_dps)
{
	traj_id = 4U;
	traj_p.f8_tilt_deg = tilt_deg;
	traj_p.f8_laps = laps;
	traj_p.yaw_rate_dps = yaw_rate_dps;
}

static TrajParams_t s_tp;                         /* traj_p latched at traj_go                    */
static float s_tph = 0.0f;                        /* time in the current phase [s]                */
static float s_om = 0.0f;                         /* circle / figure-8 rate [rad per path second] */
static float s_ref_x = 0.0f, s_ref_y = 0.0f, s_ref_z = 0.0f; /* last move reference (return start) */
static uint8_t s_stopped = 0U;                    /* traj_stop ended the move                     */
static traj_shape_t s_shape = { 0U, 0.0f, 0U, 0.0f, 0.0f, 1.0f }; /* circle / figure-8 shape, set by Traj_Check */
static float s_yaw_cmd = 0.0f;                    /* slewed yaw reference [deg]                   */
static float s_goto_s = 0.0f;                     /* goto time after the speed stretch [s]        */
static float s_from_x = 0.0f, s_from_y = 0.0f, s_from_z = 0.0f; /* setpoint at traj_go (goto start) */

static float Traj_Ramp01(float u)
{
	if (u <= 0.0f) {
		return 0.0f;
	}
	if (u >= 1.0f) {
		return 1.0f;
	}
	return 0.5f - 0.5f * cosf(TRAJ_PI * u);
}

/* Path time for the circle and figure-8. Profile 1: the path speed rises from 0 over ramp_s and falls back to 0
   over the last ramp_s, so the path time reaches move_s - ramp_s at move_s. Profile 0: the path time is t. */
static float Traj_Warp(float t)
{
	float T = s_tp.move_s;
	float R = s_tp.ramp_s;
	if (s_tp.profile == 2U) {
		float w = 0.0f;
		TrajShape_WarpDerivs(2U, t, T, R, &w, NULL, NULL);
		return w;
	}

	if (s_tp.profile == 0U) {
		return t;
	}
	if (t <= 0.0f) {
		return 0.0f;
	}
	if (t >= T) {
		return T - R;
	}
	if (t < R) {
		return 0.5f * t - R / (2.0f * TRAJ_PI) * sinf(TRAJ_PI * t / R);
	}
	if (t > T - R) {
		float u = T - t;
		return (T - R) - (0.5f * u - R / (2.0f * TRAJ_PI) * sinf(TRAJ_PI * u / R));
	}
	return 0.5f * R + (t - R);
}

/* Nose along the circle / figure-8 at move time t [deg]: the x/y path tangent minus where the nose sits at yaw 0
   (taken in path angle, so it stays defined where the profile-1 ramps stop the path). */
static float Traj_FollowYaw(float t)
{
	return AutoflyTask_WrapDeg(TrajShape_TangentDeg(&s_shape, s_om * Traj_Warp(t), s_om) - s_tp.yaw_nose_deg);
}

/* Offset from the start point at move time t: x/y/z all in cm. */
static void Traj_Offset(float t, float *dx, float *dy, float *dz)
{
	float a = 0.0f;   /* step / zigzag offset along s_tp.axis [cm] */

	*dx = 0.0f;
	*dy = 0.0f;
	*dz = 0.0f;
	if (traj_active == 1U) {
		/* step: out for the first half of move_s, back for the second half */
		if (s_tp.profile == 2U) {
			float p_up = 0.0f, p_dn = 0.0f;
			TrajShape_Quintic1D(t, s_tp.ramp_s, 0.0f, 0.0f, 0.0f, 1.0f, 0.0f, 0.0f, &p_up, NULL, NULL);
			TrajShape_Quintic1D(t - 0.5f * s_tp.move_s, s_tp.ramp_s, 0.0f, 0.0f, 0.0f, 1.0f, 0.0f, 0.0f, &p_dn, NULL, NULL);
			a = s_tp.step_cm * (p_up - p_dn);
		} else
		if (s_tp.profile == 0U) {
			a = (t < 0.5f * s_tp.move_s) ? s_tp.step_cm : 0.0f;
		} else {
			a = s_tp.step_cm * (Traj_Ramp01(t / s_tp.ramp_s) -
			                    Traj_Ramp01((t - 0.5f * s_tp.move_s) / s_tp.ramp_s));
		}
	} else if (traj_active == 2U) {
		/* zigzag from 0, + side first; profile 1 ramps the amplitude in and out */
		float p = s_tp.zz_hz * t;
		p -= floorf(p);
		if (s_tp.zz_shape == 0U) {
			a = (p < 0.25f) ? 4.0f * p : ((p < 0.75f) ? 2.0f - 4.0f * p : 4.0f * p - 4.0f);
		} else {
			a = sinf(2.0f * TRAJ_PI * p);
		}
		a *= s_tp.zz_amp_cm;
		if (s_tp.profile != 0U) {
			a *= Traj_Ramp01(t / s_tp.ramp_s) * Traj_Ramp01((s_tp.move_s - t) / s_tp.ramp_s);
		}
	} else if (traj_active == 3U || traj_active == 4U) {
		TrajShape_Offset(&s_shape, s_om * Traj_Warp(t), dx, dy, dz);
		return;
	}
	if (s_tp.axis == 0U) {
		*dx = a;
	} else if (s_tp.axis == 1U) {
		*dy = a;
	} else {
		*dz = a;
	}
}

/* Range, peak speed and floor/ceiling check of s_tp around traj_home_*; sets s_om. 0 = OK, else the refusal code. */
static uint8_t Traj_Check(void)
{
	float lo = 0.0f, hi = 0.0f;                          /* step / zigzag offset range on the axis [cm] */
	float x0 = 0.0f, x1 = 0.0f, y0 = 0.0f, y1 = 0.0f, z0 = 0.0f, z1 = 0.0f; /* offset box [cm]     */
	float v = 0.0f;                                      /* peak reference speed [cm/s]                 */
	float xm = 0.0f, ym = 0.0f;                          /* farthest |corner| of the box on x, y [cm]  */
	float path_s = s_tp.move_s - ((s_tp.profile != 0U) ? s_tp.ramp_s : 0.0f);
	float zt;                                            /* ceiling of the box on z [cm]               */
	wfb_safety_limits_t lim;

	if (traj_id < 1U || traj_id > 4U) {
		return 0xE2U;
	}
	if (s_tp.move_s < 5.0f || s_tp.ret_s < 3.0f || s_tp.ret_s > 10.0f || s_tp.hold_s < 0.0f ||
	    s_tp.hold_s > 5.0f || s_tp.move_s + s_tp.ret_s + s_tp.hold_s > TRAJ_TOTAL_MAX_S + 0.001f ||
	    s_tp.profile > 2U || s_tp.axis > 2U ||
	    (s_tp.profile != 0U && (s_tp.ramp_s < 0.5f || s_tp.ramp_s > 0.25f * s_tp.move_s))) {
		return 0xEEU;
	}
	if (s_tp.yaw_follow > 1U || s_tp.yaw_rate_dps < 10.0f || s_tp.yaw_rate_dps > 180.0f ||
	    s_tp.yaw_nose_deg < -180.0f || s_tp.yaw_nose_deg > 180.0f ||
	    (s_tp.yaw_follow == 1U && s_tp.ret_s + s_tp.hold_s < 180.0f / s_tp.yaw_rate_dps)) {
		return 0xEEU;   /* the yaw must be able to swing back to the start heading before the end */
	}
	s_om = 0.0f;
	s_shape.id = traj_id;
	s_shape.circ_r_cm = s_tp.circ_r_cm;
	s_shape.f8_type = s_tp.f8_type;
	s_shape.f8_a_cm = s_tp.f8_a_cm;
	s_shape.tilt_s = 0.0f;
	s_shape.tilt_c = 1.0f;
	if (traj_id == 1U) {
		if (s_tp.step_cm == 0.0f || fabsf(s_tp.step_cm) > ((s_tp.profile == 0U) ? 50.0f : 100.0f)) {
			return 0xEEU;   /* a hard step (profile 0) is limited to 50 cm */
		}
		if (s_tp.profile == 2U) {
			v = 1.875f * fabsf(s_tp.step_cm) / s_tp.ramp_s;
		} else if (s_tp.profile == 1U) {
			v = 0.5f * TRAJ_PI * fabsf(s_tp.step_cm) / s_tp.ramp_s;
		}
		lo = (s_tp.step_cm < 0.0f) ? s_tp.step_cm : 0.0f;
		hi = (s_tp.step_cm > 0.0f) ? s_tp.step_cm : 0.0f;
	} else if (traj_id == 2U) {
		if (s_tp.zz_amp_cm <= 0.0f || s_tp.zz_amp_cm > 80.0f || s_tp.zz_hz <= 0.0f || s_tp.zz_hz > 1.0f ||
		    s_tp.zz_shape > 1U) {
			return 0xEEU;
		}
		v = ((s_tp.zz_shape == 0U) ? 4.0f : 2.0f * TRAJ_PI) * s_tp.zz_amp_cm * s_tp.zz_hz;
		lo = -s_tp.zz_amp_cm;
		hi = s_tp.zz_amp_cm;
	} else if (traj_id == 3U) {
		if (s_tp.circ_r_cm < 10.0f || s_tp.circ_r_cm > 80.0f || s_tp.circ_laps <= 0.0f || s_tp.circ_laps > 3.0f) {
			return 0xEEU;
		}
		s_om = 2.0f * TRAJ_PI * s_tp.circ_laps / path_s;
		v = s_om * s_tp.circ_r_cm;
		x0 = -2.0f * s_tp.circ_r_cm;
		y0 = -s_tp.circ_r_cm;
		y1 = s_tp.circ_r_cm;
	} else {
		if (s_tp.f8_a_cm < 10.0f || s_tp.f8_a_cm > 80.0f || s_tp.f8_laps <= 0.0f || s_tp.f8_laps > 4.0f ||
		    s_tp.f8_type > 1U || s_tp.f8_tilt_deg < -60.0f || s_tp.f8_tilt_deg > 60.0f) {
			return 0xEEU;
		}
		s_shape.tilt_s = sinf(s_tp.f8_tilt_deg * (TRAJ_PI / 180.0f));
		s_shape.tilt_c = cosf(s_tp.f8_tilt_deg * (TRAJ_PI / 180.0f));
		s_om = 2.0f * TRAJ_PI * s_tp.f8_laps / path_s;
		if (s_tp.f8_type == 1U) {
			v = 1.42f * s_om * s_tp.f8_a_cm;
			x0 = -0.5f * s_tp.f8_a_cm;
			x1 = 0.5f * s_tp.f8_a_cm;
			y0 = -s_tp.f8_a_cm;
			y1 = s_tp.f8_a_cm;
		} else {
			v = s_om * s_tp.f8_a_cm;
			x0 = -2.0f * s_tp.f8_a_cm;
			y0 = -0.36f * s_tp.f8_a_cm;
			y1 = 0.36f * s_tp.f8_a_cm;
		}
		/* tilted plane: the y lobes (symmetric, -y0 = y1) shrink by cos and climb / sink by sin; rotation
		   keeps the speed */
		z1 = fabsf(y1 * s_shape.tilt_s);
		z0 = -z1;
		y0 *= s_shape.tilt_c;
		y1 *= s_shape.tilt_c;
		if (z1 > TRAJ_Z_OFF_MAX_CM) {
			return 0xEEU;
		}
	}
	if (traj_id <= 2U) {
		if (s_tp.axis == 0U) {
			x0 = lo;
			x1 = hi;
		} else if (s_tp.axis == 1U) {
			y0 = lo;
			y1 = hi;
		} else {
			if (-lo > TRAJ_Z_OFF_MAX_CM || hi > TRAJ_Z_OFF_MAX_CM) {
				return 0xEEU;
			}
			z0 = lo;
			z1 = hi;
		}
	}
	/* the return flies at most the farthest box corner back in ret_s, cosine peak = pi/2 x the mean speed */
	xm = (-x0 > x1) ? -x0 : x1;
	ym = (-y0 > y1) ? -y0 : y1;
	zt = (-z0 > z1) ? -z0 : z1;
	if (v > TRAJ_V_MAX_CMS ||
	    0.5f * TRAJ_PI * sqrtf(xm * xm + ym * ym + zt * zt) / s_tp.ret_s > TRAJ_V_MAX_CMS) {
		return 0xEEU;
	}

	if (traj_fence_off == 0U) {
		wfb_traj_limits_t tlim;
		wfb_traj_default_limits(&tlim);
		if (traj_home_x + x0 * 0.01f < -tlim.x_abs_m || traj_home_x + x1 * 0.01f > tlim.x_abs_m ||
		    traj_home_y + y0 * 0.01f < -tlim.y_abs_m || traj_home_y + y1 * 0.01f > tlim.y_abs_m) {
			return 0xEFU;
		}
		wfb_safety_default_limits(&lim);
		zt = lim.ceiling_m - lim.soft_margin_m;
		if (traj_home_z + z1 * 0.01f > zt) {
			return 0xEFU;
		}
	}
	if (traj_home_z + z0 * 0.01f < TRAJ_Z_MIN_M) {
		return 0xEFU;   /* the floor holds even with the fence off (a tilted figure-8 dips up to 40 cm) */
	}
	return 0U;
}

/* All four virtual sticks centred: hold, and the RC heartbeat stays fresh (call inside a critical section). */
static void Traj_SticksCentred(void)
{
	RCInput_SetVirtualStick(RC_AXIS_THR, 0.0f);
	RCInput_SetVirtualStick(RC_AXIS_PITCH, 0.0f);
	RCInput_SetVirtualStick(RC_AXIS_ROLL, 0.0f);
	RCInput_SetVirtualStick(RC_AXIS_YAW, 0.0f);
}

/* Reference rates for the stabilizer's feed-forward (g_traj_ff_preset). The preset and the GS circle / sinusoid /
 * figure-8 paths are pure position references, so without these the P position loops trail them by v / Kp. Finite
 * difference of this tick's continuous reference against the last one (before the waypoint_spacing hold); 0 while
 * neither runs. The GS paths get no yaw-rate FF (the circle turns the nose through yawPID.Des alone). */
float g_traj_vff_x = 0.0f, g_traj_vff_y = 0.0f;   /* cm/s, world      */
float g_traj_vff_z = 0.0f;                        /* m/s              */
float g_traj_vff_yaw = 0.0f;                      /* deg/s, yawPID frame */
float g_traj_aff_x = 0.0f, g_traj_aff_y = 0.0f;   /* cm/s^2, world    */
float g_traj_aff_z = 0.0f;                        /* m/s^2            */
float g_traj_fut_x[3] = { 0.0f, 0.0f, 0.0f };     /* [des_d, vff_d, aff_d] at t + d*dt */
float g_traj_fut_y[3] = { 0.0f, 0.0f, 0.0f };
uint8_t g_traj_fut_valid = 0U;
volatile float   g_traj_slow_s = 1.0f;            /* active slow-clock scale s(t) in [0, 1] */
volatile uint8_t g_traj_wp_seq = 0U;              /* increments once per waypoint arrival */
volatile uint8_t g_traj_wp_last_idx = 0U;
volatile float   g_traj_wp_t_plan_s = 0.0f;
volatile float   g_traj_wp_t_actual_s = 0.0f;
volatile float   g_traj_wp_peak_err_cm = 0.0f;
volatile float   g_traj_wp_time_lost_s = 0.0f;
static float s_slow_s = 1.0f, s_slow_ds_dt = 0.0f;
static traj_wp_tracker_t s_wp_trk;

const traj_wp_tracker_t *Traj_GetWpTracker(void)
{
	return &s_wp_trk;
}
static uint8_t s_ff_have = 0U;
static float s_ff_x, s_ff_y, s_ff_z, s_ff_yaw;   /* last tick's reference */
static float s_c_x, s_c_y, s_c_z;                /* this tick's reference */

static void Traj_Commit(float x, float y, float z)
{
	s_c_x = x;
	s_c_y = y;
	s_c_z = z;
	AutoflyTask_CommitRef(x, y, z);
}

static void Traj_FFClear(void)
{
	g_traj_vff_x = 0.0f;
	g_traj_vff_y = 0.0f;
	g_traj_vff_z = 0.0f;
	g_traj_vff_yaw = 0.0f;
	g_traj_aff_x = 0.0f;
	g_traj_aff_y = 0.0f;
	g_traj_aff_z = 0.0f;
	g_traj_fut_valid = 0U;
	TrajFF_SetPresetAcc(0.0f, 0.0f);
	s_ff_have = 0U;
}

static void Traj_FFUpdate(void)
{
	if (s_ff_have) {
		g_traj_vff_x = (s_c_x - s_ff_x) / AFLY_DT_S;
		g_traj_vff_y = (s_c_y - s_ff_y) / AFLY_DT_S;
		g_traj_vff_z = (s_c_z - s_ff_z) / AFLY_DT_S;
		g_traj_vff_yaw = TrajShape_WrapDeg(s_yaw_cmd - s_ff_yaw) / AFLY_DT_S;
		if (traj_active == 1U && s_tp.profile == 0U) {   /* hard step: no rate, only a one-tick spike */
			g_traj_vff_x = 0.0f;
			g_traj_vff_y = 0.0f;
			g_traj_vff_z = 0.0f;
		}
	}
	s_ff_x = s_c_x;
	s_ff_y = s_c_y;
	s_ff_z = s_c_z;
	s_ff_yaw = s_yaw_cmd;
	s_ff_have = 1U;
	g_traj_aff_x = 0.0f;
	g_traj_aff_y = 0.0f;
	g_traj_aff_z = 0.0f;
	g_traj_fut_valid = 0U;
	TrajFF_SetPresetAcc(0.0f, 0.0f);
	if ((traj_active == 3U || traj_active == 4U) && traj_phase == 1U &&
	    (s_tp.profile == 2U || g_xyc_pred_lead != 0U || g_traj_slow_on != 0U)) {
		float w = 0.0f, dw = 0.0f, ddw = 0.0f;
		float pos_p[3], vel_p[3], acc_p[3];
		TrajShape_WarpDerivs(s_tp.profile, s_tph, s_tp.move_s, s_tp.ramp_s, &w, &dw, &ddw);
		TrajShape_Derivs(&s_shape, s_om * w, s_om * dw, s_om * ddw, pos_p, vel_p, acc_p);
		{
			float vfx = 0.0f, vfy = 0.0f, afx = 0.0f, afy = 0.0f;
			TrajShape_ScaleFF(s_slow_s, s_slow_ds_dt, vel_p[0], vel_p[1], acc_p[0], acc_p[1],
			                  &vfx, &vfy, &afx, &afy);
			g_traj_vff_x = vfx;
			g_traj_vff_y = vfy;
			g_traj_aff_x = afx;
			g_traj_aff_y = afy;
		}
		if (g_traj_ff_preset != 0U && g_xy_ctrl_kind == 0U) {
			TrajFF_SetPresetAcc(g_traj_aff_x, g_traj_aff_y);
		}
		if (g_xyc_pred_lead != 0U) {
			float dt_lead = s_slow_s * (float)XyCtrl_Delay() * 0.01f;
			float t_fut = s_tph + dt_lead;
			float wf = 0.0f, dwf = 0.0f, ddwf = 0.0f;
			float pf[3], vf[3], af[3], vfx = 0.0f, vfy = 0.0f, afx = 0.0f, afy = 0.0f;
			if (t_fut > s_tp.move_s) t_fut = s_tp.move_s;
			TrajShape_WarpDerivs(s_tp.profile, t_fut, s_tp.move_s, s_tp.ramp_s, &wf, &dwf, &ddwf);
			TrajShape_Derivs(&s_shape, s_om * wf, s_om * dwf, s_om * ddwf, pf, vf, af);
			TrajShape_ScaleFF(s_slow_s, 0.0f, vf[0], vf[1], af[0], af[1], &vfx, &vfy, &afx, &afy);
			g_traj_fut_x[0] = traj_home_x + pf[0];
			g_traj_fut_x[1] = vfx;
			g_traj_fut_x[2] = afx;
			g_traj_fut_y[0] = traj_home_y + pf[1];
			g_traj_fut_y[1] = vfy;
			g_traj_fut_y[2] = afy;
			g_traj_fut_valid = 1U;
		}
	}
}

static void Traj_End(uint8_t status)
{
	if (RCInput_GetAuthority()) {
		taskENTER_CRITICAL();
		RCInput_SetAuthority(0U);
		taskEXIT_CRITICAL();
	}
	traj_active = 0U;
	traj_phase = 0U;
	traj_stop = 0U;
	traj_status = status;
	Traj_FFClear();
}

/* TWC.execute held by an owner a preset must not fight. A WFB HOVER only holds the point, so a preset takes it over:
 * the glue yields HOVER while traj_active (wfb_glue.c) and holds again at the start point once the preset is back. */
static uint8_t Traj_TwcBusy(void)
{
	if (TWC.execute == 0)
		return 0U;
	return (g_wfb_status.gs_flight_active > 0.5f && g_wfb_status.prim_state == (float)WFB_PRIM_HOVER) ? 0U : 1U;
}

static void Traj_Start(void)
{
	uint8_t code;

	if (FlightFSM_GetState() != FLIGHT_STATE_ARMED || flight_phase != FLIGHT_PHASE_FLYING ||
	    DroneStatus.FlyMode != FlyMode_SDK) {
		traj_status = 0xE0U;
		return;
	}
	if (RCInput_GetAuthority() || Traj_TwcBusy() ||
	    sinusoid_path.active || circle_path.active || figure8_path.active) {
		traj_status = 0xE1U;
		return;
	}
	s_tp = traj_p;
	traj_home_x = Ctrler.locxPID.Des;
	traj_home_y = Ctrler.locyPID.Des;
	traj_home_z = Ctrler.Z_posPID.Des;
	traj_home_yaw = Ctrler.yawPID.Des;
	s_yaw_cmd = traj_home_yaw;
	s_from_x = traj_home_x;
	s_from_y = traj_home_y;
	s_from_z = traj_home_z;
	if (s_tp.start_fixed > 1U || (s_tp.start_fixed == 1U &&
	    !(fabsf(s_tp.start_x_cm) <= 500.0f && fabsf(s_tp.start_y_cm) <= 500.0f &&
	      s_tp.start_z_m >= 0.5f && s_tp.start_z_m <= 1.5f && s_tp.goto_s >= 2.0f && s_tp.goto_s <= 10.0f &&
	      s_tp.settle_s >= 0.0f && s_tp.settle_s <= 5.0f))) {
		traj_status = 0xEEU;
		return;
	}
	if (s_tp.start_fixed == 1U) {
		/* the preset runs around the fixed point, so Traj_Check fences the box around it */
		float ddx = s_tp.start_x_cm - s_from_x;
		float ddy = s_tp.start_y_cm - s_from_y;
		float ddz = 100.0f * (s_tp.start_z_m - s_from_z);
		float need = 0.5f * TRAJ_PI * sqrtf(ddx * ddx + ddy * ddy + ddz * ddz) / TRAJ_V_MAX_CMS;
		traj_home_x = s_tp.start_x_cm;
		traj_home_y = s_tp.start_y_cm;
		traj_home_z = s_tp.start_z_m;
		s_goto_s = (need > s_tp.goto_s) ? need : s_tp.goto_s;   /* cosine blend peaks at pi/2 x the mean speed */
	}
	code = Traj_Check();
	if (code != 0U) {
		traj_status = code;
		return;
	}

	/* SetAuthority(1) leaves the virtual throttle at -1: centre it before any other task can run */
	taskENTER_CRITICAL();
	RCInput_SetAuthority(1U);
	Traj_SticksCentred();
	TWC.execute = 0;   /* a WFB hover hold: the preset owns the setpoints now */
	taskEXIT_CRITICAL();

	AutoflyTask_WaypointReset();
	s_tph = 0.0f;
	s_stopped = 0U;
	s_ref_x = traj_home_x;
	s_ref_y = traj_home_y;
	s_ref_z = traj_home_z;
	traj_t = 0.0f;
	traj_stop = 0U;
	traj_active = traj_id;
	traj_phase = (s_tp.start_fixed == 1U) ? 4U : 1U;
	s_slow_s = 1.0f;
	s_slow_ds_dt = 0.0f;
	g_traj_slow_s = 1.0f;
	TrajWp_InitPreset(&s_wp_trk, &s_shape, s_tp.profile, s_tp.move_s, s_tp.ramp_s,
	                  (traj_id == 3U) ? s_tp.circ_laps : s_tp.f8_laps,
	                  s_tp.step_cm, s_tp.axis, g_traj_arrive_r_cm);
	traj_status = 1U;
}

static void Traj_Tick(void)
{
	float dx, dy, dz;

	if (traj_go != 0U) {
		traj_go = 0U;
		traj_id_seen = traj_id;
		if (traj_id >= (uint8_t)'1' && traj_id <= (uint8_t)'4') {
			traj_id = (uint8_t)(traj_id - (uint8_t)'0');    /* typed as the character '1'..'4' */
		}
		if (traj_active == 0U) {
			Traj_Start();
		}
	}
	if (traj_active == 0U) {
		traj_stop = 0U;
		if (circle_path.active || sinusoid_path.active || figure8_path.active) {
			Traj_FFUpdate();          /* the GS path's Traj_Commit of this tick (AutoflyTask runs it first) */
			g_traj_vff_yaw = 0.0f;
		} else {
			Traj_FFClear();
		}
		return;
	}

	/* takeover (any stick move drops the authority), disarm, landing, or a GS path: stop here, no return */
	if (!RCInput_GetAuthority() || FlightFSM_GetState() != FLIGHT_STATE_ARMED ||
	    flight_phase != FLIGHT_PHASE_FLYING || DroneStatus.FlyMode != FlyMode_SDK ||
	    Traj_TwcBusy() || sinusoid_path.active || circle_path.active || figure8_path.active) {
		Traj_End(3U);
		return;
	}

	taskENTER_CRITICAL();
	Traj_SticksCentred();
	TWC.execute = 0;   /* a WFB hover tick that ran before the glue saw traj_active */
	taskEXIT_CRITICAL();

	if (traj_phase == 1U && g_traj_slow_on != 0U) {
		float ex = s_c_x - Ctrler.locxPID.FB;
		float ey = s_c_y - Ctrler.locyPID.FB;
		float err_cm = sqrtf(ex * ex + ey * ey);
		s_slow_s = TrajShape_SlowClockStep(s_slow_s, err_cm, g_traj_slow_elo_cm, g_traj_slow_ehi_cm,
		                                   TRAJ_SLOW_RECOVER_PER_S, AFLY_DT_S, &s_slow_ds_dt);
		g_traj_slow_s = s_slow_s;
		s_tph += s_slow_s * AFLY_DT_S;
	} else {
		s_slow_s = 1.0f;
		s_slow_ds_dt = 0.0f;
		g_traj_slow_s = 1.0f;
		s_tph += AFLY_DT_S;
	}
	traj_t += AFLY_DT_S;

	/* traj_stop before the move: finish the flight to the start point, then hold and end there */
	if ((traj_phase == 4U || traj_phase == 5U) && traj_stop != 0U) {
		s_stopped = 1U;
		traj_stop = 0U;
		if (traj_phase == 5U) {
			traj_phase = 3U;
			s_tph = 0.0f;
		}
	}
	if (traj_phase == 4U) {
		/* fly from the setpoint at traj_go to the fixed start point, cosine blend over s_goto_s */
		float k = Traj_Ramp01(s_tph / s_goto_s);
		Traj_Commit(s_from_x + (traj_home_x - s_from_x) * k,
		            s_from_y + (traj_home_y - s_from_y) * k,
		            s_from_z + (traj_home_z - s_from_z) * k);
		if (s_tph >= s_goto_s) {
			AutoflyTask_WaypointReset();
			Traj_Commit(traj_home_x, traj_home_y, traj_home_z);
			traj_phase = s_stopped ? 3U : 5U;
			s_tph = 0.0f;
		}
	} else if (traj_phase == 5U) {
		Traj_Commit(traj_home_x, traj_home_y, traj_home_z);
		if (s_tph >= s_tp.settle_s) {
			traj_phase = 1U;
			s_tph = 0.0f;
		}
	}

	if (traj_phase == 1U) {
		if (traj_stop != 0U || s_tph >= s_tp.move_s) {
			s_stopped = traj_stop;
			traj_stop = 0U;
			traj_phase = 2U;
			s_tph = 0.0f;
		} else {
			Traj_Offset(s_tph, &dx, &dy, &dz);
			s_ref_x = traj_home_x + dx;
			s_ref_y = traj_home_y + dy;
			s_ref_z = traj_home_z + 0.01f * dz;
			Traj_Commit(s_ref_x, s_ref_y, s_ref_z);
			{
				uint8_t wp_i = 0U;
				if (TrajWp_Tick(&s_wp_trk, AFLY_DT_S, s_slow_s,
				                Ctrler.locxPID.FB - traj_home_x, Ctrler.locyPID.FB - traj_home_y,
				                s_ref_x - traj_home_x, s_ref_y - traj_home_y, &wp_i) != 0U) {
					g_traj_wp_seq++;
					g_traj_wp_last_idx = wp_i;
					g_traj_wp_t_plan_s = s_wp_trk.entries[wp_i].t_plan_s;
					g_traj_wp_t_actual_s = s_wp_trk.entries[wp_i].t_actual_s;
					g_traj_wp_peak_err_cm = s_wp_trk.entries[wp_i].peak_err_cm;
					g_traj_wp_time_lost_s = s_wp_trk.entries[wp_i].time_lost_s;
				}
			}
		}
	}
	if (traj_phase == 2U) {
		/* fly back from the last move reference to the start point, cosine blend over ret_s */
		float k = Traj_Ramp01(s_tph / s_tp.ret_s);
		Traj_Commit(s_ref_x + (traj_home_x - s_ref_x) * k,
		            s_ref_y + (traj_home_y - s_ref_y) * k,
		            s_ref_z + (traj_home_z - s_ref_z) * k);
		if (s_tph >= s_tp.ret_s) {
			/* commit the start point exactly (the quantizer may hold up to waypoint_spacing short of it) */
			AutoflyTask_WaypointReset();
			Traj_Commit(traj_home_x, traj_home_y, traj_home_z);
			traj_phase = 3U;
			s_tph = 0.0f;
		}
	} else if (traj_phase == 3U) {
		Traj_Commit(traj_home_x, traj_home_y, traj_home_z);
		if (s_tph >= s_tp.hold_s) {
			Traj_End(s_stopped ? 4U : 2U);
			return;
		}
	}

	/* yaw: nose along the path while a circle / figure-8 moves (turned to the first heading while it
	   settles), back to the heading at traj_go after; slewed */
	if (s_tp.yaw_follow == 1U && (traj_active == 3U || traj_active == 4U)) {
		float target = (traj_phase == 1U) ? Traj_FollowYaw(s_tph) :
		               (traj_phase == 5U && !s_stopped) ? Traj_FollowYaw(0.0f) : traj_home_yaw;
		s_yaw_cmd = TrajShape_YawStep(s_yaw_cmd, target, s_tp.yaw_rate_dps * AFLY_DT_S);
	} else {
		s_yaw_cmd = traj_home_yaw;
	}
	Ctrler.yawPID.Des = s_yaw_cmd;
	Traj_FFUpdate();
}

/* ------------------------------------------------------------------
 * Public API: 5 ms entry point
 * ------------------------------------------------------------------ */

void AutoflyTask(void)
{
	AutoflyTask_PathArbitrate();

	if (circle_path.active) {
		AutoflyTask_RunCircle();
	} else if (sinusoid_path.active) {
		AutoflyTask_RunSinusoid();
	} else if (figure8_path.active) {
		AutoflyTask_RunFigure8();
	}

	Traj_Tick();

	if (((sbus_channel[5] == AFLY_KEY_SBUS_HIGH) && (sbus_channel[4] == AFLY_KEY_SBUS_HIGH) && (sbus_lost == 0)) ) {
		KeyPressedTimeMS += AFLY_TICK_MS;
	} else {
		KeyPressedTimeMS = 0U;
	}

		if(KeyPressedTimeMS >=AFLY_KEY_HOLD_MS )
		{
			KeySDKflag =1;
		}
		else 
		{
			KeySDKflag =0;
		}
		
		if(KeySDKflag ==1)
		{
			SDK_StateMachine_Loop(); 
		}
		else
		{
			SDK_StateMachine_Reset();
		}
}

/* ------------------------------------------------------------------
 * Public API: legacy SDK script state machine
 * ------------------------------------------------------------------ */

void SDK_StateMachine_Init(void)
{
	CurrentSDKState = 0;
	/* The script table is empty (the old program was removed), so SDKStateMAX wraps to 0xFFFFFFFE. */
	SDKStateMAX = CurrentSDKState-2;
	CurrentSDKState=0;
}


void SDK_StateMachine_Loop(void)
{
	static unsigned int LastSDKState;
	switch ( SDK_StateMachine[ CurrentSDKState ] )
	{
		
		case SDK_Cmd_DelayWake:
			if(LastSDKState!=SDK_Cmd_DelayWake)
			{
				Ctrler.locxPID.Des = Ctrler.locxPID.FB;
				Ctrler.locyPID.Des = Ctrler.locyPID.FB;
				Ctrler.yawPID.Des  = Ctrler.yawPID.FB;
				Ctrler.Z_posPID.Des= Ctrler.Z_posPID.FB;
				
				temp_V_x=-1;
				temp_V_y=-1;
				temp_V_h=0;
				temp_Gyroz=0;
			}
			if(SDK_StateMachine[ CurrentSDKState +1 ] >=20)SDK_DelayWakeFlag=1;
			else SDK_DelayWakeFlag=0;

			break;
			
		
		case SDK_Cmd_TakeOff:
    
			Ctrler.Z_posPID.Des = SDK_Height;
			if(Ctrler.Z_posPID.FB >=SDK_Height_L && Ctrler.Z_posPID.FB<=SDK_Height_H)
				SDK_StateMachine[ CurrentSDKState +1 ]=0;
		  
			break;
			

		case SDK_Cmd_PosHold:
			if(LastSDKState!=SDK_Cmd_PosHold)
			{		
				Ctrler.locyPID.Des = Ctrler.locyPID.FB;
				Ctrler.locxPID.Des = Ctrler.locxPID.FB;
			}
			stm32_to_linux_flag.flag1 = 0;
			take_off_flag = 1;
			temp_V_x=-1;
		  temp_V_y=-1;	
			temp_Gyroz=0;
		
					
			break;
			
			
		case SDK_Cmd_Pos1:
			

			break;				

		
		case SDK_Cmd_Land:
			
        Ctrler.Z_posPID.Des =  0.0;
				SDKLandFlag = 1;

			if(Ctrler.Z_posPID.FB <=SDK_LAND_DONE_Z)
			{
					SDK_StateMachine[ CurrentSDKState +1 ]=0;
					KeySDKflag=0;
					FlightFSM_Event(FLIGHT_EVENT_DANGEROUS_STOP);
			}
			break;
			
		default:
			break;
	}
	
	LastSDKState = SDK_StateMachine[ CurrentSDKState ];
	
	if(SDK_StateMachine[ CurrentSDKState +1 ]<=1000 && SDK_StateMachine[CurrentSDKState +1 ]>=10)\
						SDK_StateChangeFlag=1;
	else SDK_StateChangeFlag=0;
	
	if(SDK_StateMachine[ CurrentSDKState +1 ]>=5) SDK_StateMachine[ CurrentSDKState +1 ] -=5;
	if(SDK_StateMachine[ CurrentSDKState +1 ]<=10 && CurrentSDKState<SDKStateMAX)CurrentSDKState+=2;
}

void SDK_Set_V_Loc(void)
{
	
	/* Horizontal velocity setpoint while the script is in a position or search state */
	if(
		   SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Search0
	       || SDK_StateMachine[ CurrentSDKState ]==  SDK_Cmd_PosHold
	         || SDK_StateMachine[ CurrentSDKState ]==  SDK_Cmd_Pos1
	         || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Pos2
  		      || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Pos3  
	           || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Pos4 
                || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Pos5  	
	               || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Searchgan 
	                || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_SearchLand 
	                     || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Land 
	                      || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Search1 
	                         || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_FollowLine 
												 || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_go_to_land 
													|| SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_SearchLand_down 
		)
	{
		if(SBUS_CH_VALID(PITCH_CH)) /* stick outside its dead band: the pilot sets the horizontal rate */
				Ctrler.locysPID.Des = -((Remoter.PitCtrler-3000)/1000.0)*Stick_to_MAX_Horizontal_Rate;
		else if(temp_V_y== -1) 
		{  
			if(y_test ==0) /* first tick: hold the current position */
			{
				Ctrler.locyPID.Des =Ctrler.locyPID.FB;
				y_test=1;
			}
			else {
				if(Ctrler.locyPID.U>task_vmax)
				{
				  Ctrler.locysPID.Des = task_vmax;
				}
				else if(Ctrler.locyPID.U<-task_vmax)
				{
				  Ctrler.locysPID.Des = -task_vmax;
				}
				else
				{
				Ctrler.locysPID.Des = Ctrler.locyPID.U;
				}
			}
		}
		else if (temp_V_y != -1) 
		{
			y_test=0;
			linux_data.temp_v_y = temp_V_y ;  
			if(linux_data.temp_v_y > task_vmax)
			{
				Ctrler.locysPID.Des = task_vmax;
			}
			else if(linux_data.temp_v_y < -task_vmax)
			{
			  Ctrler.locysPID.Des = -task_vmax;
			}
			else
			{
			 Ctrler.locysPID.Des = linux_data.temp_v_y;
			}
		}
		
		if(SBUS_CH_VALID(ROLL_CH)) /* stick outside its dead band: the pilot sets the horizontal rate */
				Ctrler.locxsPID.Des = -((Remoter.RolCtrler-3000)/1000.0)*Stick_to_MAX_Horizontal_Rate;  
		else if(temp_V_x == -1)
		{ 
			if(x_test ==0) /* first tick: hold the current position */
			{
				Ctrler.locxPID.Des =Ctrler.locxPID.FB;
				x_test=1;	
			}
			else 
			{
				if(Ctrler.locxPID.U>task_vmax){
					Ctrler.locxsPID.Des = task_vmax;
				}
				else if(Ctrler.locxPID.U<-task_vmax)
				{
				  Ctrler.locxsPID.Des = -task_vmax;
				}
				else{
			    Ctrler.locxsPID.Des = Ctrler.locxPID.U;
				}
			}
		}	
		else if(temp_V_x!= -1)
		{
			x_test =0;
			linux_data.temp_v_x = temp_V_x;
			if(linux_data.temp_v_x > task_vmax)
			{
				Ctrler.locxsPID.Des = task_vmax;
			}
			else if(linux_data.temp_v_x < -task_vmax)
			{
			  Ctrler.locxsPID.Des = -task_vmax;
			}
			else
			{
				Ctrler.locxsPID.Des = linux_data.temp_v_x;
			}
		}
	}
}

void SDK_Set_Gyroz(void)
{
	if(SDK_StateMachine[ CurrentSDKState ]==SDK_Cmd_FollowLine
			|| SDK_StateMachine[ CurrentSDKState ]==  SDK_Cmd_Surround
		   	|| SDK_StateMachine[ CurrentSDKState ]==  SDK_Cmd_PowerLine
		 	|| SDK_StateMachine[ CurrentSDKState ]==  SDK_Cmd_Search0
				|| SDK_StateMachine[ CurrentSDKState ]==  SDK_Cmd_Pos1
			|| SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Pos2
				|| SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Pos3
	        || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Pos4  
	       || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Pos5  
	        || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Searchgan 
	      || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_SearchLand 
	           || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_Search1 
	                 || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_FollowLine 
													 || SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_go_to_land 
													|| SDK_StateMachine[ CurrentSDKState ]== SDK_Cmd_SearchLand_down 
	
	   )
	{
		if(SBUS_CH_VALID(YAW_CH))
			Ctrler.gyrozPID.Des =  ((Remoter.YawCtrler-3000)/1000.0)*Stick_to_MAX_GyroZ ;
		else if(temp_Gyroz == 0)
		{
			if(yaw_test==0) /* first tick: hold the current heading */
			{
				Ctrler.yawPID.Des = Ctrler.yawPID.FB;
				yaw_test =1;
			}
			else if(yaw_test==1)
			{			
				if(Ctrler.yawPID.U>task_yawmax)
					Ctrler.gyrozPID.Des  = task_yawmax;
				else if(Ctrler.yawPID.U< -task_yawmax)
						Ctrler.gyrozPID.Des  = -task_yawmax;
				else
					Ctrler.gyrozPID.Des = Ctrler.yawPID.U ;				
			}
		}
		else
		{
			Ctrler.gyrozPID.Des = temp_Gyroz ;
		  yaw_test=0;
		}
	}				
}


void SDK_StateMachine_Reset(void)
{
	CurrentSDKState = 0;
	SDK_StateMachine_Init();
}
