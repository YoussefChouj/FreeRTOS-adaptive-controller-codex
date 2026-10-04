#include "pid.h"
#include "math.h"

/* One row per loop, tunables only; the runtime fields (Des FB Up Ui Ud E PreE SumE U) start at 0.
   E is the loop error and U its output, in the units of the loop (row label); a tick is one call.
   Bounds are plausibility ranges (PROPOSED); what CMD 0x01 may write is PID_CMD_ROW below.
   @Kp      U/E        [0, 1000]  gains, U = Up + Ui + Ud
   @Ki      U/(E*tick) [0, 200]   Ui = Ki*SumE
   @Kd      U*tick/E   [0, 200]   Ud = Kd*(E - PreE)
   @UMax    U          [0, 1000]  limit on the total output U
   @UpMax   U          [0, 1000]  limit on the P term
   @UiMax   U          [0, 1000]  limit on the I term
   @UdMax   U          [0, 1000]  limit on the D term
   @SumEMax E*tick     [0, 1e6]   limit on the summed error, so |Ui| <= Ki*SumEMax as well as UiMax
   @EMin    E          [0, 1e4]   integral separation: error is summed only while |E| < EMin
   Change history per loop is below the table. */
#define PID_ROW(Kp, Ki, Kd, UMax, UpMax, UiMax, UdMax, SumEMax, EMin) \
    { 0, 0, Kp, Ki, Kd, 0, 0, 0, 0, 0, 0, 0, UMax, UpMax, UiMax, UdMax, SumEMax, EMin }

CtrlerTypeDef Ctrler={
/*          Kp    Ki     Kd    UMax  UpMax  UiMax  UdMax  SumEMax  EMin     member       loop */
    /* Angle Ki 0.1 -> 0.02 (2026-10-01): at 200 Hz, Ki 0.1 with Kp 3 is an underdamped PI (wn 0.71 Hz, zeta 0.34);
       active8 hover showed its I part (4.2 deg/s) above the P part (3.6) in 0.5-1.3 Hz, mean only 0.17 deg/s = the 0.9 Hz sway.
       Ki 0.02 -> wn 0.32 Hz, zeta 0.75. */
    PID_ROW(3.0,  0.02,   8,    200,  200,   26,    10,    1300,    10    ), /* pitchPID     pitch angle */ /* F1x: Ui 26 / SumE 1300 / EMin 10 so the lean is carried (WP-10) */
    PID_ROW(3.0,  0.02,   8,    200,  200,   26,    10,    1300,    10    ), /* rollPID      roll angle */  /* F1x: Ui 26 / SumE 1300 / EMin 10 so the lean is carried (WP-10) */
    PID_ROW(6.0,  0.04,  0,    160,  160,   2,     10,    50,      2     ), /* yawPID       yaw angle */

    PID_ROW(5,    0.01,   10,   300,  300,   160,   100,   16000,   50    ), /* gyroxPID     roll rate  (inner) */ /* F1x */
    PID_ROW(5,    0.01,   10,   300,  300,   160,   100,   16000,   50    ), /* gyroyPID     pitch rate (inner) */ /* F1x */
    PID_ROW(8.0,  0.005, 0.02, 650,  650,   500,   10,    100000,  1000  ), /* gyrozPID     yaw rate   (inner) */

    PID_ROW(0.7,  0.005, 0.1,  1.0,  0.9,   0.3,   0.3,   30,      0.3   ), /* Z_posPID     altitude */
    PID_ROW(400,  0.435, 0,    300,  300,   100,   60,    250,     10    ), /* Z_ratePID    climb rate (inner) */

    PID_ROW(0.8,  0.0013, 4.0,  300,  300,   5,     50,    3850,    10    ), /* locxPID      position x */ /* F3 */
    PID_ROW(0.8,  0.0013, 4.0,  300,  300,   5,     50,    3850,    10    ), /* locyPID      position y */ /* F3 */
    PID_ROW(3.0,  0.008,  6.0,  600,  600,   100,   100,   12500,   10    ), /* locxsPID     velocity x (inner) */ /* F3 */
    PID_ROW(3.0,  0.008,  6.0,  600,  600,   100,   100,   12500,   10    ), /* locysPID     velocity y (inner) */ /* F3 */

    PID_ROW(1.0,  0,     2.0,  40,   40,    0,     2,     2,       2     ), /* stree_yaw_speed */
    PID_ROW(0.6,  0,     0.0,  80,   70,    0,     5,     2,       2     )  /* stree_pitch_speed */
};

