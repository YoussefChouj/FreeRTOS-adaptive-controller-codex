#ifndef __RM_ROBOT_H__
#define __RM_ROBOT_H__

#include "data_types.h"
#include "stm32f4xx.h"

/* Anti-windup mode selector for ComputePID(). Default 0 = original behaviour. */
typedef enum
{
	AW_LEGACY   = 0,   // EMin integral-separation + SumE clamp (unchanged default)
	AW_CLAMP    = 1,   // conditional integration keyed on ACTUAL output saturation
	AW_BACKCALC = 2    // back-calculation observer (requires Kt > 0)
} PID_AntiWindup_e;

typedef struct
{
  float Des;//���Ʊ���Ŀ��ֵ
  float FB;//���Ʊ�������ֵ
	
	float Kp;//����ϵ��Kp
	float Ki;//����ϵ��Ki
	float Kd;//΢��ϵ��Kd
	
	float Up;//�������
	float Ui;//�������
	float Ud;//΢�����
	
	float E;//����ƫ��
	float PreE;//�ϴ�ƫ��
  float SumE;//��ƫ��
	float U;//����PID������
	
	float UMax;//PID�����������ֵ������������ʱ������ֵ
	float UpMax;//������������ֵ
	float UiMax;//������������ֵ
	float UdMax;//΢����������ֵ
	float SumEMax;//���ֱ���ֵ
	float EMin;//���ַ�����ֵ

	/* --- Anti-windup extension (appended; zero-init keeps legacy default) --- */
	int   aw_mode;   // PID_AntiWindup_e: 0=legacy(default), 1=clamp, 2=back-calc
	float Kt;        // back-calculation gain, used only when aw_mode==AW_BACKCALC
}PIDTypeDef;

typedef struct 
{
	PIDTypeDef    pitchPID;
	PIDTypeDef    rollPID;
	PIDTypeDef    yawPID;
	PIDTypeDef    gyroxPID;
	PIDTypeDef    gyroyPID;
	PIDTypeDef    gyrozPID;
	PIDTypeDef    Z_posPID;
	PIDTypeDef    Z_ratePID;
	PIDTypeDef    locxPID;
	PIDTypeDef    locyPID;
	PIDTypeDef    locxsPID;
	PIDTypeDef    locysPID;
	
	PIDTypeDef   stree_yaw_speed ;
	PIDTypeDef   stree_pitch_speed ;
}CtrlerTypeDef;


typedef struct
{
	unsigned int LeftStick_LeftDown_cnt;
	unsigned int LeftStick_LeftUp_cnt;
	unsigned int LeftStick_RightDown_cnt;
	unsigned int LeftStick_RightUp_cnt;
	
	unsigned int RightStick_LeftDown_cnt;
	unsigned int RightStick_LeftUp_cnt;
	unsigned int RightStick_RightDown_cnt;
	unsigned int RightStick_RightUp_cnt;
}StickMotionTypeDef;


typedef struct
{
	unsigned int  PIDingDelayMS;
	unsigned char SensorsStatus;
	unsigned char AdjustStatus;
	unsigned char ARM_Status;
	unsigned char FlyMode;
	_Bool         Is_GetingGyroZero;

}DroneStatusTypeDef;


	typedef struct
{
	unsigned short System_cnt;
	double         System_cnt_100ms;
	unsigned short IMUSampleTask_fps;
	unsigned short IMUUpdateTask_fps;
	unsigned short stabilizerTask_fps;
	unsigned short remoter_task_fps;
	unsigned short USART1_task_fps;
	unsigned short USART2_task_fps;
	unsigned short USART4_task_fps;
	unsigned short USART5_task_fps;
	unsigned short AutoflyTask_fps;
	
	unsigned short IMUSampleTask_cnt;
	unsigned short IMUUpdateTask_cnt;
	unsigned short stabilizerTask_cnt;
	unsigned short remoter_task_cnt;
	unsigned short USART1_task_cnt;
	unsigned short USART2_task_cnt;
	unsigned short USART4_task_cnt;
	unsigned short USART5_task_cnt;
	unsigned short AutoflyTask_cnt;
}SYSTEM_MONITOR;

typedef struct
{
	unsigned short PitCtrler;
	unsigned short RolCtrler;
	unsigned short YawCtrler;
	unsigned short ThrCtrler;
	unsigned short DinggaoSwitch;
	unsigned short DingdianSwitch;
	unsigned short StopSwitch;
}RemoterTypeDef;

typedef struct
{
	USART_TypeDef* USARTx;            //����
	DMA_Stream_TypeDef* DMAy_Streamx; //DMA������
	UCHAR8* pMailbox;                 //����(��Ч����)����
  __IO UCHAR8* pDMAbuf;             //DMA����
	USHORT16 MbLen;                   //mailbox����
	USHORT16 DMALen;                  //DMA����
	USHORT16 rxConter;                //����DMA����
	USHORT16 rxBufferPtr;             //�ϴεĳ���  ����Ҳ����λ��
  USHORT16 rxSize;                  //���ν��յĳ���
}USART_RX_TypeDef;

