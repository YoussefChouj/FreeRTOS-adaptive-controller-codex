#ifndef __SINS_H
#define __SINS_H
#include "stm32f4xx.h"
#include "bmi088_driver.h"
#include "StabilizerTask.h"
#include "Filter.h"

typedef   signed  char   int8;
typedef unsigned  char   uint8;
typedef unsigned  char   byte;
typedef   signed  short  int16;
typedef unsigned  short  uint16;

#define LocXY_DT  0.005f
#define   H_DT    0.005f
#define _Z    0
#define _X    1
#define _Y    2

#define MAX(a,b)  ((a)>(b)?(a):(b))
#define MIN(a,b)  ((a)<(b)?(a):(b))



typedef struct
{
  float x;
  float y;
  float z;
}Vector3f;



#define Axis_Num  3
#define Save_Num  50
typedef struct
{
	float Position[Axis_Num];//位置估计量
	float Speed[Axis_Num];//速度估计量
	float Acc[Axis_Num];//加速度估计量
	float Pos_History[Axis_Num][Save_Num];//历史惯导位置
	float Last_Acc[Axis_Num];
	float Origin_Pos[Axis_Num];
	float Origin_Vel[Axis_Num];
	float Origin_Acc[Axis_Num];
	float SpeedDelta[Axis_Num];
	
	float pos_correction[Axis_Num];
	float acc_correction[Axis_Num];
	float vel_correction[Axis_Num];
}SINSTypeDef;//   Strapdown inertial navigation system


#define Num  50
typedef struct
{
 float Position[Axis_Num];//位置估计量
 float Speed[Axis_Num];//速度估计量
 float Acceleration[Axis_Num];//加速度估计量
 float Pos_History[Axis_Num][Num];//历史惯导位置
 float Vel_History[Axis_Num][Num];//历史惯导速度
 float Acce_History[Axis_Num][Num];//历史惯导速度
 float Acce_Bias[Axis_Num];//惯导加速度漂移量估计量
 float Acce_Bias_All[Axis_Num];//惯导加速度漂移量估计量
 float Last_Acceleration[Axis_Num];
 float Last_Speed[Axis_Num];
}SINS;

#define AcceMax     4096  //   4096
#define AcceGravity 9.80f


#ifndef M_PI_F
 #define M_PI_F 3.141592653589793f
#endif
//#ifndef PI
// # define PI M_PI_F
//#endif
#ifndef M_PI_2
 # define M_PI_2 1.570796326794897f
#endif
//Single precision conversions
#define DEG_TO_RAD 0.017453292519943295769236907684886f
#define RAD_TO_DEG 57.295779513082320876798154814105f

extern float Altitude_Delta;
extern SINSTypeDef   stSINS;
extern float Sin_Pitch,Sin_Roll,Sin_Yaw;
extern float Cos_Pitch,Cos_Roll,Cos_Yaw;

float constrain_float(float amt, float low, float high);
int16_t constrain_int16_t(int16_t amt, int16_t low, int16_t high);

void  SINS_Prepare(void);
void  Strapdown_INS_High(float height_INS_raw);//  cm  !!!!!!!!!!!!!
void Strapdown_INS_Horizontal(float locx,float locy);
void imuComputeRotationMatrix(void);
void Vector_From_EarthFrame2BodyFrame(Vector3f *ef,Vector3f *bf);
void Strapdown_INS_High_Kalman(void);

void  KalmanFilter(float Observation,//位置观测量
                   uint16 Pos_Delay_Cnt,//观测传感器延时量
                   SINS *Ins_Kf,//惯导结构体
                   float System_drive,//系统原始驱动量，惯导加速度
                   float *Q,
                   float R,
                   float dt,
                   uint16 N,
                   uint8_t *update_flag);
									 
typedef struct
{
	float in_est_d;   //Estimator
	float in_obs;    //Observation
	
	float fix_kp;
	float e_limit;

/////	
	float e;

	float out;
}_fix_inte_filter_st;

typedef struct
{
	float in_est;    //Estimator
	float in_obs;    //Observation
	
	float fix_ki;
	float ei_limit;     //


/////	
	float e;
	float ei;

	float out;
}_inte_fix_filter_st;
									 
void WCZ_Data_Calc(u8 dT_ms,u8 wcz_f_pause,s32 wcz_acc_get,s32 ref_height)	;		


void WCZ_Data_Reset(void);

void fix_inte_filter(float dT,_fix_inte_filter_st *data);

extern float  KalmanFilter_data;

extern _fix_inte_filter_st wcz_spe_fus,wcz_hei_fus;
extern _inte_fix_filter_st wcz_acc_fus;

#endif
