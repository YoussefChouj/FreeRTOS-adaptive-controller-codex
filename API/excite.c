/**
 * @module     excite.c
 * @subsystem  control
 * @owner      Stabilizer_Task (TASK/StabilizerTask.c): Excite_Update each 200 Hz tick, Excite_Get added to the
 *             outer (position/attitude) or inner (rate) setpoint and undone after the loop. Send_Task (send_data.c):
 *             CMD 0x2E start/abort, Excite_GetState/GetReason for telemetry.
 * @purpose    Excitation pattern library (docs/design/2026-10-22-mrac-experiment-stack.md, item 1): 24 multisine rows,
 *             one per axis x layer x speed, on disjoint harmonics so several axes can run at once. Host mirror:
 *             ground_station/analysis/excite_patterns.py (its parity test parses EXCITE_ROW below).
 * @inputs     layer, speed, axis mask, periods (CMD 0x2E); DroneStatus, RC activity, sysid state (guards).
 * @outputs    per-axis setpoint add for one layer (0.0f when idle), state and refusal/abort reason.
 */

#include "excite.h"
#include <math.h>
#include "global_declare.h"   // DroneStatus, FlyMode_SDK
#include "pid.h"              // Ctrler
#include "rc_input.h"         // RCInput_IsActive
#include "sysid.h"            // SysID_GetState (one excitation at a time)

#define EXC_PI          3.14159265f
#define EXC_PEAK_GRID   512U    /* samples per period for the peak normalisation (host mirror uses the same) */
#define EXC_MAX_H       10U     /* max harmonics per row */

typedef struct {
    uint8_t axis, layer, speed;
    float   T_s;      /* base period: f0 = 1/T */
    uint8_t j0, nh;   /* harmonic slots j0 .. j0+nh-1 of the axis */
    float   amp;      /* peak after normalisation, unit of the layer's setpoint */
    float   clamp;    /* |output| limit */
} ExciteRow_t;

/* EXCITE_ROW legend:
   @axis    -    [0, 5]        EXC_X .. EXC_ROLL (excite.h)
   @layer   -    [0, 1]        EXC_OUTER = position/attitude setpoint, EXC_INNER = rate setpoint
   @speed   -    [0, 1]        EXC_SLOW / EXC_FAST harmonic set
   @T_s     s    [5, 60]       base period, f0 = 1/T_s; one value per (layer, speed)
   @j0      -    [0, 9]        first harmonic slot of the axis
   @nh      -    [1, 10]       number of harmonics (slots j0 .. j0+nh-1)
   @amp     unit [0.01, 50]    peak after normalisation, unit of the layer's setpoint (see below)
   @clamp   unit [0.1, 100]    |output| limit, same unit */
#define EXCITE_ROW(axis, layer, speed, T_s, j0, nh, amp, clamp) { axis, layer, speed, T_s, j0, nh, amp, clamp }

/* All values PROPOSED (10-22 spec), none measured. Units: outer x/y cm, z m, yaw/pitch/roll deg;
 * inner x/y cm/s, z m/s, yaw/pitch/roll deg/s. Inner p/r/yaw/z clamps = sysid's rate clamps. */