typedef struct
{
	FP32 rol;
	FP32 pit;
	FP32 yaw;
	FP32 wx;
	FP32 wy;
	FP32 wz;
}ST_IMU_DATA;
//�Ӿ����ݴ����ṹ��


typedef union
{
	ST_IMU_DATA st_imu_data;
	u8 data[24];
}UN_IMU_DATA;

typedef struct
{
    float angle_180;
    float angle_180_pre;
    float angle_inf;
} ST_ANGLE;

typedef struct
{
	FP32 Kp;
	FP32 Ki;
	FP32 Kd;	
}PID_Param;


//������
typedef enum {OFF = 0, ON = 1, TWINKLE = 2}LED_MODE;
typedef enum {INIT = 0, NORMAL = 1, CALIBRATION = 2}IMU_MODE;
typedef enum {LOOP = 0, IDENTIFY = 1}CTRL_MODE;

typedef struct
{
	float preout;
	float out;
	float in;
	float off_freq;
	float samp_tim;
}ST_LPF;



/*PID�������ṹ��*/
typedef struct 
{
  FP32 fpDes;//���Ʊ���Ŀ��ֵ
  FP32 fpFB;//���Ʊ�������ֵ
	
	FP32 fpKp;//����ϵ��Kp
	FP32 fpKi;//����ϵ��Ki
	FP32 fpKd;//΢��ϵ��Kd
	
	FP32 fpUp;//�������
	FP32 fpUi;//�������
	FP32 fpUd;//΢�����
	
	FP32 fpE;//����ƫ��
	FP32 fpPreE;//�ϴ�ƫ��
  FP32 fpSumE;//��ƫ��
	FP32 fpU;//����PID������
	
	FP32 fpUMax;//PID�����������ֵ������������ʱ������ֵ
	FP32 fpEpMax;//������������ֵ
	FP32 fpEiMax;//������������ֵ
	FP32 fpEdMax;//΢����������ֵ
	FP32 fpEMin;//��������
}ST_PID;

typedef struct
{
	FP32 fpRawValue; //��ǰ����ֵ
	FP32 fpPreRawValue; // �ϴβ���ֵ
	FP32 fpDiff;  //���β���ƫ��
	FP32 fpSumValue; //�������ֵ
	FP32 fpOffsetValue; //�궨��λֵ
	bool InitState; //��ʼ����ɱ�־
}ST_GYRO;

typedef struct
{
	SINT32 Renew_FLAG;
	SINT32 Beat_State;
	FP32 Beat_Yaw;
	FP32 Beat_Pitch;
}ST_ENEMY_POS;

typedef union
{
	ST_ENEMY_POS stEnemyPos;	
	UCHAR8 ucPosData[16];
}UN_VIRTUAL_DATA;

typedef struct
{
	FP32 Is_Rcg_FLAG;  //led_flag
	FP32 E_Pitch;   //������
	FP32 E_Yaw;     //openmv
}ST_ENEMY_Aim;

typedef union
{
	ST_ENEMY_Aim stEnemyE;	
	UCHAR8 ucEData[12];
}UN_AIM_DATA;

typedef struct
{
	FP32 AimToPC;
	FP32 PitchToAim;	
	FP32 YawToAim;
}ST_SEND_ANGLE;

typedef union
{
	ST_SEND_ANGLE st_SendAngle;
	UCHAR8 ucPitchData[12]; 
}Send_Aim_DATA;

typedef union
{
	FP32 VisualState;
	UCHAR8 ucStateData[4];
}SEND_VISUAL_STATE;


typedef struct
{
	double raw_value;
	double xbuf[18];
	double ybuf[18];
	double filtered_value;
}Filter_t;



typedef struct
{
	USHORT16 TxHead1;
	USHORT16 TxHead2;
  FP32 data[10];
  USHORT16 TxTail1;
	USHORT16 TxTail2;
}ST_OFFLINE_TX_BUF;

typedef union
{
	ST_OFFLINE_TX_BUF st_usart_tx_buf;
	UCHAR8 ucdata[48];
}UN_OFFLINE_TX_BUF;


typedef struct
{
  float raw_value;
  float filtered_value[2];
  float xhat_data[2], xhatminus_data[2], z_data[2],Pminus_data[4], K_data[4];
  float P_data[4];
  float AT_data[4], HT_data[4];
  float A_data[4];
  float H_data[4];
  float Q_data[4];
  float R_data[4];
} kalman_filter_init_t;


//typedef struct
//{
//	FP32 x1;
//	FP32 x2;
//	FP32 x;
//	FP32 r;
//	FP32 h;
//	FP32 aim;
//}TD;

enum
{
	X = 0,
	Y = 1,
	Z = 2,
	VEC_XYZ,
};

typedef struct
{
	float q0;//q0;
	float q1;//q1;
	float q2;//q2;
	float q3;//q3;
	
	float gkp;
	float gki;

	float x_vec[VEC_XYZ];
	float y_vec[VEC_XYZ];
	float z_vec[VEC_XYZ];

	float a_acc[VEC_XYZ];
	float gacc_deadzone[VEC_XYZ];
	float gra_acc[VEC_XYZ];
	
	float rol;
	float pit;
	float yaw;
} _imu_st ;



#endif
