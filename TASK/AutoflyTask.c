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

	AutoflyTask_CommitRef(
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
			AutoflyTask_CommitRef(cont_x, cont_y, cont_z);
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

	AutoflyTask_CommitRef(cx, cy, figure8_path.center_z);
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
#define TRAJ_V_MAX_CMS     80.0f        /* peak reference speed above this is refused [cm/s]                    */
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
	float   f8_laps;    /* figure-8 laps in move_s, up to 3                                            */
} TrajParams_t;

volatile uint8_t traj_id     = 0U;  /* 1 step, 2 zigzag, 3 circle, 4 figure-8                                */
volatile uint8_t traj_go     = 0U;  /* write 1 to start traj_id (ignored while one runs); the firmware clears it */
volatile uint8_t traj_stop   = 0U;  /* write 1 to end the move early and fly back                           */
volatile uint8_t traj_active = 0U;  /* id of the running preset, 0 idle                                     */
volatile uint8_t traj_phase  = 0U;  /* 0 idle, 1 move, 2 return, 3 hold at the start point                 */
volatile uint8_t traj_status = 0U;  /* 1 running, 2 done, 3 aborted (takeover/landing), 4 stopped and back,
                                       0xE0 not flying, 0xE1 busy (GS path or GS authority), 0xE2 bad traj_id,
                                       0xEE a traj_p field out of range or too fast, 0xEF outside the soft fence */
volatile uint8_t traj_id_seen = 0U; /* traj_id as read at the last traj_go (explains a 0xE2)                */
volatile float   traj_t      = 0.0f; /* time since traj_go [s]                                              */
volatile float   traj_home_x = 0.0f, traj_home_y = 0.0f, traj_home_z = 0.0f, traj_home_yaw = 0.0f; /* start point */
TrajParams_t traj_p = {                 /* watch-window struct, latched at traj_go (same pattern as vp_user) */
	1U, 0U, 0U, 0U,                 /* profile smooth, axis x, zigzag triangle, figure-8 Bernoulli */
	18.0f, 5.0f, 2.0f, 3.0f,        /* move, return, hold, ramp [s]: 25 s in total                 */
	40.0f,                          /* step [cm]                                                   */
	30.0f, 0.2f,                    /* zigzag amplitude [cm], frequency [Hz]                       */
	40.0f, 1.0f,                    /* circle radius [cm], laps                                    */
	50.0f, 1.0f                     /* figure-8 size [cm], laps                                    */
};

static TrajParams_t s_tp;                         /* traj_p latched at traj_go                    */
static float s_tph = 0.0f;                        /* time in the current phase [s]                */
static float s_om = 0.0f;                         /* circle / figure-8 rate [rad per path second] */
static float s_ref_x = 0.0f, s_ref_y = 0.0f, s_ref_z = 0.0f; /* last move reference (return start) */
static uint8_t s_stopped = 0U;                    /* traj_stop ended the move                     */

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