static const ExciteRow_t s_rows[] = {
    /*          axis       layer      speed     T_s    j0 nh  amp     clamp  */
    EXCITE_ROW(EXC_X,     EXC_OUTER, EXC_SLOW, 40.0f, 0, 4, 15.0f,  30.0f ),
    EXCITE_ROW(EXC_Y,     EXC_OUTER, EXC_SLOW, 40.0f, 0, 4, 15.0f,  30.0f ),
    EXCITE_ROW(EXC_Z,     EXC_OUTER, EXC_SLOW, 40.0f, 0, 4,  0.10f,  0.20f),
    EXCITE_ROW(EXC_YAW,   EXC_OUTER, EXC_SLOW, 40.0f, 0, 4, 15.0f,  30.0f ),
    EXCITE_ROW(EXC_PITCH, EXC_OUTER, EXC_SLOW, 40.0f, 0, 4,  3.0f,   8.0f ),
    EXCITE_ROW(EXC_ROLL,  EXC_OUTER, EXC_SLOW, 40.0f, 0, 4,  3.0f,   8.0f ),
    EXCITE_ROW(EXC_X,     EXC_INNER, EXC_SLOW, 10.0f, 0, 5, 15.0f,  30.0f ),
    EXCITE_ROW(EXC_Y,     EXC_INNER, EXC_SLOW, 10.0f, 0, 5, 15.0f,  30.0f ),
    EXCITE_ROW(EXC_Z,     EXC_INNER, EXC_SLOW, 10.0f, 0, 5,  0.15f,  0.40f),
    EXCITE_ROW(EXC_YAW,   EXC_INNER, EXC_SLOW, 10.0f, 0, 5, 30.0f,  60.0f ),
    EXCITE_ROW(EXC_PITCH, EXC_INNER, EXC_SLOW, 10.0f, 0, 5, 30.0f,  90.0f ),
    EXCITE_ROW(EXC_ROLL,  EXC_INNER, EXC_SLOW, 10.0f, 0, 5, 30.0f,  90.0f ),
    EXCITE_ROW(EXC_X,     EXC_OUTER, EXC_FAST, 40.0f, 4, 4,  8.0f,  30.0f ),
    EXCITE_ROW(EXC_Y,     EXC_OUTER, EXC_FAST, 40.0f, 4, 4,  8.0f,  30.0f ),
    EXCITE_ROW(EXC_Z,     EXC_OUTER, EXC_FAST, 40.0f, 4, 4,  0.05f,  0.20f),
    EXCITE_ROW(EXC_YAW,   EXC_OUTER, EXC_FAST, 40.0f, 4, 4,  8.0f,  30.0f ),
    EXCITE_ROW(EXC_PITCH, EXC_OUTER, EXC_FAST, 40.0f, 4, 4,  2.0f,   8.0f ),
    EXCITE_ROW(EXC_ROLL,  EXC_OUTER, EXC_FAST, 40.0f, 4, 4,  2.0f,   8.0f ),
    EXCITE_ROW(EXC_X,     EXC_INNER, EXC_FAST, 10.0f, 5, 5, 10.0f,  30.0f ),
    EXCITE_ROW(EXC_Y,     EXC_INNER, EXC_FAST, 10.0f, 5, 5, 10.0f,  30.0f ),
    EXCITE_ROW(EXC_Z,     EXC_INNER, EXC_FAST, 10.0f, 5, 5,  0.10f,  0.40f),
    EXCITE_ROW(EXC_YAW,   EXC_INNER, EXC_FAST, 10.0f, 5, 5, 20.0f,  60.0f ),
    EXCITE_ROW(EXC_PITCH, EXC_INNER, EXC_FAST, 10.0f, 5, 5, 20.0f,  90.0f ),
    EXCITE_ROW(EXC_ROLL,  EXC_INNER, EXC_FAST, 10.0f, 5, 5, 20.0f,  90.0f ),
};
#define EXC_N_ROWS (sizeof(s_rows) / sizeof(s_rows[0]))

/* Guards: sysid's safety row (alt 0.30-1.50 m, soft xy 50 cm, angle 30 deg); xy from the start position. */
#define EXC_ALT_MIN_M     0.30f
#define EXC_ALT_MAX_M     1.50f
#define EXC_SOFT_XY_CM    50.0f
#define EXC_ANGLE_LIM_DEG 30.0f

volatile uint8_t excite_state = EXC_IDLE;
volatile uint8_t excite_reason = EXC_OK;
volatile uint8_t excite_layer = 0U, excite_speed = 0U, excite_mask = 0U;
volatile uint16_t excite_period = 0U;
volatile float excite_out[EXC_AXES];

static const ExciteRow_t *s_row[EXC_AXES];   /* row per excited axis, NULL when off */
static float s_gain[EXC_AXES];               /* amp / peak of the unit-amplitude sum */
static uint32_t s_n;                         /* tick since start */
static uint32_t s_n_period;                  /* ticks per base period */
static uint32_t s_n_total;                   /* ticks in the run */
static float s_cx, s_cy;                     /* start position [cm] */

static uint16_t exc_harmonic(uint8_t axis, const ExciteRow_t *r, uint8_t j)
{
    return (uint16_t)(axis + 1U + 6U * (r->j0 + j));
}

/* Unit-amplitude Schroeder multisine of row r at sample m of a period of n_p samples. */
static float exc_raw(uint8_t axis, const ExciteRow_t *r, uint32_t m, uint32_t n_p)
{
    float s = 0.0f;
    uint8_t j;
    for (j = 0U; j < r->nh; j++) {
        uint32_t k = ((uint32_t)exc_harmonic(axis, r, j) * m) % n_p;
        float ph = -EXC_PI * (float)j * (float)(j + 1U) / (float)r->nh;
        s += cosf(2.0f * EXC_PI * (float)k / (float)n_p + ph);
    }
    return s;
}

static const ExciteRow_t *exc_find(uint8_t axis, uint8_t layer, uint8_t speed)
{
    uint8_t i;
    for (i = 0U; i < EXC_N_ROWS; i++) {
        if (s_rows[i].axis == axis && s_rows[i].layer == layer && s_rows[i].speed == speed) return &s_rows[i];
    }
    return 0;
}