/* Change history (newest first)
 * WP-13 F1x+F3+F5w+F6a integration (2026-10-01)
 *   pitch, roll: UiMax 10->26, SumEMax 120->1300, EMin 3->10 (F1x carry lean)
 *   gyrox, gyroy: UiMax 20->160, SumEMax 1000->16000, EMin 2->50 (F1x)
 *   locx, locy: Ki 0.01->0.0013, UiMax 20->5, SumEMax 200->3850, EMin 30->10 (F3)
 *   locxs, locys: Ki 0->0.008, SumEMax 200->12500 (F3)

 * pitch, roll angle
 *   2026-09-29 Kp 2.6->3.0, Kd 9.5->8: restored to FreeRTOS_original (operator: tuned on this drone);
 *              UiMax 10, SumEMax 120 kept (original UiMax 0 = no I term).
 *   flight_1784538359 Kp 3.0->2.6 for phase margin, Kd 8.0->9.5 for damping (PM=30 deg fix).
 *   legacy: skywalker 4, letian40A 3 (pitch/roll Kp per frame).
 * yaw angle
 *   2026-09-29 Kp 6.5->6.0, Kd 1.5->0: restored to FreeRTOS_original; UMax/UpMax 160 kept.
 *   flight_1784538359 Kp 6.0->6.5 for tracking, Kd 0->1.5 to damp a 0.4 Hz oscillation.
 *   legacy: Kp 3->4.
 * gyrox (roll rate)
 *   2026-09-29 Kd 8->10: restored to FreeRTOS_original; flight16 at Kd 8 had a roll 25 Hz limit cycle
 *              of ~19.7 dps vs ~14.7 dps at Kd 10 (flight15).
 *   2026-09-29 Kd 10->8: flight15 roll rate err rms 10.8 vs pitch 5.4 dps, U std 105 vs 69.
 * gyroz (yaw rate)
 *   2026-09-29 Kp 4->8, Ki 0.005->0.001, Kd 2.0->0.02: restored to FreeRTOS_original; limits kept,
 *              so Ui cap = Ki*SumEMax = 100 vs flight16 hover trim U~110 (Ui~106 at Ki 0.005).
 *   2026-09-27 EMin 20->1000: gate |E|<EMin blocked all integration at E~110 (flight7).
 *   2026-09-27 Ki 0.001->0.005, UiMax 60->500, SumEMax 2000->100000: old Ui cap Ki*SumEMax = 2,
 *              so flight6 held a P-only -50 deg/s drift with U~450.
 *   2026-09-27 UMax/UpMax 350->650: flight5 U pinned at 350 the whole flight.
 *   2026-09-13 Kp 8->4, Kd 0.02->2.0: rate-loop damping against overshoot from saturation.
 * Z_rate (climb rate)
 *   2026-09-29 Kd 1.5->0: restored to FreeRTOS_original.
 *   2026-09-28 EMin 0.1->10, SumEMax 30->250, UiMax 60->100: gate |E|<0.1 + Ui cap 13 left hover
 *              thrust on P, steady E~0.5 m/s (flight_test_pid_4).
 *   flight_1784538359 Kd 0->1.5 to damp a 0.4 Hz oscillation.
 * locx, locy (position)
 *   2026-09-29 Kp 0.6->0.8: restored to FreeRTOS_original.
 *   2026-09-29 Kp 0.8->0.6: flight15 0.5-0.8 Hz pendulum sway from pos-hold, drift +-13 cm.
 *   legacy: hui fei zhe 1.8 outKP, 1.8 0.45 inKP KI.
 */
