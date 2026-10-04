/**
 * @module     SINS.c
 * @subsystem  sensors
 * @owner      none: no function or global here has a caller outside this file (git grep over *.c *.h *.s, WP-41).
 *             API/pid.c read Cos_Yaw/Sin_Yaw until ac782ba; the yaw rotation now lives in StabilizerTask.c
 *             (Cos_Yaw_01/Sin_Yaw_01). Kept because USER/JX_FLY.uvprojx lists the file and SINS.h declares these symbols;
 *             deleting the module is a separate decision (PROPOSED in the WP-41 report).
 * @purpose    Legacy strapdown inertial navigation: rotates the body-frame accelerometer into the earth frame and runs a
 *             third-order complementary filter per axis that corrects the integrated acceleration, velocity and position
 *             with a delayed height (Z) or position (X/Y) measurement.
 * @inputs     imu_data.pit/rol/yaw [deg], acc_data.real_acc_* [m/s^2], height_INS_raw [cm], locx/locy [cm].
 * @outputs    stSINS (Acc/Speed/Position per axis, cm units), rMat, Sin_ and Cos_ of pitch/roll/yaw, Altitude_Delta.
 */

#include "SINS.h"
#include "math.h"
#include "StabilizerTask.h"

/* ------------------------------------------------------------------
 * Module state
 * ------------------------------------------------------------------ */

SINSTypeDef stSINS;

/* Body-to-earth rotation, rebuilt by imuComputeRotationMatrix. */
float Sin_Pitch=0,Sin_Roll=0,Sin_Yaw=0;
float Cos_Pitch=0,Cos_Roll=0,Cos_Yaw=0;
float rMat[3][3];

/* ------------------------------------------------------------------
 * Frame rotation
 * ------------------------------------------------------------------ */

float sqf(float x) {return ((x)*(x));}

/* Rotation matrix from the negated IMU Euler angles (pitch about X, roll about Y, yaw about Z). */
void imuComputeRotationMatrix(void)
{
	float Pitch,Roll,Yaw;
	Pitch = -imu_data.pit;
	Roll  = -imu_data.rol;
	Yaw   = -imu_data.yaw;

  Sin_Pitch=sin(Pitch* DEG2RAD);
  Cos_Pitch=cos(Pitch* DEG2RAD);
  Sin_Roll=sin(Roll* DEG2RAD);
  Cos_Roll=cos(Roll* DEG2RAD);
  Sin_Yaw=sin(Yaw* DEG2RAD);
  Cos_Yaw=cos(Yaw* DEG2RAD);

  rMat[0][0]=Cos_Yaw* Cos_Roll;
  rMat[0][1]=Sin_Pitch*Sin_Roll*Cos_Yaw-Cos_Pitch * Sin_Yaw;
  rMat[0][2]=Sin_Pitch * Sin_Yaw+Cos_Pitch * Sin_Roll * Cos_Yaw;

  rMat[1][0]=Sin_Yaw * Cos_Roll;
  rMat[1][1]=Sin_Pitch * Sin_Roll * Sin_Yaw +Cos_Pitch * Cos_Yaw;
  rMat[1][2]=Cos_Pitch * Sin_Roll * Sin_Yaw - Sin_Pitch * Cos_Yaw;

  rMat[2][0]=-Sin_Roll;
  rMat[2][1]= Sin_Pitch * Cos_Roll;
  rMat[2][2]= Cos_Pitch * Cos_Roll;
}

/* ef = rMat * bf */
void Vector_From_BodyFrame2EarthFrame(Vector3f *bf,Vector3f *ef)
{
  ef->x=rMat[0][0]*bf->x+rMat[0][1]*bf->y+rMat[0][2]*bf->z;
  ef->y=rMat[1][0]*bf->x+rMat[1][1]*bf->y+rMat[1][2]*bf->z;
  ef->z=rMat[2][0]*bf->x+rMat[2][1]*bf->y+rMat[2][2]*bf->z;
}

/* bf = rMat^T * ef */
void Vector_From_EarthFrame2BodyFrame(Vector3f *ef,Vector3f *bf)
{
  bf->x=rMat[0][0]*ef->x+rMat[1][0]*ef->y+rMat[2][0]*ef->z;
  bf->y=rMat[0][1]*ef->x+rMat[1][1]*ef->y+rMat[2][1]*ef->z;
  bf->z=rMat[0][2]*ef->x+rMat[1][2]*ef->y+rMat[2][2]*ef->z;
}

