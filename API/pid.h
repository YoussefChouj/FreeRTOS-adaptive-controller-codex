#ifndef __PID_H
#define __PID_H	 

#include "robot_types.h"
#include "global_declare.h"
#include "GlobalUse_Basic_Function.h"
#include "SINS.h"

void ComputePID(PIDTypeDef *pPID);
void ComputeYawPID(PIDTypeDef *pPID);
void Clear_Structure(void);

extern CtrlerTypeDef Ctrler;

void ComputePID_Gated(PIDTypeDef *pPID, uint8_t integrate);
void ComputePID_GatedHold(PIDTypeDef *pPID, uint8_t integrate, uint8_t hold);
float AttTrim_Apply(float des_deg, float trim_deg, float lim_deg, uint8_t flying);

/* Gain lease (WP-28, default OFF, API/pid.c GAIN_LEASE_ROW): Renew before each CMD 0x01 write, Tick every loop. */
void PID_GainLeaseRenew(uint32_t now_ms, uint8_t airborne);
void PID_GainLeaseTick(uint32_t now_ms, uint8_t airborne);

/* CMD 0x01 targets (API/pid.c PID_CMD_ROW): axis 0..6 in the command's order, gain 0 Kp 1 Ki 2 Kd. */
#define PID_CMD_AXES 7U
extern const float pid_cmd_max[PID_CMD_AXES][3];
PIDTypeDef *PID_CmdLoop(uint8_t axis);
uint8_t PID_CmdGainOk(uint8_t axis, uint8_t gain, float val);

typedef struct {
    float prev_x, prev_y, vf_x, vf_y;
    uint8_t primed;
} TrajFF_t;

void TrajFF_Reset(TrajFF_t *s);
void TrajFF_Step(TrajFF_t *s, uint8_t active, float tx_cm, float ty_cm, float dt_s, float tau_s, float vmax_cms,
                 float *vff_x, float *vff_y, float *aff_x, float *aff_y);

#endif
