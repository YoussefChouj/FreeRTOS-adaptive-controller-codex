#include "global_declare.h"

/*------------------------------------------------------
�����ܣ����ȫ�ֱ���
--------------------------------------------------------*/


SYSTEM_MONITOR system_monitor  =  {0}; //ϵͳ������

RemoterTypeDef  Remoter;               //ң����

DroneStatusTypeDef DroneStatus;        //���˻�״̬

StickMotionTypeDef StickMotion;        //ң��������

volatile uint8_t sbus_lost = 0;
volatile uint32_t sbus_last_valid_tick = 0;
volatile uint8_t bench_mode_active =
#if RC_BENCH_DEFAULT_ENABLE
    1U
#else
    0U
#endif
;

/* Motor bench-test mode (CMD 0x16) — see global_declare.h. */
volatile uint8_t  motor_test_active   = 0U;
volatile uint8_t  motor_test_id       = 0U;
volatile uint16_t motor_test_ccr      = 2000U;
volatile uint32_t motor_test_watchdog = 0U;

float gs_max_horizontal_speed_mps = 1.0f;
float gs_max_vertical_speed_mps = 1.0f;
float gs_max_pitch_deg = 15.0f;
float gs_max_roll_deg = 15.0f;
float gs_throttle_min_pct = 0.0f;
float gs_throttle_max_pct = 1.0f;

volatile uint8_t TWC_arrived = 0;
volatile uint8_t sbus_flyup_trigger = 0;

volatile uint8_t sbus_path_trigger = 0;

volatile SinusoidPath_t sinusoid_path = {0};

volatile CirclePath_t circle_path = {0};

volatile Figure8Path_t figure8_path = {0};

volatile float waypoint_spacing = 5.0f;  /* default 5 cm waypoint density (loc-PID units = cm); 0 = continuous */

volatile uint8_t GS_KeySDKflag = 0;


