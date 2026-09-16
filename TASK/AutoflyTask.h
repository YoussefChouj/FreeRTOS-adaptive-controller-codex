#ifndef   __AUTOFLY_TASK_H_
#define   __AUTOFLY_TASK_H_
#include "global_declare.h"
#include "robot_types.h"
#include "RemoterTask.h"
#include "stm32f4xx_it.h"
#include "send_data.h"

#define  task_vmax 50.0f
#define  task_yawmax 30.0f

extern unsigned char KeySDKflag,SDK_StateChangeFlag,SDK_DelayWakeFlag,SDKLandFlag;

extern float x_test ;
extern float y_test ;
extern float temp_V_x ;
extern float temp_V_y ;
extern int take_off_flag;

void AutoflyTask(void);
void AutoflyTask_RunSinusoid(void);
void AutoflyTask_RunCircle(void);
void AutoflyTask_RunFigure8(void);

void SDK_StateMachine_Reset(void);
void SDK_StateMachine_Loop(void);
void SDK_StateMachine_Init(void);

void SDK_Set_V_Loc(void);
void SDK_Set_H_Loc(void);
void SDK_Set_Gyroz(void);
void SDK_Set_Pos_Loc(void);
void SDK_Set_Yaw(void);

#endif
