#ifndef __ROBOT_TYPES_H
#define __ROBOT_TYPES_H

typedef enum
{
	AW_LEGACY   = 0,   // EMin integral-separation + SumE clamp (unchanged default)
	AW_CLAMP    = 1,   // conditional integration keyed on ACTUAL output saturation
	AW_BACKCALC = 2    // back-calculation observer (requires Kt > 0)
} PID_AntiWindup_e;

typedef struct
{
  float Des;
  float FB;
	
	float Kp;
	float Ki;
	float Kd;
	
	float Up;
	float Ui;
	float Ud;
	
	float E;
	float PreE;
  float SumE;
	float U;
	
	float UMax;
	float UpMax;
	float UiMax;
	float UdMax;
	float SumEMax;
	float EMin;

	int   aw_mode;
	float Kt;
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
} CtrlerTypeDef;

#endif
