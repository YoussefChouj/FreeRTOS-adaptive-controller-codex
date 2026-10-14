/**
 * @module     land_xy_gate.c
 * @subsystem  guidance
 * @purpose    camera-gated landing descent: pause the LANDING sink while the fused xy
 *             error or a stale camera says the drone is not over the pad (VAR 46).
 */
// ------------------------------------------------------------------------------
// Camera-gated landing descent: see land_xy_gate.h
// ------------------------------------------------------------------------------

#include "land_xy_gate.h"

void LandXyGate_Reset(LandXyGateState *s)
{
	s->hold = 0U;
	s->held = 0U;
}

uint8_t LandXyGate_Step(LandXyGateState *s, const LandXyGateIn *in)
{
	uint8_t fresh;
	if (in->gate_cm <= 0.0f || !in->fused || in->z_m < LAND_XY_GATE_FLOOR_M || s->held >= LAND_XY_GATE_MAX_TICKS) {
		s->hold = 0U;
		return 0U;
	}
	fresh = (uint8_t)(!in->stale && in->fix_age_ms < LAND_XY_GATE_FRESH_MS);
	if (!fresh || in->err_cm > in->gate_cm)
		s->hold = 1U;
	else if (in->err_cm < in->gate_cm * LAND_XY_GATE_HYST)
		s->hold = 0U;
	if (s->hold)
		s->held++;
	return s->hold;
}