static uint8_t exc_guard(uint8_t flying)
{
    if (DroneStatus.ARM_Status == 0) return EXC_R_DISARMED;
    if (DroneStatus.FlyMode != FlyMode_SDK) return EXC_R_MODE;
    if (!flying) return EXC_R_GROUND;
    if (RCInput_IsActive(RC_AXIS_PITCH) || RCInput_IsActive(RC_AXIS_ROLL) || RCInput_IsActive(RC_AXIS_YAW)) return EXC_R_RC;
    if (Ctrler.Z_posPID.FB < EXC_ALT_MIN_M || Ctrler.Z_posPID.FB > EXC_ALT_MAX_M) return EXC_R_ALT;
    if (fabsf(Ctrler.pitchPID.FB) > EXC_ANGLE_LIM_DEG || fabsf(Ctrler.rollPID.FB) > EXC_ANGLE_LIM_DEG) return EXC_R_ANGLE;
    if (SysID_GetState() != (uint8_t)SYSID_IDLE) return EXC_R_SYSID;
    return EXC_OK;
}

static void exc_off(uint8_t reason)
{
    uint8_t a;
    excite_state = EXC_IDLE;
    excite_reason = reason;
    for (a = 0U; a < EXC_AXES; a++) { s_row[a] = 0; excite_out[a] = 0.0f; }
}

/* Called from the GS command task; the control task only reads s_row/s_gain while RUNNING,
 * and RUNNING is set last. flying is not known here: the first Excite_Update checks it. */
uint8_t Excite_Start(uint8_t layer, uint8_t speed, uint8_t mask, uint8_t n_periods)
{
    uint8_t a, g;
    float T_s = 0.0f;
    if (excite_state != EXC_IDLE) exc_off(EXC_R_CMD);
    if (layer > EXC_INNER || speed > EXC_FAST || mask == 0U || mask >= (1U << EXC_AXES) ||
        n_periods == 0U || n_periods > 10U) {
        excite_reason = EXC_R_ARGS;
        return EXC_R_ARGS;
    }
    g = exc_guard(1U);
    if (g != EXC_OK) { excite_reason = g; return g; }
    for (a = 0U; a < EXC_AXES; a++) {
        const ExciteRow_t *r = (mask & (1U << a)) ? exc_find(a, layer, speed) : 0;
        s_row[a] = r;
        s_gain[a] = 0.0f;
        if (r) T_s = r->T_s;   /* one layer + speed share T (table rows) */
    }
    s_n_period = (uint32_t)(T_s / EXCITE_DT + 0.5f);
    for (a = 0U; a < EXC_AXES; a++) {
        const ExciteRow_t *r = s_row[a];
        float peak = 0.0f;
        uint32_t i;
        if (!r || r->nh == 0U || r->nh > EXC_MAX_H) { s_row[a] = 0; continue; }
        for (i = 0U; i < EXC_PEAK_GRID; i++) {
            float v = fabsf(exc_raw(a, r, i * s_n_period / EXC_PEAK_GRID, s_n_period));
            if (v > peak) peak = v;
        }
        s_gain[a] = (peak > 0.0f) ? r->amp / peak : 0.0f;
    }
    s_n = 0U;
    s_n_total = s_n_period * n_periods;
    s_cx = Ctrler.locxPID.FB;
    s_cy = Ctrler.locyPID.FB;
    excite_layer = layer;
    excite_speed = speed;
    excite_mask = mask;
    excite_period = 0U;
    excite_reason = EXC_OK;
    excite_state = EXC_RUNNING;
    return EXC_OK;
}

void Excite_Abort(uint8_t reason)
{
    if (excite_state != EXC_IDLE) exc_off(reason);
}

void Excite_Update(uint8_t flying)
{
    uint8_t a, g;
    float t, env;
    if (excite_state != EXC_RUNNING) return;
    g = exc_guard(flying);
    if (g == EXC_OK && (fabsf(Ctrler.locxPID.FB - s_cx) > EXC_SOFT_XY_CM ||
                        fabsf(Ctrler.locyPID.FB - s_cy) > EXC_SOFT_XY_CM)) g = EXC_R_XY;
    if (g != EXC_OK) { exc_off(g); return; }
    if (s_n >= s_n_total) { exc_off(EXC_R_DONE); return; }
    t = (float)s_n * EXCITE_DT;
    env = 1.0f;
    if (t < EXCITE_FADE_S) env = 0.5f - 0.5f * cosf(EXC_PI * t / EXCITE_FADE_S);
    else if ((float)(s_n_total - s_n) * EXCITE_DT < EXCITE_FADE_S)
        env = 0.5f - 0.5f * cosf(EXC_PI * (float)(s_n_total - s_n) * EXCITE_DT / EXCITE_FADE_S);
    for (a = 0U; a < EXC_AXES; a++) {
        const ExciteRow_t *r = s_row[a];
        float u = 0.0f;
        if (r) {
            u = env * s_gain[a] * exc_raw(a, r, s_n % s_n_period, s_n_period);
            if (u > r->clamp) u = r->clamp;
            if (u < -r->clamp) u = -r->clamp;
        }
        excite_out[a] = u;
    }
    excite_period = (uint16_t)(s_n / s_n_period);
    s_n++;
}

float Excite_Get(uint8_t axis, uint8_t layer)
{
    if (excite_state != EXC_RUNNING || axis >= EXC_AXES || layer != excite_layer) return 0.0f;
    return excite_out[axis];
}
