#include "RemoterTask.h"
#include "FreeRTOS.h"
#include "task.h"
#include "rc_input.h"
#include "flight_fsm.h"

float channel[4];
/*************************************************************************
�� �� ����void remoter_task(void)
�������ܣ�ң��������
��    ע��10msִ��һ��
*************************************************************************/
void remoter_task(void)
{
	
			// ��ң����
//		channel[0]= (sbus_channel[0]-1024)/672.0*1000.0+3000.0; 
//	  channel[1]= (sbus_channel[1]-1024)/672.0*1000.0+3000.0; 
//	  channel[2]= (sbus_channel[2]-1024)/672.0*1000.0+3000.0; 
//		channel[3]= (sbus_channel[3]-1024)/672.0*1000.0+3000.0; 
	
	
    channel[0]= (sbus_channel[0]-1000)/800.0*1000.0+3000.0; 
	  channel[1]= (sbus_channel[1]-1000)/800.0*1000.0+3000.0; 
	  channel[2]= (sbus_channel[2]-1000)/800.0*1000.0+3000.0; 
		channel[3]= (sbus_channel[3]-1000)/800.0*1000.0+3000.0; 
		
//		channel[0] = Clip(channel[0],2000,4000);
//		channel[1] = Clip(channel[1],2000,4000);
//		channel[2] = Clip(channel[2],2000,4000);
//		channel[3] = Clip(channel[3],2000,4000);
	
		if(channel[0]<1800||channel[0]>4200)channel[0]=3000;
		if(channel[1]<1800||channel[1]>4200)channel[1]=3000;
		if(channel[2]<1800||channel[2]>4200)channel[2]=3000;
		if(channel[3]<1800||channel[3]>4200)channel[3]=3000;

		Remoter.PitCtrler	= channel[1] ;
		Remoter.RolCtrler = channel[0] ;
		Remoter.ThrCtrler = channel[2] ;
		Remoter.YawCtrler = channel[3] ;

	{
		TickType_t now = xTaskGetTickCount();
		if (sbus_last_valid_tick == 0U) {
			if (now > pdMS_TO_TICKS(500)) {
				sbus_lost = 1U;
			}
		} else {
			if ((now - sbus_last_valid_tick) > pdMS_TO_TICKS(500)) {
				sbus_lost = 1U;
			} else {
				sbus_lost = 0U;
			}
		}
	}

	RCInput_UpdateNeutral();
	RCInput_Update();
}

/* Stick gesture thresholds in normalised [-1, 1] units.
 * Former raw thresholds: MAX ~4000, MIN ~2000, MID ~3000 on [2000,4000] scale. */
#define is_Stick_MAX(value)      ( (value) >  0.75f )
#define is_Stick_MIN(value)      ( (value) < -0.75f )
#define is_Stick_MID(value)      ( (value) > -0.1f && (value) < 0.1f )

