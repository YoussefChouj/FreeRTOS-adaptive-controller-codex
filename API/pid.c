#include "pid.h"

CtrlerTypeDef Ctrler={

/*des FB  Kp   Ki    Kd Up Ui Ud E preE SumE U Umax Upmax Uimax Udmax SumEmax Emin***/
	//pitch: reduced Kp 3.0->2.6 for more phase margin, increased Kd 8.0->9.5 for better damping (PM=30° fix, flight_1784538359)
	{ 0,  0, 2.6, 0.1,  9.5, 0, 0, 0, 0, 0,   0,  0, 200,  200,  10,  10,  120,     3},
	//roll: same tuning as pitch (PM=30° fix, flight_1784538359)
	{ 0,  0, 2.6, 0.1,  9.5, 0, 0, 0, 0, 0,   0,  0, 200,  200,  10,  10,  120,     3},
	//yaw: increased Kp 6.0->6.5 for tracking, added Kd 0->1.5 to damp 0.4Hz oscillation (flight_1784538359)
	{ 0,  0, 6.5, 0.04,  1.5, 0, 0, 0, 0, 0,   0,  0, 160,  160,  2,  10,     50,     2},
	//skywalker  4         letian40A    3       pitch/roll KP
	//yaw KP   3   -> 4
	

	{ 0,  0, 5   , 0.01 ,10, 0,0, 0, 0, 0,   0,  0, 300,  300,  20,  100,  1000,   2},//gyrox
	{ 0,  0, 5   , 0.01 ,10, 0,0, 0, 0, 0,   0,  0, 300,  300,  20,  100,  1000,   2},//gyroy
	{ 0,  0, 4.0, 0.001 ,2.0, 0,0, 0, 0, 0,  0,  0, 350,  350,  60,  10,   2000,     20},//gyroz  Kp 8->4, Kd 0.02->2.0: add rate-loop damping to fight overshoot from saturation (2026-09-13); Umax 350 (kept from 2026-07-19); EMin 20 + SumEMax 2000 kept as-is
	
	{ 0,  0, 0.7, 0.005 ,0.1, 0,0, 0, 0, 0,   0,  0, 1.0 , 0.9,  0.3,  0.3,  30,     0.3},//h
	{ 0,  0, 400,  0.435,  1.5,0,0, 0, 0, 0,   0, 0,300, 300, 60,   60,    30,    0.1},//h rate: added Kd 0->1.5 to damp 0.4Hz oscillation (flight_1784538359)
	
		/*des FB  Kp   Ki  Kd   Up Ui Ud E preE SumE U Umax Upmax Uimax Udmax SumEmax Emin***/	  //�����Ĳ���
	//hui fei zhe   1.8outKP    1.8 0.45inKP KI
	{ 0,  0, 0.8    , 0.01 ,4.0, 0,0, 0, 0, 0,   0,  0, 300, 300,  20,   50,     200,     30},//locx
	{ 0,  0, 0.8    , 0.01 ,4.0, 0,0, 0, 0, 0,   0,  0, 300, 300,  20,   50,     200,     30},//locy
	{ 0,  0, 3.0      , 0 ,6.00, 0,0, 0, 0, 0,   0,  0, 600, 600, 100,   100,     200,   10},//locxs
	{ 0,  0, 3.0      , 0 ,6.00, 0,0, 0, 0, 0,   0,  0, 600, 600, 100,   100,     200,   10},//locys
	
		{ 0,  0, 1.0    , 0    ,2.0, 0,0, 0, 0, 0,   0,  0, 40, 40, 0,   2,     2,    2},  //stree_yaw_speed
	  { 0,  0, 0.6    , 0    ,0.0, 0,0, 0, 0, 0,   0,  0, 80, 70, 0,   5,     2,    2}  //stree_pitch_speed
};                  

/******���ַ��롢�����޷���P I D ����޷���������޷�***********************/
void ComputePID(PIDTypeDef *pPID)
{
	pPID->E = pPID->Des - pPID->FB;//���㵱ǰƫ��

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
