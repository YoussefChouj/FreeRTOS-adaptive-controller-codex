/* Host unit test for API/pid_fallback.h (Section C, lab-friction-2026-10-15).
 * Build and run:
 *   gcc -std=c99 -Wall -Werror -I../../API test_pid_fallback.c -o t && ./t
 */
#include <stdio.h>
#include <assert.h>
#include <stdint.h>
#include "pid_fallback.h"

/* Mock controller and outer-loop kinds matching firmware */
#define XY_CTRL_PID  0U
#define XY_CTRL_LQR  2U
#define CTRL_PID     0U
#define CTRL_MRAC    1U

static void test_disarmed_ignores_direct_and_rc(void)
{
	PidFallbackState state = {0U, 0U};
	uint16_t sbus[16];
	uint8_t edge;
	int i;

	for (i = 0; i < 16; i++) sbus[i] = 1800U;

	/* Direct command while disarmed */
	edge = PidFallback_Step(&state, 0U, 1U, 0U, 0U, sbus);
	assert(edge == 0U);
	assert(state.latched == 0U);
	assert(state.high_ticks == 0U);

	/* RC aux switch while disarmed for multiple ticks */
	for (i = 0; i < 10; i++) {
		edge = PidFallback_Step(&state, 0U, 0U, 9U, 0U, sbus);
		assert(edge == 0U);
		assert(state.latched == 0U);
		assert(state.high_ticks == 0U);
	}
}

static void test_direct_cmd_in_flight_latches(void)
{
	PidFallbackState state = {0U, 0U};
	uint16_t sbus[16];
	uint8_t edge;
	int i;

	for (i = 0; i < 16; i++) sbus[i] = 1000U;

	/* In flight (armed = 1), direct command pulse */
	edge = PidFallback_Step(&state, 1U, 1U, 0U, 0U, sbus);
	assert(edge == 1U);
	assert(state.latched == 1U);

	/* Direct command cleared on next tick: must remain latched, no new edge */
	edge = PidFallback_Step(&state, 1U, 0U, 0U, 0U, sbus);
	assert(edge == 0U);
	assert(state.latched == 1U);

	/* Remains latched for 100 ticks */
	for (i = 0; i < 100; i++) {
		edge = PidFallback_Step(&state, 1U, 0U, 0U, 0U, sbus);
		assert(edge == 0U);
		assert(state.latched == 1U);
	}
}

static void test_disarm_unlatches(void)
{
	PidFallbackState state = {0U, 0U};
	uint16_t sbus[16];
	uint8_t edge;
	int i;

	for (i = 0; i < 16; i++) sbus[i] = 1000U;

	/* Latch in flight */
	edge = PidFallback_Step(&state, 1U, 1U, 0U, 0U, sbus);
	assert(edge == 1U);
	assert(state.latched == 1U);

	/* Disarm: unlatches immediately */
	edge = PidFallback_Step(&state, 0U, 0U, 0U, 0U, sbus);
	assert(edge == 0U);
	assert(state.latched == 0U);
	assert(state.high_ticks == 0U);
}

static void test_rc_aux_switch_debounce_and_latches(void)
{
	PidFallbackState state = {0U, 0U};
	uint16_t sbus[16];
	uint8_t edge;
	int i;

	for (i = 0; i < 16; i++) sbus[i] = 1000U;
	sbus[8] = 1694U; /* Channel 9 active */

	/* Tick 1: high */
	edge = PidFallback_Step(&state, 1U, 0U, 9U, 0U, sbus);
	assert(edge == 0U);
	assert(state.latched == 0U);
	assert(state.high_ticks == 1U);

	/* Tick 2: high */
	edge = PidFallback_Step(&state, 1U, 0U, 9U, 0U, sbus);
	assert(edge == 0U);
	assert(state.latched == 0U);
	assert(state.high_ticks == 2U);

	/* Tick 3: debounce satisfied (3 ticks) -> edge trigger! */
	edge = PidFallback_Step(&state, 1U, 0U, 9U, 0U, sbus);
	assert(edge == 1U);
	assert(state.latched == 1U);

	/* Tick 4: switch moved back down to low (1000U) */
	sbus[8] = 1000U;
	edge = PidFallback_Step(&state, 1U, 0U, 9U, 0U, sbus);
	assert(edge == 0U);
	assert(state.latched == 1U); /* latched until disarm */

	/* Stays latched */
	for (i = 0; i < 50; i++) {
		edge = PidFallback_Step(&state, 1U, 0U, 9U, 0U, sbus);
		assert(edge == 0U);
		assert(state.latched == 1U);
	}
}