/* Gain lease (WP-28 livetune), default OFF. In flight, the first CMD 0x01 write snapshots Kp Ki Kd of the seven
   0x01 axes and opens a lease; each later 0x01 write renews it. No 0x01 write for lease_ms, or the drone leaving the
   air, restores the snapshot and closes the lease, so a candidate left by a lost link cannot outlive the link.
   On the ground the lease is off: gains written there (a verify flight's) stay.
   @enable   -   [0, 1]          1 = on, 0 = off (CMD 0x01 behaves as before)
   @lease_ms ms  [100, 60000]    renewal deadline; the GS re-sends the active gains within it */
#define GAIN_LEASE_ROW(enable, lease_ms) { (enable), (lease_ms) }
static const struct { uint8_t enable; uint32_t lease_ms; } s_gain_lease_cfg = GAIN_LEASE_ROW(0, 2000);

static PIDTypeDef *const s_lease_pid[7] = { &Ctrler.pitchPID, &Ctrler.rollPID, &Ctrler.yawPID,
    &Ctrler.gyroxPID, &Ctrler.gyroyPID, &Ctrler.gyrozPID, &Ctrler.Z_ratePID };  /* CMD 0x01 axis order */
static float s_lease_gain[7][3];
static uint8_t s_lease_open = 0U;
static uint32_t s_lease_ms = 0U;

void PID_GainLeaseRenew(uint32_t now_ms, uint8_t airborne)
{
    uint8_t a;
    if (!s_gain_lease_cfg.enable || !airborne) return;
    if (!s_lease_open) {
        for (a = 0U; a < 7U; a++) {
            s_lease_gain[a][0] = s_lease_pid[a]->Kp;
            s_lease_gain[a][1] = s_lease_pid[a]->Ki;
            s_lease_gain[a][2] = s_lease_pid[a]->Kd;
        }
        s_lease_open = 1U;
    }
    s_lease_ms = now_ms;
}

void PID_GainLeaseTick(uint32_t now_ms, uint8_t airborne)
{
    uint8_t a;
    if (!s_lease_open || (airborne && (uint32_t)(now_ms - s_lease_ms) <= s_gain_lease_cfg.lease_ms)) return;
    for (a = 0U; a < 7U; a++) {
        s_lease_pid[a]->Kp = s_lease_gain[a][0];
        s_lease_pid[a]->Ki = s_lease_gain[a][1];
        s_lease_pid[a]->Kd = s_lease_gain[a][2];
    }
    s_lease_open = 0U;
}

/* CMD 0x01 write bounds (TASK/send_data.c Cmd_PidGain), one row per 0x01 axis in s_lease_pid order; the minimum
   is 0. Rule: max(200, 2 x the PID_ROW default), so every boot gain can be written back and doubled
   (API/tests/test_pid_guards.c checks it). Before WP-38 one bound of 200 covered every gain, so Z_ratePID Kp 400
   could not be written back. Plausibility limits against a garbled write, not stability limits (PROPOSED).
   @Kp  U/E        [0, 1000]  largest Kp the command accepts
   @Ki  U/(E*tick) [0, 200]   largest Ki
   @Kd  U*tick/E   [0, 200]   largest Kd */
