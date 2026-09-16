#include "AutoflyTask.h"
#include "math.h"
#include "flight_fsm.h"
#include "rc_input.h"

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

void AutoflyTask_RunCircle(void)
{
	const float dt = 0.005f;

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
	Ctrler.yawPID.Des = circle_path.theta * RAD2DEG;

	if (circle_path.duration > 0.0f &&
	    circle_path.t_elapsed >= circle_path.duration) {
		circle_path.active = 0U;
		TWC.execute = 0;
	}
}

void AutoflyTask_RunSinusoid(void)
{
	const float dt = 0.005f;

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

	Ctrler.yawPID.Des = 0.0f;

	if (sinusoid_path.duration > 0.0f &&
	    sinusoid_path.t_elapsed >= sinusoid_path.duration) {
		sinusoid_path.active = 0;
		TWC.execute = 0;
	}
}

void AutoflyTask_RunFigure8(void)
{
	const float dt = 0.005f;
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
	Ctrler.yawPID.Des = 0.0f;

	if (figure8_path.duration > 0.0f &&
	    figure8_path.t_elapsed >= figure8_path.duration) {
		figure8_path.active = 0U;
		TWC.execute = 0;
	}
}

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

	if (((sbus_channel[5] == 1800) && (sbus_channel[4] == 1800) && (sbus_lost == 0)) ) {
		KeyPressedTimeMS += 5U;
	} else {
		KeyPressedTimeMS = 0U;
	}

		if(KeyPressedTimeMS >=2000 )
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
#define SDK_Cmd_TakeOff        0  //���
#define SDK_Cmd_Land           1  //����
#define SDK_Cmd_Search0        2  //ԭ��תȦ����
#define SDK_Cmd_Search1        3  //�����ƶ�����
#define SDK_Cmd_PosHold        4  //����
#define SDK_Cmd_Circle         5  //��ɵ��Բ
#define SDK_Cmd_FollowLine     6  //ѭ��
#define SDK_Cmd_PowerLine      7  //ѭ����    ����ר��
#define SDK_Cmd_Surround       8  //�Ʒ�      ����ר��
#define SDK_Cmd_GetLine        9  //����    ����ר��
#define SDK_Cmd_GetClose       10 //����    ����ר��
#define SDK_Cmd_DelayWake      11  //��ʱ����
#define SDK_Cmd_Pos1           12  //����1
#define SDK_Cmd_Pos2           13  //����2
#define SDK_Cmd_Pos3           14  //����3   
#define SDK_Cmd_Pos4           15  //����4
#define SDK_Cmd_SearchLand     16  //����4
#define SDK_Cmd_SearchLand_down 17  //����4
#define SDK_Cmd_Searchgan      18  //����4
#define SDK_Cmd_Pos5           19  //����4
#define SDK_Cmd_Pos6           20  //����4
#define SDK_Cmd_Pos7           21  //����4
#define SDK_Cmd_Pos8           22  //����4
#define SDK_Cmd_Pos9           23  //����4
#define SDK_Cmd_Pos10          24  //����4
#define  SDK_Cmd_go_to_land    25  //����4
#define V_max `10

void SDK_StateMachine_Init(void)
{
	CurrentSDKState = 0;
	
//	SDK_StateMachine[CurrentSDKState++] = SDK_Cmd_DelayWake;
//	SDK_StateMachine[CurrentSDKState++] = 3000;
//	
//	SDK_StateMachine[CurrentSDKState++] = SDK_Cmd_TakeOff;
//	SDK_StateMachine[CurrentSDKState++] = 3000;

//	SDK_StateMachine[CurrentSDKState++] = SDK_Cmd_PosHold;
//	SDK_StateMachine[CurrentSDKState++] = 500;

//	SDK_StateMachine[CurrentSDKState++] = SDK_Cmd_Pos1;
//	SDK_StateMachine[CurrentSDKState++] = 5000000;


//	SDK_StateMachine[CurrentSDKState++] = SDK_Cmd_Land;
//	SDK_StateMachine[CurrentSDKState++] = 5000;
	
	SDKStateMAX = CurrentSDKState-2;
	CurrentSDKState=0;
}

#define  SDK_Height     0.7f
#define  SDK_Height_H   (SDK_Height+0.05f)
#define  SDK_Height_L   (SDK_Height-0.05f)


void SDK_StateMachine_Loop(void)
{
	static unsigned int LastSDKState;
	switch ( SDK_StateMachine[ CurrentSDKState ] )
	{
		
/*************************************************************************************/	
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
			
/*************************************************************************************/
		
		case SDK_Cmd_TakeOff:
    
			Ctrler.Z_posPID.Des = SDK_Height;
			if(Ctrler.Z_posPID.FB >=SDK_Height_L && Ctrler.Z_posPID.FB<=SDK_Height_H)
				SDK_StateMachine[ CurrentSDKState +1 ]=0;
		  
			break;
			

/*************************************************************************************/
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
			
			
/*************************************************************************************/
		case SDK_Cmd_Pos1:
			

			break;				

/*************************************************************************************/					
		
		case SDK_Cmd_Land:
			
        Ctrler.Z_posPID.Des =  0.0;
				SDKLandFlag = 1;

			if(Ctrler.Z_posPID.FB <=0.2f)
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
	
/*********************************�����ٶȷ���**********************************************/		
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
		if(SBUS_CH_VALID(PITCH_CH))//��˶�Ӧ����ˮƽ�ٶ�
				Ctrler.locysPID.Des = -((Remoter.PitCtrler-3000)/1000.0)*Stick_to_MAX_Horizontal_Rate;
		else if(temp_V_y== -1) 
		{  
			if(y_test ==0) //ֻ����һ��
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
		
		if(SBUS_CH_VALID(ROLL_CH))//��˶�Ӧ����ˮƽ�ٶ�
				Ctrler.locxsPID.Des = -((Remoter.RolCtrler-3000)/1000.0)*Stick_to_MAX_Horizontal_Rate;  
		else if(temp_V_x == -1)
		{ 
			if(x_test ==0) //ֻ����һ��
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
			if(yaw_test==0) //��־λ��ֻ����һ��
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
	//if( CurrentSDKState !=0 ) KeySDKflag=0;
	CurrentSDKState = 0;
	SDK_StateMachine_Init();
}
