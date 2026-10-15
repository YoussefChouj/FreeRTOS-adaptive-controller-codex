#ifndef PID_FALLBACK_H
#define PID_FALLBACK_H

/* PID fallback state machine (2026-10-15, lab-friction C).
 * Header-only so firmware (TASK/StabilizerTask.c) and host unit tests share exact logic. */

#include <stdint.h>

#define FALLBACK_ON_MIN          1400U    /* above mid: unassigned SBUS ch idles at ~1000-1024 */
#define FALLBACK_DEBOUNCE_TICKS  3U       /* 15 ms at 200 Hz */

typedef struct {
	uint8_t latched;        /* 1 while latched in flight, cleared on disarm */
	uint8_t high_ticks;     /* debounce tick counter */
} PidFallbackState;

/* Step the fallback state machine for one tick.
 *
 * Parameters:
 *   state:         pointer to persistent PidFallbackState
 *   armed:         1 if armed, 0 if disarmed (DroneStatus.ARM_Status == Armed)
 *   direct_cmd:    direct trigger flag (e.g. g_ctrl_pid_fallback != 0)
 *   ch_cfg:        configured RC aux channel (0 = disabled, 1..16 = sbus_channel[ch-1])
 *   sbus_lost:     1 if RC link lost, 0 if valid
 *   sbus_channels: pointer to array of SBUS channel values (sbus_channel)
 *
 * Returns:
 *   1 on rising transition edge (transition from unlatched to latched: trigger xy_ctrl_reset)
 *   0 otherwise.
 */
static inline uint8_t PidFallback_Step(PidFallbackState *state,
                                       uint8_t armed,
                                       uint8_t direct_cmd,
                                       uint8_t ch_cfg,
                                       uint8_t sbus_lost,
                                       const uint16_t *sbus_channels)
{
	uint8_t trigger = 0U;
	uint8_t edge = 0U;

	if (!armed) {
		state->latched = 0U;
		state->high_ticks = 0U;
		return 0U;
	}

	if (direct_cmd != 0U) {
		trigger = 1U;
	}

	if (ch_cfg >= 1U && ch_cfg <= 16U && sbus_channels != 0) {
		if (!sbus_lost && (sbus_channels[ch_cfg - 1U] > FALLBACK_ON_MIN)) {
			if (state->high_ticks < FALLBACK_DEBOUNCE_TICKS) {
				state->high_ticks++;
			}
		} else {
			state->high_ticks = 0U;
		}
		if (state->high_ticks >= FALLBACK_DEBOUNCE_TICKS) {
			trigger = 1U;
		}
	} else {
		state->high_ticks = 0U;
	}

	if (trigger && !state->latched) {
		state->latched = 1U;
		edge = 1U;
	}

	return edge;
}

#endif /* PID_FALLBACK_H */
