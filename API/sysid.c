/**
 * @module     sysid.c
 * @subsystem  control
 * @owner      Stabilizer_Task (TASK/StabilizerTask.c): SysID_Update each 200 Hz tick, SysID_IsAxisActive/
 *             SysID_GetRateSetpoint to override the excited axis's rate setpoint. Send_Task (send_data.c): SysID_Start/
 *             SysID_Abort/SysID_SetGeofence from CMD 0x14, SysID_GetDither/GetState/GetAxis for telemetry.
 * @purpose    Automated system-identification excitation (ADR-0004): a log chirp or a Schroeder-phased multisine on one
 *             rate axis, with cosine ramps, firmware-checked start preconditions, continuous abort guards (arm, mode, RC
 *             dead-man, altitude band, green-zone geofence, attitude) and a RECOVERY return to the captured centre.
 * @inputs     axis, signal, f0/f1 [Hz], amplitude (deg/s, m/s for Z), duration [s] (Start); DroneStatus, Ctrler FB
 *             values, RC activity (guards).
 * @outputs    rate setpoint override for the active axis, mrac_flags.id_frame_on, Ctrler loc/Z Des on recovery.
 */

#include "sysid.h"
#include <math.h>
#include "global_declare.h"   // DroneStatus, FlyMode_SDK
#include "pid.h"              // Ctrler (CtrlerTypeDef global)
#include "mrac.h"             // mrac_flags.id_frame_on
#include "rc_input.h"         // RCInput_IsActive (RC dead-man)

#ifndef M_PI
#define M_PI 3.14159265358979323846f
#endif

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

/* Multisine component count (array size): log-spaced over [f0, f1], Schroeder-phased for a low crest; 20 tones give a
   dense enough FRF to resolve 2nd-order and higher dynamics. */
#define SYSID_MS_K          20
#define SYSID_SCAN_T_MAX_S  8.0f     /* [s] longest stretch scanned for the multisine peak at Start */
#define SYSID_SCAN_DT_S     0.002f   /* [s] peak-scan step                                            */
#define SYSID_PEAK_MARGIN   1.05f    /* 5% margin on the scanned peak                                 */

/* Safety envelope.
   @ramp_t         s    [0.2, 5]     cosine ramp in and out
   @recovery_t     s    [0.5, 10]    settle time in RECOVERY before returning to IDLE
   @soft_xy_cm     cm   [10, 150]    green-zone soft boundary (abort); closed-loop dither (StabilizerTask += injection)
                                     holds station, so this is a safety net, kept tight for wall margin
   @alt_min_m      m    [0.2, 1.0]   lowest altitude (above ground effect)
   @alt_max_m      m    [0.5, 1.7]   highest altitude (ceiling margin; workflow B caps hover at 1.7 m)
   @angle_lim_deg  deg  [10, 45]     attitude abort on any axis */
typedef struct {
    float ramp_t, recovery_t, soft_xy_cm, alt_min_m, alt_max_m, angle_lim_deg;
} sysid_safety_t;
#define SYSID_SAFETY_ROW(ramp_t, recovery_t, soft_xy_cm, alt_min_m, alt_max_m, angle_lim_deg)     { (ramp_t), (recovery_t), (soft_xy_cm), (alt_min_m), (alt_max_m), (angle_lim_deg) }

static const sysid_safety_t s_safe =
/*               ramp_t recovery_t soft_xy_cm alt_min_m alt_max_m angle_lim_deg */
    SYSID_SAFETY_ROW(1.5f, 2.0f,      50.0f,     0.30f,    1.50f,    30.0f);

/* Signal shape and Start sanitising.
   @ms_preemp  -   [0, 2]      multisine pre-emphasis exponent: tone weight = (f_k/f0)^ms_preemp. The roll/pitch rate
                               plant is integrator-like (|G| ~ 1/w) in the low band, so a flat multisine starves the
                               output above ~5 Hz; 1 cancels the 1/w roll-off so output coherence is ~flat over [f0, f1].
                               0 = flat input (old behaviour). The peak is re-normalised after weighting, so the
                               amplitude clamp is unchanged.
   @f0_min_hz  Hz  [0.05, 1]   lowest start frequency
   @dur_min_s  s   [0.5, 5]    shortest RUNNING window
   @dur_max_s  s   [10, 120]   longest RUNNING window */
