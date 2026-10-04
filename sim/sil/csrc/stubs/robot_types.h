/* SIL host stand-in for Global_file/robot_types.h: the PIDTypeDef / CtrlerTypeDef layout of lines 7-62, so
 * API/pid.c and API/mrac.c see one struct (API/tests/stubs has a 4-member CtrlerTypeDef for MRAC-only builds). */
#ifndef SIL_STUB_ROBOT_TYPES_H
#define SIL_STUB_ROBOT_TYPES_H

#include <stdint.h>

typedef enum
{
	AW_LEGACY   = 0,
	AW_CLAMP    = 1,
	AW_BACKCALC = 2
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
} PIDTypeDef;

typedef struct
{
	PIDTypeDef pitchPID;
	PIDTypeDef rollPID;
	PIDTypeDef yawPID;
	PIDTypeDef gyroxPID;
	PIDTypeDef gyroyPID;
	PIDTypeDef gyrozPID;
	PIDTypeDef Z_posPID;
	PIDTypeDef Z_ratePID;
	PIDTypeDef locxPID;
	PIDTypeDef locyPID;
	PIDTypeDef locxsPID;
	PIDTypeDef locysPID;
	PIDTypeDef stree_yaw_speed;
	PIDTypeDef stree_pitch_speed;
} CtrlerTypeDef;

/* CMSIS intrinsics used by MRAC_SetPrior under MRAC_ENABLE_SIGMA_PRIOR */
static inline uint32_t __get_PRIMASK(void) { return 0; }
static inline void __disable_irq(void) {}
static inline void __set_PRIMASK(uint32_t pri) { (void)pri; }

#endif
