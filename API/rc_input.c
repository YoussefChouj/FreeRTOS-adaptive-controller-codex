#include "rc_input.h"
#include "FreeRTOS.h"
#include "task.h"
#include "global_declare.h"

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

/* Stick dead-zone thresholds in normalised units.
 * Derived from former SBUS macros: SBUS_OFFSET=100 and SBUS_THR_OFFSET=50,
 * both relative to a 1000-unit half-range, so 100/1000 = 0.10, 50/1000 = 0.05. */
#define RC_ACTIVE_THRESHOLD      0.10f
#define RC_THR_ACTIVE_THRESHOLD  0.05f

/* Bench-mode height zero (CMD 0x07): when active, the OF height is offset so the drone
 * treats its resting position as z=0. Prop wash displaces it by ±Δ and the position
 * controller does not fight to return to a virtual ground. Safe on 4DOF fixture. */

/* Physical RC takeover rate threshold (per 10 ms RemoterTask tick).
 * Compares consecutive tick values, not a fixed snapshot.
 * 0.05 = 5 % of full range per tick → catches a full-range deflection
 * in ~0.4 s while ignoring RC noise (< 0.005/tick) and slow drift. */
#define RC_PHYSICAL_RATE_DELTA  0.05f

/* Ticks after authority grant before takeover check activates.
 * 10 × 10 ms = 100 ms for SBUS and Remoter values to settle. */
#define RC_AUTHORITY_GRACE_TICKS 10U

/* ------------------------------------------------------------------
 * Module state  (all private to this translation unit)
 * ------------------------------------------------------------------ */
static float      s_virtual[4]       = {0.0f, 0.0f, 0.0f, 0.0f};
static float      s_physical_snap[4] = {0.0f, 0.0f, 0.0f, 0.0f};
static uint8_t    s_authority        = 0U;
static TickType_t s_last_update_tick = 0U;
static uint8_t    s_heartbeat_active = 0U;
static uint8_t    s_heartbeat_lost   = 0U;
static uint16_t   s_authority_grace  = 0U;

/* Physical stick neutral auto-calibration.
 * The transmitter's mechanical stick centre can sit off the nominal 3000 raw
 * units (measured: a standing ~+0.95 deg pitch-lean command with sticks
 * released across several flights). We capture the resting centre of pitch/roll/
 * yaw once at boot and subtract it in RCInput_Get so released sticks read true
 * zero. Throttle is never offset (its rest position is stick-down, not centre).
 * Capture is gated on a valid SBUS link and every axis sitting within a small
 * window of 3000, so a stick held off-centre during boot cannot poison it. */
#define RC_NEUTRAL_WINDOW   200.0f   /* max |raw-3000| accepted as "at rest"      */
#define RC_NEUTRAL_FRAMES   50U      /* ~0.5 s of stable frames averaged (10 ms)  */

static float      s_neutral[4]   = {0.0f, 0.0f, 0.0f, 0.0f}; /* raw-unit centre offset; [thr,pit,rol,yaw] */
static uint8_t    s_neutral_done = 0U;
static uint16_t   s_neutral_cnt  = 0U;
static float      s_neutral_acc[3] = {0.0f, 0.0f, 0.0f};     /* pit,rol,yaw sums */

/* ------------------------------------------------------------------
 * Internal helpers
 * ------------------------------------------------------------------ */

/* Convert from SBUS-scaled [2000, 4000] (centre 3000) to [-1, 1]. */
static float s_normalize(float raw)
{
    return (raw - 3000.0f) / 1000.0f;
}