/* Earth-frame acceleration without gravity, in cm/s^2 (Y negated to the navigation frame). */
void  SINS_Prepare(void)
{
  Vector3f Body_Frame,Earth_Frame;

  Body_Frame.x= acc_data.real_acc_y;
  Body_Frame.y= acc_data.real_acc_x;
  Body_Frame.z= acc_data.real_acc_z;

	imuComputeRotationMatrix();
  Vector_From_BodyFrame2EarthFrame(&Body_Frame,&Earth_Frame);

  stSINS.Origin_Acc[_X]=Earth_Frame.x;
  stSINS.Origin_Acc[_Y]=-Earth_Frame.y;
	stSINS.Origin_Acc[_Z]=Earth_Frame.z;

  stSINS.Origin_Acc[_Z]-=9.8f;      /* remove gravity      */
  stSINS.Origin_Acc[_Z]*=100;       /* m/s^2 -> cm/s^2     */
  stSINS.Origin_Acc[_X]*=100;
  stSINS.Origin_Acc[_Y]*=100;
}

/* ------------------------------------------------------------------
 * Height (Z) channel: third-order complementary filter
 * ------------------------------------------------------------------ */

/* Time constant [s] of the Z correction loop; the three gains follow from it. SPL06_Sync_Cnt is the history index
   (one entry per 2 calls, 10 ms) that lines the estimate up with the delayed barometer/height measurement. */
float TIME_CONTANST_ZER=2.0f;
#define K_ACC_ZER 	        (10.0f / (TIME_CONTANST_ZER * TIME_CONTANST_ZER * TIME_CONTANST_ZER))
#define K_VEL_ZER	        (5.0f / (TIME_CONTANST_ZER * TIME_CONTANST_ZER))
#define K_POS_ZER               (5.0f / TIME_CONTANST_ZER)
#define SPL06_Sync_Cnt 6

float Altitude_Delta=0;
/* One H_DT step of the Z channel; height_INS_raw is the height measurement [cm]. */
void Strapdown_INS_High(float height_INS_raw)
{
  uint16 Cnt=0;
  static uint16_t Save_Cnt=0;
  Save_Cnt++;

  /* innovation: measurement minus the delayed estimate [cm] */
  Altitude_Delta=height_INS_raw-stSINS.Pos_History[_Z][SPL06_Sync_Cnt];

  /* integrate the three correction terms */
  stSINS.acc_correction[_Z] += Altitude_Delta* K_ACC_ZER*H_DT ;
  stSINS.vel_correction[_Z] += Altitude_Delta* K_VEL_ZER*H_DT ;
  stSINS.pos_correction[_Z] += Altitude_Delta* K_POS_ZER*H_DT ;

  stSINS.Last_Acc[_Z]=stSINS.Acc[_Z];
  stSINS.Acc[_Z]=stSINS.Origin_Acc[_Z]+stSINS.acc_correction[_Z];
  /* second-order Runge-Kutta (trapezoid) velocity increment: H_DT = 5 ms is long and the accelerometer is not
     smooth, so a higher order buys nothing */
  stSINS.SpeedDelta[_Z]=(stSINS.Last_Acc[_Z]
                    +stSINS.Acc[_Z])*H_DT/2.0f;
  stSINS.Origin_Pos[_Z]+=(stSINS.Speed[_Z]+0.5f*stSINS.SpeedDelta[_Z])*H_DT;
  stSINS.Position[_Z]=stSINS.Origin_Pos[_Z]+stSINS.pos_correction [_Z];
  stSINS.Origin_Vel[_Z]+=stSINS.SpeedDelta[_Z];
  stSINS.Speed[_Z]=stSINS.Origin_Vel[_Z]+stSINS.vel_correction[_Z];

  if(Save_Cnt>=2)   /* shift the position history every 2 calls (10 ms) */
  {
    for(Cnt=Save_Num-1;Cnt>0;Cnt--)
    {
      stSINS.Pos_History[_Z][Cnt]=stSINS.Pos_History[_Z][Cnt-1];
    }
    stSINS.Pos_History[_Z][0]=stSINS.Position[_Z];
    Save_Cnt=0;
  }
}

/* ------------------------------------------------------------------
 * Horizontal (X/Y) channels: third-order complementary filter
 * ------------------------------------------------------------------ */

/* Time constant [s] of the X/Y correction loop. LocXY_SINS_Delay_Cnt is the history index (50 ms per entry) matched to
   the position measurement delay. */
