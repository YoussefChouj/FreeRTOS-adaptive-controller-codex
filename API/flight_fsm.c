/**
 * @module     flight_fsm.c
 * @subsystem  safety
 * @owner      no task of its own: FlightFSM_Event is called from Remoter_Task (RC arm/disarm/stop/recover),
 *             Send_Task (GS commands), Stabilizer_Task (crash stop, landed disarm) and Autofly_Task (stop);
 *             FlightFSM_Init once at boot. Every transition runs inside a critical section.
 * @purpose    The arm state machine. Transitions (any other event is ignored):
 *               DISARMED  --ARM_REQUEST (estimator ready + pre-arm pass)--> ARMED
 *               DISARMED  --DANGEROUS_STOP-->                               EMERGENCY
 *               ARMED     --DISARM_REQUEST | DANGEROUS_STOP-->              DISARMED | EMERGENCY, phase GROUND_IDLE
 *               EMERGENCY --RECOVER_SDK | DISARM_REQUEST-->                 DISARMED, phase GROUND_IDLE
 *             Every transition clears g_motor_idle_enabled.
 * @inputs     FlightEvent_t, IMU_EstimatorReady() (API/imu_update.c), PreArm_Allows() (API/prearm.c).
 * @outputs    s_state (FlightFSM_GetState), DroneStatus.ARM_Status/FlyMode, flight_phase, g_motor_idle_enabled.
 */
#include "flight_fsm.h"
#include "FreeRTOS.h"
#include "task.h"
#include "global_declare.h"
#include "imu_update.h"   /* IMU_EstimatorReady() pre-arm gate */
#include "prearm.h"       /* PreArm_Refresh()/PreArm_Allows(): enabled pre-arm checks, docs/firmware-safety.md */

static FlightState_t s_state = FLIGHT_STATE_DISARMED;
volatile FlightPhase_t flight_phase = FLIGHT_PHASE_GROUND_IDLE;
volatile uint8_t g_motor_idle_enabled = 0U;

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

/* One transition: idle gate off, new state, DroneStatus follows. */
static void s_enter(FlightState_t to)
{
    g_motor_idle_enabled = 0U;
    s_state = to;
    s_sync(s_state);
}

/* A transition out of ARMED or EMERGENCY also returns the sub-phase to GROUND_IDLE. */
static void s_leave_flight(FlightState_t to)
{
    s_enter(to);
    flight_phase = FLIGHT_PHASE_GROUND_IDLE;
}

void FlightFSM_Init(void)
{
    taskENTER_CRITICAL();
    s_state = FLIGHT_STATE_DISARMED;
    g_motor_idle_enabled = 0U;
    s_sync(s_state);
    taskEXIT_CRITICAL();
}

void FlightFSM_Event(FlightEvent_t event)
{
    if (event == FLIGHT_EVENT_ARM_REQUEST) {
        PreArm_Refresh();   /* fresh inputs for every arm attempt, RC and ground station alike */
    }
    taskENTER_CRITICAL();
    switch (s_state) {
    case FLIGHT_STATE_DISARMED:
        /* Pre-arm gate: refuse to arm until the attitude estimator has converged
         * (A2). IMU_EstimatorReady() has a hard timeout fallback so this can
         * never lock the pilot out. DANGEROUS_STOP is never gated. */
        if (event == FLIGHT_EVENT_ARM_REQUEST && IMU_EstimatorReady() && PreArm_Allows()) { s_enter(FLIGHT_STATE_ARMED); }
        if (event == FLIGHT_EVENT_DANGEROUS_STOP)                                          { s_enter(FLIGHT_STATE_EMERGENCY); }
        break;
    case FLIGHT_STATE_ARMED:
        if (event == FLIGHT_EVENT_DISARM_REQUEST) { s_leave_flight(FLIGHT_STATE_DISARMED); }
        if (event == FLIGHT_EVENT_DANGEROUS_STOP) { s_leave_flight(FLIGHT_STATE_EMERGENCY); }
        break;
    case FLIGHT_STATE_EMERGENCY:
        if (event == FLIGHT_EVENT_RECOVER_SDK)    { s_leave_flight(FLIGHT_STATE_DISARMED); }
        if (event == FLIGHT_EVENT_DISARM_REQUEST) { s_leave_flight(FLIGHT_STATE_DISARMED); }
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