#define PID_CMD_ROW(Kp, Ki, Kd) { (Kp), (Ki), (Kd) }
const float pid_cmd_max[PID_CMD_AXES][3] = {
/*              Kp    Ki    Kd       axis  loop       default Kp / Ki / Kd */
    PID_CMD_ROW(200,  200,  200), /* 0     pitchPID   3 / 0.02 / 8 */
    PID_CMD_ROW(200,  200,  200), /* 1     rollPID    3 / 0.02 / 8 */
    PID_CMD_ROW(200,  200,  200), /* 2     yawPID     6 / 0.04 / 0 */
    PID_CMD_ROW(200,  200,  200), /* 3     gyroxPID   5 / 0.01 / 10 */
    PID_CMD_ROW(200,  200,  200), /* 4     gyroyPID   5 / 0.01 / 10 */
    PID_CMD_ROW(200,  200,  200), /* 5     gyrozPID   8 / 0.005 / 0.02 */
    PID_CMD_ROW(800,  200,  200)  /* 6     Z_ratePID  400 / 0.435 / 0 */
};

PIDTypeDef *PID_CmdLoop(uint8_t axis)
{
    return (axis < PID_CMD_AXES) ? s_lease_pid[axis] : (PIDTypeDef *)0;
}

/* 1 if CMD 0x01 may write val to (axis, gain): known target, finite, in [0, pid_cmd_max]. NaN fails both tests. */
uint8_t PID_CmdGainOk(uint8_t axis, uint8_t gain, float val)
{
    return (uint8_t)((axis < PID_CMD_AXES) && (gain < 3U) && (val >= 0.0f) && (val <= pid_cmd_max[axis][gain]));
}

/******���ַ��롢�����޷���P I D ����޷���������޷�***********************/
#define PID_FINITE_LIMIT      1e12f    /* |x| above this counts as non-finite; no loop signal comes near it */
#define PID_IS_NONFINITE(x) (((x) != (x)) || ((x) > PID_FINITE_LIMIT) || ((x) < -PID_FINITE_LIMIT))
#define PID_YAW_TURN_DEG      360.0f   /* yaw error is folded into one turn ... */
#define PID_YAW_HALF_TURN_DEG 180.0f   /* ... centred on 0: |E| <= 180 */

/* NaN guards. A non-finite input or state zeroes the loop for this tick instead of reaching the next
   stage or the mixer; the next finite tick starts from a clean integrator. */
static void PID_Zero(PIDTypeDef *pPID)
{
	pPID->SumE = 0.0f;
	pPID->PreE = 0.0f;
	pPID->E    = 0.0f;
	pPID->Up   = 0.0f;
	pPID->Ui   = 0.0f;
	pPID->Ud   = 0.0f;
	pPID->U    = 0.0f;
}

/* Loops that form E themselves: 1 (loop zeroed) if E, SumE or PreE is non-finite. */
static int PID_BadState(PIDTypeDef *pPID)
{
	if (!PID_IS_NONFINITE(pPID->E) && !PID_IS_NONFINITE(pPID->SumE) && !PID_IS_NONFINITE(pPID->PreE)) return 0;
	PID_Zero(pPID);
	return 1;
}

/* Output: a non-finite U or SumE leaves U and the integrator at 0. */
static void PID_GuardOut(PIDTypeDef *pPID)
{
	if (PID_IS_NONFINITE(pPID->U) || PID_IS_NONFINITE(pPID->SumE))
	{
		pPID->SumE = 0.0f;
		pPID->U    = 0.0f;
	}
}