static void test_rc_aux_glitch_rejection(void)
{
	PidFallbackState state = {0U, 0U};
	uint16_t sbus[16];
	uint8_t edge;
	int i;

	for (i = 0; i < 16; i++) sbus[i] = 1000U;

	/* 2 ticks high */
	sbus[8] = 1694U;
	PidFallback_Step(&state, 1U, 0U, 9U, 0U, sbus);
	PidFallback_Step(&state, 1U, 0U, 9U, 0U, sbus);
	assert(state.high_ticks == 2U);
	assert(state.latched == 0U);

	/* 1 tick low glitch: resets debounce accumulator */
	sbus[8] = 1000U;
	edge = PidFallback_Step(&state, 1U, 0U, 9U, 0U, sbus);
	assert(edge == 0U);
	assert(state.high_ticks == 0U);
	assert(state.latched == 0U);

	/* 2 more ticks high: still not 3 in a row */
	sbus[8] = 1694U;
	PidFallback_Step(&state, 1U, 0U, 9U, 0U, sbus);
	edge = PidFallback_Step(&state, 1U, 0U, 9U, 0U, sbus);
	assert(edge == 0U);
	assert(state.high_ticks == 2U);
	assert(state.latched == 0U);
}

static void test_sbus_lost_inhibits_rc(void)
{
	PidFallbackState state = {0U, 0U};
	uint16_t sbus[16];
	uint8_t edge;
	int i;

	for (i = 0; i < 16; i++) sbus[i] = 1694U;

	/* sbus_lost = 1: RC channel trigger must be ignored */
	for (i = 0; i < 10; i++) {
		edge = PidFallback_Step(&state, 1U, 0U, 9U, 1U, sbus);
		assert(edge == 0U);
		assert(state.latched == 0U);
		assert(state.high_ticks == 0U);
	}
}

static void test_channel_0_and_out_of_range_disabled(void)
{
	PidFallbackState state = {0U, 0U};
	uint16_t sbus[16];
	uint8_t edge;
	int i;

	for (i = 0; i < 16; i++) sbus[i] = 1800U;

	/* ch_cfg = 0: default disabled */
	for (i = 0; i < 10; i++) {
		edge = PidFallback_Step(&state, 1U, 0U, 0U, 0U, sbus);
		assert(edge == 0U);
		assert(state.latched == 0U);
	}

	/* ch_cfg = 17: out of range */
	edge = PidFallback_Step(&state, 1U, 0U, 17U, 0U, sbus);
	assert(edge == 0U);
	assert(state.latched == 0U);
}

static void test_bumpless_handshake_simulation(void)
{
	PidFallbackState state = {0U, 0U};
	uint16_t sbus[16];
	uint8_t s_xyc_kind = XY_CTRL_LQR;
	uint32_t xy_reset_count = 0U;
	uint8_t fallback_active = 0U;
	int i;

	for (i = 0; i < 16; i++) sbus[i] = 1000U;

	/* Drone airborne and armed with LQR */
	assert(s_xyc_kind == XY_CTRL_LQR);
	assert(xy_reset_count == 0U);

	/* Direct fallback asserted in flight */
	{
		uint8_t edge = PidFallback_Step(&state, 1U, 1U, 0U, 0U, sbus);
		if (edge) {
			s_xyc_kind = XY_CTRL_PID;
			xy_reset_count++;
		}
		fallback_active = state.latched;
	}

	assert(fallback_active == 1U);
	assert(s_xyc_kind == XY_CTRL_PID);
	assert(xy_reset_count == 1U);

	/* Subsequent 20 ticks: PID remains active, xy_reset is NOT called repeatedly */
	for (i = 0; i < 20; i++) {
		uint8_t edge = PidFallback_Step(&state, 1U, 0U, 0U, 0U, sbus);
		if (edge) {
			s_xyc_kind = XY_CTRL_PID;
			xy_reset_count++;
		}
		fallback_active = state.latched;
		assert(fallback_active == 1U);
		assert(s_xyc_kind == XY_CTRL_PID);
		assert(xy_reset_count == 1U); /* bumpless: only reset once on edge */
	}

	/* Disarm: unlatches */
	{
		uint8_t edge = PidFallback_Step(&state, 0U, 0U, 0U, 0U, sbus);
		fallback_active = state.latched;
		assert(edge == 0U);
		assert(fallback_active == 0U);
	}
}

int main(void)
{
	test_disarmed_ignores_direct_and_rc();
	test_direct_cmd_in_flight_latches();
	test_disarm_unlatches();
	test_rc_aux_switch_debounce_and_latches();
	test_rc_aux_glitch_rejection();
	test_sbus_lost_inhibits_rc();
	test_channel_0_and_out_of_range_disabled();
	test_bumpless_handshake_simulation();

	printf("test_pid_fallback: all passed\n");
	return 0;
}
