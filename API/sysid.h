// ------------------------------------------------------------------------------
// Automated system-identification excitation module (Phase 2 — see docs/adr/0004)
// ------------------------------------------------------------------------------
// Generates a chirp/multisine test signal added to ONE axis's RATE setpoint
// (gyro*.Des), with the outer angle loop bypassed on that axis. Wraps it in a
// safety FSM (precheck gates, green-zone geofence, RC dead-man, ramp in/out,
// auto-recovery). Ticked at the 200 Hz control rate from StabilizerTask.
//
// Injection is closed-loop and telemetry-only-affecting except for the single
// excited axis; all other axes stay normally stabilized. Default state = IDLE.
// ------------------------------------------------------------------------------

#ifndef SYSID_H
#define SYSID_H

#include <stdint.h>

#define SYSID_DT   0.005f   // control period (200 Hz) — must match StabilizerTask

typedef enum {
    SYSID_AXIS_PITCH = 0,
    SYSID_AXIS_ROLL  = 1,
    SYSID_AXIS_YAW   = 2,
    SYSID_AXIS_Z     = 3
} SysID_Axis_e;

/* World-frame axis aliases for outer-loop velocity (target 3) and position (target 4) runs. */
#define SYSID_AXIS_X      SYSID_AXIS_PITCH
#define SYSID_AXIS_Y      SYSID_AXIS_ROLL

/* Excitation target selector (VAR_WRITE 55, sysid_target). */
typedef enum {
    SYSID_TARGET_RATE   = 0,
    SYSID_TARGET_TORQUE = 1,
    SYSID_TARGET_ANGLE  = 2,
    SYSID_TARGET_VEL    = 3,
    SYSID_TARGET_POS    = 4
} SysID_Target_e;

/* Minimum altitude (m) required to start and maintain Z-axis outer-loop SysID (targets 3, 4).
 * PROPOSED - not flight-validated. */
#define SYSID_Z_MIN_ALT_M 0.40f

typedef enum {
    SYSID_SIG_CHIRP     = 0,
    SYSID_SIG_MULTISINE = 1
} SysID_Signal_e;

typedef enum {
    SYSID_IDLE = 0,
    SYSID_RAMP_IN,
    SYSID_RUNNING,
    SYSID_RAMP_OUT,
    SYSID_RECOVERY
} SysID_State_e;

// Request a run. Returns 1 if preconditions pass and the run is armed, else 0 (rejected).
// f0,f1 in Hz; amp in deg/s (rate); duration in seconds.
uint8_t SysID_Start(SysID_Axis_e axis, SysID_Signal_e sig, float f0, float f1, float amp, float duration);

// Abort an active run immediately -> RECOVERY (excitation released, outer loop re-engages).
void SysID_Abort(void);
#define SysID_Stop SysID_Abort

// Tick the signal generator + safety FSM. Call once per control loop (200 Hz), before the
// rate-setpoint cascade. Reads plant state (attitude, rate, position) for geofence/abort checks.
void SysID_Update(void);

// 1 if SysID is currently driving this axis's rate setpoint (RAMP_IN/RUNNING/RAMP_OUT only).
uint8_t SysID_IsAxisActive(SysID_Axis_e axis);

// The rate setpoint (deg/s) to apply to the active axis when SysID_IsAxisActive() is true.
float SysID_GetRateSetpoint(SysID_Axis_e axis);

// Excitation target, VAR_WRITE 55 (u8 0..4), latched at SysID_Start; a write while the FSM is not IDLE is
// refused with VAR_WRITE_REJECT_BUSY. 0 = rate setpoint injection (above). 1 = torque: the excitation d is
// added to the rate-loop output c before the mixer (u = c + d, Mix_Compute) and SysID_IsAxisActive returns 0.
// 2 = angle setpoint (deg, pitch/roll). 3 = velocity setpoint (cm/s for X/Y, m/s for Z).
// 4 = position setpoint (cm for X/Y, m for Z).
extern volatile uint8_t sysid_target;

// d for a target-1 run (mixer units): the excitation on the run's axis while RAMP_IN/RUNNING/RAMP_OUT and
// flying != 0 (flight_phase FLYING), clamped to the SYSID_TORQUE_ROW amp_max; 0 otherwise (target 0, other
// axis, not flying, IDLE, RECOVERY after an abort, non-finite).
float SysID_GetTorque(SysID_Axis_e axis, uint8_t flying);

// Outer-loop setpoint injection hooks (targets 2, 3, 4). Return `des_*` + clamped excitation when the
// matching target and axis are active in RAMP_IN/RUNNING/RAMP_OUT while armed in SDK mode; return `des_*`
// unchanged otherwise (including immediately upon SysID_Stop/SysID_Abort, disarm, or mode change).
float SysID_GetAngleSetpoint(uint8_t axis, float des_deg);
float SysID_GetVelSetpoint(uint8_t axis, float des_vel);
float SysID_GetPosSetpoint(uint8_t axis, float des_pos);

// The current raw excitation/dither value (axis native unit; 0 when IDLE/RECOVERY). Logged in the
// 0x03 ID frame as the EXOGENOUS instrument for closed-loop identification: unlike the rate
// setpoint `r` (which also carries the outer-loop output), the dither is uncorrelated with plant/
// sensor noise, so the offline IV estimator G = Phi_xd/Phi_ud is unbiased at ALL frequencies —
// including the low band where the outer loop otherwise contaminates `r`. Scale is irrelevant
// (it cancels in the ratio).
float SysID_GetDither(void);

// Current FSM state (for telemetry / dashboard).
uint8_t SysID_GetState(void);

// The axis of the current/last run (SysID_Axis_e as u8). The 0x03 ID frame logs only this one
// excited axis (the other three are dead weight during a single-axis run) so the frame is small
// enough to stream at 200 Hz. Valid during a run; stale (last axis) while IDLE.
uint8_t SysID_GetAxis(void);

// Enable/disable the green-zone XY geofence (1=on default, 0=off). When OFF the soft-boundary
// abort AND the start precondition are skipped — intended ONLY for closed-loop runs under direct
// pilot watch (RC dead-man stays the final authority). All other aborts remain active.
void SysID_SetGeofence(uint8_t enabled);

#endif // SYSID_H