void ComputePID(PIDTypeDef *pPID)
{
	if (PID_IS_NONFINITE(pPID->Des) || PID_IS_NONFINITE(pPID->FB) ||
	    PID_IS_NONFINITE(pPID->SumE) || PID_IS_NONFINITE(pPID->PreE))
	{
		PID_Zero(pPID);
		return;
	}

	pPID->E = pPID->Des - pPID->FB;
	if (PID_IS_NONFINITE(pPID->E))
	{
		PID_Zero(pPID);
		return;
	}//���㵱ǰƫ��

	if(pPID->aw_mode == AW_CLAMP)
	{
		/* --- Conditional integration keyed on ACTUAL output saturation ---
		   Integrate tentatively, build the output, then if the output actually
		   saturates AND the error would push it further into the limit, undo the
		   accumulation (freeze the integrator). Unlike legacy, this reacts to real
		   saturation, not to error size, and does not use EMin. */
		float SumE_prev = pPID->SumE;
		float u_presat;
		pPID->SumE += pPID->E;
		value_limit( pPID->SumE , -pPID->SumEMax , pPID->SumEMax );
		pPID->Ui = pPID->Ki * pPID->SumE;
		value_limit( pPID->Ui , -pPID->UiMax , pPID->UiMax );

		pPID->Up = pPID->Kp * pPID->E;
		value_limit( pPID->Up , -pPID->UpMax , pPID->UpMax );
		pPID->Ud = pPID->Kd * ( pPID->E - pPID->PreE );
		value_limit( pPID->Ud , -pPID->UdMax , pPID->UdMax );

		u_presat = pPID->Up + pPID->Ui + pPID->Ud;
		pPID->U = u_presat;
		value_limit( pPID->U , -pPID->UMax , pPID->UMax );

		if( pPID->U != u_presat &&
		    ((u_presat > 0 && pPID->E > 0) || (u_presat < 0 && pPID->E < 0)) )
		{
			pPID->SumE = SumE_prev;                 // revert: don't wind further in
			pPID->Ui = pPID->Ki * pPID->SumE;
			value_limit( pPID->Ui , -pPID->UiMax , pPID->UiMax );
			pPID->U = pPID->Up + pPID->Ui + pPID->Ud;
			value_limit( pPID->U , -pPID->UMax , pPID->UMax );
		}
	}
	else if(pPID->aw_mode == AW_BACKCALC)
	{
		/* --- Back-calculation observer ---
		   Integrate every tick, form the output, then bleed the accumulator by the
		   actual saturation error (U_sat - U_presat) scaled by Kt. Kt MUST be > 0,
		   else this degrades to pure always-integrate. No EMin gate. */
		float u_presat;
		pPID->SumE += pPID->E;
		value_limit( pPID->SumE , -pPID->SumEMax , pPID->SumEMax );
		pPID->Ui = pPID->Ki * pPID->SumE;
		value_limit( pPID->Ui , -pPID->UiMax , pPID->UiMax );

		pPID->Up = pPID->Kp * pPID->E;
		value_limit( pPID->Up , -pPID->UpMax , pPID->UpMax );
		pPID->Ud = pPID->Kd * ( pPID->E - pPID->PreE );
		value_limit( pPID->Ud , -pPID->UdMax , pPID->UdMax );

		u_presat = pPID->Up + pPID->Ui + pPID->Ud;
		pPID->U = u_presat;
		value_limit( pPID->U , -pPID->UMax , pPID->UMax );

		pPID->SumE += pPID->Kt * (pPID->U - u_presat); // observer correction
		value_limit( pPID->SumE , -pPID->SumEMax , pPID->SumEMax );
	}
	else
	{
		/* --- AW_LEGACY (default): original behaviour, unchanged --- */
		if(((pPID->U <= pPID->UMax && pPID->E > 0) || (pPID->U >= -pPID->UMax && pPID->E < 0)) \
			    && ABS(pPID->E) < pPID->EMin)//���ַ���
		{
			pPID->SumE += pPID->E;//����ƫ�����
		}
		value_limit( pPID->SumE , -pPID->SumEMax , pPID->SumEMax );//�����޷�
		pPID->Ui = pPID->Ki * pPID->SumE;
		value_limit( pPID->Ui , -pPID->UiMax , pPID->UiMax );

		pPID->Up = pPID->Kp * pPID->E;
		value_limit( pPID->Up , -pPID->UpMax , pPID->UpMax );

		pPID->Ud = pPID->Kd * ( pPID->E - pPID->PreE );
		value_limit( pPID->Ud , -pPID->UdMax , pPID->UdMax );

		pPID->U = pPID->Up + pPID->Ui + pPID->Ud;/*λ��ʽPID���㹫ʽ*/
		value_limit( pPID->U , -pPID->UMax , pPID->UMax );  /*PID��������޷�*/
	}

	PID_GuardOut(pPID);

	pPID->PreE = pPID->E ;//���汾��ƫ��
}


