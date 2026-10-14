// ------------------------------------------------------------------------------
// Camera pad gate for the ground-station land (2026-10-14, PROPOSED, not flown)
// ------------------------------------------------------------------------------
// PX4 PLD_HACC_RAD rule: align over the target first, then descend. The land's SETTLE (API/wfb_prim.c)
// does not start the descent until the camera-fused xy is within a radius of the pad (the origin) on a
// fresh camera fix; the descent itself stays the normal fast one, never paused. If the gate never opens,
// the SETTLE timeout still lands, with the target kept on the pad.
// Off unless gate_cm > 0 (VAR 46 g_land_xy_gate_cm) and fusion is mode 2 or 3.
// ------------------------------------------------------------------------------

#ifndef LAND_XY_GATE_H
#define LAND_XY_GATE_H

#include <stdint.h>

#define LAND_XY_GATE_FRESH_MS    500U    /* an accepted camera fix newer than this is fresh, PROPOSED */

#define LAND_XY_GATE_OFF   0U   /* VAR 46 = 0 or fusion 0/1: old behaviour */
#define LAND_XY_GATE_OK    1U   /* over the pad on a fresh fix: the descent may start */
#define LAND_XY_GATE_WAIT  2U   /* off the pad or no fresh fix: keep settling */

typedef struct {
	float    gate_cm;      /* radius; <= 0 = gate off */
	float    err_cm;       /* |fused xy - pad xy| */
	uint8_t  fused;        /* camera fusion mode 2 or 3 */
	uint8_t  stale;        /* g_ext_pos.stale */
	uint32_t fix_age_ms;   /* now - last accepted camera fix */
} LandXyGateIn;

/* Stateless: LAND_XY_GATE_OFF / _OK / _WAIT for this tick. */
uint8_t LandXyGate_Check(const LandXyGateIn *in);

#endif
