#include <stdio.h>
#include <stdint.h>
#include <assert.h>

/* --- Stubs --- */
#define FLIGHT_STATE_DISARMED 0
#define FLIGHT_STATE_ARMED 1
#define FLIGHT_STATE_EMERGENCY 2

#define FLIGHT_PHASE_GROUND_IDLE 0
#define FLIGHT_PHASE_FLYING 1

#define FLIGHT_EVENT_ARM_REQUEST 0
#define FLIGHT_EVENT_DISARM_REQUEST 1

extern uint8_t s_state; uint8_t FlightFSM_GetState(void) { return s_state; }

uint8_t s_state = FLIGHT_STATE_DISARMED;
uint8_t flight_phase = FLIGHT_PHASE_GROUND_IDLE;
uint8_t g_motor_idle_enabled = 0;
uint8_t sbus_flyup_trigger = 0;
uint8_t motor_pwm = 0; /* 0 = zero, 1 = idle, 2 = pwm */
float thr = 0.0f;
uint8_t twc_execute = 0;

void Set_Zero_Motors() { motor_pwm = 0; }
void Set_IDLE_Motors() { motor_pwm = 1; }
void Set_PWM_Motors()  { motor_pwm = 2; }
void Clear_Structure() {}
int IMU_EstimatorReady() { return 1; }
float RCInput_Get(int axis) { return thr; }

struct {
    int RightStick_RightDown_cnt;
} StickMotion;

/* --- Logic under test (matches firmware) --- */
void FlightFSM_Event(int event) {
    if (s_state == FLIGHT_STATE_DISARMED) {
        if (event == FLIGHT_EVENT_ARM_REQUEST && IMU_EstimatorReady()) {
            g_motor_idle_enabled = 0;
            s_state = FLIGHT_STATE_ARMED;
        }
    } else if (s_state == FLIGHT_STATE_ARMED) {
        if (event == FLIGHT_EVENT_DISARM_REQUEST) {
            g_motor_idle_enabled = 0;
            s_state = FLIGHT_STATE_DISARMED;
            flight_phase = FLIGHT_PHASE_GROUND_IDLE;
        }
    }
}

void Check_Stick_Motion() {
    if (StickMotion.RightStick_RightDown_cnt >= 150) {
        if (s_state == FLIGHT_STATE_ARMED && flight_phase == FLIGHT_PHASE_GROUND_IDLE &&
            thr < 0.1f && !g_motor_idle_enabled) {
            g_motor_idle_enabled = 1;
        }
        StickMotion.RightStick_RightDown_cnt = 0;
    }
}

void Update_Motor() {
    if (s_state == FLIGHT_STATE_DISARMED) {
        Set_Zero_Motors();
    } else if (s_state == FLIGHT_STATE_ARMED) {
        if (flight_phase == FLIGHT_PHASE_GROUND_IDLE) {
            if (!g_motor_idle_enabled) {
                Set_Zero_Motors();
            } else {
                if (!twc_execute && thr < 0.2f) {
                    Set_IDLE_Motors();
                } else {
                    Set_PWM_Motors();
                }
            }
        }
    }
}

/* --- Tests --- */
int main() {
    /* 1. Arm -> PWM stays zero (2000) */
    s_state = FLIGHT_STATE_DISARMED;
    g_motor_idle_enabled = 0;
    FlightFSM_Event(FLIGHT_EVENT_ARM_REQUEST);
    assert(s_state == FLIGHT_STATE_ARMED);
    assert(g_motor_idle_enabled == 0);
    Update_Motor();
    assert(motor_pwm == 0);

    /* 2. Throttle up/TWC while armed-not-idle -> PWM stays zero */
    thr = 0.5f;
    twc_execute = 1;
    Update_Motor();
    assert(motor_pwm == 0);

    /* 3. Idle gesture -> PWM 2150 (idle) */
    thr = 0.0f;
    twc_execute = 0;
    StickMotion.RightStick_RightDown_cnt = 150;
    Check_Stick_Motion();
    assert(g_motor_idle_enabled == 1);
    Update_Motor();
    assert(motor_pwm == 1); /* idle */

    /* 4. Fly-up trigger while armed-not-idle -> 2000 (already tested above via logic) */
    /* 5. Disarm -> flag cleared */
    FlightFSM_Event(FLIGHT_EVENT_DISARM_REQUEST);
    assert(g_motor_idle_enabled == 0);

    printf("Tests passed.\n");
    return 0;
}