typedef struct {
    float ms_preemp, f0_min_hz, dur_min_s, dur_max_s;
} sysid_shape_t;
#define SYSID_SHAPE_ROW(ms_preemp, f0_min_hz, dur_min_s, dur_max_s)     { (ms_preemp), (f0_min_hz), (dur_min_s), (dur_max_s) }

static const sysid_shape_t s_shape =
/*              ms_preemp f0_min_hz dur_min_s dur_max_s */
    SYSID_SHAPE_ROW(1.0f,    0.1f,     1.0f,     60.0f);

// Per-axis amplitude clamps (rate units: deg/s for P/R/Y, m/s for Z)
static float sysid_amp_max(SysID_Axis_e a)
{
    switch (a) {
        case SYSID_AXIS_PITCH:
        case SYSID_AXIS_ROLL:  return 90.0f;  // deg/s
        case SYSID_AXIS_YAW:   return 60.0f;  // deg/s
        case SYSID_AXIS_Z:     return 0.40f;  // m/s
        default:               return 0.0f;
    }
}

// ---- Module state -------------------------------------------------------------------
static SysID_State_e s_state = SYSID_IDLE;
static SysID_Axis_e  s_axis;
static SysID_Signal_e s_sig;
static float s_f0, s_f1, s_amp, s_duration;
static float s_t_sig;        // [s] time since RAMP_IN start
static float s_t_recover;    // [s] time in RECOVERY
static float s_phase;        // chirp phase accumulator
static float s_rate_cmd;     // current excitation rate setpoint (axis native unit)
static float s_cx, s_cy, s_cz; // captured green-zone centre (cx,cy in cm; cz in m)
static float s_ms_f[SYSID_MS_K];
static float s_ms_phi[SYSID_MS_K];
static float s_ms_amp[SYSID_MS_K]; // per-tone amplitude weight (frequency pre-emphasis, see s_shape.ms_preemp)
static float s_ms_norm = 1.0f;  // peak-normalization so multisine peak ~= amp (safety)
static uint8_t s_geofence_en = 1U; // green-zone XY geofence enable (CMD 0x14 idx 7; default ON)

// ---- Helpers ------------------------------------------------------------------------
static void sysid_finish(void)
{
    s_state = SYSID_IDLE;
    s_rate_cmd = 0.0f;
    mrac_flags.id_frame_on = 0; // restore normal A/B telemetry
}

static void sysid_enter_recovery(void)
{
    s_state = SYSID_RECOVERY;
    s_t_recover = 0.0f;
    s_rate_cmd = 0.0f;
    // Release override (IsAxisActive() will now be false) and command return-to-centre.
    // The existing position cascade pulls the drone back; the excited axis re-levels via its
    // outer angle loop now that we no longer override its rate setpoint.
    Ctrler.locxPID.Des   = s_cx;
    Ctrler.locyPID.Des   = s_cy;
    Ctrler.Z_posPID.Des  = s_cz;
}

// Returns 1 if any abort condition currently holds.
static int sysid_abort_condition(void)
{
    if (DroneStatus.ARM_Status == 0) return 1;
    if (DroneStatus.FlyMode != FlyMode_SDK) return 1;
    // RC dead-man: any attitude stick deflected -> instant pilot takeover.
    if (RCInput_IsActive(RC_AXIS_PITCH) || RCInput_IsActive(RC_AXIS_ROLL) || RCInput_IsActive(RC_AXIS_YAW)) return 1;
    // Altitude band.
    if (Ctrler.Z_posPID.FB < s_safe.alt_min_m || Ctrler.Z_posPID.FB > s_safe.alt_max_m) return 1;
    // Green-zone soft boundary (cm). Skipped when the geofence is disabled (pilot-watch override).
    if (s_geofence_en) {
        if (fabsf(Ctrler.locxPID.FB - s_cx) > s_safe.soft_xy_cm) return 1;
        if (fabsf(Ctrler.locyPID.FB - s_cy) > s_safe.soft_xy_cm) return 1;
    }
    // Attitude runaway (any axis).
    if (fabsf(Ctrler.pitchPID.FB) > s_safe.angle_lim_deg) return 1;
    if (fabsf(Ctrler.rollPID.FB)  > s_safe.angle_lim_deg) return 1;
    return 0;
}

