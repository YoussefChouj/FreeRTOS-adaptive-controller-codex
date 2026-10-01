#ifndef __GLOBAL_DECLARE_H
#define __GLOBAL_DECLARE_H
#include <stdint.h>
#include <stdbool.h>

typedef uint8_t u8;
typedef int32_t s32;
typedef float FP32;
typedef int16_t s16;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int8_t s8;

typedef uint8_t UCHAR8;
typedef int16_t SSHORT16;
typedef uint32_t UINT32;
typedef int _linux_flag;

#define VEC_XYZ 3
typedef struct { float preout; float out; float in; float off_freq; float samp_tim; } ST_LPF;

#define value_limit(val, min, max) do { if ((val) < (min)) (val) = (min); else if ((val) > (max)) (val) = (max); } while (0)
#define ABS(x) ((x) < 0 ? -(x) : (x))

#define AW_LEGACY 0
#define AW_CLAMP 1
#define AW_BACKCALC 2

typedef struct {
    float FB; float Des; float Kp; float Ki; float Kd;
    float Up; float Ui; float Ud; float E; float PreE;
    float SumE; float U; float UMax; float UpMax; float UiMax;
    float UdMax; float SumEMax; float EMin;
    int aw_mode; float Kt;
} PIDTypeDef;

typedef struct {
    PIDTypeDef pitchPID, rollPID, yawPID;
    PIDTypeDef gyroxPID, gyroyPID, gyrozPID;
    PIDTypeDef Z_posPID, Z_ratePID;
    PIDTypeDef locxPID, locyPID, locxsPID, locysPID;
    PIDTypeDef stree_yaw_speed, stree_pitch_speed;
} CtrlerTypeDef;

extern CtrlerTypeDef Ctrler;

typedef struct {
    short motor1;
    short motor2;
    short motor3;
    short motor4;
} ST_MOTOR;
extern ST_MOTOR mymotor;

typedef struct {
    uint8_t ARM_Status;
} DroneStatusTypeDef;
extern DroneStatusTypeDef DroneStatus;

typedef struct {
    float pit;
    float rol;
    float a_acc[3];
    float yaw;
    float x_vec[3], y_vec[3], z_vec[3];
} _imu_st;
extern _imu_st imu_data;

extern uint8_t sbus_flyup_trigger;
extern uint8_t sbus_path_trigger;
extern uint8_t TWC_arrived;
extern float sbus_channel[20];
extern uint8_t sbus_lost;

#define Armed 1
#define DisArmed 0
#define portTICK_PERIOD_MS 1
#define g_estimator_ready 1
#define g_imu_settle_metric 1
#define DEG2RAD 0.0174532925f
#define RAD2DEG 57.2957795f
#define GRAVITY_MSS 9.81f
#define Lin_Acc_X_body 0
#define Lin_Acc_Y_body 0
#define Lin_Acc_Z_body 0
#define Gravity_Body_X 0
#define Gravity_Body_Y 0
#define Gravity_Body_Z 0

extern int motor_test_watchdog;
extern int bench_mode_active;
extern int motor_test_active;

#define motor_test_id 0
#define motor_test_ccr 0
#define Motor_PWM_ZERO 1000
#define gs_throttle_min_pct 0
#define gs_throttle_max_pct 1
#define gs_max_vertical_speed_mps 1
#define gs_max_horizontal_speed_mps 1
#define gs_max_pitch_deg 1
#define gs_max_roll_deg 1
#define Stick_to_MAX_GyroZ 1

/* Missing macros/funcs */
static inline uint32_t __get_PRIMASK(void) { return 0; }
static inline void __disable_irq(void) {}
static inline void __set_PRIMASK(uint32_t pri) { (void)pri; }
void Set_PWM_Motors(void);
void Set_IDLE_Motors(void);
int RPM_Get(int i);
void ThrustEst_Update(const float pwm[4], const uint16_t rpm[4], float acc_z, float pit, float rol);
#endif