float TIME_CONTANST_XY =   2.5f;
#define K_ACC_XY	     (2.0f / (TIME_CONTANST_XY * TIME_CONTANST_XY * TIME_CONTANST_XY))
#define K_VEL_XY             (4.0f / (TIME_CONTANST_XY * TIME_CONTANST_XY))
#define K_POS_XY             (4.0f / TIME_CONTANST_XY)

uint16_t LocXY_SINS_Delay_Cnt=4;

/* One LocXY_DT step of the X and Y channels; locx/locy are the position measurements [cm]. */
void Strapdown_INS_Horizontal(float locx,float locy)
{
  uint16 Cnt=0;
	float locX_Delta=0,locY_Delta=0;
	static uint16_t LocXY_Save_Period_Cnt=0;

  LocXY_Save_Period_Cnt++;
  if(LocXY_Save_Period_Cnt>=10)   /* shift the position history every 10 calls (50 ms) */
  {
    for(Cnt=Save_Num-1;Cnt>0;Cnt--)
    {
      stSINS.Pos_History[_X][Cnt]=stSINS.Pos_History[_X][Cnt-1];
      stSINS.Pos_History[_Y][Cnt]=stSINS.Pos_History[_Y][Cnt-1];
    }
    stSINS.Pos_History[_X][0]=stSINS.Position[_X];
    stSINS.Pos_History[_Y][0]=stSINS.Position[_Y];
    LocXY_Save_Period_Cnt=0;
  }

  locX_Delta = locx - stSINS.Pos_History[_X][LocXY_SINS_Delay_Cnt];
  locY_Delta = locy - stSINS.Pos_History[_Y][LocXY_SINS_Delay_Cnt];

  stSINS.acc_correction[_X] += locX_Delta* K_ACC_XY*LocXY_DT;
  stSINS.vel_correction[_X] += locX_Delta* K_VEL_XY*LocXY_DT;
  stSINS.pos_correction[_X] += locX_Delta* K_POS_XY*LocXY_DT;

  stSINS.acc_correction[_Y] += locY_Delta* K_ACC_XY*LocXY_DT;
  stSINS.vel_correction[_Y] += locY_Delta* K_VEL_XY*LocXY_DT;
  stSINS.pos_correction[_Y] += locY_Delta* K_POS_XY*LocXY_DT;

  /* X: corrected acceleration -> velocity increment -> raw and corrected position and velocity */
  stSINS.Acc[_X]=stSINS.Origin_Acc[_X]+stSINS.acc_correction[_X];
  stSINS.SpeedDelta[_X]=stSINS.Acc[_X]*LocXY_DT;
  stSINS.Origin_Pos[_X]+=(stSINS.Speed[_X]+0.5f*stSINS.SpeedDelta[_X])*LocXY_DT;
  stSINS.Position[_X]=stSINS.Origin_Pos[_X]+stSINS.pos_correction[_X];
  stSINS.Origin_Vel[_X]+=stSINS.SpeedDelta[_X];
  stSINS.Speed[_X]=stSINS.Origin_Vel[_X]+stSINS.vel_correction[_X];

  /* Y: same steps */
  stSINS.Acc[_Y]=stSINS.Origin_Acc[_Y]+stSINS.acc_correction[_Y];
  stSINS.SpeedDelta[_Y]=stSINS.Acc[_Y]*LocXY_DT;
  stSINS.Origin_Pos[_Y]+=(stSINS.Speed[_Y]+0.5f*stSINS.SpeedDelta[_Y])*LocXY_DT;
  stSINS.Position[_Y]=stSINS.Origin_Pos[_Y]+stSINS.pos_correction[_Y];
  stSINS.Origin_Vel[_Y]+=stSINS.SpeedDelta[_Y];
  stSINS.Speed[_Y]=stSINS.Origin_Vel[_Y]+stSINS.vel_correction[_Y];
}

/* ------------------------------------------------------------------
 * Math helpers
 * ------------------------------------------------------------------ */

/* sqrtf that returns 0 instead of NaN for a negative input. */
float safe_sqrt(float v)
{
  float ret = sqrtf(v);
  if (isnan(ret)) {
    return 0;
  }
  return ret;
}

/* Clamp to [low, high]; NaN maps to the midpoint so it cannot propagate (+-Inf clamp normally). */
float constrain_float(float amt, float low, float high)
{
  if (isnan(amt)) {
    return (low+high)*0.5f;
  }
  return ((amt)<(low)?(low):((amt)>(high)?(high):(amt)));
}