// Raw signal in ~[-1,1] at the current s_t_sig (advances s_phase for the chirp).
static float sysid_signal_raw(void)
{
    if (s_sig == SYSID_SIG_MULTISINE) {
        float acc = 0.0f;
        int k;
        for (k = 0; k < SYSID_MS_K; k++) {
            acc += s_ms_amp[k] * sinf(2.0f * M_PI * s_ms_f[k] * s_t_sig + s_ms_phi[k]);
        }
        return acc / s_ms_norm; // peak-normalized at Start so |out| <= ~1 (peak ~= amp)
    } else {
        // Log chirp: hold f0 during ramp-in, sweep f0->f1 across the RUNNING window, hold f1 in ramp-out.
        float tau = (s_t_sig - s_safe.ramp_t) / (s_duration > 1e-3f ? s_duration : 1e-3f);
        float f;
        if (tau < 0.0f) tau = 0.0f;
        if (tau > 1.0f) tau = 1.0f;
        f = s_f0 * powf(s_f1 / s_f0, tau);
        s_phase += 2.0f * M_PI * f * SYSID_DT;
        return sinf(s_phase);
    }
}

// Amplitude envelope (cosine ramp in/out) as a function of s_t_sig; also drives state transitions.
static float sysid_envelope(void)
{
    float t_run_end  = s_safe.ramp_t + s_duration;
    float t_full_end = t_run_end + s_safe.ramp_t;
    if (s_t_sig < s_safe.ramp_t) {
        s_state = SYSID_RAMP_IN;
        return 0.5f * (1.0f - cosf(M_PI * s_t_sig / s_safe.ramp_t));
    } else if (s_t_sig < t_run_end) {
        s_state = SYSID_RUNNING;
        return 1.0f;
    } else if (s_t_sig < t_full_end) {
        float te = (s_t_sig - t_run_end) / s_safe.ramp_t;
        s_state = SYSID_RAMP_OUT;
        return 0.5f * (1.0f + cosf(M_PI * te));
    }
    return -1.0f; // sentinel: excitation window complete
}

// Multisine tables for [f0, f1]: log-spaced tones, Schroeder phases, pre-emphasis weights, peak normalisation.
static void sysid_design_multisine(float f0, float f1, float duration)
{
    int k;

    // Precompute multisine frequencies (log-spaced), Schroeder phases, and pre-emphasis weights.
    // Weight (f_k/f0)^PREEMP boosts high-f tones to counter the plant's low-pass roll-off so the
    // OUTPUT energy (and coherence) is balanced across the band instead of starving above ~5 Hz.
    for (k = 0; k < SYSID_MS_K; k++) {
        float frac = (SYSID_MS_K > 1) ? ((float)k / (float)(SYSID_MS_K - 1)) : 0.0f;
        s_ms_f[k]   = f0 * powf(f1 / f0, frac);
        s_ms_phi[k] = -M_PI * (float)k * (float)k / (float)SYSID_MS_K;
        s_ms_amp[k] = powf(s_ms_f[k] / f0, s_shape.ms_preemp);
    }
    // Peak-normalize: weighted log-spaced tones leave a crest factor ~4, so RMS-scaling would
    // overshoot the commanded amplitude 2-3x. Scan the WEIGHTED waveform once and divide by its
    // peak so |out| <= ~1 (peak ~= amp) -> the safety amplitude clamp holds regardless of PREEMP.
    {
        float tscan = (duration < SYSID_SCAN_T_MAX_S) ? duration : SYSID_SCAN_T_MAX_S;
        float tt, peak = 1e-6f;
        int n;
        for (tt = 0.0f; tt <= tscan; tt += SYSID_SCAN_DT_S) {
            float acc = 0.0f;
            for (n = 0; n < SYSID_MS_K; n++)
                acc += s_ms_amp[n] * sinf(2.0f * M_PI * s_ms_f[n] * tt + s_ms_phi[n]);
            if (fabsf(acc) > peak) peak = fabsf(acc);
        }
        s_ms_norm = peak * SYSID_PEAK_MARGIN;
    }
}

