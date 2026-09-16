#ifndef __FLIGHT_FSM_H__
#define __FLIGHT_FSM_H__

#include "stm32f4xx.h"

typedef enum {
    FLIGHT_STATE_DISARMED  = 0,
    FLIGHT_STATE_ARMED     = 1,
    FLIGHT_STATE_EMERGENCY = 2
} FlightState_t;

/* Sub-phase within FLIGHT_STATE_ARMED.
 * GROUND_IDLE : motors spinning at idle RPM, drone on ground before takeoff.
 * FLYING      : drone is airborne, full PID active.
 * LANDING     : ch5 LAND triggered, throttle ramp committing to touchdown.
 * LANDED      : altitude stable, auto-disarm fired. */
typedef enum {
    FLIGHT_PHASE_GROUND_IDLE = 0,
    FLIGHT_PHASE_FLYING      = 1,
    FLIGHT_PHASE_LANDING     = 2,
    FLIGHT_PHASE_LANDED      = 3
} FlightPhase_t;

extern volatile FlightPhase_t flight_phase;

typedef enum {
    FLIGHT_EVENT_ARM_REQUEST    = 0,
    FLIGHT_EVENT_DISARM_REQUEST = 1,
    FLIGHT_EVENT_DANGEROUS_STOP = 2,
    FLIGHT_EVENT_RECOVER_SDK    = 3
} FlightEvent_t;

void          FlightFSM_Init(void);
void          FlightFSM_Event(FlightEvent_t event);
FlightState_t FlightFSM_GetState(void);

#endif /* __FLIGHT_FSM_H__ */
