/* Landing descent profile (2026-10-15, lab-friction D2). Header only so the host test can compile it.
 *
 * Sink rate by height (Z_posPID.FB, m):
 *   above LAND_FAST_ALT              LAND_VZ_MPS       (F8 2026-10-06)
 *   LAND_SLOW_ALT .. LAND_FAST_ALT   LAND_VZ_FAST_MPS  (F8)
 *   below LAND_SLOW_ALT              LAND_VZ_SLOW_MPS  NEW: touchdown vz on 2026-10-15 was -0.5 (tuned)
 *                                    to -2.2 m/s (lqr), MEASURED. 0.25 is PROPOSED, not flown. The 10-02
 *                                    0.15 m/s stage floated in ground effect; the sink bias (Des_VHeight)
 *                                    still pushes through a float once Des reaches the floor.
 * XY near the ground (2026-10-19, PROPOSED, not flown):
 *   LAND_XY_FREEZE_ALT .. LAND_XY_HOLD_ALT   hold band: the xy loops stay closed (P + D), their integrators are
 *                                    held (no accumulation, no reset, so the trim they learned in hover stays),
 *                                    xy_ctrl is off, and the velocity-loop output is clamped to LAND_XY_HOLD_UMAX.
 *   below LAND_XY_FREEZE_ALT         touchdown: the WP-21 B contact path, xy lean zeroed, integrators cleared.
 * The 10-15 freeze started at 0.12 m: on 2026-10-15 flight 09 the flow-only EKF reported a false 0.15-0.19 m/s
 * once z < 0.11 m and the controller chased it. On 2026-10-19 all eight landings ended 17-41 cm off the pad
 * (camera), drifting +x/-y; the last 12 cm, with the xy loops off and their integrator trim cleared, moved 4-26 cm
 * (EKF) of it, and the rest built up higher (docs/lab/landing-offset-analysis-2026-10-19.md). The clamp bounds a
 * false-velocity chase in the hold band to about 8.7 deg of lean instead of 31. */
#ifndef LAND_PROFILE_H
#define LAND_PROFILE_H

#include <stdint.h>

#define LAND_VZ_MPS          0.65f    /* F8 2026-10-06: was 0.40 (F7 1.0->0.5 m took 1.34 s vs M8 0.69 s) */
#define LAND_FAST_ALT        0.40f
#define LAND_VZ_FAST_MPS     0.50f    /* F8: was 0.70; touchdown speed, impact energy goes with speed^2 */
#define LAND_SLOW_ALT        0.15f    /* PROPOSED 2026-10-15 */
#define LAND_VZ_SLOW_MPS     0.25f    /* PROPOSED 2026-10-15 */
#define LAND_XY_FREEZE_ALT   0.04f    /* PROPOSED 2026-10-19: touchdown; was 0.12 (2026-10-15) */
#define LAND_XY_HOLD_ALT     0.12f    /* PROPOSED 2026-10-19 */
#define LAND_XY_HOLD_UMAX    150.0f   /* PROPOSED 2026-10-19: cm/s^2, about 8.7 deg of lean (locxsPID UMax 600) */

/* Sink rate (m/s, positive down) at height z. */
static inline float LandProfile_SinkMps(float z)
{
	if (z < LAND_SLOW_ALT) return LAND_VZ_SLOW_MPS;
	if (z < LAND_FAST_ALT) return LAND_VZ_FAST_MPS;
	return LAND_VZ_MPS;
}

/* 1 while landing below LAND_XY_FREEZE_ALT: no xy lean, xy integrators cleared. */
static inline uint8_t LandProfile_XyFreeze(uint8_t landing, float z)
{
	return (uint8_t)(landing && z < LAND_XY_FREEZE_ALT);
}

/* 1 while landing below LAND_XY_HOLD_ALT: xy integrators held, xy lean clamped to LAND_XY_HOLD_UMAX.
 * Below LAND_XY_FREEZE_ALT the freeze takes over. */
static inline uint8_t LandProfile_XyHold(uint8_t landing, float z)
{
	return (uint8_t)(landing && z < LAND_XY_HOLD_ALT);
}

#endif