static float s_clamp(float v, float lo, float hi)
{
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

float RCInput_Get(RC_Axis_t axis)
{
    float v;

    if (s_authority) {
        /* PC has authority: virtual sticks take over, physical RC ignored.
         * RC mode switch (ch10) remains the hard kill — it acts on FlyMode,
         * not on this function, so it always works regardless of authority. */
        switch (axis) {
            case RC_AXIS_THR:   v = s_virtual[0]; break;
            case RC_AXIS_PITCH: v = s_virtual[1]; break;
            case RC_AXIS_ROLL:  v = s_virtual[2]; break;
            case RC_AXIS_YAW:   v = s_virtual[3]; break;
            default:            v = 0.0f;          break;
        }
    } else {
        /* Pilot mode: physical RC sticks. */
        switch (axis) {
            case RC_AXIS_THR:   v = s_normalize((float)Remoter.ThrCtrler); break;
            case RC_AXIS_PITCH: v = s_normalize((float)Remoter.PitCtrler - s_neutral[1]); break;
            case RC_AXIS_ROLL:  v = s_normalize((float)Remoter.RolCtrler - s_neutral[2]); break;
            case RC_AXIS_YAW:   v = s_normalize((float)Remoter.YawCtrler - s_neutral[3]); break;
            default:            v = 0.0f; break;
        }
    }

    v = s_clamp(v, -1.0f, 1.0f);

    return v;
}

int RCInput_IsActive(RC_Axis_t axis)
{
    float v, threshold;

    /* When PC has authority, apply dead-zones to ALL axes so that spring-returned
     * sticks (value = 0.0) are treated as "not active".  This lets the position
     * PID outputs flow through to the velocity loop (TWC / auto-position tracking).
     * When a VRC slider is actively deflected past the threshold the stick value
     * takes over, overriding the position PID — same behaviour as physical RC. */
    if (s_authority) {
        if ((unsigned int)axis < 4U) {
            v = s_virtual[(unsigned int)axis];
            threshold = (axis == RC_AXIS_THR) ? RC_THR_ACTIVE_THRESHOLD
                                               : RC_ACTIVE_THRESHOLD;
            return (v > threshold || v < -threshold) ? 1 : 0;
        }
        return 0;
    }

    v         = RCInput_Get(axis);
    threshold = (axis == RC_AXIS_THR) ? RC_THR_ACTIVE_THRESHOLD : RC_ACTIVE_THRESHOLD;

    return (v > threshold || v < -threshold) ? 1 : 0;
}

void RCInput_SetVirtualStick(RC_Axis_t axis, float val)
{
    if ((unsigned int)axis < 4U) {
        s_virtual[(unsigned int)axis] = s_clamp(val, -1.0f, 1.0f);
        s_last_update_tick            = xTaskGetTickCount();
        s_heartbeat_active            = 1U;
        s_heartbeat_lost              = 0U;
    }
}

void RCInput_SetAuthority(uint8_t has_authority)
{
    if (has_authority) {
        /* Pre-arm throttle to minimum to prevent a sudden jump.
         * Physical RC throttle is at -1.0 when the pilot's stick is down;
         * s_virtual[0] would otherwise be 0.0 (centre) → drone climbs. */
        s_virtual[0] = -1.0f;
        /* Pitch/roll/yaw stay at 0.0 (centre is safe for those axes). */
        /* Seed the rate-of-change buffer with current physical RC values so
         * the first Update() tick compares against "now", not against 0.0. */
        s_physical_snap[0] = s_normalize((float)Remoter.ThrCtrler);
        s_physical_snap[1] = s_normalize((float)Remoter.PitCtrler);
        s_physical_snap[2] = s_normalize((float)Remoter.RolCtrler);
        s_physical_snap[3] = s_normalize((float)Remoter.YawCtrler);
        s_authority_grace = RC_AUTHORITY_GRACE_TICKS;
        s_authority  = 1U;
    } else {
        /* Relinquish: reset all virtual sticks to centre so the drone
         * transitions to altitude / position hold on the next cycle. */
        s_authority      = 0U;
        s_virtual[0]     = 0.0f;
        s_virtual[1]     = 0.0f;
        s_virtual[2]     = 0.0f;
        s_virtual[3]     = 0.0f;
        s_heartbeat_active = 0U;
        s_heartbeat_lost   = 0U;
        s_authority_grace  = 0U;
    }
}

uint8_t RCInput_GetAuthority(void)
{
    return s_authority;
}

void RCInput_Update(void)
{
    if (!s_authority) {
        return;
    }

    /* --- Physical RC takeover check (emergency manual override) ---
     * Rate-of-change detection: fires when the pilot actively moves a stick.
     * This is the pilot's emergency override and MUST work even during a GS-
     * controlled flight (path presets / VRC), so it is intentionally NOT
     * suppressed by GS_KeySDKflag or by an active GS keepalive heartbeat.
     * Only gated on a valid SBUS link: if sbus_lost, physical stick readings
     * are stale/invalid and cannot be trusted to detect motion.
     * On trigger, authority is dropped → RCInput_Get() returns physical sticks
     * and AutoflyTask_PathArbitrate() stops all presets (clean handoff to
     * manual alt/position-hold). The ch10 DANGEROUS_STOP remains the hard kill.
     * The heartbeat watchdog below is the separate failsafe for GS disconnect. */
    if (!sbus_lost) {
        float cur0 = s_normalize((float)Remoter.ThrCtrler);
        float cur1 = s_normalize((float)Remoter.PitCtrler);
        float cur2 = s_normalize((float)Remoter.RolCtrler);
        float cur3 = s_normalize((float)Remoter.YawCtrler);

        if (s_authority_grace > 0U) {
            /* Keep snap rolling during grace — no takeover check.
             * Lets SBUS settle before rate-of-change detection starts. */
            s_authority_grace--;
            s_physical_snap[0] = cur0;
            s_physical_snap[1] = cur1;
            s_physical_snap[2] = cur2;
            s_physical_snap[3] = cur3;
        } else {
            float d0 = cur0 - s_physical_snap[0]; if (d0 < 0.0f) d0 = -d0;
            float d1 = cur1 - s_physical_snap[1]; if (d1 < 0.0f) d1 = -d1;
            float d2 = cur2 - s_physical_snap[2]; if (d2 < 0.0f) d2 = -d2;
            float d3 = cur3 - s_physical_snap[3]; if (d3 < 0.0f) d3 = -d3;
            s_physical_snap[0] = cur0;
            s_physical_snap[1] = cur1;
            s_physical_snap[2] = cur2;
            s_physical_snap[3] = cur3;

            if (d0 > RC_PHYSICAL_RATE_DELTA || d1 > RC_PHYSICAL_RATE_DELTA ||
                d2 > RC_PHYSICAL_RATE_DELTA || d3 > RC_PHYSICAL_RATE_DELTA) {
                s_authority        = 0U;
                s_heartbeat_active = 0U;
                s_heartbeat_lost   = 0U;
                GS_KeySDKflag      = 0U; /* GS no longer holds SDK authority after manual takeover */
                return;
            }
        }
    }

    /* --- Heartbeat watchdog ---
     * Fires only after the first CMD 0x06 has arrived (s_heartbeat_active=1).
     * The GS keepalive sends CMD 0x06 at 50 Hz, so this never fires in normal
     * operation.  If the GS process crashes or the cable is pulled, the
     * keepalive stops and after RC_HEARTBEAT_TIMEOUT_MS authority is revoked
     * so the physical RC pilot can take over safely. */
    if (!s_heartbeat_active) {
        return;
    }

    TickType_t now     = xTaskGetTickCount();
    TickType_t elapsed = now - s_last_update_tick;

    if (elapsed > pdMS_TO_TICKS(RC_HEARTBEAT_TIMEOUT_MS)) {
        s_authority        = 0U;
        s_virtual[0]       = 0.0f;
        s_virtual[1]       = 0.0f;
        s_virtual[2]       = 0.0f;
        s_virtual[3]       = 0.0f;
        s_heartbeat_lost   = 1U;
    }
}

void RCInput_UpdateNeutral(void)
{
    float pit, rol, yaw;

    if (s_neutral_done) {
        return;
    }

    /* Need a live SBUS link; stale readings must never be captured as neutral. */
    if (sbus_lost) {
        s_neutral_cnt    = 0U;
        s_neutral_acc[0] = 0.0f;
        s_neutral_acc[1] = 0.0f;
        s_neutral_acc[2] = 0.0f;
        return;
    }

    pit = (float)Remoter.PitCtrler - 3000.0f;
    rol = (float)Remoter.RolCtrler - 3000.0f;
    yaw = (float)Remoter.YawCtrler - 3000.0f;

    /* Any axis deflected past the rest window => a stick is being held; restart. */
    if (pit >  RC_NEUTRAL_WINDOW || pit < -RC_NEUTRAL_WINDOW ||
        rol >  RC_NEUTRAL_WINDOW || rol < -RC_NEUTRAL_WINDOW ||
        yaw >  RC_NEUTRAL_WINDOW || yaw < -RC_NEUTRAL_WINDOW) {
        s_neutral_cnt    = 0U;
        s_neutral_acc[0] = 0.0f;
        s_neutral_acc[1] = 0.0f;
        s_neutral_acc[2] = 0.0f;
        return;
    }

    s_neutral_acc[0] += pit;
    s_neutral_acc[1] += rol;
    s_neutral_acc[2] += yaw;
    s_neutral_cnt++;

    if (s_neutral_cnt >= RC_NEUTRAL_FRAMES) {
        s_neutral[1] = s_neutral_acc[0] / (float)RC_NEUTRAL_FRAMES; /* pitch */
        s_neutral[2] = s_neutral_acc[1] / (float)RC_NEUTRAL_FRAMES; /* roll  */
        s_neutral[3] = s_neutral_acc[2] / (float)RC_NEUTRAL_FRAMES; /* yaw   */
        s_neutral_done = 1U;
    }
}

int RCInput_IsHeartbeatLost(void)
{
    return (int)s_heartbeat_lost;
}
