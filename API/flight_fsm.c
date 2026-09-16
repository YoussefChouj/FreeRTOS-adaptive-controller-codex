#include "flight_fsm.h"
#include "FreeRTOS.h"
#include "task.h"
#include "global_declare.h"
#include "imu_update.h"   /* IMU_EstimatorReady() pre-arm gate */

static FlightState_t s_state = FLIGHT_STATE_DISARMED;
volatile FlightPhase_t flight_phase = FLIGHT_PHASE_GROUND_IDLE;

static void s_sync(FlightState_t st)
{
    if (st == FLIGHT_STATE_ARMED) {
        DroneStatus.ARM_Status = Armed;
        DroneStatus.FlyMode    = FlyMode_SDK;
    } else if (st == FLIGHT_STATE_EMERGENCY) {
        DroneStatus.ARM_Status = DisArmed;
        DroneStatus.FlyMode    = FlyMode_DangerousStop;
    } else {
        DroneStatus.ARM_Status = DisArmed;
        DroneStatus.FlyMode    = FlyMode_SDK;
    }
}

void FlightFSM_Init(void)
{
    taskENTER_CRITICAL();
    s_state = FLIGHT_STATE_DISARMED;
    s_sync(s_state);
    taskEXIT_CRITICAL();
}

void FlightFSM_Event(FlightEvent_t event)
{
    taskENTER_CRITICAL();
    switch (s_state) {
    case FLIGHT_STATE_DISARMED:
        /* Pre-arm gate: refuse to arm until the attitude estimator has converged
         * (A2). IMU_EstimatorReady() has a hard timeout fallback so this can
         * never lock the pilot out. DANGEROUS_STOP is never gated. */
        if (event == FLIGHT_EVENT_ARM_REQUEST && IMU_EstimatorReady()) { s_state = FLIGHT_STATE_ARMED;     s_sync(s_state); }
        if (event == FLIGHT_EVENT_DANGEROUS_STOP)                      { s_state = FLIGHT_STATE_EMERGENCY; s_sync(s_state); }
        break;
    case FLIGHT_STATE_ARMED:
        if (event == FLIGHT_EVENT_DISARM_REQUEST) { s_state = FLIGHT_STATE_DISARMED;  s_sync(s_state); flight_phase = FLIGHT_PHASE_GROUND_IDLE; }
        if (event == FLIGHT_EVENT_DANGEROUS_STOP) { s_state = FLIGHT_STATE_EMERGENCY; s_sync(s_state); flight_phase = FLIGHT_PHASE_GROUND_IDLE; }
        break;
    case FLIGHT_STATE_EMERGENCY:
        if (event == FLIGHT_EVENT_RECOVER_SDK)    { s_state = FLIGHT_STATE_DISARMED;  s_sync(s_state); flight_phase = FLIGHT_PHASE_GROUND_IDLE; }
        if (event == FLIGHT_EVENT_DISARM_REQUEST) { s_state = FLIGHT_STATE_DISARMED;  s_sync(s_state); flight_phase = FLIGHT_PHASE_GROUND_IDLE; }
        break;
    default:
        break;
    }
    taskEXIT_CRITICAL();
}

FlightState_t FlightFSM_GetState(void)
{
    FlightState_t st;
    taskENTER_CRITICAL();
    st = s_state;
    taskEXIT_CRITICAL();
    return st;
}