void ComputeYawPID(PIDTypeDef *pPID)
{
	/* fmodf first: the two steps below fold one turn only, so a Des more than a turn and a half
	 * from FB (e.g. a heading that keeps counting up) would otherwise leave |E| >= 180. */
	pPID->E = fmodf(pPID->Des - pPID->FB, PID_YAW_TURN_DEG);//���㵱ǰƫ��
	if (PID_BadState(pPID)) return;   /* NaN/Inf Des or FB, or a poisoned integrator */
	
	if(pPID->E>=PID_YAW_HALF_TURN_DEG)pPID->E-=PID_YAW_TURN_DEG;
	if(pPID->E<=-PID_YAW_HALF_TURN_DEG)pPID->E+=PID_YAW_TURN_DEG;

	if(((pPID->U <= pPID->UMax && pPID->E > 0) || (pPID->U >= -pPID->UMax && pPID->E < 0)) \
		    && ABS(pPID->E) < pPID->EMin)//���ַ���
	{
		pPID->SumE += pPID->E;//����ƫ�����
	}
	value_limit( pPID->SumE , -pPID->SumEMax , pPID->SumEMax );//�����޷�
	pPID->Ui = pPID->Ki * pPID->SumE;
	value_limit( pPID->Ui , -pPID->UiMax , pPID->UiMax );
	
	pPID->Up = pPID->Kp * pPID->E;
	value_limit( pPID->Up , -pPID->UpMax , pPID->UpMax );
	
	pPID->Ud = pPID->Kd * ( pPID->E - pPID->PreE );
	value_limit( pPID->Ud , -pPID->UdMax , pPID->UdMax );
	
	pPID->U = pPID->Up + pPID->Ui + pPID->Ud;/*λ��ʽPID���㹫ʽ*/
  value_limit( pPID->U , -pPID->UMax , pPID->UMax );  /*PID��������޷�*/
  
	
	
	PID_GuardOut(pPID);
	pPID->PreE = pPID->E ;//���汾��ƫ��
}


void Clear_Structure(void)
{
	Ctrler.pitchPID.SumE=0;
	Ctrler.rollPID.SumE=0;
	Ctrler.yawPID.SumE=0;
	Ctrler.gyroxPID.SumE=0;
	Ctrler.gyroyPID.SumE=0;
	Ctrler.gyrozPID.SumE=0;
	Ctrler.Z_posPID.SumE=0;
	Ctrler.Z_ratePID.SumE=0;
	/* FIX 2026-09-13 (flight-bug-hunt Tier 1.1): zero position/velocity-loop
	   integrators too. Without this, locxPID/locyPID.SumE accumulated every
	   disarmed tick from OF drift (Des is locked at first-tick FB, FB keeps
	   drifting in the disarmed state) and saturated to SumEMax=200 within
	   seconds. On the first armed tick the saturated Ui drove
	   locxsPID.Des to its ±120 clamp, locxsPID.U to ±360 saturation, and
	   attitude Des to its ±15 deg limit — motors spun up hard, takeoff
	   instability. Velocity-loop SumE was already protected by EMin=10 but
	   zeroing them here for consistency. */
	Ctrler.locxPID.SumE=0;
	Ctrler.locyPID.SumE=0;
	Ctrler.locxsPID.SumE=0;
	Ctrler.locysPID.SumE=0;

	Ctrler.locxPID.Des=Ctrler.locxPID.FB;
	Ctrler.locyPID.Des=Ctrler.locyPID.FB;

//	Ctrler.locxsPID.Des=0;
//	Ctrler.locysPID.Des=0;
//	Ctrler.pitchPID.Des=0;
//	Ctrler.rollPID.Des=0;
	Ctrler.yawPID.Des = Ctrler.yawPID.FB;
}

