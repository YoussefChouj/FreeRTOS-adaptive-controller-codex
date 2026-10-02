#ifndef __PID_H
#define __PID_H	 

#include "robot_types.h"
#include "global_declare.h"
#include "GlobalUse_Basic_Function.h"
#include "SINS.h"

void ComputePID(PIDTypeDef *pPID);
void ComputeYawPID(PIDTypeDef *pPID);
void Clear_Structure(void);

void ComputePID_locx(PIDTypeDef *pPID);
void ComputePID_locy(PIDTypeDef *pPID);

extern CtrlerTypeDef Ctrler;

void ComputePID_Gated(PIDTypeDef *pPID, uint8_t integrate);
float AttTrim_Apply(float des_deg, float trim_deg, float lim_deg, uint8_t flying);

typedef struct {
    float prev_x, prev_y, vf_x, vf_y;
    uint8_t primed;
} TrajFF_t;

void TrajFF_Reset(TrajFF_t *s);
void TrajFF_Step(TrajFF_t *s, uint8_t active, float tx_cm, float ty_cm, float dt_s, float tau_s, float vmax_cms,
                 float *vff_x, float *vff_y, float *aff_x, float *aff_y);

#endif
