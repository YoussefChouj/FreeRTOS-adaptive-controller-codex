/**
 * @module     land_xy_gate.c
 * @subsystem  guidance
 * @purpose    camera pad gate: the land's descent starts only once the fused xy is within
 *             VAR 46 of the pad on a fresh camera fix (align, then descend fast).
 */
// ------------------------------------------------------------------------------
// Camera pad gate: see land_xy_gate.h
// ------------------------------------------------------------------------------

#include "land_xy_gate.h"

uint8_t LandXyGate_Check(const LandXyGateIn *in)
{
	if (!(in->gate_cm > 0.0f) || !in->fused)
		return LAND_XY_GATE_OFF;
	if (in->stale || in->fix_age_ms >= LAND_XY_GATE_FRESH_MS || !(in->err_cm <= in->gate_cm))
		return LAND_XY_GATE_WAIT;   /* NaN error waits too */
	return LAND_XY_GATE_OK;
}
