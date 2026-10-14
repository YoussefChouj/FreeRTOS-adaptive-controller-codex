// ------------------------------------------------------------------------------
// Camera-gated landing descent (2026-10-14, PROPOSED, not flown)
// ------------------------------------------------------------------------------
// The ArduPilot PLND_XY_DIST_MAX / PX4 PLD_HACC_RAD rule: during LANDING, pause the descent (hold
// altitude) while the camera-fused xy position is farther than a radius from the landing target, or
// while the camera fix is not fresh, so the xy hold pulls the drone back over the pad before it sinks.
// Below LAND_XY_GATE_FLOOR_M the descent commits (PX4 PLD_FAPPR_ALT idea), and the total pause per landing
// is capped at LAND_XY_GATE_MAX_TICKS so a lost camera or an unreachable target still lands.
// Off unless gate_cm > 0 (VAR 46 g_land_xy_gate_cm) and fusion is mode 2 or 3.
// ------------------------------------------------------------------------------

#ifndef LAND_XY_GATE_H
#define LAND_XY_GATE_H

#include <stdint.h>

#define LAND_XY_GATE_FLOOR_M     0.30f   /* below this altitude the descent no longer pauses, PROPOSED */
#define LAND_XY_GATE_FRESH_MS    500U    /* an accepted camera fix newer than this is fresh, PROPOSED */
#define LAND_XY_GATE_HYST        0.6f    /* a pause ends below gate_cm * this (no stop-go chatter), PROPOSED */
#define LAND_XY_GATE_MAX_TICKS   1600U   /* total pause per landing: 8 s at the 200 Hz Des_Height tick, PROPOSED */

typedef struct {
	uint8_t  hold;    /* 1 = descent paused this tick */
	uint16_t held;    /* ticks paused so far in this landing */
} LandXyGateState;

typedef struct {
	float    gate_cm;      /* pause radius; <= 0 = gate off */
	float    err_cm;       /* |fused xy - target xy| */
	float    z_m;          /* altitude */
	uint8_t  fused;        /* camera fusion mode 2 or 3 */
	uint8_t  stale;        /* g_ext_pos.stale */
	uint32_t fix_age_ms;   /* now - last accepted camera fix */
} LandXyGateIn;

void LandXyGate_Reset(LandXyGateState *s);

/* One LANDING tick: returns 1 while the descent should pause. */
uint8_t LandXyGate_Step(LandXyGateState *s, const LandXyGateIn *in);

#endif