// ---- Public API ---------------------------------------------------------------------
uint8_t SysID_Start(SysID_Axis_e axis, SysID_Signal_e sig, float f0, float f1, float amp, float duration)
{
    float amax;

    if (s_state != SYSID_IDLE) return 0;          // a run is already active
    if ((uint32_t)axis > (uint32_t)SYSID_AXIS_Z) return 0;    // also a "negative" id (PITCH is 0)
    // Z-axis excitation is not yet wired: Compute_Motor only overrides the P/R/Y rate
    // setpoints (StabilizerTask.c), there is no Z_ratePID.Des injection, and Z lacks its
    // own altitude/ground-effect abort guards. Reject Z so a run can't report "active"
    // while doing nothing. Full Z wiring is follow-up (ADR-0004 finding #1).
    if (axis == SYSID_AXIS_Z) return 0;

    // Preconditions (firmware-checkable gates).
    if (DroneStatus.ARM_Status == 0) return 0;
    if (DroneStatus.FlyMode != FlyMode_SDK) return 0;
    if (Ctrler.Z_posPID.FB < s_safe.alt_min_m || Ctrler.Z_posPID.FB > s_safe.alt_max_m) return 0;
    // Must start inside the green zone (skipped when the geofence is disabled).
    if (s_geofence_en &&
        (fabsf(Ctrler.locxPID.FB) > s_safe.soft_xy_cm || fabsf(Ctrler.locyPID.FB) > s_safe.soft_xy_cm)) return 0;

    // Sanitize parameters.
    if (f0 < s_shape.f0_min_hz) f0 = s_shape.f0_min_hz;
    if (f1 < f0)   f1 = f0;
    amax = sysid_amp_max(axis);
    if (amp < 0.0f) amp = -amp;
    if (amp > amax) amp = amax;
    if (duration < s_shape.dur_min_s) duration = s_shape.dur_min_s;
    if (duration > s_shape.dur_max_s) duration = s_shape.dur_max_s;

    s_axis = axis; s_sig = sig;
    s_f0 = f0; s_f1 = f1; s_amp = amp; s_duration = duration;
    s_t_sig = 0.0f; s_phase = 0.0f; s_rate_cmd = 0.0f;

    // Capture green-zone centre (cx,cy in cm; cz in m). OF origin is reset by the CMD handler first.
    s_cx = Ctrler.locxPID.FB;
    s_cy = Ctrler.locyPID.FB;
    s_cz = Ctrler.Z_posPID.FB;

    sysid_design_multisine(f0, f1, duration);

    mrac_flags.id_frame_on = 1; // auto-enable the high-rate ID capture
    s_state = SYSID_RAMP_IN;
    return 1;
}

void SysID_Abort(void)
{
    if (s_state == SYSID_IDLE) return;
    sysid_enter_recovery();
}

void SysID_Update(void)
{
    float env, raw;

    if (s_state == SYSID_IDLE) {
        s_rate_cmd = 0.0f;
        return;
    }

    if (s_state == SYSID_RECOVERY) {
        s_rate_cmd = 0.0f;
        s_t_recover += SYSID_DT;
        if (s_t_recover >= s_safe.recovery_t) sysid_finish();
        return;
    }

    // Active states (RAMP_IN / RUNNING / RAMP_OUT): check aborts first.
    if (sysid_abort_condition()) {
        sysid_enter_recovery();
        return;
    }

    s_t_sig += SYSID_DT;
    env = sysid_envelope();         // also sets s_state among RAMP_IN/RUNNING/RAMP_OUT
    if (env < 0.0f) {               // window complete -> graceful finish
        sysid_finish();
        return;
    }
    raw = sysid_signal_raw();
    s_rate_cmd = env * s_amp * raw; // mean-zero excitation in the axis's rate unit
}

uint8_t SysID_IsAxisActive(SysID_Axis_e axis)
{
    if (axis != s_axis) return 0;
    return (s_state == SYSID_RAMP_IN || s_state == SYSID_RUNNING || s_state == SYSID_RAMP_OUT) ? 1U : 0U;
}

float SysID_GetRateSetpoint(SysID_Axis_e axis)
{
    (void)axis;
    return s_rate_cmd;
}

float SysID_GetDither(void)
{
    return s_rate_cmd; // raw excitation; 0 in IDLE/RECOVERY (sysid_finish/enter_recovery clear it)
}

uint8_t SysID_GetAxis(void)
{
    return (uint8_t)s_axis;
}

uint8_t SysID_GetState(void)
{
    return (uint8_t)s_state;
}

void SysID_SetGeofence(uint8_t enabled)
{
    s_geofence_en = enabled ? 1U : 0U;
}
