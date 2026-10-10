#ifndef LAND_GATE_H
#define LAND_GATE_H

#include <stdint.h>

/* Always-on auto-land gate, every flight (RC or ground station): a sagging pack while FLYING starts the same
 * landing as RC ch5. It fires once per flight and the reason stays in g_land_gate_trip until the next takeoff.
 * A motor pinned at the PWM cap is LOG-ONLY (2026-10-10 decision: one unloaded flight left 109 ms of margin, so it
 * would false-trip with loads, lim_mode 2 and the tilted figure-8): g_land_gate_sat_n counts pinned runs of at
 * least sat_hold_s and g_land_gate_sat_max_s keeps the longest run, both cleared at takeoff.
 * Why: vp15 arm300 2026-10-09 rested at 15.9 V, read 14.5 V a second after takeoff, pinned motor 2 at 4000 for
 * 410 ms at 14.0 V (t 20 s) and flipped at 14.05 V (t 43 s). The wfb_safety LOW_V check only runs in
 * workflow-B ground-station flights. */

typedef enum {
    LAND_GATE_NONE  = 0,
    LAND_GATE_LOW_V = 1,   /* pack under land_v for land_v_hold_s */
    LAND_GATE_SAT   = 2    /* unused since 2026-10-10: motor pinning is logged, never lands */
} land_gate_trip_t;

#define LAND_GATE_EN_LOW_V  0x01U
#define LAND_GATE_EN_SAT    0x02U   /* count pinned runs in g_land_gate_sat_n (log only) */

typedef struct {
    float land_v;          /* V */
    float land_v_hold_s;   /* s */
    float vbat_valid_v;    /* V, readings under this are a dead ADC, not a flat pack */
    float sat_ccr;         /* CCR, the mixer range is [2000, 4000] */
    float sat_hold_s;      /* s */
} land_gate_limits_t;

typedef struct {
    float   vbat_v;        /* real_voltage, refreshed at 1 Hz */
    float   motor_max;     /* largest of the four motor commands this tick */
    float   dt_s;
    uint8_t flying;        /* flight_phase == FLIGHT_PHASE_FLYING */
} land_gate_in_t;

typedef struct {
    float   low_v_t;
    float   sat_t;
    uint8_t was_flying;
    uint8_t fired;
    uint8_t sat_counted;   /* this pinned run is already in g_land_gate_sat_n */
} land_gate_state_t;

extern volatile uint8_t g_land_gate_enable;   /* LAND_GATE_EN_* bits, both on at boot; write 0 to turn the gate off */
extern volatile uint8_t g_land_gate_trip;     /* land_gate_trip_t of the last trip, cleared at takeoff */
extern volatile uint8_t g_land_gate_sat_n;    /* pinned runs >= sat_hold_s this flight (EN_SAT on), saturates at 255 */
extern volatile float   g_land_gate_sat_max_s; /* longest pinned run this flight, s */

void    LandGate_DefaultLimits(land_gate_limits_t *out);
void    LandGate_Reset(land_gate_state_t *s);
/* Returns the trip reason on the one tick the gate fires, otherwise LAND_GATE_NONE. */
uint8_t LandGate_Step(land_gate_state_t *s, const land_gate_limits_t *lim, uint8_t enable, const land_gate_in_t *in);

#endif