void ComputePID_Gated(PIDTypeDef *pPID, uint8_t integrate)
{
    if (integrate != 0) {
        ComputePID(pPID);
    } else {
        pPID->SumE = 0;
        ComputePID(pPID);
        pPID->SumE = 0;
        pPID->Ui = 0;
        pPID->U = pPID->Up + pPID->Ud;
        value_limit(pPID->U, -pPID->UMax, pPID->UMax);
    }
}

/* WP-21 B: ComputePID_Gated, but hold == 1 keeps SumE/Ui at their previous value (the
 * Z-loop P0 Finding 2 pattern) instead of zeroing them, so P/D still act.  U is rebuilt
 * from the held Ui (ComputePID already added this tick's increment to it).  A non-finite
 * saved value is not restored: ComputePID's NaN/inf reset wins.  WP-37: moved here from
 * TASK/StabilizerTask.c (the static ComputePID_Hold of the xy position and velocity loops);
 * sim/sil/csrc/sil_server.c still carries its own static copy under the old name. */
void ComputePID_GatedHold(PIDTypeDef *pPID, uint8_t integrate, uint8_t hold)
{
    float sumE = pPID->SumE;
    float ui   = pPID->Ui;
    float u;
    ComputePID_Gated(pPID, integrate);
    if (hold && integrate && fabsf(sumE) < PID_FINITE_LIMIT && fabsf(ui) < PID_FINITE_LIMIT) {
        pPID->SumE = sumE;
        pPID->Ui   = ui;
        u = pPID->Up + ui + pPID->Ud;
        if (u >  pPID->UMax) u =  pPID->UMax;
        if (u < -pPID->UMax) u = -pPID->UMax;
        pPID->U = u;
    }
}

float AttTrim_Apply(float des_deg, float trim_deg, float lim_deg, uint8_t flying)
{
    if (flying) {
        float val = des_deg + trim_deg;
        value_limit(val, -lim_deg, lim_deg);
        return val;
    }
    return des_deg;
}

void TrajFF_Reset(TrajFF_t *s)
{
    s->prev_x = 0;
    s->prev_y = 0;
    s->vf_x = 0;
    s->vf_y = 0;
    s->primed = 0;
}

void TrajFF_Step(TrajFF_t *s, uint8_t active, float tx_cm, float ty_cm, float dt_s, float tau_s, float vmax_cms,
                 float *vff_x, float *vff_y, float *aff_x, float *aff_y)
{
    float vx, vy, alpha, vf_x_new, vf_y_new;

    if (active == 0) {
        TrajFF_Reset(s);
        *vff_x = 0.0f;
        *vff_y = 0.0f;
        *aff_x = 0.0f;
        *aff_y = 0.0f;
        return;
    }

    if (!s->primed) {
        s->prev_x = tx_cm;
        s->prev_y = ty_cm;
        s->primed = 1;
        s->vf_x = 0.0f;
        s->vf_y = 0.0f;
        *vff_x = 0.0f;
        *vff_y = 0.0f;
        *aff_x = 0.0f;
        *aff_y = 0.0f;
        return;
    }

    vx = (tx_cm - s->prev_x) / dt_s;
    vy = (ty_cm - s->prev_y) / dt_s;
    value_limit(vx, -vmax_cms, vmax_cms);
    value_limit(vy, -vmax_cms, vmax_cms);
    
    s->prev_x = tx_cm;
    s->prev_y = ty_cm;
    
    *vff_x = vx;
    *vff_y = vy;
    
    alpha = 1.0f - expf(-dt_s / tau_s);
    vf_x_new = s->vf_x + alpha * (vx - s->vf_x);
    vf_y_new = s->vf_y + alpha * (vy - s->vf_y);
    
    *aff_x = (vf_x_new - s->vf_x) / dt_s;
    *aff_y = (vf_y_new - s->vf_y) / dt_s;
    
    s->vf_x = vf_x_new;
    s->vf_y = vf_y_new;
}