void Check_Stick_Motion(void)
{
	/* Gesture recognition must read physical sticks, never virtual ones.
	 * When authority=1 (IDLE mode), RCInput_Get() returns virtual[]=0 which
	 * makes is_Stick_MAX(yaw) always false → arm gesture never fires. */
	float eff_thr = ((float)Remoter.ThrCtrler - 3000.0f) / 1000.0f;
	float eff_pit = ((float)Remoter.PitCtrler - 3000.0f) / 1000.0f;
	float eff_rol = ((float)Remoter.RolCtrler - 3000.0f) / 1000.0f;
	float eff_yaw = ((float)Remoter.YawCtrler - 3000.0f) / 1000.0f;
	
	if( is_Stick_MIN(eff_thr) &&  is_Stick_MAX(eff_yaw) )//  arm  ����
		StickMotion.LeftStick_RightDown_cnt++;
	else StickMotion.LeftStick_RightDown_cnt=0;
	
	if( is_Stick_MIN(eff_thr) &&  is_Stick_MIN(eff_yaw) )  // disarm
		StickMotion.LeftStick_LeftDown_cnt++;
	else StickMotion.LeftStick_LeftDown_cnt=0;
	
	if( is_Stick_MAX(eff_thr) &&  is_Stick_MIN(eff_yaw) )  // adjust
		StickMotion.LeftStick_LeftUp_cnt++;
	else StickMotion.LeftStick_LeftUp_cnt=0;

	if( is_Stick_MAX(eff_thr) &&  is_Stick_MAX(eff_yaw) )  // debug
		StickMotion.LeftStick_RightUp_cnt++;
	else StickMotion.LeftStick_RightUp_cnt=0;
	
	
	if( is_Stick_MIN(eff_pit) &&  is_Stick_MIN(eff_rol) )  // 
		StickMotion.RightStick_LeftDown_cnt++;
	else StickMotion.RightStick_LeftDown_cnt=0;
	
	if( is_Stick_MIN(eff_pit) &&  is_Stick_MAX(eff_rol) )  // 
		StickMotion.RightStick_RightDown_cnt++;
	else StickMotion.RightStick_RightDown_cnt=0;

	if( is_Stick_MAX(eff_pit) &&  is_Stick_MIN(eff_rol) )  // 
		StickMotion.RightStick_LeftUp_cnt++;
	else StickMotion.RightStick_LeftUp_cnt=0;

	if( is_Stick_MAX(eff_pit) &&  is_Stick_MAX(eff_rol) )  // 
		StickMotion.RightStick_RightUp_cnt++;
	else StickMotion.RightStick_RightUp_cnt=0;

	

	if(StickMotion.LeftStick_RightDown_cnt>=ARM_Delay_time)
	{
		FlightFSM_Event(FLIGHT_EVENT_ARM_REQUEST);
		StickMotion.LeftStick_RightDown_cnt=0;StickMotion.LeftStick_LeftDown_cnt=0;
	}
	if(StickMotion.LeftStick_LeftDown_cnt>=DISARM_Delay_time)
	{
		FlightFSM_Event(FLIGHT_EVENT_DISARM_REQUEST);
		StickMotion.LeftStick_RightDown_cnt=0;StickMotion.LeftStick_LeftDown_cnt=0;
	}
}
/*************************************************************************
�� �� ����void Check_Fly_Mode(void)
�������ܣ�������״̬
��    ע��PA10(USART1_RX)
*************************************************************************/
void Check_Fly_Mode(void)
{
	static int DangerousStop_cnt = 0;
	Check_Stick_Motion();
	
		if( sbus_channel[9] <=500 ) //���˲��������Ϸ�
	{
			DangerousStop_cnt ++;
	}
	else
	{
		DangerousStop_cnt = 0;
	}

	if(DangerousStop_cnt>10) //50ms
	{
		FlightFSM_Event(FLIGHT_EVENT_DANGEROUS_STOP);
	}
	else
	{
		FlightFSM_Event(FLIGHT_EVENT_RECOVER_SDK);
	}

	/* --- SBUS ch5 (MODE_CH): rising edge into LAND position triggers landing.
	 * Only valid from FLYING phase — ignored when on ground (won't start ramp if not airborne).
	 * ch5 no longer sets authority; authority is managed by CMD 0x0E only.       */
	{
		static uint8_t ch5_prev = 0U;
		uint8_t ch5_now = (MODE_CH > 1300) ? 1U : 0U;
		if (ch5_now && !ch5_prev && flight_phase == FLIGHT_PHASE_FLYING)
		{
			flight_phase = FLIGHT_PHASE_LANDING;
			TWC.execute  = 0U;
		}
		ch5_prev = ch5_now;
	}

	/* --- SBUS ch7 (FLYUP_CH): rising-edge fly-up to Z=0.5 m trigger -----------
	 * Arms the drone if disarmed, then sets sbus_flyup_trigger.
	 * Authority is released in StabilizerTask when the trigger is consumed.   */
	{
		static uint8_t ch6_prev = 0U;
		uint8_t ch6_now = (FLYUP_CH > 500U) ? 1U : 0U;
		if (ch6_now && !ch6_prev)
		{
			if (FlightFSM_GetState() == FLIGHT_STATE_DISARMED)
				FlightFSM_Event(FLIGHT_EVENT_ARM_REQUEST);
			sbus_flyup_trigger = 1U;
		}
		ch6_prev = ch6_now;
	}

	/* --- SBUS ch8 (PATH_EXEC_CH): rising-edge preset-path trigger --------------
	 * Arms the drone (if needed) and sets sbus_path_trigger so the path
	 * execution handler can launch the preset path loaded from the GS.         */
	{
		static uint8_t ch8_prev = 0U;
		uint8_t ch8_now = (PATH_EXEC_CH > 500U) ? 1U : 0U;
		if (ch8_now && !ch8_prev)
		{
			if (FlightFSM_GetState() == FLIGHT_STATE_DISARMED)
			{
				FlightFSM_Event(FLIGHT_EVENT_ARM_REQUEST);
				RCInput_SetAuthority(1U);
			}
			sbus_path_trigger = 1U;
		}
		ch8_prev = ch8_now;
	}
}