/* Offset from the start point at move time t: x/y/z all in cm. */
static void Traj_Offset(float t, float *dx, float *dy, float *dz)
{
	float a = 0.0f;   /* step / zigzag offset along s_tp.axis [cm] */

	*dx = 0.0f;
	*dy = 0.0f;
	*dz = 0.0f;
	if (traj_active == 1U) {
		/* step: out for the first half of move_s, back for the second half */
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
	} else if (traj_active == 3U) {
		/* circle through the start point, centre circ_r_cm toward -x, leaves toward +y */
		float th = s_om * Traj_Warp(t);
		*dx = s_tp.circ_r_cm * (cosf(th) - 1.0f);
		*dy = s_tp.circ_r_cm * sinf(th);
		return;
	} else if (traj_active == 4U) {
		/* figure-8 from the start point, leaves toward +y */
		float th = s_om * Traj_Warp(t);
		float s = sinf(th);
		float c = cosf(th);
		if (s_tp.f8_type == 1U) {
			*dx = 0.5f * s_tp.f8_a_cm * sinf(2.0f * th);   /* Gerono: crosses at the start point */
			*dy = s_tp.f8_a_cm * s;
		} else {
			*dx = s_tp.f8_a_cm * (c / (1.0f + s * s) - 1.0f); /* Bernoulli: starts at the +x tip */
			*dy = s_tp.f8_a_cm * s * c / (1.0f + s * s);
		}
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

/* Range, peak speed and soft-fence check of s_tp around traj_home_*; sets s_om. 0 = OK, else the refusal code. */
static uint8_t Traj_Check(void)
{
	float lo = 0.0f, hi = 0.0f;                          /* step / zigzag offset range on the axis [cm] */
	float x0 = 0.0f, x1 = 0.0f, y0 = 0.0f, y1 = 0.0f, z0 = 0.0f, z1 = 0.0f; /* offset box [cm]     */
	float v = 0.0f;                                      /* peak reference speed [cm/s]                 */
	float path_s = s_tp.move_s - ((s_tp.profile == 1U) ? s_tp.ramp_s : 0.0f);
	float xm, ym, zt;
	wfb_safety_limits_t lim;

	if (traj_id < 1U || traj_id > 4U) {
		return 0xE2U;
	}
	if (s_tp.move_s < 5.0f || s_tp.ret_s < 3.0f || s_tp.ret_s > 10.0f || s_tp.hold_s < 0.0f ||
	    s_tp.hold_s > 5.0f || s_tp.move_s + s_tp.ret_s + s_tp.hold_s > TRAJ_TOTAL_MAX_S + 0.001f ||
	    s_tp.profile > 1U || s_tp.axis > 2U ||
	    (s_tp.profile == 1U && (s_tp.ramp_s < 0.5f || s_tp.ramp_s > 0.25f * s_tp.move_s))) {
		return 0xEEU;
	}
	s_om = 0.0f;
	if (traj_id == 1U) {
		if (s_tp.step_cm == 0.0f || fabsf(s_tp.step_cm) > ((s_tp.profile == 0U) ? 50.0f : 100.0f)) {
			return 0xEEU;   /* a hard step (profile 0) is limited to 50 cm */
		}
		if (s_tp.profile == 1U) {
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
		if (s_tp.f8_a_cm < 10.0f || s_tp.f8_a_cm > 80.0f || s_tp.f8_laps <= 0.0f || s_tp.f8_laps > 3.0f ||
		    s_tp.f8_type > 1U) {
			return 0xEEU;
		}
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

	wfb_safety_default_limits(&lim);
	xm = lim.fence_x_m - lim.soft_margin_m;
	ym = lim.fence_y_m - lim.soft_margin_m;
	zt = lim.ceiling_m - lim.soft_margin_m;
	if ((traj_home_x + x0) * 0.01f < -xm || (traj_home_x + x1) * 0.01f > xm ||
	    (traj_home_y + y0) * 0.01f < -ym || (traj_home_y + y1) * 0.01f > ym ||
	    traj_home_z + z0 * 0.01f < TRAJ_Z_MIN_M || traj_home_z + z1 * 0.01f > zt) {
		return 0xEFU;
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
}

static void Traj_Start(void)
{
	uint8_t code;

	if (FlightFSM_GetState() != FLIGHT_STATE_ARMED || flight_phase != FLIGHT_PHASE_FLYING ||
	    DroneStatus.FlyMode != FlyMode_SDK) {
		traj_status = 0xE0U;
		return;
	}
	if (RCInput_GetAuthority() || TWC.execute != 0 ||
	    sinusoid_path.active || circle_path.active || figure8_path.active) {
		traj_status = 0xE1U;
		return;
	}
	s_tp = traj_p;
	traj_home_x = Ctrler.locxPID.Des;
	traj_home_y = Ctrler.locyPID.Des;
	traj_home_z = Ctrler.Z_posPID.Des;
	traj_home_yaw = Ctrler.yawPID.Des;
	code = Traj_Check();
	if (code != 0U) {
		traj_status = code;
		return;
	}

	/* SetAuthority(1) leaves the virtual throttle at -1: centre it before any other task can run */
	taskENTER_CRITICAL();
	RCInput_SetAuthority(1U);
	Traj_SticksCentred();
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
	traj_phase = 1U;
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
		return;
	}

	/* takeover (any stick move drops the authority), disarm, landing, or a GS path: stop here, no return */
	if (!RCInput_GetAuthority() || FlightFSM_GetState() != FLIGHT_STATE_ARMED ||
	    flight_phase != FLIGHT_PHASE_FLYING || DroneStatus.FlyMode != FlyMode_SDK ||
	    TWC.execute != 0 || sinusoid_path.active || circle_path.active || figure8_path.active) {
		Traj_End(3U);
		return;
	}

	taskENTER_CRITICAL();
	Traj_SticksCentred();
	taskEXIT_CRITICAL();

	s_tph += AFLY_DT_S;
	traj_t += AFLY_DT_S;

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
			AutoflyTask_CommitRef(s_ref_x, s_ref_y, s_ref_z);
		}
	}
	if (traj_phase == 2U) {
		/* fly back from the last move reference to the start point, cosine blend over ret_s */
		float k = Traj_Ramp01(s_tph / s_tp.ret_s);
		AutoflyTask_CommitRef(s_ref_x + (traj_home_x - s_ref_x) * k,
		                      s_ref_y + (traj_home_y - s_ref_y) * k,
		                      s_ref_z + (traj_home_z - s_ref_z) * k);
		if (s_tph >= s_tp.ret_s) {
			/* commit the start point exactly (the quantizer may hold up to waypoint_spacing short of it) */
			AutoflyTask_WaypointReset();
			AutoflyTask_CommitRef(traj_home_x, traj_home_y, traj_home_z);
			traj_phase = 3U;
			s_tph = 0.0f;
		}
	} else if (traj_phase == 3U) {
		AutoflyTask_CommitRef(traj_home_x, traj_home_y, traj_home_z);
		if (s_tph >= s_tp.hold_s) {
			Traj_End(s_stopped ? 4U : 2U);
			return;
		}
	}
	Ctrler.yawPID.Des = traj_home_yaw;
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
