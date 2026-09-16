#ifndef __RC_INPUT_H__
#define __RC_INPUT_H__

#include "stm32f4xx.h"

/*
 * RCInput — single seam for all stick input.
 *
 * Hides the authority / physical-RC decision from every caller.
 * All axes return a normalised float:
 *   RC_AXIS_THR   : [-1.0, +1.0]  -1 = full down,  0 = center/hold,  +1 = full up
 *   RC_AXIS_PITCH : [-1.0, +1.0]  -1 = full nose-up, +1 = full nose-down (matches Remoter sign)
 *   RC_AXIS_ROLL  : [-1.0, +1.0]
 *   RC_AXIS_YAW   : [-1.0, +1.0]
 *
 * Authority flag (set by RCInput_SetAuthority, driven by CMD 0x0E from GS):
 *   authority = 0  (pilot mode)   — physical RC sticks, sbus_lost not required
 *   authority = 1  (PC offboard)  — virtual sticks from CMD 0x06; physical RC sticks ignored
 *
 * The physical RC mode switch (ch10 via Check_Fly_Mode) remains the hard kill switch
 * regardless of authority: ch10 LOW → DANGEROUS_STOP stops motors unconditionally.
 *
 * Heartbeat: if authority=1 and no SetVirtualStick call arrives within
 * RC_HEARTBEAT_TIMEOUT_MS, the module relinquishes authority and resets all
 * sticks to center, causing the drone to transition to altitude/position hold.
 */

typedef enum {
    RC_AXIS_THR   = 0,
    RC_AXIS_PITCH = 1,
    RC_AXIS_ROLL  = 2,
    RC_AXIS_YAW   = 3
} RC_Axis_t;

/* Throttle threshold below which Update_Motor considers motors idle.
 * Equivalent to the former raw check: eff_rc_thr() < 2150  →  (2150-3000)/1000 */
#define RC_IDLE_THR_THRESHOLD  (-0.85f)

/* Timeout for PC heartbeat when authority=1 */
#define RC_HEARTBEAT_TIMEOUT_MS  500U

/*
 * Get the normalised input for one axis. [-1.0, +1.0]
 */
float   RCInput_Get(RC_Axis_t axis);

/*
 * Returns 1 if the axis has an active (non-centre) command.
 *   authority=0: returns 1 only when the stick is significantly off-centre.
 *   authority=1: always returns 1 (PC is always commanding).
 */
int     RCInput_IsActive(RC_Axis_t axis);

/*
 * Called by send_data.c when CMD 0x06 arrives.
 * val must be normalised [-1.0, +1.0].
 * Also resets the PC heartbeat timer.
 */
void    RCInput_SetVirtualStick(RC_Axis_t axis, float val);

/*
 * Set or clear PC authority over all axes.
 * Clearing authority (has_authority=0) also resets sticks to centre.
 */
void    RCInput_SetAuthority(uint8_t has_authority);
uint8_t RCInput_GetAuthority(void);

/*
 * Call once per 10 ms from remoter_task().
 * Checks the PC heartbeat. On timeout, relinquishes authority and resets
 * sticks to centre so the drone enters altitude/position hold.
 */
void    RCInput_Update(void);

/*
 * Call once per 10 ms from remoter_task(), after the Remoter.*Ctrler fields and
 * sbus_lost have been refreshed. Auto-captures the physical stick neutral for
 * pitch/roll/yaw once at boot (transmitter centre can sit off the nominal 3000
 * raw units) so released sticks read true zero. Throttle is never offset.
 */
void    RCInput_UpdateNeutral(void);

/* Returns 1 once after a heartbeat timeout (clears on next call to RCInput_SetAuthority). */
int     RCInput_IsHeartbeatLost(void);

#endif /* __RC_INPUT_H__ */
