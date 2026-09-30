#include "pid.h"

/* One row per loop, tunables only; the runtime fields (Des FB Up Ui Ud E PreE SumE U) start at 0.
   Kp Ki Kd           gains, U = Up + Ui + Ud
   UMax               limit on the total output U
   UpMax UiMax UdMax  limits on the P, I and D terms
   SumEMax            limit on the summed error, so |Ui| <= Ki*SumEMax as well as UiMax
   EMin               integral separation: error is summed only while |E| < EMin
   Change history per loop is below the table. */
#define PID_ROW(Kp, Ki, Kd, UMax, UpMax, UiMax, UdMax, SumEMax, EMin) \
    { 0, 0, Kp, Ki, Kd, 0, 0, 0, 0, 0, 0, 0, UMax, UpMax, UiMax, UdMax, SumEMax, EMin }

CtrlerTypeDef Ctrler={
/*          Kp    Ki     Kd    UMax  UpMax  UiMax  UdMax  SumEMax  EMin     member       loop */
    PID_ROW(3.0,  0.1,   8,    200,  200,   10,    10,    120,     3     ), /* pitchPID     pitch angle */
    PID_ROW(3.0,  0.1,   8,    200,  200,   10,    10,    120,     3     ), /* rollPID      roll angle */
    PID_ROW(6.0,  0.04,  0,    160,  160,   2,     10,    50,      2     ), /* yawPID       yaw angle */

    PID_ROW(5,    0.01,  10,   300,  300,   20,    100,   1000,    2     ), /* gyroxPID     roll rate  (inner) */
    PID_ROW(5,    0.01,  10,   300,  300,   20,    100,   1000,    2     ), /* gyroyPID     pitch rate (inner) */
    PID_ROW(8.0,  0.005, 0.02, 650,  650,   500,   10,    100000,  1000  ), /* gyrozPID     yaw rate   (inner) */

    PID_ROW(0.7,  0.005, 0.1,  1.0,  0.9,   0.3,   0.3,   30,      0.3   ), /* Z_posPID     altitude */
    PID_ROW(400,  0.435, 0,    300,  300,   100,   60,    250,     10    ), /* Z_ratePID    climb rate (inner) */

    PID_ROW(0.8,  0.01,  4.0,  300,  300,   20,    50,    200,     30    ), /* locxPID      position x */
    PID_ROW(0.8,  0.01,  4.0,  300,  300,   20,    50,    200,     30    ), /* locyPID      position y */
    PID_ROW(3.0,  0,     6.00, 600,  600,   100,   100,   200,     10    ), /* locxsPID     velocity x (inner) */
    PID_ROW(3.0,  0,     6.00, 600,  600,   100,   100,   200,     10    ), /* locysPID     velocity y (inner) */

    PID_ROW(1.0,  0,     2.0,  40,   40,    0,     2,     2,       2     ), /* stree_yaw_speed */
    PID_ROW(0.6,  0,     0.0,  80,   70,    0,     5,     2,       2     )  /* stree_pitch_speed */
};

/* Change history (newest first)
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

/******���ַ��롢�����޷���P I D ����޷���������޷�***********************/
#define PID_IS_NONFINITE(x) (((x) != (x)) || ((x) > 1e12f) || ((x) < -1e12f))

void ComputePID(PIDTypeDef *pPID)
{
	if (PID_IS_NONFINITE(pPID->Des) || PID_IS_NONFINITE(pPID->FB) ||
	    PID_IS_NONFINITE(pPID->SumE) || PID_IS_NONFINITE(pPID->PreE))
	{
		pPID->SumE = 0.0f;
		pPID->PreE = 0.0f;
		pPID->E    = 0.0f;
		pPID->Up   = 0.0f;
		pPID->Ui   = 0.0f;
		pPID->Ud   = 0.0f;
		pPID->U    = 0.0f;
		return;
	}

	pPID->E = pPID->Des - pPID->FB;
	if (PID_IS_NONFINITE(pPID->E))
	{
		pPID->SumE = 0.0f;
		pPID->PreE = 0.0f;
		pPID->E    = 0.0f;
		pPID->Up   = 0.0f;
		pPID->Ui   = 0.0f;
		pPID->Ud   = 0.0f;
		pPID->U    = 0.0f;
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

	if (PID_IS_NONFINITE(pPID->U) || PID_IS_NONFINITE(pPID->SumE))
	{
		pPID->SumE = 0.0f;
		pPID->U    = 0.0f;
	}

	pPID->PreE = pPID->E ;//���汾��ƫ��
}


void ComputeYawPID(PIDTypeDef *pPID)
{
	pPID->E = pPID->Des - pPID->FB;//���㵱ǰƫ��
	
	if(pPID->E>=180)pPID->E-=360;
	if(pPID->E<=-180)pPID->E+=360;

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
  
	
	
	pPID->PreE = pPID->E ;//���汾��ƫ��
}


//����x��yλ�÷���pid����������ϵת������������ϵ
void ComputePID_locx(PIDTypeDef *pPID)
{
	//pPID->E = pPID->Des - pPID->FB;//���㵱ǰƫ��

	Ctrler.locxPID.E = (Ctrler.locxPID.Des-Ctrler.locxPID.FB)*Cos_Yaw - (Ctrler.locyPID.Des-Ctrler.locyPID.FB)*Sin_Yaw ;
	
	pPID->E = 	Ctrler.locxPID.E ;
	
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
	
	pPID->PreE = pPID->E ;//���汾��ƫ��
}

void ComputePID_locy(PIDTypeDef *pPID)
{
	//pPID->E = pPID->Des - pPID->FB;//���㵱ǰƫ��

	Ctrler.locyPID.E = (Ctrler.locyPID.Des-Ctrler.locyPID.FB)*Cos_Yaw + (Ctrler.locxPID.Des-Ctrler.locxPID.FB)*Sin_Yaw ;
	
	pPID->E = Ctrler.locyPID.E ;
	
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
